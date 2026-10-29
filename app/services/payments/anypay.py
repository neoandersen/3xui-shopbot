import hashlib
import hmac
import logging
import urllib.parse
from typing import Any, Dict, Optional, Tuple
import aiohttp

logger = logging.getLogger(__name__)

ANYPAY_MERCHANT_URL = "https://anypay.io/merchant"
ANYPAY_API_URL = "https://anypay.io/api"


class AnyPayService:
    def __init__(
        self,
        project_id: str,
        secret_key: str,
        api_key: str = "",
        panel_domain: str = "",
        port: int = 8443,
        bot_username: str = "neovpn_servers_bot",
        currency: str = "RUB"
    ):
        self.project_id = str(project_id or "").strip()
        self.secret_key = str(secret_key or "").strip()
        self.api_key = str(api_key or "").strip()
        self.panel_domain = str(panel_domain or "").strip().rstrip("/")
        self.port = port
        self.bot_username = str(bot_username or "neovpn_servers_bot").strip().lstrip("@")
        self.currency = str(currency or "RUB").strip().upper()

    def is_configured(self) -> bool:
        return bool(self.project_id and self.secret_key)

    def get_webhook_url(self) -> str:
        if self.panel_domain:
            if self.port in (80, 443) or "neo-vpn.click" in self.panel_domain:
                return f"https://{self.panel_domain}/webhook/anypay"
            return f"https://{self.panel_domain}:{self.port}/webhook/anypay"
        return "https://neo-vpn.click/webhook/anypay"

    def get_bot_url(self) -> str:
        return f"https://t.me/{self.bot_username}"

    def calculate_payment_sign(
        self,
        pay_id: str,
        amount: str,
        currency: Optional[str] = None,
        merchant_id: Optional[str] = None,
        secret_key: Optional[str] = None
    ) -> str:
        """
        sign = hashlib.sha256(f"{currency}:{amount}:{secret_key}:{merchant_id}:{pay_id}".encode('utf-8')).hexdigest()
        """
        merchant_id = str(merchant_id if merchant_id is not None else self.project_id).strip()
        secret_key = str(secret_key if secret_key is not None else self.secret_key).strip()
        pay_id = str(pay_id).strip()
        currency = str(currency or self.currency).strip().upper()
        amount = str(amount).strip()

        sign_source = f"{currency}:{amount}:{secret_key}:{merchant_id}:{pay_id}"
        return hashlib.sha256(sign_source.encode("utf-8")).hexdigest()

    def generate_payment_url(
        self,
        pay_id: str = "",
        amount: float = 0.0,
        description: str = "Оплата подписки NeoVPN",
        currency: Optional[str] = None,
        success_url: Optional[str] = None,
        fail_url: Optional[str] = None,
        merchant_id: Optional[str] = None,
        secret_key: Optional[str] = None
    ) -> Optional[str]:
        merchant_id = str(merchant_id if merchant_id is not None else self.project_id).strip()
        secret_key = str(secret_key if secret_key is not None else self.secret_key).strip()
        pay_id = str(pay_id).strip()
        currency = str(currency or self.currency).strip().upper()

        if not (merchant_id and secret_key):
            logger.error("AnyPay не настроен: отсутствует project_id или secret_key")
            return None

        amount_str = f"{float(amount):.2f}".strip()
        sign = self.calculate_payment_sign(
            pay_id=pay_id,
            amount=amount_str,
            currency=currency,
            merchant_id=merchant_id,
            secret_key=secret_key
        )

        default_redirect = self.get_bot_url()
        desc = str(description or f"Оплата заказа #{pay_id}").strip()
        params = {
            "merchant_id": merchant_id,
            "pay_id": pay_id,
            "amount": amount_str,
            "currency": currency,
            "desc": desc,
            "success_url": str(success_url or default_redirect).strip(),
            "fail_url": str(fail_url or default_redirect).strip(),
            "sign": sign.strip()
        }

        query_string = urllib.parse.urlencode(params)
        payment_url = f"{ANYPAY_MERCHANT_URL}?{query_string}"

        secret_key_masked = f"{secret_key[:4]}...{secret_key[-4:]}" if len(secret_key) >= 8 else "****"
        logger.info(f"[AnyPay Debug] raw_string_for_sign: '{currency}:{amount_str}:{secret_key_masked}:{merchant_id}:{pay_id}'")
        logger.info(f"[AnyPay Debug] generated_sign: {sign}")
        logger.info(f"[AnyPay Debug] full_payment_url: {payment_url}")

        return payment_url

    @staticmethod
    def verify_notification_signature(
        data: Dict[str, Any],
        secret_key: str
    ) -> bool:
        """
        expected_sign = hashlib.sha256(f"{currency}:{amount}:{pay_id}:{merchant_id}:{status}:{secret_key}".encode('utf-8')).hexdigest()
        """
        merchant_id = str(data.get("merchant_id") or "").strip()
        secret_key = str(secret_key or "").strip()
        pay_id = str(data.get("pay_id") or "").strip()
        currency = str(data.get("currency") or "").strip().upper()

        if not secret_key:
            return False

        amount = str(data.get("amount") or "").strip()
        status = str(data.get("status") or "").strip()
        sign = str(data.get("sign") or "").strip()

        if not (currency and amount and pay_id and merchant_id and status and sign):
            logger.warning(f"AnyPay IPN: не все обязательные параметры переданы: {data}")
            return False

        sign_source = f"{currency}:{amount}:{pay_id}:{merchant_id}:{status}:{secret_key}"
        expected_sign = hashlib.sha256(sign_source.encode("utf-8")).hexdigest()

        return hmac.compare_digest(sign.lower(), expected_sign.lower())

    async def get_payment_status(self, pay_id: str) -> Optional[str]:
        """Опциональный запрос статуса платежа через AnyPay API (если задан api_key)"""
        if not (self.project_id and self.api_key):
            return None

        # Sign for AnyPay API requests: sha256(f"payments{project_id}{api_key}")
        sign_source = f"payments{self.project_id}{self.api_key}"
        sign = hashlib.sha256(sign_source.encode("utf-8")).hexdigest()
        url = f"{ANYPAY_API_URL}/payments/{self.project_id}"
        params = {
            "project_id": self.project_id,
            "trans_id": str(pay_id),
            "sign": sign
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        payments = data.get("result", {}).get("payments", {})
                        if str(pay_id) in payments:
                            return payments[str(pay_id)].get("status")
        except Exception as e:
            logger.warning(f"AnyPay API status check error for pay_id={pay_id}: {e}")

        return None
