import hashlib
import hmac
import logging
from typing import Any, Dict, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

LAVA_API_BASE_URL = "https://gate.lava.top"


class LavaTopService:
    def __init__(self, api_key: str, offer_id: str, panel_domain: str, port: int = 8443):
        self.api_key = api_key.strip() if api_key else ""
        self.offer_id = offer_id.strip() if offer_id else ""
        self.panel_domain = panel_domain.strip().rstrip("/") if panel_domain else ""
        self.port = port

    def is_configured(self) -> bool:
        return bool(self.api_key and self.offer_id)

    def get_webhook_url(self) -> str:
        return f"https://{self.panel_domain}:{self.port}/webhook/lavatop"

    def get_success_url(self) -> str:
        return f"https://{self.panel_domain}:{self.port}/payment-success"

    async def create_invoice(
        self,
        order_id: str,
        amount_rub: float,
        user_tg_id: int
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if not self.is_configured():
            logger.error("Lava.top не настроен: отсутствует api_key или offer_id")
            return False, None, None

        endpoint = f"{LAVA_API_BASE_URL}/api/v1/invoices"
        headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "offerId": self.offer_id,
            "price": round(float(amount_rub), 2),
            "currency": "RUB",
            "orderId": str(order_id),
            "email": f"tg_{user_tg_id}@bot.local",
            "hookUrl": self.get_webhook_url(),
            "successUrl": self.get_success_url()
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, json=payload, headers=headers, timeout=15) as resp:
                    resp_json = await resp.json()
                    if resp.status in (200, 201):
                        data = resp_json.get("data", resp_json)
                        payment_url = data.get("paymentUrl") or data.get("url")
                        invoice_id = str(data.get("id") or data.get("invoiceId") or "")
                        return True, payment_url, invoice_id
                    
                    logger.error(f"Lava.top API error [{resp.status}]: {resp_json}")
                    return False, None, None
        except Exception as e:
            logger.exception(f"Исключение при вызове Lava.top create_invoice: {e}")
            return False, None, None

    async def get_invoice_status(self, invoice_id: str) -> Optional[str]:
        if not self.api_key or not invoice_id:
            return None

        endpoint = f"{LAVA_API_BASE_URL}/api/v1/invoices/{invoice_id}"
        headers = {
            "X-Api-Key": self.api_key,
            "Accept": "application/json"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        invoice_data = data.get("data", data)
                        return invoice_data.get("status")
                    return None
        except Exception as e:
            logger.error(f"Ошибка проверки статуса инвойса Lava.top {invoice_id}: {e}")
            return None

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature_header: str, api_key: str) -> bool:
        if not signature_header or not api_key or not raw_body:
            return False
        
        expected_sig = hmac.new(
            api_key.encode("utf-8"),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_sig.lower(), signature_header.strip().lower())
