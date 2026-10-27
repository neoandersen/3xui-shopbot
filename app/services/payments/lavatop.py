"""Lava.top payment gateway module for 3X-UI ShopBot.
Documentation & Specs: https://gate.lava.top/api/v3/
REST API v3, HMAC-SHA256 webhooks, cards (RF & Worldwide), SBP for digital goods.
"""
import hmac
import hashlib
import json
import logging
import urllib.request
import urllib.error
from typing import Optional, Tuple, Dict, Any

from shop_bot.data_manager.database import get_setting
from shop_bot.services import currency_service

logger = logging.getLogger(__name__)

LAVATOP_API_BASE = "https://gate.lava.top/api/v3"
LAVATOP_API_V1 = "https://gate.lava.top/api/v1"


def is_lavatop_enabled() -> bool:
    """Check if Lava.top gateway is enabled and configured."""
    enabled = (get_setting("lavatop_enabled") or "false").lower() in ("true", "1", "yes", "on")
    api_key = (get_setting("lavatop_api_key") or "").strip()
    offer_id = (get_setting("lavatop_offer_id") or "").strip()
    return bool(enabled and api_key and offer_id)


def verify_lavatop_webhook_signature(
    raw_body: bytes | str,
    signature_header: str,
    webhook_key: Optional[str] = None
) -> bool:
    """
    Verify incoming webhook HMAC-SHA256 signature from Lava.top.
    expected = HMAC-SHA256(raw_body, lavatop_webhook_key)
    """
    key = (webhook_key or get_setting("lavatop_webhook_key") or "").strip()
    if not key or not signature_header:
        logger.warning("Lava.top: missing webhook secret key or signature header")
        return False

    body_bytes = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
    expected_hex = hmac.new(key.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

    clean_received = signature_header.strip()
    if clean_received.lower().startswith("sha256="):
        clean_received = clean_received[7:]

    is_valid = hmac.compare_digest(expected_hex.lower(), clean_received.lower())
    if not is_valid:
        logger.warning(f"Lava.top signature mismatch: received={clean_received}, expected={expected_hex}")
    return is_valid


def create_lavatop_invoice(
    order_id: str,
    amount_usd: float,
    telegram_id: int,
    tariff_id: Optional[int] = None,
    keys_count: int = 1,
    target_currency: Optional[str] = None,
    email: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], float, float]:
    """
    Create dynamic invoice via Lava.top REST API v3.
    Returns: (payment_url, invoice_id, converted_amount, exchange_rate)
    """
    api_key = (get_setting("lavatop_api_key") or "").strip()
    offer_id = (get_setting("lavatop_offer_id") or "").strip()
    cfg_currency = (get_setting("lavatop_currency") or "RUB").upper().strip()

    if not api_key or not offer_id:
        logger.error("Lava.top: api_key or offer_id is missing")
        return None, None, 0.0, 0.0

    target = (target_currency or cfg_currency or "RUB").upper().strip()
    if target not in ("RUB", "USD"):
        target = "RUB"

    if target == "USD":
        converted_amount = round(float(amount_usd), 2)
        rate = 1.0
    else:
        converted_amount, rate = currency_service.convert_from_usd(amount_usd, target)
        converted_amount = round(converted_amount, 2)

    user_email = email or f"user_{telegram_id}@shop.local"

    payload = {
        "email": user_email,
        "offerId": offer_id,
        "currency": target,
        "price": converted_amount,
        "orderId": str(order_id),
        "customFields": {
            "telegram_id": int(telegram_id),
            "tariff_id": tariff_id if tariff_id is not None else 0,
            "keys_count": int(keys_count)
        }
    }

    url = f"{LAVATOP_API_BASE}/invoice"
    body_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body_data,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "3XUI-ShopBot/1.4.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_bytes = resp.read()
            resp_data = json.loads(resp_bytes.decode("utf-8"))
            logger.info(f"Lava.top invoice creation response for {order_id}: {resp_data}")

            data = resp_data.get("data") if isinstance(resp_data.get("data"), dict) else resp_data
            payment_url = data.get("paymentUrl") or data.get("payment_url") or data.get("url")
            invoice_id = data.get("invoiceId") or data.get("invoice_id") or data.get("id")

            if payment_url:
                return str(payment_url), str(invoice_id or ""), converted_amount, rate
            else:
                logger.error(f"Lava.top response missing paymentUrl: {resp_data}")
                return None, None, converted_amount, rate
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"Lava.top HTTP error {e.code}: {error_body}")
        return None, None, 0.0, 0.0
    except Exception as e:
        logger.error(f"Failed to call Lava.top API: {e}", exc_info=True)
        return None, None, 0.0, 0.0


def check_lavatop_invoice_status(invoice_id: str) -> Dict[str, Any]:
    """
    Check invoice status via Lava.top API.
    Supports GET /api/v1/invoices/{invoice_id} or /api/v3/invoice/{invoice_id}.
    """
    api_key = (get_setting("lavatop_api_key") or "").strip()
    if not api_key or not invoice_id:
        return {}

    # Try v1 first as defined in spec
    endpoints = [
        f"{LAVATOP_API_V1}/invoices/{invoice_id}",
        f"{LAVATOP_API_BASE}/invoice/{invoice_id}"
    ]

    for endpoint in endpoints:
        req = urllib.request.Request(
            endpoint,
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "3XUI-ShopBot/1.4.0"
            },
            method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as e:
            logger.debug(f"Lava.top status check query failed on {endpoint}: {e}")

    return {}
