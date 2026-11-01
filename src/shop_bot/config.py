import html as py_html
import re
from aiogram import html

CHOOSE_PLAN_MESSAGE = "Выберите подходящий тариф:"
CHOOSE_PAYMENT_METHOD_MESSAGE = "Выберите удобный способ оплаты:"
VPN_INACTIVE_TEXT = "❌ <b>Статус VPN:</b> Неактивен (срок истек)"
VPN_NO_DATA_TEXT = "ℹ️ <b>Статус VPN:</b> У вас пока нет активных ключей."

MAX_KEYS = 50
DEFAULT_CURRENCY = "USD"
DEFAULT_CURRENCY_SYMBOL = "$"

def get_profile_text(username, total_spent, total_months, vpn_status_text, user_id=None):
    raw_user = str(username or "").strip()

    # Снимаем любые ошибочно наложенные теги разметки (напр. <b>...</b>),
    # чтобы предотвратить превращение их в &lt;b&gt; при экранировании.
    changed = True
    while changed and raw_user:
        changed = False
        for tag in ("b", "code", "i", "s", "u"):
            open_tag, close_tag = f"<{tag}>", f"</{tag}>"
            if raw_user.startswith(open_tag) and raw_user.endswith(close_tag):
                raw_user = raw_user[len(open_tag):-len(close_tag)].strip()
                changed = True

    if not raw_user or raw_user.lower() in ("none", ""):
        safe_username = "Не указан"
    elif raw_user == "Пользователь":
        safe_username = "Пользователь"
    elif raw_user.startswith("@"):
        safe_username = f"@{py_html.escape(raw_user.lstrip('@'))}"
    elif re.match(r'^[a-zA-Z0-9_]{3,32}$', raw_user):
        safe_username = f"@{py_html.escape(raw_user)}"
    else:
        safe_username = py_html.escape(raw_user)

    id_line = ""
    if user_id is not None:
        raw_id = str(user_id).strip()
        changed = True
        while changed and raw_id:
            changed = False
            for tag in ("b", "code", "i", "s", "u"):
                open_tag, close_tag = f"<{tag}>", f"</{tag}>"
                if raw_id.startswith(open_tag) and raw_id.endswith(close_tag):
                    raw_id = raw_id[len(open_tag):-len(close_tag)].strip()
                    changed = True
        safe_id = py_html.escape(raw_id)
        id_line = f"\n🆔 <b>ID:</b> <code>{safe_id}</code>"

    try:
        total_spent_val = float(total_spent or 0)
    except (ValueError, TypeError):
        total_spent_val = 0.0

    try:
        total_months_val = int(total_months or 0)
    except (ValueError, TypeError):
        total_months_val = 0

    return (
        f"👤 <b>Профиль:</b> {safe_username}{id_line}\n\n"
        f"💰 <b>Потрачено всего:</b> ${total_spent_val:.2f}\n"
        f"📅 <b>Приобретено месяцев:</b> {total_months_val}\n\n"
        f"{vpn_status_text}"
    )

def get_vpn_active_text(days_left, hours_left):
    return (
        f"✅ <b>Статус VPN:</b> Активен\n"
        f"⏳ <b>Осталось:</b> {days_left} д. {hours_left} ч."
    )

def get_key_info_text(key_number, expiry_date, created_date, connection_string):
    expiry_formatted = expiry_date.strftime('%d.%m.%Y в %H:%M')
    created_formatted = created_date.strftime('%d.%m.%Y в %H:%M')
    
    return (
        f"<b>🔑 Информация о ключе #{key_number}</b>\n\n"
        f"<b>➕ Приобретён:</b> {created_formatted}\n"
        f"<b>⏳ Действителен до:</b> {expiry_formatted}\n\n"
        f"{html.code(connection_string)}"
    )

def get_purchase_success_text(action: str, key_number: int, expiry_date, connection_string: str):
    action_text = "обновлен" if action == "extend" else "готов"
    expiry_formatted = expiry_date.strftime('%d.%m.%Y в %H:%M')

    return (
        f"🎉 <b>Ваш ключ #{key_number} {action_text}!</b>\n\n"
        f"⏳ <b>Он будет действовать до:</b> {expiry_formatted}\n\n"
        f"{html.code(connection_string)}"
    )