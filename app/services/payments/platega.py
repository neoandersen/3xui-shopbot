import hmac
import json
import logging
from typing import Any, Dict, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

PLATEGA_API_URL = "https://app.platega.io/transaction/process"


class PlategaService:
    def __init__(self, merchant_id: str, secret: str, panel_domain: str, port: int = 8443):
        self.merchant_id = merchant_id.strip() if merchant_id else ""
        self.secret = secret.strip() if secret else ""
        self.panel_domain = panel_domain.strip().rstrip("/") if panel_domain else ""
        self.port = port

    def is_configured(self) -> bool:
        return bool(self.merchant_id and self.secret)

    def get_webhook_url(self) -> str:
        return f"https://{self.panel_domain}:{self.port}/webhook/platega"

    def get_return_url(self) -> str:
        return f"https://{self.panel_domain}:{self.port}/payment-success"

    def get_failed_url(self) -> str:
        return f"https://{self.panel_domain}:{self.port}/payment-fail"

    async def create_payment(
        self,
        order_id: str,
        amount_rub: float,
        description: str = ""
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if not self.is_configured():
            logger.error("Platega.io не настроен: отсутствует merchant_id или secret")
            return False, None, None

        headers = {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "paymentDetails": {
                "amount": round(float(amount_rub), 2),
                "currency": "RUB"
            },
            "description": description or f"Оплата заказа #{order_id}",
            "returnUrl": self.get_return_url(),
            "failedUrl": self.get_failed_url(),
            "payload": str(order_id)
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(PLATEGA_API_URL, json=payload, headers=headers, timeout=15) as resp:
                    raw_text = await resp.text()
                    try:
                        resp_json = json.loads(raw_text)
                    except Exception:
                        resp_json = {}

                    if resp.status in (200, 201):
                        payment_url = resp_json.get("paymentUrl") or resp_json.get("url")
                        tx_id = str(resp_json.get("id") or resp_json.get("transactionId") or "")
                        return True, payment_url, tx_id
                    
                    logger.error(f"Platega.io API error [{resp.status}]: {raw_text or resp_json}")
                    return False, None, None
        except Exception as e:
            logger.exception(f"Исключение при вызове Platega create_payment: {e}")
            return False, None, None

    @staticmethod
    def verify_webhook_auth(header_secret: str, expected_secret: str) -> bool:
        if not header_secret or not expected_secret:
            return False
        return hmac.compare_digest(header_secret.strip(), expected_secret.strip())
