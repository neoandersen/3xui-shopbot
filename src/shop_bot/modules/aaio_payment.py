"""Aaio payment gateway module for NeoVPN ShopBot.
Documentation & Specs: https://aaio.so/api/service
No legal entity required (0% IP/FOP).
Supports bank cards (RUB, UAH, KZT, World), SBP, and P2P payments.
"""
import hashlib
import logging
import urllib.parse
from typing import Optional, Tuple, Dict, Any

from shop_bot.data_manager.database import get_setting
from shop_bot.services import currency_service

logger = logging.getLogger(__name__)

AAIO_PAY_URL = "https://aaio.so/merchant/get_pay_url"


def is_aaio_enabled() -> bool:
    """Check if Aaio gateway is enabled and configured."""
    enabled = (get_setting("aaio_enabled") or "false").lower() == "true"
    merchant_id = get_setting("aaio_merchant_id")
    secret_1 = get_setting("aaio_secret_1")
    secret_2 = get_setting("aaio_secret_2")
    return bool(enabled and merchant_id and secret_1 and secret_2)


def generate_aaio_signature(
    merchant_id: str,
    amount: float,
    currency: str,
    secret_1: str,
    order_id: str
) -> str:
    """
    Generate Aaio payment creation signature (using Secret 1).
    sign = SHA256(merchant_id:amount:currency:secret_1:order_id)
    """
    amount_str = f"{float(amount):.2f}"
    raw = f"{merchant_id}:{amount_str}:{currency}:{secret_1}:{order_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_aaio_webhook_signature(
    merchant_id: str,
    amount: Any,
    currency: str,
    secret_2: str,
    order_id: str,
    received_sign: str
) -> bool:
    """
    Verify Aaio webhook notification signature (using Secret 2).
    expected_sign = SHA256(merchant_id:amount:currency:secret_2:order_id)
    """
    if not (merchant_id and secret_2 and order_id and received_sign):
        return False

    # Aaio passes amount as string or float (e.g. '100.00' or 100)
    try:
        amount_float = float(amount)
        amount_candidates = [str(amount), f"{amount_float:.2f}", f"{amount_float:g}"]
    except (ValueError, TypeError):
        amount_candidates = [str(amount)]

    received_lower = received_sign.strip().lower()

    for amt in amount_candidates:
        raw = f"{merchant_id}:{amt}:{currency}:{secret_2}:{order_id}"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest().lower()
        if expected == received_lower:
            return True

    logger.warning(
        f"Aaio signature mismatch: received={received_lower}, tested amounts={amount_candidates}"
    )
    return False


def create_aaio_payment_url(
    order_id: str,
    amount_usd: float,
    target_currency: str = "RUB",
    description: str = "NeoVPN Subscription",
    email: Optional[str] = None
) -> Tuple[Optional[str], float, float]:
    """
    Create Aaio payment URL.
    Converts amount_usd to target_currency using 24h currency cache.
    Returns: (payment_url, amount_target, exchange_rate)
    """
    merchant_id = (get_setting("aaio_merchant_id") or "").strip()
    secret_1 = (get_setting("aaio_secret_1") or "").strip()

    if not merchant_id or not secret_1:
        logger.error("Aaio merchant_id or secret_1 is not configured")
        return None, 0.0, 0.0

    target = (target_currency or "RUB").upper().strip()
    # Aaio natively supports RUB, UAH, EUR, USD
    if target not in ("RUB", "UAH", "EUR", "USD"):
        target = "RUB"

    converted_amount, rate = currency_service.convert_from_usd(amount_usd, target)
    sign = generate_aaio_signature(merchant_id, converted_amount, target, secret_1, order_id)

    params: Dict[str, Any] = {
        "merchant_id": merchant_id,
        "amount": f"{converted_amount:.2f}",
        "currency": target,
        "order_id": order_id,
        "sign": sign,
        "desc": description,
        "lang": "ru"
    }
    if email:
        params["email"] = email

    url = f"{AAIO_PAY_URL}?{urllib.parse.urlencode(params)}"
    return url, converted_amount, rate
