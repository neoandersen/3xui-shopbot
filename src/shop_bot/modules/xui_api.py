import uuid
from datetime import datetime, timedelta
import logging
import re
from urllib.parse import urlparse
from typing import List, Dict

import requests
import urllib3

from py3xui import Api, Client, Inbound

from shop_bot.data_manager.database import get_host, get_key_by_email, get_setting

# Disable TLS warning noise when verifying self-signed 3X-UI panel certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Persistent host sessions to retain CSRF tokens and cookies between calls
_HOST_SESSIONS: Dict[str, requests.Session] = {}


def get_or_create_host_session(base_url: str) -> requests.Session:
    """Retrieve or initialize a persistent requests.Session for the host base URL."""
    clean_base = base_url.rstrip("/")
    session = _HOST_SESSIONS.get(clean_base)
    if session is None:
        session = requests.Session()
        _HOST_SESSIONS[clean_base] = session
    return session


def _bind_session_to_api(api: Api, session: requests.Session) -> None:
    """
    Bind requests.Session to py3xui Api instance and its sub-APIs (client, inbound, database, server).
    Ensures all underlying HTTP calls route through session and preserve X-Csrf-Token and session cookies.
    """
    setattr(api, "http_session", session)
    setattr(api, "_http_session", session)

    sub_apis = [
        getattr(api, "client", None),
        getattr(api, "inbound", None),
        getattr(api, "database", None),
        getattr(api, "server", None),
    ]

    for sub in sub_apis:
        if not sub:
            continue
        setattr(sub, "http_session", session)
        setattr(sub, "_http_session", session)

        orig_request = getattr(sub, "_request_with_retry", None)
        if orig_request is not None and not getattr(sub, "_session_bound", False):
            def make_patched(sub_instance, orig_func):
                def patched_request_with_retry(method, url, headers, **kwargs):
                    target_method = session.post if getattr(method, "__name__", "") == "post" else (
                        session.get if getattr(method, "__name__", "") == "get" else session.request
                    )
                    req_headers = dict(headers or {})
                    for k, v in session.headers.items():
                        if k not in req_headers:
                            req_headers[k] = v
                    return orig_func(target_method, url, req_headers, **kwargs)
                return patched_request_with_retry

            sub._request_with_retry = make_patched(sub, orig_request)
            sub._session_bound = True


def login_to_host(host_url: str, username: str, password: str, inbound_id: int) -> tuple[Api | None, Inbound | None]:
    base_url = host_url.rstrip("/")
    try:
        session = get_or_create_host_session(base_url)

        # 1. Probe base URL to initialize session cookie and parse CSRF token
        try:
            resp = session.get(f"{base_url}/", verify=False, timeout=10)
            token_match = re.search(
                r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
                resp.text,
                re.IGNORECASE,
            )
            if not token_match:
                # Fallback in case attribute order is reversed: content="..." name="csrf-token"
                token_match = re.search(
                    r'content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']',
                    resp.text,
                    re.IGNORECASE,
                )

            if token_match:
                csrf_token = token_match.group(1).strip()
                session.headers["X-Csrf-Token"] = csrf_token
                logger.debug(f"CSRF token successfully extracted from '{base_url}/'")
            else:
                logger.debug(f"No CSRF meta tag found in markup at '{base_url}/', keeping existing session token if any")
        except Exception as probe_err:
            logger.warning(f"Failed to probe '{base_url}/' for CSRF token: {probe_err}")

        # 2. Update session headers with origin, referer, XMLHttpRequest
        session.headers["Origin"] = base_url
        session.headers["Referer"] = f"{base_url}/"
        session.headers["X-Requested-With"] = "XMLHttpRequest"

        # 3. Initialize py3xui Api and bind session
        api = Api(host=host_url, username=username, password=password)
        _bind_session_to_api(api, session)

        current_csrf = session.headers.get("X-Csrf-Token")
        if current_csrf:
            if hasattr(api, "csrf_token"):
                try:
                    api.csrf_token = current_csrf
                except Exception:
                    pass
            for sub in (getattr(api, "client", None), getattr(api, "inbound", None), getattr(api, "database", None), getattr(api, "server", None)):
                if sub and hasattr(sub, "csrf_token"):
                    try:
                        sub.csrf_token = current_csrf
                    except Exception:
                        pass

        # 4. Authenticate via py3xui using active session
        api.login()

        # Ensure session cookie is preserved across session calls
        if hasattr(api, "session") and api.session and hasattr(api, "cookie_name") and api.cookie_name:
            session.cookies.set(api.cookie_name, api.session)

        # 5. Fetch target inbound
        inbounds: List[Inbound] = api.inbound.get_list()
        target_inbound = next((inbound for inbound in inbounds if inbound.id == inbound_id), None)
        
        if target_inbound is None:
            logger.error(f"Входящий трафик с ID '{inbound_id}' не найден на хосте '{host_url}'")
            return None, None
        return api, target_inbound
    except Exception as e:
        logger.error(f"Не удалось выполнить вход или получить входящий трафик для хоста '{host_url}': {e}", exc_info=True)
        return None, None


def get_connection_string(inbound: Inbound, user_uuid: str, host_url: str, remark: str) -> str | None:
    if not inbound: return None
    settings = inbound.stream_settings.reality_settings.get("settings")
    if not settings: return None
    
    public_key = settings.get("publicKey")
    fp = settings.get("fingerprint")
    server_names = inbound.stream_settings.reality_settings.get("serverNames")
    short_ids = inbound.stream_settings.reality_settings.get("shortIds")
    port = inbound.port
    
    if not all([public_key, server_names, short_ids]): return None
    
    parsed_url = urlparse(host_url)
    short_id = short_ids[0]
    
    connection_string = (
        f"vless://{user_uuid}@{parsed_url.hostname}:{port}"
        f"?type=tcp&security=reality&pbk={public_key}&fp={fp}&sni={server_names[0]}"
        f"&sid={short_id}&spx=%2F&flow=xtls-rprx-vision#{remark}"
    )
    return connection_string

def get_subscription_link(user_uuid: str, host_url: str, host_name: str | None = None, sub_token: str | None = None) -> str:
    """Build client subscription URL via normalization:
    f"{sub_domain.rstrip('/')}/{sub_path.strip('/')}/{client_sub_id}"
    Parameters:
    - sub_domain (default: "https://sub.neo-vpn.click")
    - sub_path (default: "/sub/")
    """
    client_sub_id = (sub_token or user_uuid or "").strip()
    sub_domain = None
    sub_path = None

    if host_name:
        try:
            host = get_host(host_name)
            if host:
                sub_domain = (host.get("sub_domain") or "").strip()
                sub_path = (host.get("sub_path") or "").strip()
                # Backwards compatibility with legacy subscription_url
                if not sub_domain and host.get("subscription_url"):
                    legacy_url = host.get("subscription_url").strip().replace("{token}", "").rstrip('/')
                    parsed = urlparse(legacy_url)
                    if parsed.scheme and parsed.netloc:
                        sub_domain = f"{parsed.scheme}://{parsed.netloc}"
                        sub_path = parsed.path or "/sub/"
                    else:
                        sub_domain = legacy_url
        except Exception:
            pass

    if not sub_domain:
        sub_domain = "https://sub.neo-vpn.click"
    if not sub_path:
        sub_path = "/sub/"

    norm_domain = sub_domain.rstrip('/')
    norm_path = sub_path.strip('/')
    if norm_path:
        return f"{norm_domain}/{norm_path}/{client_sub_id}"
    return f"{norm_domain}/{client_sub_id}"

def update_or_create_client_on_panel(api: Api, inbound_id: int, email: str, days_to_add: int | None = None, target_expiry_ms: int | None = None) -> tuple[str | None, int | None, str | None]:
    try:
        base_url = str(getattr(api, "host", "") or "").rstrip("/")
        session = getattr(api, "http_session", None) or getattr(api, "_http_session", None)
        if not session and hasattr(api, "client"):
            session = getattr(api.client, "http_session", None) or getattr(api.client, "_http_session", None)
        if not session:
            session = get_or_create_host_session(base_url)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        target_inbounds = [1, 2]

        # 1. Получение списка клиентов хоста (для проверки существования):
        # Запрос: GET f"{base_url}/panel/api/clients/list"
        clients_list = []
        try:
            r = session.get(f"{base_url}/panel/api/clients/list", timeout=10, verify=False)
            if r.status_code == 200:
                clients_list = r.json().get('obj', []) or []
            else:
                logger.warning(f"GET /panel/api/clients/list вернул статус {r.status_code}: {r.text[:200]}")
        except Exception as list_err:
            logger.warning(f"Ошибка при запросе списка клиентов 3X-UI: {list_err}")

        # Находим клиента по c.get('email') == email
        existing_client = next((c for c in clients_list if c.get('email') == email), None)

        # Вычисляем new_expiry_ms
        if target_expiry_ms is not None:
            new_expiry_ms = int(target_expiry_ms)
        else:
            if days_to_add is None:
                raise ValueError("Либо days_to_add, либо target_expiry_ms должны быть указаны")
            now_ms = int(datetime.now().timestamp() * 1000)
            if existing_client:
                curr_exp = int(existing_client.get('expiryTime') or 0)
                if curr_exp > now_ms:
                    new_expiry_dt = datetime.fromtimestamp(curr_exp / 1000) + timedelta(days=days_to_add)
                else:
                    new_expiry_dt = datetime.now() + timedelta(days=days_to_add)
            else:
                new_expiry_dt = datetime.now() + timedelta(days=days_to_add)
            new_expiry_ms = int(new_expiry_dt.timestamp() * 1000)

        # 3. ПРОДЛЕНИЕ / ОБНОВЛЕНИЕ клиента (если клиент уже найден в списке):
        if existing_client:
            client_uuid = existing_client.get('uuid') or existing_client.get('id')
            if not client_uuid or not isinstance(client_uuid, str) or len(client_uuid) < 10:
                db_key = get_key_by_email(email)
                if db_key and db_key.get('xui_client_uuid'):
                    client_uuid = db_key['xui_client_uuid']
                else:
                    client_uuid = str(uuid.uuid4())
            else:
                client_uuid = str(client_uuid)

            client_sub_token = existing_client.get('subId') or existing_client.get('subscription')
            if not client_sub_token:
                import secrets
                client_sub_token = secrets.token_hex(12)
            else:
                client_sub_token = str(client_sub_token)

            # Нативные эндпоинты 3X-UI v3.10+
            url_email = f"{base_url}/panel/api/clients/update/{email}"
            url_std = f"{base_url}/panel/api/clients/update"

            payload_direct = {
                "id": str(client_uuid),
                "email": str(email),
                "expiryTime": int(new_expiry_ms),
                "enable": True,
                "subId": str(client_sub_token),
                "reset": 0,
                "inboundIds": target_inbounds
            }
            payload_wrapped = {
                "client": {
                    "id": str(client_uuid),
                    "email": str(email),
                    "expiryTime": int(new_expiry_ms),
                    "enable": True,
                    "subId": str(client_sub_token),
                    "reset": 0
                },
                "inboundIds": target_inbounds
            }

            updated = False
            try:
                res = session.post(url_email, json=payload_direct, headers=headers, timeout=10, verify=False)
                if res.status_code == 200 and res.json().get('success'):
                    logger.info(f"Клиент '{email}' ({client_uuid}) успешно обновлён в 3X-UI через {url_email}.")
                    updated = True
            except Exception as up_err:
                logger.warning(f"Ошибка при обновлении через {url_email}: {up_err}")

            if not updated:
                try:
                    res_std = session.post(url_std, json=payload_wrapped, headers=headers, timeout=10, verify=False)
                    if res_std.status_code == 200 and res_std.json().get('success'):
                        logger.info(f"Клиент '{email}' ({client_uuid}) успешно обновлён в 3X-UI через {url_std}.")
                        updated = True
                except Exception as up_std_err:
                    logger.warning(f"Ошибка при обновлении через {url_std}: {up_std_err}")

            if not updated:
                logger.warning(f"Нативный API update не сработал для '{email}', выполняю py3xui fallback...")
                try:
                    inbound_to_modify = api.inbound.get_by_id(inbound_id)
                    if inbound_to_modify and inbound_to_modify.settings and inbound_to_modify.settings.clients:
                        for cl in inbound_to_modify.settings.clients:
                            if getattr(cl, 'email', None) == email:
                                cl.expiry_time = new_expiry_ms
                                cl.enable = True
                                cl.reset = 0
                                if hasattr(api, 'client') and hasattr(api.client, 'update'):
                                    api.client.update(client_uuid, cl)
                                    logger.info(f"Клиент {email} обновлен через api.client.update (fallback).")
                                    updated = True
                                    break
                except Exception as fb_err:
                    logger.warning(f"py3xui fallback error: {fb_err}")

            return str(client_uuid), int(new_expiry_ms), str(client_sub_token)

        # 2. Добавление НОВОГО клиента:
        else:
            import secrets
            db_key = get_key_by_email(email)
            if db_key and db_key.get('xui_client_uuid'):
                client_uuid = str(db_key['xui_client_uuid'])
            else:
                client_uuid = str(uuid.uuid4())

            client_sub_token = secrets.token_hex(12)

            payload = {
                "client": {
                    "id": str(client_uuid),
                    "email": str(email),
                    "flow": "xtls-rprx-vision",
                    "limitIp": 0,
                    "totalGB": 0,
                    "expiryTime": int(new_expiry_ms),
                    "enable": True,
                    "tgId": 0,
                    "subId": str(client_sub_token),
                    "reset": 0
                },
                "inboundIds": target_inbounds
            }

            url = f"{base_url}/panel/api/clients/add"
            res = session.post(url, json=payload, headers=headers, timeout=10, verify=False)
            if res.status_code == 200 and res.json().get('success'):
                logger.info(f"Клиент '{email}' успешно создан в 3X-UI (inboundIds: {target_inbounds}).")
                return str(client_uuid), int(new_expiry_ms), str(client_sub_token)
            else:
                logger.warning(f"POST {url} вернул status {res.status_code}: {res.text}. Пробуем fallback...")
                try:
                    new_cl = Client(
                        id=client_uuid,
                        email=email,
                        enable=True,
                        flow="xtls-rprx-vision",
                        expiry_time=new_expiry_ms
                    )
                    setattr(new_cl, "reset", 0)
                    setattr(new_cl, "subId", client_sub_token)
                    if hasattr(api, 'client') and hasattr(api.client, 'add'):
                        api.client.add(inbound_id=inbound_id, clients=[new_cl])
                        logger.info(f"Клиент {email} добавлен через api.client.add (fallback).")
                        return str(client_uuid), int(new_expiry_ms), str(client_sub_token)
                except Exception as add_fb_err:
                    logger.error(f"py3xui add fallback failed: {add_fb_err}")

                return str(client_uuid), int(new_expiry_ms), str(client_sub_token)

    except Exception as e:
        logger.error(f"Ошибка в update_or_create_client_on_panel: {e}", exc_info=True)
        return None, None, None

async def create_or_update_key_on_host(host_name: str, email: str, days_to_add: int | None = None, expiry_timestamp_ms: int | None = None) -> Dict | None:
    host_data = get_host(host_name)
    if not host_data:
        logger.error(f"Сбой рабочего процесса: Хост '{host_name}' не найден в базе данных.")
        return None

    api, inbound = login_to_host(
        host_url=host_data['host_url'],
        username=host_data['host_username'],
        password=host_data['host_pass'],
        inbound_id=host_data['host_inbound_id']
    )
    if not api or not inbound:
        logger.error(f"Сбой рабочего процесса: Не удалось войти или найти inbound на хосте '{host_name}'.")
        return None
        
    # Prefer exact expiry when provided (e.g., switching hosts), otherwise add days (purchase/extend/trial)
    client_uuid, new_expiry_ms, client_sub_token = update_or_create_client_on_panel(
        api, inbound.id, email, days_to_add=days_to_add, target_expiry_ms=expiry_timestamp_ms
    )

    if not client_uuid:
        logger.error(f"Сбой рабочего процесса: Не удалось создать/обновить клиента '{email}' на хосте '{host_name}'.")
        return None
    
    connection_string = get_subscription_link(client_uuid, host_data['host_url'], host_name, sub_token=client_sub_token)
    
    logger.info(f"Успешно обработан ключ для '{email}' на хосте '{host_name}'.")
    
    
    return {
        "client_uuid": client_uuid,
        "email": email,
        "expiry_timestamp_ms": new_expiry_ms,
        "connection_string": connection_string,
        "host_name": host_name,
        "sub_id": client_sub_token or client_uuid
    }

async def get_key_details_from_host(key_data: dict) -> dict | None:
    host_name = key_data.get('host_name')
    if not host_name:
        logger.error(f"Не удалось получить данные ключа: отсутствует host_name для key_id {key_data.get('key_id')}")
        return None

    host_db_data = get_host(host_name)
    if not host_db_data:
        logger.error(f"Не удалось получить данные ключа: хост '{host_name}' не найден в базе данных.")
        return None

    api, inbound = login_to_host(
        host_url=host_db_data['host_url'],
        username=host_db_data['host_username'],
        password=host_db_data['host_pass'],
        inbound_id=host_db_data['host_inbound_id']
    )
    if not api or not inbound: return None

    client_sub_token = None
    base_url = host_db_data['host_url'].rstrip('/')
    session = getattr(api, "http_session", None) or getattr(api, "_http_session", None) or get_or_create_host_session(base_url)

    # 1. Попробуем извлечь subId из нативного API /panel/api/clients/list
    try:
        r = session.get(f"{base_url}/panel/api/clients/list", timeout=10, verify=False)
        if r.status_code == 200:
            clients_list = r.json().get('obj', []) or []
            for c in clients_list:
                if (c.get('uuid') == key_data.get('xui_client_uuid') or 
                    str(c.get('id')) == str(key_data.get('xui_client_uuid')) or 
                    c.get('email') == key_data.get('email')):
                    client_sub_token = c.get('subId') or c.get('subscription')
                    if client_sub_token:
                        break
    except Exception as list_err:
        logger.debug(f"get_key_details_from_host: не удалось прочесть /panel/api/clients/list: {list_err}")

    # 2. Fallback на inbound.settings.clients
    if not client_sub_token:
        try:
            if inbound.settings and inbound.settings.clients:
                for client in inbound.settings.clients:
                    if getattr(client, "id", None) == key_data['xui_client_uuid'] or getattr(client, "email", None) == key_data.get('email'):
                        candidate_fields = ("subId", "subscription", "sub_id", "subscriptionId", "subscription_token")
                        for attr in candidate_fields:
                            val = getattr(client, attr, None)
                            if not val and hasattr(client, "get"):
                                val = client.get(attr)
                            if val:
                                client_sub_token = val
                                break
                        break
        except Exception:
            pass

    connection_string = get_subscription_link(key_data['xui_client_uuid'], host_db_data['host_url'], host_name, sub_token=client_sub_token)
    return {"connection_string": connection_string}

async def delete_client_on_host(host_name: str, client_email: str) -> bool:
    host_data = get_host(host_name)
    if not host_data:
        logger.error(f"Не удалось удалить клиента: хост '{host_name}' не найден.")
        return False

    api, inbound = login_to_host(
        host_url=host_data['host_url'],
        username=host_data['host_username'],
        password=host_data['host_pass'],
        inbound_id=host_data['host_inbound_id']
    )

    if not api:
        logger.error(f"Не удалось удалить клиента: ошибка входа на хост '{host_name}'.")
        return False
        
    try:
        base_url = host_data['host_url'].rstrip('/')
        session = getattr(api, "http_session", None) or getattr(api, "_http_session", None) or get_or_create_host_session(base_url)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        del_url = f"{base_url}/panel/api/clients/del/{client_email}"
        res = session.post(del_url, headers=headers, timeout=10, verify=False)
        if res.status_code == 200:
            data = res.json() if res.text else {}
            if data.get('success'):
                logger.info(f"Клиент '{client_email}' успешно удалён с хоста '{host_name}' через {del_url}.")
                return True
            else:
                msg = str(data.get('msg', ''))
                if 'not found' in msg.lower():
                    logger.info(f"Клиент '{client_email}' уже отсутствует на хосте '{host_name}': {msg}")
                    return True
                logger.warning(f"Удаление через {del_url} вернуло: {msg}")

        # Fallback to py3xui
        client_to_delete = get_key_by_email(client_email)
        if client_to_delete and client_to_delete.get('xui_client_uuid') and inbound:
            try:
                api.client.delete(inbound.id, client_to_delete['xui_client_uuid'])
                logger.info(f"Клиент '{client_email}' удалён через api.client.delete (fallback).")
                return True
            except Exception as fb_err:
                logger.warning(f"api.client.delete fallback error: {fb_err}")
                return False
        return True
    except Exception as e:
        logger.error(f"Не удалось удалить клиента '{client_email}' с хоста '{host_name}': {e}", exc_info=True)
        return False