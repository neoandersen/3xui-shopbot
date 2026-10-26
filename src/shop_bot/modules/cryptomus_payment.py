"""Cryptomus payment gateway module for NeoVPN ShopBot.
Documentation & Specs: https://doc.cryptomus.com/business/payments/creating-invoice
Personal Merchant / Business (0% IP/FOP).
Supports top 8 cryptocurrencies: USDT, USDC, BTC, ETH, XRP, XMR, LTC, TON.
"""
import base64
import hashlib
import json
import logging
import urllib.request
from typing import Optional, Dict, Any, Tuple

from shop_bot.data_manager.database import get_setting

logger = logging.getLogger(__name__)

CRYPTOMUS_API_BASE = "https://api.cryptomus.com/v1"
SUPPORTED_CRYPTOS = ["USDT", "USDC", "BTC", "ETH", "XRP", "XMR", "LTC", "TON"]


def is_cryptomus_enabled() -> bool:
    """Check if Cryptomus gateway is enabled and configured."""
    enabled = (get_setting("cryptomus_enabled") or "false").lower() == "true"
    merchant_id = get_setting("cryptomus_merchant_id")
    api_key = get_setting("cryptomus_api_key")
    return bool(enabled and merchant_id and api_key)


def generate_cryptomus_sign(payload_json_str: str, api_key: str) -> str:
    """
    Generate Cryptomus MD5 signature for API requests or webhooks.
    sign = MD5(Base64(compact_json) + API_KEY)
    """
    b64_str = base64.b64encode(payload_json_str.encode("utf-8")).decode("utf-8")
    return hashlib.md5((b64_str + api_key).encode("utf-8")).hexdigest()


def verify_cryptomus_webhook(raw_data: str, api_key: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verify incoming webhook payload from Cryptomus.
    Cryptomus embeds 'sign' in the JSON body.
    """
    if not raw_data or not api_key:
        return False, None

    try:
        data = json.loads(raw_data)
    except Exception as e:
        logger.error(f"Failed to parse Cryptomus webhook JSON: {e}")
        return False, None

    received_sign = data.get("sign")
    if not received_sign:
        logger.warning("Cryptomus webhook missing 'sign' field")
        return False, data

    # Create payload copy without sign
    data_copy = {k: v for k, v in data.items() if k != "sign"}

    # Cryptomus requires exact JSON serialization
    serialized_options = [
        json.dumps(data_copy, separators=(",", ":"), ensure_ascii=False),
        json.dumps(data_copy, separators=(",", ":")),
        json.dumps(data_copy, ensure_ascii=False),
    ]

    for serialized in serialized_options:
        expected_sign = generate_cryptomus_sign(serialized, api_key)
        if expected_sign.lower() == received_sign.lower():
            return True, data

    logger.warning(f"Cryptomus sign mismatch. Received: {received_sign}")
    return False, data


def create_cryptomus_invoice(
    order_id: str,
    amount_usd: float,
    to_currency: Optional[str] = None,
    callback_url: Optional[str] = None,
    return_url: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Create invoice on Cryptomus.
    Base currency is USD; Cryptomus handles quotation into chosen crypto.
    Returns: (payment_url, invoice_uuid)
    """
    merchant_id = (get_setting("cryptomus_merchant_id") or "").strip()
    api_key = (get_setting("cryptomus_api_key") or "").strip()

    if not merchant_id or not api_key:
        logger.error("Cryptomus merchant_id or api_key is missing")
        return None, None

    payload: Dict[str, Any] = {
        "amount": f"{float(amount_usd):.2f}",
        "currency": "USD",
        "order_id": str(order_id),
        "is_payment_multiple": False,
        "lifetime": 3600,
    }

    if to_currency and to_currency.upper() in SUPPORTED_CRYPTOS:
        payload["to_currency"] = to_currency.upper()

    domain = (get_setting("domain") or "").strip()
    if callback_url:
        payload["url_callback"] = callback_url
    elif domain:
        scheme = "https" if not domain.startswith("http") else ""
        d = domain if domain.startswith("http") else f"{scheme}://{domain}"
        payload["url_callback"] = f"{d.rstrip('/')}:8443/webhook/cryptomus"

    if return_url:
        payload["url_return"] = return_url

    compact_json = json.dumps(payload, separators=(",", ":"))
    sign = generate_cryptomus_sign(compact_json, api_key)

    headers = {
        "merchant": merchant_id,
        "sign": sign,
        "Content-Type": "application/json",
        "User-Agent": "NeoVPN-ShopBot/1.3.0"
    }

    url = f"{CRYPTOMUS_API_BASE}/payment"
    req = urllib.request.Request(url, data=compact_json.encode("utf-8"), headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            if resp_data.get("state") == 0 and "result" in resp_data:
                result = resp_data["result"]
                pay_url = result.get("url")
                uuid_val = result.get("uuid")
                logger.info(f"Cryptomus invoice created successfully: order_id={order_id}, uuid={uuid_val}")
                return pay_url, uuid_val
            else:
                logger.error(f"Cryptomus error response: {resp_data}")
                return None, None
    except Exception as e:
        logger.error(f"Failed to call Cryptomus API: {e}", exc_info=True)
        return None, None

def check_cryptomus_payment_status(order_id: Optional[str] = None, uuid_val: Optional[str] = None) -> Dict[str, Any]:
    """Check payment status from Cryptomus API (/v1/payment/info)."""
    merchant_id = (get_setting("cryptomus_merchant_id") or "").strip()
    api_key = (get_setting("cryptomus_api_key") or "").strip()
    if not merchant_id or not api_key:
        return {}

    payload: Dict[str, Any] = {}
    if uuid_val:
        payload["uuid"] = uuid_val
    elif order_id:
        payload["order_id"] = str(order_id)
    else:
        return {}

    compact_json = json.dumps(payload, separators=(",", ":"))
    sign = generate_cryptomus_sign(compact_json, api_key)
    headers = {
        "merchant": merchant_id,
        "sign": sign,
        "Content-Type": "application/json",
        "User-Agent": "NeoVPN-ShopBot/1.3.0"
    }
    url = f"{CRYPTOMUS_API_BASE}/payment/info"
    req = urllib.request.Request(url, data=compact_json.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Cryptomus status check error: {e}")
        return {}
