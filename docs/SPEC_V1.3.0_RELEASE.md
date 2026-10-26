# КОНСОЛИДИРОВАННАЯ ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ РЕЛИЗА v1.3.0
**Проект:** NeoVPN ShopBot (`3xui-shopbot` / `@neovpn_servers_bot`)  
**Версия релиза:** 1.3.0-stable  
**Статус:** Утверждено к внедрению  
**Базовая валюта:** Доллар США (USD, $)  
**Сетевой периметр:** Защищённый (UFW 100/100, Nginx Reverse Proxy)

---

## Содержание
1. [Пункт 1. Мультивалютное расчётное ядро (База USD + 24h Кэш)](#пункт-1-мультивалютное-расчётное-ядро-база-usd--24h-кэш)
2. [Пункт 2. Платёжная экосистема без юридических лиц (0% ИП / ФОП)](#пункт-2-платёжная-экосистема-без-юридических-лиц-0-ип--фоп)
3. [Пункт 3. Маршрутизация подписок через субдомен 443 (Без 2096)](#пункт-3-маршрутизация-подписок-через-субдомен-443-без-2096)
4. [Пункт 4. Коррекция матрицы портов безопасности (UFW Whitelist)](#пункт-4-коррекция-матрицы-портов-безопасности-ufw-whitelist)
5. [Пункт 5. Устранение сбоя Speedtest Runner (SSH-клиент 2.1.3)](#пункт-5-устранение-сбоя-speedtest-runner-ssh-клиент-213)
6. [Пункт 6. Архитектура Multi-Key и пакетная покупка (1:N)](#пункт-6-архитектура-multi-key-и-пакетная-покупка-1n)

---

## Пункт 1. Мультивалютное расчётное ядро (База USD + 24h Кэш)

### 1.1. Базовый системный эквивалент
* Вся тарифная сетка, балансы пользователей (`users.balance`), накопления партнёров (`users.referral_balance`, `users.referral_balance_all`) и расчёты транзакций фиксируются в **USD ($)**.
* Во избежание ошибок округления чисел с плавающей точкой (`IEEE 754 float precision errors`), значения в базе данных хранятся в центах (целые числа `INTEGER`) либо нормализуются функцией `round(value, 2)`.

### 1.2. Пользовательский выбор валюты (Preferred Currency)
В модель пользователя добавляется поле предпочитаемой валюты отображения.
* Поле в БД: `users.preferred_currency VARCHAR(5) DEFAULT 'USD'`.
* Поддерживаемый список:
  * 🇺🇸 `USD` — Доллар США ($)
  * 🇪🇺 `EUR` — Евро (€)
  * 🇺🇦 `UAH` — Украинская гривна (₴)
  * 🇷🇺 `RUB` — Российский рубль (₽)
  * 🇰🇿 `KZT` — Казахстанский тенге (₸)
* При отображении каталога тарифов, суммы пополнения и баланса бот конвертирует базовые значения USD в `preferred_currency` по зафиксированному курсу.

### 1.3. Сервис котировок с фиксацией на 24 часа (`currency_service.py`)
Таблица `exchange_rates`:
```sql
CREATE TABLE IF NOT EXISTS exchange_rates (
    currency_pair VARCHAR(16) PRIMARY KEY, -- 'USD_EUR', 'USD_UAH', 'USD_BTC'
    rate REAL NOT NULL,
    updated_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
```

**Бизнес-логика кэширования:**
* Время жизни курса: строго **86 400 секунд (24 часа)**:
  $$\text{expires\_at} = \text{updated\_at} + 86400$$
* Если текущий Unix Timestamp меньше `expires_at`, повторные внешние API-запросы блокируются.
* **Снапшот инвойса:** При выставлении счёта на оплату курс фиксируется в `transactions.exchange_rate`, гарантируя неизменность суммы в течение срока действия инвойса.

```python
# src/shop_bot/services/currency_service.py
import time
import requests
import logging

CACHE_TTL = 86400  # 24 часа

class CurrencyService:
    def __init__(self, db_conn):
        self.db = db_conn

    def get_rate(self, target_currency: str) -> float:
        if target_currency.upper() == "USD":
            return 1.0

        pair = f"USD_{target_currency.upper()}"
        now = int(time.time())

        cursor = self.db.cursor()
        cursor.execute("SELECT rate, expires_at FROM exchange_rates WHERE currency_pair = ?", (pair,))
        row = cursor.fetchone()

        if row and now < row[1]:
            return row[0]

        # Обновление курса через внешний API (только по истечении 24h)
        new_rate = self._fetch_external_rate(target_currency)
        expires_at = now + CACHE_TTL

        cursor.execute("""
            INSERT INTO exchange_rates (currency_pair, rate, updated_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(currency_pair) DO UPDATE SET
                rate = excluded.rate,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
        """, (pair, new_rate, now, expires_at))
        self.db.commit()
        return new_rate

    def _fetch_external_rate(self, currency: str) -> float:
        try:
            res = requests.get(f"https://open.er-api.com/v6/latest/USD", timeout=5).json()
            return float(res["rates"].get(currency.upper(), 1.0))
        except Exception as e:
            logging.error(f"Error updating exchange rate for {currency}: {e}")
            return 1.0
```

---

## Пункт 2. Платёжная экосистема без юридических лиц (0% ИП / ФОП)

Все классические эквайринги (Stripe, Monobank, LiqPay, WayForPay), требующие юридических лиц, ФОП 2–3 групп, выписки КВЭД и комплаенс-договоры на цифровые услуги, исключены. Подключены провайдеры, работающие с физическими лицами (Digital/P2P Aggregators).

```
                      ┌─────────────────────────────────┐
                      │    Запрос на оплату подписки     │
                      └────────────────┬────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────────┐                         ┌───────────────────────────┐
│       Шлюз Aaio.so        │                         │     Шлюз Cryptomus.com    │
│  Фиат: UAH, RUB, KZT, EUR │                         │  Крипта (8 монет) + P2P   │
│  Банковские карты / СБП   │                         │  Режим: Personal Merchant │
└───────────┬───────────────┘                         └───────────┬───────────────┘
            │                                                     │
            │ POST /webhook/aaio                                  │ POST /webhook/cryptomus
            │ (Sign: SHA256 w/ Secret2)                           │ (Sign: MD5 Base64 w/ APIKey)
            ▼                                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     NeoVPN Webhook Server (Port: 8443)                          │
│        1. Проверка цифровой подписи (timing-attack safe via compare_digest)     │
│        2. Идемпотентность транзакции по provider_payment_id                     │
│        3. Авто-вызов xui_api.py (CSRF session) -> начисление ключей             │
│        4. Начисление реферального бонуса в USD -> Telegram Push пользователю    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1. Шлюз Aaio (`src/shop_bot/modules/aaio_payment.py`)
* **Методы оплаты:** Банковские карты (UAH, RUB, KZT, Международные), СБП.
* **Генерация ссылки:**
  $$\text{sign} = \text{SHA-256}(\text{merchant\_id} : \text{amount} : \text{currency} : \text{secret\_1} : \text{order\_id})$$
* **Верификация вебхука:**
  $$\text{expected\_sign} = \text{SHA-256}(\text{merchant\_id} : \text{amount} : \text{currency} : \text{secret\_2} : \text{order\_id})$$
  Ответ вебхука при валидной сигнатуре: HTTP 200 со строкой `OK`.

### 2.2. Шлюз Cryptomus (`src/shop_bot/modules/cryptomus_payment.py`)
* **Поддерживаемый пул криптовалют:**
  1. `USDT` (TRC20, TON, BEP20)
  2. `USDC` (ERC20, Polygon, Solana)
  3. `BTC` (Bitcoin)
  4. `ETH` (Ethereum)
  5. `XRP` (Ripple с учётом Destination Tag)
  6. `XMR` (Monero)
  7. `LTC` (Litecoin)
  8. `TON` (The Open Network)
* **Создание инвойса:** `POST https://api.cryptomus.com/v1/payment`
  * Заголовки: `merchant: <UUID>`, `sign: <md5_hash>`.
  * Алгоритм подписи:
    $$\text{sign} = \text{MD5}(\text{Base64}(\text{json\_compact}) + \text{API\_KEY})$$
* **Вебхук на порт 8443:**
  Извлечение поля `sign`, удаление его из тела, проверка `MD5(Base64(raw_body) + API_KEY)`. Зачисление при статусах `paid` и `paid_over`.

### 2.3. Ревизия существующих шлюзов
* **Telegram Stars:** Инвойсы формируются через `send_invoice`, сумма в USD пересчитывается в XTR по курсу 1 XTR = $0.02. Обработка через `pre_checkout_query` и `successful_payment`.
* **CryptoBot, Heleket, YooKassa, YooMoney:** Проверка входящих подписей, конвертация в USD, проверка уникальности транзакции.

---

## Пункт 3. Маршрутизация подписок через субдомен 443 (Без 2096)

Для обеспечения 100% проходимости через сотовых операторов и корпоративные брандмауэры нестандартный порт 2096 изолируется внутри хоста. Выдача подписок осуществляется по чистому 443 порту через субдомен.

### 3.1. Раздельная схема хранения параметров (по аналогии с 3X-UI)
Вместо монолитной строки с шаблоном `{token}` в модель хоста `xui_hosts` внедрены два независимых поля:
* `sub_domain TEXT DEFAULT 'https://sub.neo-vpn.click'`
* `sub_path TEXT DEFAULT '/sub/'`

**Нормализация и сборка клиентской ссылки:**
```python
def build_client_subscription_url(sub_domain: str, sub_path: str, sub_id: str) -> str:
    norm_domain = (sub_domain or "https://sub.neo-vpn.click").rstrip('/')
    norm_path = (sub_path or "/sub/").strip('/')
    if norm_path:
        return f"{norm_domain}/{norm_path}/{sub_id}"
    return f"{norm_domain}/{sub_id}"
```

### 3.2. Конфигурация Nginx: `deploy/nginx/sub.neo-vpn.click.conf`
Файл устанавливается в `/etc/nginx/sites-available/sub.neo-vpn.click.conf`:
```nginx
# 1. Редирект HTTP -> HTTPS и валидация Certbot ACME
server {
    listen 80;
    listen [::]:80;
    server_name sub.neo-vpn.click;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
        allow all;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# 2. Шлюз безопасной выдачи подписок
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name sub.neo-vpn.click;

    ssl_certificate /etc/letsencrypt/live/neo-vpn.click/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/neo-vpn.click/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Поддержка стандартного /sub/ и расширенного /subscription/
    location ~* ^/(sub|subscription)/ {
        proxy_pass https://127.0.0.1:2096;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_ssl_verify off;

        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 15s;
    }

    # Маскировка корня от сетевых сканеров
    location / {
        return 404;
    }
}
```

---

## Пункт 4. Коррекция матрицы портов безопасности (UFW Whitelist)

Фактические назначения портов приведены к единому стандарту в скрипте харденинга `src/scripts/server_security_harden.sh` и веб-интерфейсе `src/shop_bot/webhook_server/templates/admin/security.html`:

| Порт / Протокол | Истинная роль в инфраструктуре NeoVPN | Статус в периметре |
|---|---|---|
| **`42357/tcp`** | **3X-UI Web Panel Core** (Панель управления узлами Xray / внутреннее API) | Белый список (Разрешён) |
| **`8443/tcp`** | **ShopBot Admin & Webhook Gateway** (Вход в веб-панель и платёжные вебхуки) | Белый список (Разрешён) |
| **`$SSH_PORT`** *(10022/tcp)* | **Кастомный порт SSH** (Защищённое администрирование хоста) | Белый список (Разрешён) |
| **`443/tcp & udp`** | **VLESS Reality / Hysteria2 / Subscriptions** (Прокси-трафик и выдача подписок) | Белый список (Разрешён) |
| **`80/tcp`** | **HTTP / ACME Certbot** (Выпуск и продление SSL Let's Encrypt) | Белый список (Разрешён) |
| **`22/tcp`** | **Стандартный SSH (Блокирован, Trap-порт)** | Чёрный список (Блокирован) |
| **`2096/tcp`** | **Внутренний сокет 3X-UI Subscriptions** | Чёрный список (Изолирован, прокси через 443) |
| **`1488/tcp`** | **Внутренний Flask backend** | Чёрный список (Локальный контейнер) |
| **`8085/tcp`** | **Nginx internal fallback** | Чёрный список (Локальный) |

---

## Пункт 5. Устранение сбоя Speedtest Runner (SSH-клиент 2.1.3)

### 5.1. Причина дефекта
На хосте `vcn-tgbot` установлена Python-версия `speedtest-cli 2.1.3`. При вызове официальных аргументов Ookla CLI (`-f json` или `--accept-license`) утилита завершалась с ошибкой `error: unrecognized arguments: -f json`, возвращая пустой `stdout`.

### 5.2. Реализация в `src/shop_bot/modules/speedtest_runner.py`
1. Приоритетный запуск команд с флагом `--json`:
   ```python
   COMMANDS = [
       "speedtest --json",
       "speedtest-cli --json",
       "/usr/local/bin/speedtest --json",
       "speedtest --accept-license --accept-gdpr -f json",
       "speedtest -f json"
   ]
   ```
2. Отказоустойчивый парсинг чистого JSON-блока из потока `stdout` (игнорирующий возможные предупреждения в `stderr`):
   ```python
   import json
   import re

   def parse_speedtest_output(raw_output: str) -> dict:
       match = re.search(r'\{.*\}', raw_output, re.DOTALL)
       if not match:
           raise ValueError("No JSON object found in speedtest output")
       
       data = json.loads(match.group(0))
       return {
           "download_mbps": round(data.get("download", 0) / 1_000_000, 2),
           "upload_mbps": round(data.get("upload", 0) / 1_000_000, 2),
           "ping_ms": round(data.get("ping", 0), 2)
       }
   ```

---

## Пункт 6. Архитектура Multi-Key и пакетная покупка (1:N)

### 6.1. Схема базы данных `user_keys`
```sql
CREATE TABLE IF NOT EXISTS user_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    host_id INTEGER NOT NULL,
    key_name TEXT NOT NULL DEFAULT 'Ключ',
    client_uuid TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    sub_id TEXT NOT NULL UNIQUE,
    traffic_limit_bytes INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expiry_time INTEGER NOT NULL, -- Unix timestamp в миллисекундах (3X-UI)
    status TEXT NOT NULL DEFAULT 'active', -- active, expired, disabled
    FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
    FOREIGN KEY (host_id) REFERENCES xui_hosts(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_user_keys_user_id ON user_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_user_keys_sub_id ON user_keys(sub_id);
```

### 6.2. Бесшовная миграция Legacy-пользователей
Если у пользователя в таблице `users` уже сохранён ключ (`users.sub_id`), миграционный скрипт автоматически создаёт запись в `user_keys` с именем `Ключ #1 (Основной)`:
```python
def migrate_legacy_keys(conn):
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, host_id, client_uuid, email, sub_id, expiry_time FROM users WHERE sub_id IS NOT NULL")
    for row in cur.fetchall():
        tg_id, host_id, uuid, email, sub_id, exp = row
        cur.execute("SELECT id FROM user_keys WHERE sub_id = ?", (sub_id,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO user_keys (user_id, host_id, key_name, client_uuid, email, sub_id, expiry_time, status)
                VALUES (?, ?, 'Ключ #1 (Основной)', ?, ?, ?, ?, 'active')
            """, (tg_id, host_id or 1, uuid, email, sub_id, exp or 0))
    conn.commit()
```

### 6.3. Виджет пакетной покупки (Telegram FSM)
При оформлении тарифа пользователю предлагается выбор количества (от 1 до 20):
```
📦 Настройка заказа:
Тариф: 1 Месяц ($3.00 / шт.)
Количество устройств: 3 шт.

💰 Итого к оплате: $9.00 (~810.00 ₽ / ~370.00 ₴)
[ ➖ 1 ]     [ 3 шт. ]     [ ➕ 1 ]
[ 💳 Оплатить $9.00 ]
[ ◀️ Назад ]
```

### 6.4. Пакетная генерация ключей в 3X-UI API
При получении подтверждения оплаты:
1. Выполняется цикл создания $N$ клиентов через `xui_api.py`.
2. Каждому клиенту присваивается уникальный идентификатор:
   * `email = f"u{user_id}_k{key_num}_{secrets.token_hex(3)}"`
   * `sub_id = secrets.token_hex(16)`
   * `client_uuid = str(uuid.uuid4())`
3. Ключи сохраняются в `user_keys`.
4. Клиенту отправляется единое сообщение со ссылками на подписки через субдомен `https://sub.neo-vpn.click/sub/{sub_id}`.

### 6.5. Личный кабинет «🔑 Мои ключи»
* Список всех активных устройств пользователя.
* Карточка конкретного ключа: ссылка, QR-код, кнопки «✏️ Переименовать» и «🔄 Продлить».
* Защита от IDOR: любые манипуляции с ключом строго проверяют `WHERE id = :key_id AND user_id = :telegram_id`.