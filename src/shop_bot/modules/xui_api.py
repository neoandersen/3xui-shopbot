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
        inbound_to_modify = api.inbound.get_by_id(inbound_id)
        if not inbound_to_modify:
            raise ValueError(f"Could not find inbound with ID {inbound_id}")

        if inbound_to_modify.settings.clients is None:
            inbound_to_modify.settings.clients = []
            
        client_index = -1
        for i, client in enumerate(inbound_to_modify.settings.clients):
            if client.email == email:
                client_index = i
                break
        
        # Determine new expiry time
        if target_expiry_ms is not None:
            new_expiry_ms = int(target_expiry_ms)
        else:
            if days_to_add is None:
                raise ValueError("Either days_to_add or target_expiry_ms must be provided")
            if client_index != -1:
                existing_client = inbound_to_modify.settings.clients[client_index]
                if existing_client.expiry_time > int(datetime.now().timestamp() * 1000):
                    current_expiry_dt = datetime.fromtimestamp(existing_client.expiry_time / 1000)
                    new_expiry_dt = current_expiry_dt + timedelta(days=days_to_add)
                else:
                    new_expiry_dt = datetime.now() + timedelta(days=days_to_add)
            else:
                new_expiry_dt = datetime.now() + timedelta(days=days_to_add)

            new_expiry_ms = int(new_expiry_dt.timestamp() * 1000)

        client_sub_token: str | None = None

        if client_index != -1:
            # Disable auto-reset/auto-renew on extension
            try:
                inbound_to_modify.settings.clients[client_index].reset = 0
            except Exception:
                pass
            inbound_to_modify.settings.clients[client_index].enable = True
            inbound_to_modify.settings.clients[client_index].expiry_time = new_expiry_ms

            existing_client = inbound_to_modify.settings.clients[client_index]
            client_uuid = existing_client.id
            try:
                sub_token_existing = None
                for attr in ("subId", "subscription", "sub_id"):
                    if hasattr(existing_client, attr):
                        val = getattr(existing_client, attr)
                        if val:
                            sub_token_existing = val
                            break
                if sub_token_existing:
                    client_sub_token = sub_token_existing
                else:
                    import secrets
                    client_sub_token = secrets.token_hex(12)
                    for attr in ("subId", "subscription", "sub_id"):
                        try:
                            setattr(existing_client, attr, client_sub_token)
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            client_uuid = str(uuid.uuid4())
            new_client = Client(
                id=client_uuid,
                email=email,
                enable=True,
                flow="xtls-rprx-vision",
                expiry_time=new_expiry_ms
            )
            # Ensure no auto-reset/auto-renew for new clients
            try:
                setattr(new_client, "reset", 0)
            except Exception:
                pass

            try:
                import secrets
                client_sub_token = secrets.token_hex(12)
                for attr in ("subId", "subscription", "sub_id"):
                    try:
                        setattr(new_client, attr, client_sub_token)
                    except Exception:
                        pass
            except Exception:
                pass
            inbound_to_modify.settings.clients.append(new_client)

        api.inbound.update(inbound_id, inbound_to_modify)

        return client_uuid, new_expiry_ms, client_sub_token

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
        "host_name": host_name
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
    try:
        if inbound.settings and inbound.settings.clients:
            for client in inbound.settings.clients:
                if getattr(client, "id", None) == key_data['xui_client_uuid'] or getattr(client, "email", None) == key_data.get('email'):
                    candidate_fields = ("subId", "subscription", "sub_id", "subscriptionId", "subscription_token")
                    for attr in candidate_fields:
                        val = None
                        if hasattr(client, attr):
                            val = getattr(client, attr)
                        else:
                            try:
                                val = client.get(attr)
                            except Exception:
                                pass
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

    if not api or not inbound:
        logger.error(f"Не удалось удалить клиента: ошибка входа или поиска inbound для хоста '{host_name}'.")
        return False
        
    try:
        client_to_delete = get_key_by_email(client_email)
        if client_to_delete:
            api.client.delete(inbound.id, client_to_delete['xui_client_uuid'])
            logger.info(f"Клиент '{client_email}' успешно удалён с хоста '{host_name}'.")
            return True
        else:
            logger.warning(f"Клиент с email '{client_email}' не найден на хосте '{host_name}' для удаления (возможно, уже удалён).")
            return True
            
    except Exception as e:
        logger.error(f"Не удалось удалить клиента '{client_email}' с хоста '{host_name}': {e}", exc_info=True)
        return False