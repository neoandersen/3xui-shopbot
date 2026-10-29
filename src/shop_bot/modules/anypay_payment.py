import hashlib
import logging
import os
import urllib.parse
from typing import Any, Dict, Optional, Tuple
from app.services.payments.anypay import AnyPayService
from shop_bot.data_manager.database import get_setting

logger = logging.getLogger(__name__)

def get_anypay_service() -> AnyPayService:
    project_id = str(get_setting("anypay_project_id") or os.getenv("ANYPAY_PROJECT_ID", "")).strip()
    secret_key = str(get_setting("anypay_secret_key") or os.getenv("ANYPAY_SECRET_KEY", "")).strip()
    api_key = str(get_setting("anypay_api_key") or os.getenv("ANYPAY_API_KEY", "")).strip()
    panel_domain = str(get_setting("panel_domain", "")).strip()
    bot_username = str(get_setting("telegram_bot_username", "neovpn_servers_bot")).strip()
    currency = str(get_setting("anypay_currency", "RUB")).strip().upper()
    return AnyPayService(
        project_id=project_id,
        secret_key=secret_key,
        api_key=api_key,
        panel_domain=panel_domain,
        port=8443,
        bot_username=bot_username,
        currency=currency
    )

def is_anypay_enabled() -> bool:
    flag = get_setting("anypay_enabled") or os.getenv("ANYPAY_ENABLED")
    enabled = str(flag).lower() in ("true", "1", "yes", "on")
    return bool(enabled and get_anypay_service().is_configured())

def calculate_payment_sign(
    pay_id: Any,
    amount: Any,
    currency: Optional[str] = None,
    project_id: Optional[str] = None,
    secret_key: Optional[str] = None,
    merchant_id: Optional[str] = None,
    description: Optional[str] = None,
    success_url: Optional[str] = None,
    fail_url: Optional[str] = None
) -> str:
    service = get_anypay_service()
    proj = project_id if project_id is not None else merchant_id
    merchant_id_val = str(proj if proj is not None else (service.project_id or getattr(service, 'merchant_id', ''))).strip()
    secret_key_val = str(secret_key if secret_key is not None else service.secret_key).strip()
    return service.calculate_payment_sign(
        pay_id=pay_id,
        amount=amount,
        currency=currency,
        description=description,
        success_url=success_url,
        fail_url=fail_url,
        merchant_id=merchant_id_val,
        secret_key=secret_key_val
    )

def generate_payment_url(
    pay_id: Any = "",
    amount: float = 0.0,
    description: str = "Оплата подписки NeoVPN",
    currency: Optional[str] = None,
    success_url: Optional[str] = None,
    fail_url: Optional[str] = None,
    project_id: Optional[str] = None,
    secret_key: Optional[str] = None,
    merchant_id: Optional[str] = None
) -> Optional[str]:
    # 1. Гарантируем числовой pay_id (integer)
    numeric_id = ''.join(filter(str.isdigit, str(pay_id)))
    if not numeric_id:
        import time
        numeric_id = str(int(time.time() * 1000))[-9:]
    pay_id_int = int(numeric_id)

    service = get_anypay_service()
    proj = project_id if project_id is not None else merchant_id
    merchant_id_val = str(proj if proj is not None else (service.project_id or getattr(service, 'merchant_id', ''))).strip()
    amount_str = f"{float(amount):.2f}"
    currency_val = str(currency or service.currency or 'RUB').strip().upper()
    desc_val = str(description or 'Оплата подписки NeoVPN').strip()
    default_redirect = service.get_bot_url() or 'https://t.me/neovpn_servers_bot'
    succ_val = str(success_url or default_redirect).strip()
    fail_val = str(fail_url or default_redirect).strip()
    secret_key_val = str(secret_key if secret_key is not None else service.secret_key).strip()

    if not (merchant_id_val and secret_key_val):
        logger.error("AnyPay не настроен: отсутствует project_id/merchant_id или secret_key")
        return None

    # 3. Расчёт подписи по формуле AnyPay SCI
    raw_sign = f"{merchant_id_val}:{pay_id_int}:{amount_str}:{currency_val}:{desc_val}:{succ_val}:{fail_val}:{secret_key_val}"
    sign = hashlib.sha256(raw_sign.encode('utf-8')).hexdigest()

    # 4. Формирование GET-параметров
    params = {
        'merchant_id': merchant_id_val,
        'pay_id': pay_id_int,
        'amount': amount_str,
        'currency': currency_val,
        'desc': desc_val,
        'success_url': succ_val,
        'fail_url': fail_val,
        'sign': sign
    }
    payment_url = f"https://anypay.io/merchant?{urllib.parse.urlencode(params)}"

    secret_key_masked = f"{secret_key_val[:4]}...{secret_key_val[-4:]}" if len(secret_key_val) >= 8 else "****"
    logger.info(f"[AnyPay Debug] raw_string_for_sign: '{merchant_id_val}:{pay_id_int}:{amount_str}:{currency_val}:{desc_val}:{succ_val}:{fail_val}:{secret_key_masked}'")
    logger.info(f"[AnyPay Debug] generated_sign: {sign}")
    logger.info(f"[AnyPay Debug] full_payment_url: {payment_url}")

    return payment_url

def verify_notification_signature(
    data: Dict[str, Any],
    secret_key: Optional[str] = None
) -> bool:
    project_id = str(data.get("project_id") or data.get("merchant_id") or "").strip()
    secret_key = str(secret_key if secret_key is not None else (get_setting("anypay_secret_key") or os.getenv("ANYPAY_SECRET_KEY", ""))).strip()
    pay_id = str(data.get("pay_id") or "").strip()
    currency = str(data.get("currency") or "").strip().upper()

    return AnyPayService.verify_notification_signature(data, secret_key=secret_key)

async def create_anypay_payment(
    order_id: str,
    amount_rub: float,
    description: str = "Оплата подписки NeoVPN",
    currency: str = "RUB"
) -> Optional[str]:
    pay_id = str(order_id).strip()
    currency = str(currency).strip().upper()
    desc = str(description).strip()
    amount = float(amount_rub)
    if amount < 50.0 and currency == 'RUB':
        amount = 50.0

    result = generate_payment_url(
        pay_id=pay_id,
        amount=amount,
        description=desc,
        currency=currency
    )
    if isinstance(result, tuple):
        return result[1] if len(result) > 1 and result[0] else None
    return result

async def check_anypay_status(pay_id: str) -> Optional[str]:
    service = get_anypay_service()
    return await service.get_payment_status(pay_id=str(pay_id).strip())
