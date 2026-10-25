# Re-exporting from shop_bot.services.ai_onboarding for specification path compatibility
from shop_bot.services.ai_onboarding import (
    generate_welcome_message,
    handle_user_question,
    get_demand_segment,
    get_demand_segment_for_tag,
    get_support_bot_link,
    sanitize_telegram_html,
    get_static_welcome_template,
    call_openrouter_api,
    ATTRIBUTION_MAP,
    STATIC_FALLBACK_TEMPLATES,
)

__all__ = [
    "generate_welcome_message",
    "handle_user_question",
    "get_demand_segment",
    "get_demand_segment_for_tag",
    "get_support_bot_link",
    "sanitize_telegram_html",
    "get_static_welcome_template",
    "call_openrouter_api",
    "ATTRIBUTION_MAP",
    "STATIC_FALLBACK_TEMPLATES",
]
