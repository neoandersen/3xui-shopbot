import os
import re
import html
import asyncio
import logging
from typing import Optional, Tuple
import aiohttp

from shop_bot.data_manager import database

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"
DEFAULT_TIMEOUT_SECONDS = 2.5
DEFAULT_SUPPORT_BOT = "neovpn_supportbot"

# Map deep-link tags to demand segments and focus areas
ATTRIBUTION_MAP = {
    "ref_yt": {
        "segment": "youtube",
        "title": "YouTube 4K & Smart TV",
        "pain": "замедление и буферизация YouTube, Smart TV и ПК",
        "solution": "обход троттлинга ТСПУ благодаря VLESS Reality с маскировкой под TLS",
        "cta": "Заберите 3 дня теста прямо сейчас и проверьте 4K видео без лагов!"
    },
    "ref_ds": {
        "segment": "discord",
        "title": "Discord & Voice UDP",
        "pain": "блокировка Discord, сбои голосовых каналов и высокий пинг",
        "solution": "оптимизация UDP-трафика, минимальный джиттер и стабильный голос в войсе",
        "cta": "Заберите 3 дня теста и возвращайтесь к комфортному общению в Discord!"
    },
    "ref_rkn": {
        "segment": "tspu",
        "title": "Обход ТСПУ и Белые списки",
        "pain": "падение WireGuard, OpenVPN, Outline и бан протоколов",
        "solution": "бессигнатурный протокол VLESS Reality, неотличимый от веб-трафика HTTPS",
        "cta": "Заберите 3 дня теста VPN, который не смогут заблокировать!"
    },
    "ref_mob": {
        "segment": "mobile",
        "title": "iOS & Android Mobile",
        "pain": "неудобные VPN для смартфонов, разряд батареи и обрывы связи",
        "solution": "легкие клиенты (Streisand, FoXray, v2rayNG), подключение по QR и нулевой жор батареи",
        "cta": "Заберите 3 дня теста и настройте смартфон в 1 клик!"
    },
    "ub_bio": {
        "segment": "organic",
        "title": "Органический переход",
        "pain": "поиск надежного и быстрого VPN без цензуры",
        "solution": "скоростные серверы до 1 Гбит/с на VLESS Reality без логов и ограничений",
        "cta": "Заберите 3 дня бесплатного доступа и наслаждайтесь свободным интернетом!"
    },
    "ref_promo": {
        "segment": "organic",
        "title": "Промо-переход",
        "pain": "поиск надежного и быстрого VPN без цензуры",
        "solution": "скоростные серверы до 1 Гбит/с на VLESS Reality без логов и ограничений",
        "cta": "Заберите 3 дня бесплатного доступа и оцените скорость!"
    }
}

STATIC_FALLBACK_TEMPLATES = {
    "ref_yt": (
        "👋 <b>Добро пожаловать в NeoVPN!</b>\n\n"
        "Устали от вечной буферизации и бесконечного «колесика» на YouTube? "
        "Мы используем протокол <b>VLESS Reality</b>, который полностью обходит замедления ТСПУ. "
        "Видео открываются мгновенно в <b>4K 60fps</b> на компьютере, смартфонах и Smart TV.\n\n"
        "🎁 <b>Заберите 3 дня бесплатного теста</b> прямо сейчас — нажмите кнопку ниже!"
    ),
    "ref_ds": (
        "👋 <b>Добро пожаловать в NeoVPN!</b>\n\n"
        "Discord снова недоступен или друзья не слышат вас в войсе? "
        "NeoVPN гарантирует кристально чистый звук без задержек: у нас оптимизирована "
        "маршрутизация <b>UDP-пакетов</b> и минимальный пинг для игр.\n\n"
        "🎁 <b>Заберите 3 дня бесплатного теста</b> и возвращайтесь в войс — жмите кнопку ниже!"
    ),
    "ref_rkn": (
        "👋 <b>Добро пожаловать в NeoVPN!</b>\n\n"
        "WireGuard, OpenVPN и Outline заблокированы провайдером? "
        "NeoVPN работает на бессигнатурном протоколе <b>VLESS Reality</b> — он маскирует ваш трафик "
        "под защищенный HTTPS-трафик и невидим для фильтров ТСПУ.\n\n"
        "🎁 <b>Заберите 3 дня надежного теста</b> без риска блокировок — жмите кнопку ниже!"
    ),
    "ref_mob": (
        "👋 <b>Добро пожаловать в NeoVPN!</b>\n\n"
        "Ищете быстрый и удобный VPN для смартфона? NeoVPN идеально работает на iOS и Android "
        "через современные клиенты (<b>Streisand, FoXray, v2rayNG</b>). "
        "Мгновенный импорт по QR-коду и минимальный расход батареи.\n\n"
        "🎁 <b>Заберите 3 дня бесплатного теста</b> и подключайтесь в один клик!"
    ),
    "organic": (
        "👋 <b>Добро пожаловать в NeoVPN!</b>\n\n"
        "Премиальный скоростной доступ к свободному интернету без ограничений и цензуры. "
        "Протокол <b>VLESS Reality</b> обеспечивает скорость до 1 Гбит/с и полную приватность.\n\n"
        "🎁 <b>Заберите 3 дня бесплатного теста</b> прямо сейчас — нажмите кнопку ниже!"
    ),
    "general": (
        "👋 <b>Добро пожаловать в NeoVPN!</b>\n\n"
        "Скоростной и стабильный VPN на базе протокола нового поколения <b>VLESS Reality</b>. "
        "Никаких блокировок, высокая скорость и простая настройка на любых устройствах.\n\n"
        "🎁 <b>Заберите 3 дня бесплатного теста</b> прямо сейчас — нажмите кнопку ниже!"
    )
}

def get_demand_segment(tag: Optional[str]) -> str:
    """Return demand segment name string for a tag."""
    return get_demand_segment_for_tag(tag)[0]

def get_demand_segment_for_tag(tag: Optional[str]) -> Tuple[str, dict]:
    """Return demand segment name and metadata for a tag."""
    if not tag:
        return "general", {}
    clean_tag = tag.strip().lower()
    if clean_tag in ATTRIBUTION_MAP:
        return ATTRIBUTION_MAP[clean_tag]["segment"], ATTRIBUTION_MAP[clean_tag]
    # Fuzzy match
    if "yt" in clean_tag or "youtube" in clean_tag:
        return "youtube", ATTRIBUTION_MAP["ref_yt"]
    if "ds" in clean_tag or "discord" in clean_tag:
        return "discord", ATTRIBUTION_MAP["ref_ds"]
    if "rkn" in clean_tag or "tspu" in clean_tag or "block" in clean_tag:
        return "tspu", ATTRIBUTION_MAP["ref_rkn"]
    if "mob" in clean_tag or "ios" in clean_tag or "android" in clean_tag:
        return "mobile", ATTRIBUTION_MAP["ref_mob"]
    if "bio" in clean_tag or "promo" in clean_tag:
        return "organic", ATTRIBUTION_MAP["ub_bio"]
    return "general", {}

def get_support_bot_link() -> str:
    """Return support bot username or full telegram link."""
    username = database.get_setting("support_bot_username") or os.getenv("SUPPORT_BOT_USERNAME", DEFAULT_SUPPORT_BOT)
    username = username.lstrip("@").strip()
    return f"@{username}"

def sanitize_telegram_html(text: str) -> str:
    """Safely converts common markdown patterns to valid Telegram HTML,
    and strips/escapes any illegal HTML tags to avoid TelegramBadRequest can't parse entities.
    """
    if not text:
        return ""

    # Replace bold **text** or __text__ with temporary placeholders
    bold_tokens = []
    def _bold_sub(m):
        bold_tokens.append(m.group(1))
        return f"__BOLD_TOKEN_{len(bold_tokens) - 1}__"

    italic_tokens = []
    def _italic_sub(m):
        italic_tokens.append(m.group(1))
        return f"__ITALIC_TOKEN_{len(italic_tokens) - 1}__"

    code_tokens = []
    def _code_sub(m):
        code_tokens.append(m.group(1))
        return f"__CODE_TOKEN_{len(code_tokens) - 1}__"

    s = re.sub(r"`([^`]+)`", _code_sub, text)
    s = re.sub(r"\*\*([^*]+)\*\*", _bold_sub, s)
    s = re.sub(r"\*([^*]+)\*", _italic_sub, s)

    # Escape HTML special chars in remaining text
    s = html.escape(s, quote=False)

    # Restore tokens as strict HTML tags
    for i, content in enumerate(code_tokens):
        s = s.replace(f"__CODE_TOKEN_{i}__", f"<code>{html.escape(content, quote=False)}</code>")
    for i, content in enumerate(bold_tokens):
        s = s.replace(f"__BOLD_TOKEN_{i}__", f"<b>{html.escape(content, quote=False)}</b>")
    for i, content in enumerate(italic_tokens):
        s = s.replace(f"__ITALIC_TOKEN_{i}__", f"<i>{html.escape(content, quote=False)}</i>")

    return s.strip()

def get_static_welcome_template(tag: Optional[str], user_name: Optional[str] = None) -> str:
    """Instant fail-safe fallback template for onboarding."""
    if not tag:
        tpl = STATIC_FALLBACK_TEMPLATES["general"]
    else:
        clean_tag = tag.strip().lower()
        if clean_tag in STATIC_FALLBACK_TEMPLATES:
            tpl = STATIC_FALLBACK_TEMPLATES[clean_tag]
        else:
            segment, _ = get_demand_segment_for_tag(tag)
            if segment in STATIC_FALLBACK_TEMPLATES:
                tpl = STATIC_FALLBACK_TEMPLATES[segment]
            else:
                tpl = STATIC_FALLBACK_TEMPLATES["general"]

    if user_name and user_name.strip():
        safe_name = html.escape(user_name.strip(), quote=False)
        tpl = tpl.replace("👋 <b>Добро пожаловать в NeoVPN!</b>", f"👋 <b>Добро пожаловать, {safe_name}!</b>")
    return tpl

async def call_openrouter_api(system_prompt: str, user_prompt: str, max_tokens: int = 250) -> Optional[str]:
    """Call OpenRouter API with strict 2.5s timeout.
    Returns response text or None if error/timeout occurs.
    """
    api_key = database.get_setting("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY")
    if not api_key or not api_key.strip():
        logger.debug("OpenRouter API key is not configured, skipping AI call.")
        return None

    model = database.get_setting("openrouter_model") or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.6,
        "max_tokens": max_tokens
    }

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "HTTP-Referer": "https://t.me/neovpn_servers_bot",
        "X-Title": "NeoVPN ShopBot",
        "Content-Type": "application/json"
    }

    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices")
                    if choices and len(choices) > 0:
                        content = choices[0].get("message", {}).get("content", "").strip()
                        if content:
                            return content
                else:
                    body = await resp.text()
                    logger.warning(f"OpenRouter API returned status {resp.status}: {body[:200]}")
                    return None
    except asyncio.TimeoutError:
        logger.info(f"OpenRouter API call timed out after {DEFAULT_TIMEOUT_SECONDS}s, switching to fallback.")
        return None
    except Exception as e:
        logger.warning(f"OpenRouter API error: {e}")
        return None

async def generate_welcome_message(tag: Optional[str], user_name: Optional[str] = None) -> str:
    """Generate personalized onboarding welcome message for a user based on deep-link tag.
    Guarantees response in <= 2.5s by using OpenRouter with strict timeout and static fallback.
    """
    segment, meta = get_demand_segment_for_tag(tag)
    display_name = (user_name or "друг").strip()

    if not meta:
        return get_static_welcome_template(tag)

    system_prompt = (
        "Ты — профессиональный старший инженер техподдержки и консультант премиального сервиса NeoVPN. "
        "Твоя задача: доброжелательно, экспертно и очень емко (до 420 символов) поприветствовать пользователя, "
        "снять его боль/страх касательно конкретной проблемы (YouTube троттлинг, блокировка Discord, падение VPN протоколов или мобильные клиенты), "
        "объяснить преимущество протокола VLESS Reality (бессигнатурный, маскируется под обычный TLS) "
        "и убедительно призвать нажать кнопку ниже, чтобы забрать 3 дня бесплатного теста. "
        "ОБЯЗАТЕЛЬНО: пиши на русском языке. Ответ должен быть строго в Telegram HTML (разрешены только теги <b>, <code>, <i>). "
        "Не используй Markdown (* или **), не используй другие теги."
    )

    user_prompt = (
        f"Имя пользователя: {display_name}\n"
        f"Контекст перехода: {meta.get('title')}\n"
        f"Проблема/Боль: {meta.get('pain')}\n"
        f"Решение NeoVPN: {meta.get('solution')}\n"
        f"Целевое действие: {meta.get('cta')}\n\n"
        "Напиши короткое, убедительное первое приветствие с призывом активировать 3-дневный тест."
    )

    try:
        ai_response = await call_openrouter_api(system_prompt, user_prompt, max_tokens=220)
        if ai_response:
            clean = sanitize_telegram_html(ai_response)
            # Ensure trial call to action is present
            if "3 дня" not in clean and "тест" not in clean.lower():
                clean += "\n\n🎁 <b>Заберите 3 дня бесплатного теста</b> — кнопка прямо под этим сообщением!"
            return clean
    except Exception as e:
        logger.warning(f"Failed to generate AI welcome message: {e}")

    return get_static_welcome_template(tag)

async def handle_user_question(user_question: str, user_name: Optional[str] = None, tag: Optional[str] = None) -> str:
    """Handle follow-up questions within the 15-minute onboarding window.
    Responds with clear instructions, mentions setup guides, and links to support bot.
    """
    support_bot = get_support_bot_link()
    display_name = (user_name or "пользователь").strip()

    system_prompt = (
        f"Ты — дружелюбный и компетентный инженер техподдержки NeoVPN. "
        f"Пользователь {display_name} задал вопрос в первые 15 минут после старта бота. "
        f"Ответь кратко (до 380 символов), четко и вежливо. "
        f"Если вопрос касается настройки — назови рекомендуемое приложение (iOS: Streisand/FoXray, Android: v2rayNG, Windows: Nekoray) "
        f"и напомни, что подробные инструкции есть в меню '❓ Как использовать'. "
        f"При любых сложных или технических вопросах направь пользователя к живому инженеру в техподдержку: {support_bot}. "
        f"Используй только безопасный Telegram HTML (<b>, <i>, <code>). Не используй Markdown."
    )

    user_prompt = f"Вопрос пользователя: {user_question}"

    try:
        ai_response = await call_openrouter_api(system_prompt, user_prompt, max_tokens=220)
        if ai_response:
            clean = sanitize_telegram_html(ai_response)
            if support_bot not in clean:
                clean += f"\n\n💬 Если остались вопросы — оператор в {support_bot} готов помочь!"
            return clean
    except Exception as e:
        logger.warning(f"Failed to handle user question with AI: {e}")

    # Smart heuristic fallback
    q = user_question.lower()
    if any(k in q for k in ["ios", "iphone", "айфон", "айпад", "apple"]):
        return (
            "📱 <b>Настройка на iOS (iPhone / iPad):</b>\n\n"
            "1. Установите приложение <b>Streisand</b> или <b>FoXray</b> из App Store.\n"
            "2. Активируйте ключ по кнопке <b>«🎁 Забрать 3 дня теста»</b> в меню.\n"
            "3. Скопируйте ключ и вставьте в приложение.\n\n"
            f"Пошаговый гайд есть в меню бота. При любых сложностях пишите нашему инженеру: {support_bot}!"
        )
    if any(k in q for k in ["android", "андроид", "самсунг", "сяоми"]):
        return (
            "🤖 <b>Настройка на Android:</b>\n\n"
            "1. Установите <b>v2rayNG</b> из Google Play.\n"
            "2. Получите ключ в боте по кнопке <b>«🎁 Забрать 3 дня теста»</b>.\n"
            "3. В приложении нажмите «+» → «Импорт профиля из буфера обмена» и нажмите Connect.\n\n"
            f"Если что-то не получается — инженер в {support_bot} на связи!"
        )
    if any(k in q for k in ["windows", "виндовс", "пк", "комп", "ноутбук"]):
        return (
            "💻 <b>Настройка на Windows:</b>\n\n"
            "1. Скачайте <b>Nekoray</b> или <b>Hiddify</b>.\n"
            "2. Скопируйте ключ из меню бота.\n"
            "3. Импортируйте ключ из буфера и включите режим TUN.\n\n"
            f"Инструкция доступна в меню бота. Оператор саппорта: {support_bot}!"
        )
    if any(k in q for k in ["mac", "мак", "macos"]):
        return (
            "🍎 <b>Настройка на macOS:</b>\n\n"
            "1. Установите <b>FoXray</b> или <b>V2Box</b> из Mac App Store.\n"
            "2. Вставьте ваш VLESS ключ из меню бота.\n\n"
            f"Пошаговый гайд ждет вас в меню. Нужна помощь? Напишите в {support_bot}!"
        )
    if any(k in q for k in ["дискорд", "discord", "войс", "голос", "джиттер", "пинг"]):
        return (
            "🎧 <b>NeoVPN & Discord:</b>\n\n"
            "NeoVPN использует протокол VLESS Reality с прямой маршрутизацией UDP. "
            "Голосовые каналы, конференции и демонстрация экрана работают без сбоев и робо-голоса!\n\n"
            f"🎁 Нажмите <b>«🎁 Забрать 3 дня теста»</b> и проверьте сами. Помощь: {support_bot}"
        )
    if any(k in q for k in ["ютуб", "youtube", "видео", "4k", "буферизац"]):
        return (
            "📺 <b>YouTube без троттлинга:</b>\n\n"
            "Благодаря VLESS Reality провайдер не видит сигнатуру YouTube и не может его замедлять. "
            "Видео воспроизводятся плавно в 4K 60fps!\n\n"
            f"🎁 Активируйте 3 дня теста в меню ниже. Вопросы по настройке Smart TV: {support_bot}"
        )

    return (
        "👋 <b>NeoVPN Ассистент:</b>\n\n"
        "Мы используем протокол нового поколения <b>VLESS Reality</b> — он обходит все блокировки "
        "и дает стабильную скорость до 1 Гбит/с на любых устройствах.\n\n"
        f"🎁 Рекомендуем забрать <b>3 дня бесплатного теста</b> по кнопке ниже, "
        f"а если требуется помощь специалиста — напишите в наш бот поддержки: {support_bot}!"
    )
