from typing import Optional, Tuple
from app.services.payments.platega import PlategaService
try:
    from src.shop_bot.data_manager.database import get_setting
except ImportError:
    from shop_bot.data_manager.database import get_setting

def get_platega_service() -> PlategaService:
    merchant_id = get_setting("platega_merchant_id", "")
    secret = get_setting("platega_secret", "")
    panel_domain = get_setting("panel_domain", "")
    return PlategaService(merchant_id=merchant_id, secret=secret, panel_domain=panel_domain, port=8443)

def is_platega_enabled() -> bool:
    enabled = str(get_setting("platega_enabled", "false")).lower() in ("true", "1", "yes")
    return bool(enabled and get_platega_service().is_configured())

async def create_platega_payment(
    order_id: str,
    amount_rub: float,
    description: str = ""
) -> Tuple[bool, Optional[str], Optional[str]]:
    service = get_platega_service()
    return await service.create_payment(order_id=order_id, amount_rub=amount_rub, description=description)
