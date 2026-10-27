"""PayOK payment gateway module for 3X-UI ShopBot.
Documentation & Specs: https://payok.io/
Suitable for individuals (P2P, cards, SBP, no legal entity required).
"""
import hashlib
import logging
import urllib.parse
from typing import Optional, Tuple, Dict, Any

from shop_bot.data_manager.database import get_setting
from shop_bot.services import currency_service

logger = logging.getLogger(__name__)

PAYOK_PAY_URL = "https://payok.io/pay"


def is_payok_enabled() -> bool:
    """Check if PayOK gateway is enabled and configured."""
    enabled = (get_setting("payok_enabled") or "false").lower() in ("true", "1", "yes", "on")
    shop_id = (get_setting("payok_shop_id") or "").strip()
    secret_key = (get_setting("payok_secret_key") or "").strip()
    return bool(enabled and shop_id and secret_key)


def generate_payok_sign(
    amount: float,
    payment: str,
    shop: str,
    currency: str,
    desc: str,
    secret_key: str
) -> str:
    """
    Generate PayOK payment creation signature.
    sign = MD5(amount|payment|shop|currency|desc|payok_secret_key)
    """
    amount_str = f"{float(amount):.2f}"
    raw = f"{amount_str}|{payment}|{shop}|{currency}|{desc}|{secret_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify_payok_webhook_sign(
    secret_key: str,
    desc: str,
    currency: str,
    shop: str,
    payment_id: str,
    amount: Any,
    received_sign: str
) -> bool:
    """
    Verify incoming webhook notification signature from PayOK.
    expected_sign = MD5(payok_secret_key|desc|currency|shop|payment_id|amount)
    """
    if not (secret_key and payment_id and received_sign):
        return False

    try:
        amt_float = float(amount)
        amount_candidates = [str(amount), f"{amt_float:.2f}", f"{amt_float:g}"]
    except (ValueError, TypeError):
        amount_candidates = [str(amount)]

    received_lower = str(received_sign).strip().lower()
    desc_candidates = [str(desc or ""), ""]

    for amt in amount_candidates:
        for d in desc_candidates:
            raw = f"{secret_key}|{d}|{currency}|{shop}|{payment_id}|{amt}"
            expected = hashlib.md5(raw.encode("utf-8")).hexdigest().lower()
            if expected == received_lower:
                return True

    logger.warning(
        f"PayOK signature mismatch: received={received_lower}, payment_id={payment_id}, tested_amounts={amount_candidates}"
    )
    return False


def create_payok_payment_url(
    order_id: str,
    amount_usd: float,
    target_currency: Optional[str] = None,
    description: Optional[str] = None,
    email: Optional[str] = None
) -> Tuple[Optional[str], float, float]:
    """
    Generate PayOK payment URL.
    Converts amount_usd to target_currency (RUB or USD).
    Returns: (payment_url, converted_amount, exchange_rate)
    """
    shop_id = (get_setting("payok_shop_id") or "").strip()
    secret_key = (get_setting("payok_secret_key") or "").strip()
    cfg_currency = (get_setting("payok_currency") or "RUB").upper().strip()

    if not shop_id or not secret_key:
        logger.error("PayOK: shop_id or secret_key is not configured in settings")
        return None, 0.0, 0.0

    target = (target_currency or cfg_currency or "RUB").upper().strip()
    if target not in ("RUB", "USD"):
        target = "RUB"

    if target == "USD":
        converted_amount = round(float(amount_usd), 2)
        rate = 1.0
    else:
        converted_amount, rate = currency_service.convert_from_usd(amount_usd, target)
        converted_amount = round(converted_amount, 2)

    desc = description or f"Оплата подписки VLESS #{order_id}"
    sign = generate_payok_sign(
        amount=converted_amount,
        payment=order_id,
        shop=shop_id,
        currency=target,
        desc=desc,
        secret_key=secret_key
    )

    params: Dict[str, Any] = {
        "amount": f"{converted_amount:.2f}",
        "payment": str(order_id),
        "shop": shop_id,
        "currency": target,
        "desc": desc,
        "sign": sign
    }
    if email:
        params["email"] = email

    url = f"{PAYOK_PAY_URL}?{urllib.parse.urlencode(params)}"
    return url, converted_amount, rate
