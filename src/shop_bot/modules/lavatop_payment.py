from typing import Optional, Tuple
from app.services.payments.lavatop import LavaTopService
try:
    from src.shop_bot.data_manager.database import get_setting
except ImportError:
    from shop_bot.data_manager.database import get_setting

def get_lavatop_service() -> LavaTopService:
    api_key = get_setting("lavatop_api_key", "")
    offer_id = get_setting("lavatop_offer_id", "")
    panel_domain = get_setting("panel_domain", "")
    return LavaTopService(api_key=api_key, offer_id=offer_id, panel_domain=panel_domain, port=8443)

def is_lavatop_enabled() -> bool:
    enabled = str(get_setting("lavatop_enabled", "false")).lower() in ("true", "1", "yes")
    return bool(enabled and get_lavatop_service().is_configured())

async def create_lavatop_payment(
    order_id: str,
    amount_rub: float,
    user_tg_id: int
) -> Tuple[bool, Optional[str], Optional[str]]:
    service = get_lavatop_service()
    return await service.create_invoice(order_id=order_id, amount_rub=amount_rub, user_tg_id=user_tg_id)

async def check_lavatop_status(external_invoice_id: str) -> Optional[str]:
    service = get_lavatop_service()
    return await service.get_invoice_status(invoice_id=external_invoice_id)
