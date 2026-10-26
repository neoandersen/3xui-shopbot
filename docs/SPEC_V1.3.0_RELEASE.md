# Спецификация релиза NeoVPN ShopBot v1.3.0

## 1. Обзор и цели релиза

Релиз **v1.3.0** финализирует стабильную архитектуру сервиса `@neovpn_servers_bot` (`3xui-shopbot`) перед запланированным переходом на Enterprise-архитектуру v2.0.0.

### Основные цели релиза:
1. **Мультивалютное расчётное ядро:** Переход на базовую системную валюту **USD ($)**, выбор предпочитаемой валюты пользователем (USD, EUR, UAH, RUB, KZT) и кэширование курсов обмена строго на **24 часа** (TTL = 86400 секунд).
2. **Web3-процессинг криптовалют:** Поддержка пула из 8 ключевых криптоактивов: **USDT, USDC, BTC, ETH, XRP, XMR, LTC, TON** с автоматической котировкой относительно базовой цены в USD.
3. **Платёжные шлюзы без юридических лиц (0% ИП / ФОП):**
   - Интеграция фиатного эквайринга **Aaio** (банковские карты UAH, RUB, KZT, World, СБП).
   - Интеграция глобального шлюза **Cryptomus** (мультивалютная крипта + встроенный P2P-эквайринг карт).
   - Ревизия и стабилизация существующих модулей: **Telegram Stars**, **CryptoBot**, **Heleket**, **YooKassa**, **YooMoney**.
4. **Маршрутизация подписок через субдомен (Изоляция порта 2096):**
   - Перевод выдачи конфигураций на стандартный HTTPS-порт 443 через субдомен `https://sub.neo-vpn.click/sub/{token}`.
   - Закрытие порта 2096 во внешнем периметре UFW через Nginx reverse proxy.
   - Добавление редактируемого шаблона базового URL подписки в карточке хоста в веб-панели управления.
5. **Коррекция матрицы безопасности (Security Dashboard):**
   - Приведение ролей и подписей портов `42357`, `8443`, `10022` и `22` в соответствие с фактической архитектурой сервера.
6. **Устранение сбоя Speedtest Runner:**
   - Адаптация раннера под установленную утилиту `speedtest-cli` (Python 2.1.3) с флагом `--json`.

---

## 2. Архитектура валютного ядра (База USD + 24h Кэш)

### 2.1. Единый базовый баланс (USD)
- Баланс пользователей (`users.balance`), сетка тарифов и партнёрские начисления (`referral_balance`, `referral_balance_all`) рассчитываются и хранятся в **USD ($)**.
- Значения хранятся в центах (целочисленный `INTEGER`) либо в `DECIMAL(10, 2)` / округление `round(val, 2)` для исключения проблем неточного представления чисел с плавающей точкой.

### 2.2. Пользовательский выбор валюты (Preferred Currency)
- В таблицу `users` добавляется колонка `preferred_currency` (`VARCHAR(5)`, default: `'USD'`).
- В профиле Telegram-бота и веб-панели доступно меню выбора отображаемой валюты:
  - 🇺🇸 **USD** ($)
  - 🇪🇺 **EUR** (€)
  - 🇺🇦 **UAH** (₴)
  - 🇷🇺 **RUB** (₽)
  - 🇰🇿 **KZT** (₸)
- При отображении каталога тарифов и баланса в интерфейсе бот пересчитывает базовые значения USD в `preferred_currency` по зафиксированному курсу.

### 2.3. Кэширование котировок (24 Hours TTL)
- Создается сервисная таблица `exchange_rates`:
  ```sql
  CREATE TABLE IF NOT EXISTS exchange_rates (
      currency_pair VARCHAR(16) PRIMARY KEY, -- 'USD_EUR', 'USD_UAH', 'USD_BTC'
      rate REAL NOT NULL,
      updated_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL
  );
  ```
- **Правило обращения к курсам:**
  - `expires_at = updated_at + 86400`.
  - При запросе конвертации сервис проверяет наличие пары в базе. Если `current_timestamp < expires_at`, используется сохраненный курс.
  - Внешний запрос к API котировок выполняется **только после истечения 24 часов**.
- **Снапшот инвойса:** При создании инвойса курс конвертации фиксируется в поле `transactions.exchange_rate`, чтобы сумма в целевой валюте оставалась неизменной в течение всего срока жизни счета.

---

## 3. Платёжная экосистема (0% ИП / ФОП)

### 3.1. Шлюз Aaio (aaio.so) — Мультивалютный фиат
- **Назначение:** Приём банковских карт (UAH, RUB, KZT, World), СБП и P2P-платежей от физических лиц.
- **Модуль:** `src/shop_bot/modules/aaio_payment.py`.
- **Создание счета:**
  - URL: `https://aaio.so/merchant/get_pay_url`.
  - Пересчёт суммы: `amount_target = round(amount_usd * rate_usd_to_target, 2)`.
  - Формирование сигнатуры (Secret 1):
    $$\text{sign} = \text{SHA-256}(\text{merchant\_id} : \text{amount} : \text{currency} : \text{secret\_1} : \text{order\_id})$$
- **Обработка вебхука:**
  - URL: `POST https://auth.neo-vpn.click:8443/webhook/aaio`.
  - Валидация сигнатуры (Secret 2):
    $$\text{expected\_sign} = \text{SHA-256}(\text{merchant\_id} : \text{amount} : \text{currency} : \text{secret\_2} : \text{order\_id})$$
  - При совпадении подписи: идемпотентное зачисление средств, возврат HTTP 200 со строкой `OK`.

### 3.2. Шлюз Cryptomus (cryptomus.com) — Мультивалютная крипта
- **Назначение:** Приём криптовалют и P2P-карт без юридического лица в режиме Personal Merchant.
- **Модуль:** `src/shop_bot/modules/cryptomus_payment.py`.
- **Поддерживаемые криптовалюты:**
  1. `USDT` (сети TRC20, TON, BEP20)
  2. `USDC` (сети ERC20, Polygon, Solana)
  3. `BTC` (Bitcoin)
  4. `ETH` (Ethereum)
  5. `XRP` (Ripple с учётом Destination Tag)
  6. `XMR` (Monero)
  7. `LTC` (Litecoin)
  8. `TON` (The Open Network)
- **Создание счета:**
  - Запрос: `POST https://api.cryptomus.com/v1/payment`.
  - Сумма передается в базовом эквиваленте USD, конвертацию выполняет шлюз.
  - Заголовки: `merchant: <UUID>`, `sign: <md5_hash>`.
  - Вычисление подписи запроса:
    $$\text{sign} = \text{MD5}(\text{Base64}(\text{json\_compact}) + \text{API\_KEY})$$
- **Обработка вебхука:**
  - URL: `POST https://auth.neo-vpn.click:8443/webhook/cryptomus`.
  - Из тела запроса извлекается поле `sign`, оставшаяся часть JSON верифицируется через `MD5(Base64(raw_body) + API_KEY)`.
  - Статусы зачисления: `paid` и `paid_over`.

### 3.3. Ревизия действующих шлюзов
- **Telegram Stars:** Конвертация суммы тарифа USD в Stars (XTR) с фиксированным курсом (например, 1 XTR = $0.02 USD или эквивалент из настроек), валидация `pre_checkout_query` и зачисление при `successful_payment`.
- **CryptoBot:** Вебхук на 8443 с обязательной верификацией заголовка `crypto-pay-api-signature` (`HMAC-SHA256`).
- **Heleket, YooKassa, YooMoney:** Проверка соответствия входящих сумм базовому USD-эквиваленту и уникальности транзакций.

---

## 4. Маршрутизация подписок через субдомен 443

### 4.1. Архитектура URL и префикс `/sub/`
- Стандартный формат ссылки подписки:
  `https://sub.neo-vpn.click/sub/{token}`
- Префикс `/sub/` строго изолирует отдачу подписок на Nginx, оставляя корень `/` для заглушки или лендинга, и соответствует стандартному параметру `Subscription Subpath` в 3X-UI.

### 4.2. Конфигурация Nginx Reverse Proxy
Файл: `deploy/nginx/sub.neo-vpn.click.conf` (размещается в `/etc/nginx/sites-available/sub.neo-vpn.click.conf` на хосте):
```nginx
server {
    listen 443 ssl http2;
    server_name sub.neo-vpn.click;

    ssl_certificate /etc/letsencrypt/live/neo-vpn.click/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/neo-vpn.click/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location /sub/ {
        proxy_pass https://127.0.0.1:2096/sub/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_ssl_verify off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 15s;
    }

    location / {
        return 404;
    }
}
```

### 4.3. Настройка шаблона в веб-панели
- В таблице `xui_hosts` поле `subscription_url` хранит базовый шаблон (по умолчанию `https://sub.neo-vpn.click/sub/`).
- В `settings.html` на карточке хоста отображается поле ввода адреса подписки. При смене домена новые ссылки генерируются моментально без правок кода.

---

## 5. Коррекция матрицы портов безопасности (UFW Whitelist)

В скрипте `src/scripts/server_security_harden.sh` и веб-шаблоне `src/shop_bot/webhook_server/templates/admin/security.html` утверждаются следующие роли:

| Порт / Протокол | Описание и роль сервиса | Статус в периметре |
|---|---|---|
| **`42357/tcp`** | **3X-UI Web Panel Core** (Панель управления узлами Xray / внутреннее API) | Белый список (Разрешён) |
| **`8443/tcp`** | **ShopBot Admin & Webhook Gateway** (Вход в панель управления и платёжные вебхуки) | Белый список (Разрешён) |
| **`$SSH_PORT`** *(10022/tcp)* | **Кастомный порт SSH** (Защищённое системное администрирование хоста) | Белый список (Разрешён) |
| **`443/tcp & udp`** | **VLESS Reality / Hysteria2 / Subscriptions** (Прокси-трафик и выдача подписок) | Белый список (Разрешён) |
| **`80/tcp`** | **HTTP / ACME Certbot** (Выпуск и автопродление сертификатов SSL Let's Encrypt) | Белый список (Разрешён) |
| **`22/tcp`** | **Стандартный SSH (Trap-порт)** | Чёрный список (Блокирован наружу) |
| **`2096/tcp`** | **Внутренний порт подписок 3X-UI** | Чёрный список (Изолирован, прокси через 443) |
| **`1488/tcp`** | **Внутренний Flask backend** | Чёрный список (Доступ только внутри Docker) |
| **`8085/tcp`** | **Nginx internal fallback** | Чёрный список (Локальный) |

---

## 6. Исправление Speedtest Runner

### 6.1. Причина ошибки
В системе `vcn-tgbot` установлена Python-версия утилиты `speedtest-cli` 2.1.3, которая принимает аргумент `--json` и падает с ошибкой при передаче флагов Ookla CLI (`-f json`).

### 6.2. Решение в `src/shop_bot/modules/speedtest_runner.py`
Команды опроса выстраиваются в порядке совместимости:
```python
SPEEDTEST_COMMANDS = [
    "speedtest --json",
    "speedtest-cli --json",
    "/usr/local/bin/speedtest --json",
    "speedtest --accept-license --accept-gdpr -f json",
    "speedtest -f json"
]
```
Раннер извлекает чистый JSON-блок из `stdout`, игнорирует сервисный вывод в `stderr`, парсит показатели `download`, `upload` (переводя bps в Mbps через деление на $10^6$) и пинг в миллисекундах.

---

## 7. Идемпотентность и сквозная цепочка активации

1. **Проверка дубликатов:** При поступлении вебхука от любого провайдера выполняется проверка:
   ```sql
   SELECT id, status FROM transactions WHERE provider = :provider AND provider_payment_id = :payment_id LIMIT 1;
   ```
   Если транзакция уже имеет статус `COMPLETED`, вебхук отвечает `200 OK` без повторных начислений.
2. **Активация подписки:**
   - Моментальный вызов функции `create_or_renew_key()` через пропатченный модуль `xui_api.py` с сохранением сессионных кук и заголовка `X-Csrf-Token`.
   - Реферальные отчисления начисляются пригласителю в USD (`users.referral_balance`).
   - Пользователь получает push-уведомление в Telegram со ссылкой подписки вида `https://sub.neo-vpn.click/sub/{token}` и сеткой инструкций 2x2:
     ```
     [ 📱 iOS ]      [ 📱 Android ]
     [ 💻 Windows ]  [ 🍎 macOS ]
     [      ⬅️ Главное меню      ]
     ```