import os
from typing import Optional, Tuple
from app.services.payments.anypay import AnyPayService
from shop_bot.data_manager.database import get_setting

def get_anypay_service() -> AnyPayService:
    project_id = get_setting("anypay_project_id") or os.getenv("ANYPAY_PROJECT_ID", "")
    secret_key = get_setting("anypay_secret_key") or os.getenv("ANYPAY_SECRET_KEY", "")
    api_key = get_setting("anypay_api_key") or os.getenv("ANYPAY_API_KEY", "")
    panel_domain = get_setting("panel_domain", "")
    bot_username = get_setting("telegram_bot_username", "neovpn_servers_bot")
    currency = get_setting("anypay_currency", "RUB")
    return AnyPayService(
        project_id=str(project_id or "").strip(),
        secret_key=str(secret_key or "").strip(),
        api_key=str(api_key or "").strip(),
        panel_domain=str(panel_domain or "").strip(),
        port=8443,
        bot_username=str(bot_username or "neovpn_servers_bot").strip(),
        currency=str(currency or "RUB").strip()
    )

def is_anypay_enabled() -> bool:
    flag = get_setting("anypay_enabled") or os.getenv("ANYPAY_ENABLED")
    enabled = str(flag).lower() in ("true", "1", "yes", "on")
    return bool(enabled and get_anypay_service().is_configured())

async def create_anypay_payment(
    order_id: str,
    amount_rub: float,
    description: str = "Оплата подписки NeoVPN",
    currency: str = "RUB"
) -> Tuple[bool, Optional[str]]:
    service = get_anypay_service()
    return service.generate_payment_url(
        pay_id=order_id,
        amount=amount_rub,
        description=description,
        currency=currency
    )

async def check_anypay_status(pay_id: str) -> Optional[str]:
    service = get_anypay_service()
    return await service.get_payment_status(pay_id=pay_id)
