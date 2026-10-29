# Исследование архитектуры и отчет реализации: NeoVPN ShopBot (AI & Attribution)

Данный документ фиксирует результаты аудита, проектирования и внедрения системы атрибуции трафика, AI-онбординга и оптимизации UI/UX для Telegram-бота продаж `@neovpn_servers_bot` (`3xui-shopbot`) в соответствии со спецификацией [docs/SHOPBOT_AI_AND_ATTRIBUTION_SPEC.md](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/docs/SHOPBOT_AI_AND_ATTRIBUTION_SPEC.md).

---

## 1. Архитектурный контекст и цели

В экосистеме NEOVPN сервис внешней лидогенерации `neovpn-promoter` (Радар) перенаправляет целевой трафик по диплинкам:
`https://t.me/neovpn_servers_bot?start=<payload>`

### Действующие сегменты и теги:
| Диплинк-тег | Сегмент спроса | Ключевая боль / Потребность | Преимущество VLESS Reality |
|---|---|---|---|
| `ref_yt` | `youtube` | Замедление YouTube / 4K Smart TV / троттлинг | Обход троттлинга ТСПУ, 4K без буферизации |
| `ref_ds` | `discord` | Блокировка Discord / войс / высокий пинг / UDP | Оптимизация UDP, стабильный войс, низкий пинг |
| `ref_rkn` | `tspu` | Блокировки ТСПУ / падение WireGuard, Outline | Бессигнатурный протокол Reality под видом HTTPS |
| `ref_mob` | `mobile` | Мобильные ОС (iOS/Android) / экономия батареи | Нативные клиенты (Streisand, FoXray, v2rayNG), 0% жор батареи |
| `ub_bio` | `organic` | Трафик из профиля юзербота-скаута | Полная свобода интернета до 1 Гбит/с |
| `ref_promo` | `organic` | Промо-кампании и спецпредложения | Быстрый тест скорости и надежности |

---

## 2. Анализ и исправление критических дефектов

### 2.1. Ошибка парсинга реферальных кодов (`ValueError: invalid literal for int()`)
- **Проблема**: В оригинальном коде `start_handler` любая строка, начинающаяся с `ref_`, безусловно парсилась как `int(args.split('_')[1])`. Это приводило к `ValueError: invalid literal for int() with base 10: 'yt'` при переходе по маркетинговым ссылкам `ref_yt`, `ref_ds`, `ref_rkn`, `ref_mob`. В результате бот падал в ошибку, терял атрибуцию и не регистрировал переход.
- **Решение**: Реализовано разделение реферальных кодов пользователей (`ref_<digits>` или `<digits>`) и маркетинговых тегов атрибуции (`ref_yt`, `ref_ds`, `ref_rkn`, `ref_mob`, `ref_promo`, `ub_bio`).

### 2.2. Устранение `TelegramBadRequest: can't parse entities`
- **Проблема**: Боты используют `DefaultBotProperties(parse_mode=ParseMode.HTML)`. Вставка необработанных строк (имена пользователей с `<`, `>`, `&`, текст сообщений тикетов саппорта с vless-ссылками или спецсимволами, markdown-токены от AI) вызывала фатальные ошибки парсинга разметки Telegram.
- **Решение**:
  1. Внедрен строгий санитизатор `sanitize_telegram_html()`, преобразующий markdown (`**bold**`, `` `code` ``, `*italic*`) в безопасные HTML-теги `<b>`, `<code>`, `<i>` с экранированием всех остальных угловых скобок и амперсандов.
  2. Все пользовательские строки (имена, темы обращений, текст сообщений) экранируются через `py_html.escape()`.
  3. Для фолбэка добавлен перехват `TelegramBadRequest: can't parse entities` со снятием тегов (`re.sub(r'<[^>]+>', '', text)`).

---

## 3. UI/UX Оптимизация витрины

### 3.1. Кнопка 3-дневного триала на первом ряду (Full-Width)
- Кнопка `[ 🎁 Забрать 3 дня теста ]` вынесена на первый ряд (row 0, width 2) во всю ширину экрана.
- Кнопка отображается динамически: только если пользователь еще не активировал триал (`trial_available and trial_is_on`).
- Обеспечена автоматическая миграция раскладки кнопок в таблице `button_configs` базы данных, гарантирующая сдвиг профиля на 2-й ряд, а триала — на 1-й ряд.

### 3.2. Сетка выбора платформ 2x2
В клавиатурах инструкций `create_howto_vless_keyboard()` и `create_howto_vless_keyboard_key()` сформирована симметричная сетка 2x2:
```
[ 📱 iOS ]      [ 📱 Android ]
[ 💻 Windows ]  [ 🍎 macOS ]
[      ⬅️ Назад в меню      ]
```
Добавлен полноценный обработчик подключения для `macOS` (`FoXray` / `V2box`), а также поддержка инструкций из карточки конкретного ключа (`howto_<os>_<key_id>`).

### 3.3. Логика выдачи 3-дневного теста VLESS Reality
- Проверка выдачи строго 1 раз на уникальный Telegram ID (`trial_used` или `trial_granted`).
- Генерация конфигурации VLESS Reality на сервере 3x-ui с уникальным email вида `trial_<username>@bot.local`.
- Фиксация времени выдачи `trial_granted_at` и учет в сквозной аналитике источников.

---

## 4. Схема БД и сквозная аналитика трафика

### 4.1. Модели SQLAlchemy 2.0 (asyncpg / PostgreSQL)
Созданы декларативные модели в пакетах `src/shop_bot/models/` и `app/models/`:
- [Base](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/src/shop_bot/models/base.py) — базовый декларативный класс.
- [User](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/src/shop_bot/models/user.py) — модель пользователя с полями:
  - `acquisition_source` (String, nullable) — тег диплинка (например, `ref_yt`).
  - `demand_segment` (String, default='general') — сегмент спроса (`youtube`, `discord`, `tspu`, `mobile`, `organic`).
  - `trial_granted` (Boolean, default=False) — факт выдачи бесплатного теста.
  - `trial_granted_at` (DateTime, nullable) — таймстамп активации триала.
  - `converted_to_paid` (Boolean, default=False) — совершение первой оплаты.
  - `last_start_at` (Integer, nullable) — unix timestamp последнего вызова `/start`.
- [TrafficSourcesStat](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/src/shop_bot/models/traffic.py) — модель сводной статистики:
  - `source_tag` (String, PK)
  - `description` (String)
  - `clicks_count` (Integer)
  - `trials_count` (Integer)
  - `sales_count` (Integer)
  - `total_income` (Float)

### 4.2. Безопасная автомиграция активной SQLite БД (`users.db`)
В [database.py](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/src/shop_bot/data_manager/database.py) при каждом запуске `initialize_db()`:
1. Создается таблица `traffic_sources_stat` с начальными описаниями всех тегов Радара.
2. Проверяется наличие и при необходимости безопасно добавляются колонки в `users` через `ALTER TABLE ADD COLUMN`.
3. Заполняются системные настройки `openrouter_api_key`, `openrouter_model`, `support_bot_username`.

### 4.3. Команды аналитики
- Добавлена команда администратора `/traffic_stats` в основном боте.
- Добавлена кнопка «🎯 Источники трафика» (`admin_traffic_stats`) в админ-меню с интерактивным просмотром конверсий (Клики → Триалы (CR) → Продажи (CR) → Выручка).

---

## 5. AI-онбординг и автоответчик (OpenRouter API)

Создан сервис [src/shop_bot/services/ai_onboarding.py](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/src/shop_bot/services/ai_onboarding.py) (с реэкспортом в `app/services/ai_onboarding.py`):

1. **Генерация персонализированного приветствия**:
   - При переходе по диплинку формируется персонализированный оффер под боль клиента (YouTube 4K, Discord UDP, ТСПУ/VPN бан, Mobile iOS/Android).
   - Лимит времени вызова OpenRouter: **строго 2.5 секунды** (`asyncio.wait_for(..., timeout=2.5)`).
   - При отсутствии ключа, ошибке сети или таймауте >2.5с срабатывает **мгновенный откат (fallback)** на выверенные шаблоны сегментов.

2. **Автоответчик первого контакта (15-минутное окно)**:
   - Если пользователь пишет в чат обычным текстом в течение 15 минут после `/start` (при пустом FSM-состоянии), бот распознает вопрос и отвечает с помощью AI с учетом контекста сегмента.
   - Под ответом выводятся кнопки:
     - `[ 🎁 Забрать 3 дня теста ]` (если триал еще не взят)
     - `[ 📖 Инструкция по подключению ]`
     - `[ 💬 Написать в техподдержку ]` (прямая ссылка на `@neovpn_supportbot`)

---

## 6. Инструкции по дальнейшей поддержке

1. **Настройка OpenRouter**:
   В панели админа или в настройках `bot_settings` задать ключ:
   - `openrouter_api_key` = `<ваш_openrouter_api_key>`
   - `openrouter_model` = `meta-llama/llama-3.3-70b-instruct` (или любая другая модель OpenRouter)
   - `support_bot_username` = `neovpn_supportbot`

2. **Добавление новых источников Радара**:
   Для добавления нового диплинк-тега (например, `ref_tg` для Telegram Proxy) достаточно расширить словарь `ATTRIBUTION_MAP` и `STATIC_FALLBACK_TEMPLATES` в `src/shop_bot/services/ai_onboarding.py`. Статистика по новому тегу начнет собираться в `traffic_sources_stat` автоматически с первого клика.

---

## 7. Реструктуризация веб-панели под стиль 3X-UI (Sidebar Layout)

В соответствии с дизайн-системой современной панели управления 3X-UI (`3x-ui` modern admin panel), выполнен полный редизайн веб-интерфейса `3xui-shopbot`:

### 7.1. Компонентная структура и макет
- **Было**: Горизонтальный Topbar (`<div class="row align-items-center">`), переполнявшийся на экранах средней ширины и приводивший к вылету кнопок и переключателя темы за пределы видимой области.
- **Стало**: Двухколоночный **Sidebar Layout** (`.xui-layout`):
  1. **Левый сайдбар (`.xui-sidebar`)** (фиксированная ширина 250px):
     - **Брендинг (`.xui-sidebar-header`)**: Логотип, название панели (`#brand-title`) с иконкой быстрой смены (`#brand-edit`), кнопка переключения темы (`#theme-toggle`) и избранное.
     - **Быстрый поиск (`.xui-search-box`)**: Интерактивный поиск по разделам меню в реальном времени с поддержкой горячих клавиш `Ctrl + K` / `Cmd + K`.
     - **Навигационное меню (`.xui-nav-menu`)**: Вертикальный список со скругленными элементами (`.xui-nav-item`), иконками Tabler Icons, светящимися активными индикаторами (`.active`), бейджами открытых тикетов (`.open-tickets-badge`).
       - Разделы: *Главная (Дашборд)*, *Пользователи*, *Ключи*, *🎯 Источники трафика*, *Поддержка*, *Монитор*, *Конструктор*, *Настройки*.
     - **Футер сайдбара (`.xui-sidebar-footer`)**: Быстрый выход из аккаунта и индикатор версии с переходом на GitHub репозиторий.
     - **Мобильный режим (Mobile Drawer)**: Скрытие сайдбара на экранах `< 992px` с плавным выдвижным drawer-меню (`.xui-sidebar.show`) и затемнением фона (`#sidebar-backdrop`).
  2. **Верхний компактный статус-бар (`.xui-topbar`)**:
     - Кнопка вызова мобильного меню (`#sidebar-toggle`).
     - Быстрый статус бота с кнопками перезапуска (`[ ⟳ Перезапуск ]`) и остановки (`[ ⏻ Стоп ]`).
     - Профиль авторизованного администратора (`admin_username`) и кнопка выхода.
  3. **Основная область контента (`.xui-content`)**:
     - `margin-left: 250px` (на десктопе), `overflow-x: hidden`, `min-width: 0` для исключения горизонтального скролла и корректного рендеринга графиков Chart.js и адаптивных таблиц.

### 7.2. Цветовая палитра и токены 3X-UI
Использованы оригинальные CSS-переменные стиля 3X-UI (Ant Design Dark / Space Cyber):
- Основной акцентный цвет: `--primary-color: #1677ff` / hover: `#4096ff`
- Фон страницы: `--xui-bg-body: #0b0e14`
- Фон сайдбара: `--xui-bg-sidebar: #0e121b`
- Фон карточек и панелей: `--xui-bg-card: #131926`
- Границы и разделители: `--xui-border: #1d2538`
- Текстовая иерархия: Основной `#e6edf3`, вторичный `#8b949e`, приглушенный `#484f58`
- Градиент активного пункта: `linear-gradient(90deg, rgba(22, 119, 255, 0.22) 0%, rgba(22, 119, 255, 0.05) 100%)` с левым неоновым индикатором `3px solid #1677ff`.

### 7.3. Новая страница сквозной аналитики: `/traffic_stats`
- В веб-панель добавлен полноценный раздел анализа источников Радара (`/traffic_stats` и `/traffic-stats`):
  - 4 сводные метрики в виде карточек 3X-UI: Общие переходы, Активации триала (CR%), Оплаченные подписки (CR%), Общая выручка.
  - Подробная таблица сегментов спроса (`ref_yt`, `ref_ds`, `ref_rkn`, `ref_mob`, `ref_promo`, `ub_bio`).
  - Информационный блок архитектуры экосистемы `neovpn-promoter` ↔ `@neovpn_servers_bot`.

---

## 8. Прямая вставка текста SSH-ключа хостов (SSH Key Direct Text Input)

Для исключения проблем с монтированием томов и правами доступа в Docker-окружении выполнена замена указания путей к файлам ключей на прямую вставку текста приватного ключа:

### 8.1. UI и шаблоны
1. **В карточках хостов (`settings.html`) и модальных окнах (`monitor.html`)**:
   - Поле пути к файлу (`ssh_key_path`) заменено на полноразмерное текстовое поле:
     ```html
     <textarea class="form-control font-monospace" name="ssh_key_content" id="ssh_key_content" rows="5" placeholder="Вставьте сюда содержимое приватного ключа (-----BEGIN ... PRIVATE KEY-----)"></textarea>
     ```
   - Добавлен статус-бейдж текущей привязки (`Ключ сохранён: <path>`).
   - Поле опционально: если оставить пустым, сохраняется ранее привязанный ключ.
   - Добавлен чекбокс «Удалить привязку ключа» (`clear_ssh_key`) для возможности переключения хоста в режим авторизации по паролю.
   - В форму добавления нового хоста добавлен выпадающий блок опциональных настроек SSH (`#addHostSshSection`).
   - На странице мониторинга (`monitor.html`) для каждого хоста добавлена кнопка вызова модального окна редактирования SSH (`#hostSshModal-<index>`).

### 8.2. Бэкенд и безопасность хранения
1. **Автоматическое создание защищенной директории**:
   - Ключи сохраняются в `/app/project/data/ssh_keys/` (с локальным fallback `data/ssh_keys/` вне Docker).
   - Применяются права доступа `0o700` на директорию и строгие `0o600` на файл ключа (`os.chmod(filepath, 0o600)`), что исключает ошибку OpenSSH `Bad permissions`.
2. **Именование файлов**:
   - Имя формируется на основе нормализованного имени хоста: `host_{safe_name}.key`.
3. **Нормализация формата (CRLF -> LF)**:
   - Вставка из браузеров на Windows приводит к переводам строк `\r\n`. Бэкенд автоматически нормализует переносы строк к Unix `\n` и гарантирует завершающий перенос строки, устраняя ошибки `Invalid key format` / `PEM format error`.
4. **Валидация ключей**:
   - Функция `validate_ssh_private_key` проверяет структуру PEM/OpenSSH и валидирует парсинг через Paramiko.

### 8.3. Универсальная загрузка ключей в Paramiko
- В `resource_monitor.py` и `speedtest_runner.py` реализована поддержка всех типов приватных ключей:
  - `paramiko.RSAKey`
  - `paramiko.Ed25519Key`
  - `paramiko.ECDSAKey`
- Поддерживается как беспарольная авторизация, так и зашифрованные ключи с паролем (`password=ssh_password`).

---

## 9. Интеграция профиля безопасности и мониторинга периметра (Security Hardening Suite v1.3.0)

В соответствии со спецификацией [docs/SECURITY_HARDENING&ADMIN_INTEGRATION.md](file:///d:/REPOS/projects/VB/source/3xui-shopbot-main/docs/SECURITY_HARDENING&ADMIN_INTEGRATION.md) в веб-панель интегрирован модуль мониторинга и активной защиты периметра:

### 9.1. Скрипт харденинга периметра: `src/scripts/server_security_harden.sh`
1. **Динамическое определение SSH-порта**: Чтение слушающего сокета через `ss -tlnp` с фолбэком на директивы `/etc/ssh/sshd_config` и `/etc/ssh/sshd_config.d/*.conf`. Если порт нестандартный, стандартный порт 22 принудительно блокируется в UFW.
2. **Тюнинг ядра и сети (`/etc/sysctl.d/99-neovpn-security.conf`)**:
   - `net.ipv4.icmp_echo_ignore_all = 1`: Stealth Mode — сервер не отвечает на ICMP echo (ping) от сетевых сканеров (Shodan, Censys).
   - `net.ipv4.icmp_ignore_bogus_error_responses = 1`: сохранение валидации Path MTU Discovery.
   - Защита от SYN Flood: `tcp_syncookies = 1`, `tcp_max_syn_backlog = 4096`.
   - Защита от IP-спуфинга: `rp_filter = 1`, логирование аномальных пакетов `log_martians = 1`.
3. **Конфигурация UFW (Default Deny)**:
   - Входящие закрыты по умолчанию (`default deny incoming`), исходящие разрешены (`default allow outgoing`).
   - Белый список: `$SSH_PORT/tcp`, `80/tcp`, `443/tcp`, `443/udp`, `8443/tcp`, `42357/tcp`.
   - Изоляция внутренних сокетов: принудительный deny для `22` (default SSH trap), `2096` (подписки 3X-UI), `1488` (локальный Flask-сервер), `8085` (Nginx fallback).
4. **Контроль Fail2ban и автоматические патчи**:
   - Проверка статуса сервиса `fail2ban-client ping`.
   - Настройка автоматических security-обновлений Debian/Ubuntu через `unattended-upgrades`.
5. **Фиксация прав на файлы (File Hardening)**:
   - Права `0600` на SQLite базы (`*.db`) и приватные SSH-ключи в `data/ssh_keys/`.
   - Права `0700` на директории ключей и `/etc/letsencrypt`.
6. **Экспорт артефактов**:
   - JSON-файл аудита: `data/security_audit.json`.
   - Статический веб-отчет: `/var/www/neo-vpn-landing/audit.html`.

### 9.2. Бэкенд админки: `src/shop_bot/modules/enhanced_admin_routes.py`
- `GET /admin/security`: загрузка `data/security_audit.json` и рендеринг дашборда безопасности.
- `POST /admin/security/run` & `POST /admin/api/security/run-hardening`: запуск `server_security_harden.sh` через `subprocess.run` с таймаутом 120 секунд и возвратом JSON-ответа `{ "ok": true, "output": "...", "audit_data": {...} }`.
- `GET /admin/api/security/status`: API-эндпоинт для polling/проверки текущих метрик защиты.

### 9.3. Фронтенд: `src/shop_bot/webhook_server/templates/admin/security.html`
- Стилистика 3X-UI / Ant Design Dark (`.card`, `.badge`, `.table-vcenter`).
- 5 ключевых KPI-виджетов: SSH Shield, ICMP Stealth, UFW Status, Fail2ban & Patches, Служебные порты.
- Матрица портов (Белый список vs Черный список) и таблица параметров ядра sysctl.
- Интерактивная кнопка «Запустить харденинг» с анимацией лоадера и выводом терминального лога в реальном времени.
- Добавлен пункт меню «Безопасность» (`<i class="ti ti-shield-check"></i>`) в сайдбар `base.html`.

## 10. Исправление авторизации в 3X-UI v3.10+ (CSRF Token & Docker Host Trigger)

### 10.1. Устранение ошибки HTTP 403 Forbidden при входе в 3X-UI v3.10.x
- **Причина**: В версиях 3X-UI 3.x+ (React SPA) Gin-middleware требует обязательного наличия валидной сессионной cookie и заголовка `X-Csrf-Token`, значение которого берется из мета-тега `<meta name="csrf-token" content="...">` при первичном GET-запросе к корню панели (`Web Base Path`). Стандартный метод `api.login()` библиотеки `py3xui` отправлял изолированный POST без CSRF-токена, получая пустой ответ HTTP 403.
- **Реализация в `src/shop_bot/modules/xui_api.py`**:
  1. Реализован менеджер персистентных сессий `_HOST_SESSIONS: Dict[str, requests.Session]` с функцией `get_or_create_host_session(base_url)`.
  2. Перед `api.login()` выполняется `session.get(f"{base_url}/", verify=False, timeout=10)`.
  3. Регулярным выражением `name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']` (и обратным порядком атрибутов) извлекается актуальный CSRF-токен.
  4. Обновляются заголовки `session.headers`:
     - `Origin`: базовый URL хоста.
     - `Referer`: базовый URL хоста со слэшем на конце.
     - `X-Requested-With`: `XMLHttpRequest`.
     - `X-Csrf-Token`: спарсенный токен.
  5. Введена функция `_bind_session_to_api(api, session)`, которая подменяет транспортные вызовы `_request_with_retry` у всех суб-API (`client`, `inbound`, `database`, `server`) на методы активного `requests.Session`.
  6. Благодаря этому заголовок `X-Csrf-Token` и сессионные cookies сохраняются и передаются во всех последующих запросах (создание клиентов, обновление, синхронизация планировщика).

### 10.2. Механизм делегирования харденинга из Docker (Host Trigger)
- **Причина**: Внутри изолированного Docker-контейнера веб-приложение не имеет доступа к `systemd`, `ufw` и `sysctl` хоста.
- **Реализация в `src/shop_bot/modules/enhanced_admin_routes.py`**:
  - При обнаружении Docker (`os.path.exists('/.dockerenv')`):
    1. Создается файл триггера `data/run_harden.trigger` с метаданными запроса.
    2. Выполняется опрос (polling до 15 секунд) появления обновленного `data/security_audit.json` либо удаления триггера хост-демоном.
    3. Клиенту возвращаются актуальные метрики аудита безопасности.
  - Вне Docker (или на хосте): скрипт `server_security_harden.sh` запускается напрямую через `subprocess` (с дев-эмуляцией для Windows).
  - В скрипте `src/scripts/server_security_harden.sh` добавлен автоматический сброс файла триггера при запуске (`rm -f "${TRIGGER_FILE}"`).

## 11. Релиз NeoVPN ShopBot v1.3.0: Мультивалютное ядро, платёжные шлюзы и нормализация подписок

### 11.1. Разделение конфигурации URL подписки (`sub_domain` и `sub_path`)
- **Проблема**: В ранних версиях ссылка подписки хранилась в виде единой строки `subscription_url` с неявным или жестко закодированным плейсхолдером `{token}`, что приводило к ошибкам генерации при смене домена или порта, а также не соответствовало структуре параметров 3X-UI.
- **Реализация**:
  1. В таблицу `xui_hosts` добавлены столбцы `sub_domain TEXT DEFAULT 'https://sub.neo-vpn.click'` и `sub_path TEXT DEFAULT '/sub/'`.
  2. Нормализация клиентской ссылки подписки в `src/shop_bot/modules/xui_api.py` (`get_subscription_link`):
     ```python
     norm_domain = (sub_domain or "https://sub.neo-vpn.click").rstrip('/')
     norm_path = (sub_path or "/sub/").strip('/')
     if norm_path:
         return f"{norm_domain}/{norm_path}/{client_sub_id}"
     return f"{norm_domain}/{client_sub_id}"
     ```
     Полностью исключен хардкод `{token}` в строке шаблона. Для легаси записей выполняется автоматический парсинг домена и пути через `urllib.parse.urlparse`.
  3. В веб-интерфейсе `settings.html`:
     - В блоке «Добавить новый хост» разделены поля ввода `sub_domain` и `sub_path`.
     - В карточке каждого существующего хоста единый инпут заменен на адаптивную сетку полей с кнопкой сохранения и динамическим превью формата клиентской ссылки.
  4. Nginx-конфигурация `deploy/nginx/sub.neo-vpn.click.conf`:
     - Принимает запросы по HTTPS 443 на субдомен `sub.neo-vpn.click`.
     - Проксирует путь `/sub/` на локальный порт `127.0.0.1:2096/sub/`, скрывая порт 2096 от сканеров и блокировок провайдеров.

### 11.2. Мультивалютное ядро и котировочный сервис (`currency_service.py`)
- **Базовая валюта**: Доллар США (`USD`, `$`). Все тарифы, балансы и реферальные начисления рассчитываются в USD.
- **Пользовательская валюта**: В таблицу `users` добавлен столбец `preferred_currency VARCHAR(5) DEFAULT 'USD'`.
- **Строгий кэш 24 часа**: В таблице `exchange_rates (currency_pair PK, rate, updated_at, expires_at)` TTL равен строго 86400 сек (`expires_at = updated_at + 86400`). Запросы к внешним API (open.er-api.com и CoinGecko) выполняются только по истечении срока действия кэша.
- **Снепшот курсов**: Поле `transactions.exchange_rate` фиксирует курс на момент создания инвойса.
- **Интерфейс Telegram**: В профиле пользователя размещены инлайн-кнопки переключения валют (USD, EUR, UAH, RUB, KZT) с динамической отметкой `✓` и автоматическим пересчетом баланса и статистики.

### 11.3. Платёжные шлюзы без комиссии для самозанятых / ИП (0% Aaio и Cryptomus)
- **Aaio (`src/shop_bot/modules/aaio_payment.py`)**:
  - Карточный эквайринг и СБП (UAH, RUB, KZT, Международные карты).
  - Формирование ссылки оплаты через `https://aaio.so/merchant/get_pay_url` с подписью SHA-256 (Secret 1).
  - Вебхук `POST /webhook/aaio` с проверкой подписи SHA-256 (Secret 2) и идемпотентной активацией подписки через `find_and_complete_pending_transaction`.
- **Cryptomus (`src/shop_bot/modules/cryptomus_payment.py`)**:
  - Топ-8 криптовалют: USDT, USDC, BTC, ETH, XRP, XMR, LTC, TON.
  - Инвойсирование в USD через `POST https://api.cryptomus.com/v1/payment` с заголовками `merchant` и `sign` (MD5 от Base64 JSON-тела + API-ключ).
  - Вебхук `POST /webhook/cryptomus` и ручная проверка статуса через `POST /v1/payment/info`.

### 11.4. Speedtest Runner и матрица портов периметра
- В `src/shop_bot/data_manager/speedtest_runner.py` команды `speedtest --json` и `speedtest-cli --json` вынесены на первое место в списке `COMMANDS` для совместимости с версией 2.1.3 на целевых хостах.
- Разработан отказоустойчивый парсер `_extract_json_from_text()`, устойчивый к служебным баннерам в stdout.
- В `security.html` и `server_security_harden.sh` обновлены описания портов матричного аудита (3X-UI Core 42357, Admin Gateway 8443, Trap SSH 22, VLESS Reality 443).

### 11.5. Архитектура Multi-Key и пакетная покупка (1:N)
- **Таблица `user_keys`**:
  - Создана таблица `user_keys (id, user_id, host_id, key_name, client_uuid, email, sub_id, traffic_limit_bytes, created_at, expiry_time, status)`.
  - Добавлены индексы `idx_user_keys_user_id` и `idx_user_keys_sub_id`.
  - Реализован хелпер `_format_key_dict()`, гарантирующий как новые атрибуты схемы v1.3.0, так и обратную совместимость для всех существующих обработчиков и веб-панели (`key_id`, `xui_client_uuid`, `key_email`, `expiry_date`, `created_date`, `comment`).
- **Автомиграция Legacy-ключей**:
  - Функция `migrate_legacy_keys(conn)` автоматически переносит все существующие ключи из `users.sub_id` и `vpn_keys` в `user_keys` с присвоением имени `Ключ #1 (Основной)` или порядкового номера.
- **FSM виджет пакетной покупки (1..20 шт.)**:
  - Состояние `PaymentProcess.waiting_for_quantity`.
  - Инлайн-виджет `[ ➖ 1 ] [ {count} шт. ] [ ➕ 1 ]`, кнопка подтверждения `[ 💳 Оплатить {сумма} ]` и возврат к тарифам.
  - Итоговая сумма заказа динамически масштабируется (`цена * количество`), учитывая промокоды и скидки.
- **Пакетная генерация ключей в 3X-UI**:
  - В `process_successful_payment` при покупке $N$ ключей выполняется цикл создания $N$ независимых клиентов через `xui_api.py` с уникальными `sub_id`, UUID и CSRF-сессией.
  - Пользователю отправляется единое сводное сообщение со всеми сгенерированными ссылками на подписку через субдомен `https://sub.neo-vpn.click/sub/{sub_id}` и инлайн-клавиатурой инструкций (iOS, Android, Windows, macOS) в сетке 2x2.
- **Управление и переименование ключей (Защита от IDOR)**:
  - В меню «🔑 Мои ключи» отображаются пользовательские имена (`key_name`).
  - В карточке ключа добавлена кнопка `[ ✏️ Переименовать ]`.
  - Состояние `KeyRename.waiting_for_key_name` с функцией `rename_user_key(key_id, user_id, new_name)`, строго проверяющей владельца на уровне SQL: `WHERE id = :key_id AND user_id = :telegram_id`.

## 12. Модуль безопасности: Nginx Overflow Shield & Network Hardening

### 12.1. Сервис аудита и применения параметров (`NginxHardeningService`)
- **Расположение**: `src/shop_bot/services/nginx_hardening_service.py`
- **Аудит конфигурации Nginx**:
  - `server_tokens off;` — сокрытие версии Nginx в ответах и страницах ошибок.
  - `client_header_buffer_size 1k;` и `large_client_header_buffers 4 8k;` — ограничение заголовков до 8 КБ (отсечение атак Buffer Overflow через URI/Cookie с кодом HTTP 414/400).
  - `client_body_buffer_size 16k;` и `client_max_body_size 10m;` — предотвращение переполнения оперативной памяти воркеров телом запроса.
  - Защитные таймауты против Slowloris: `client_header_timeout 10s;`, `client_body_timeout 10s;`, `send_timeout 10s;`, `keepalive_timeout 30s;`, `reset_timedout_connection on;`.
  - Зоны лимитов соединений и запросов: `limit_conn_zone $binary_remote_addr zone=addr_limit:10m;`, `limit_req_zone $limit_req_exempt_key zone=req_limit:10m rate=15r/s;`.
- **White-list для платёжных шлюзов**:
  - Настроена директива `map $request_uri $limit_req_exempt_key`, возвращающая пустую строку `""` для путей `/webhook/*`, `/payok-webhook`, `/lavatop-webhook`, `/cryptomus-webhook`, `/yookassa-webhook`, `/aaio-webhook`, `/platega-webhook`.
  - По спецификации Nginx пустой ключ исключает запрос из лимитирования, гарантируя 100% надёжную доставку платёжных коллбэков при пиковых нагрузках.
- **Fail-Safe Engine с автоматическим откатом**:
  - Физический статический файл конфигурации сохранён в проекте: `deploy/nginx/security_hardening.conf` и `src/shop_bot/config/security_hardening.conf`.
  - Создан автономный скрипт развертывания `deploy/nginx/install_security_hardening.sh`.
  - Перед наложением директив создается временной бэкап `security_hardening.conf.bak_<timestamp>`.
  - Атомарная запись через `.tmp` в `/etc/nginx/conf.d/security_hardening.conf`.
  - Валидация синтаксиса через `nginx -t`.
  - При успехе — мягкий `nginx -s reload` без разрыва активных соединений.
  - При синтаксической ошибке — немедленный автоматический откат к бэкапу и запись лога ошибки.

### 12.2. Защита сетевого стека ядра Linux (Sysctl)
- Контроль и применение параметров в `/etc/sysctl.d/99-neovpn-security.conf`:
  - `net.ipv4.tcp_syncookies = 1` — отражение SYN-flood атак.
  - `net.core.somaxconn = 4096` — расширение очереди прослушиваемых сокетов против переполнения.
  - `net.ipv4.tcp_max_syn_backlog = 4096` — увеличение очереди полуоткрытых TCP-соединений.
  - `net.ipv4.tcp_fin_timeout = 15` — ускоренное закрытие неактивных сокетов FIN-WAIT-2.

### 12.3. Экспресс-парсер аномалий (Log Parser)
- Мониторинг `access.log` и `error.log` Nginx за 24 часа.
- Детекция попыток эксплойта буферов, атак и превышения лимитов запросов:
  - `414` (URI Too Large — попытки переполнения буфера URI)
  - `400` / `494` (Bad Request / Header Too Large — аномальные заголовки)
  - `413` (Payload Too Large — превышение размера тела запроса)
  - `429` (Too Many Requests — срабатывание rate limit `limit_req`)
  - Падения воркеров Nginx (`worker process ... exited on signal / died`).

### 12.4. Расширение API и Веб-интерфейс
- **API эндпоинты** (`src/shop_bot/modules/enhanced_admin_routes.py`):
  - `GET /api/security/nginx` (алиас `/admin/api/security/nginx`) — статус харденинга, директив, sysctl и счётчик аномалий.
  - `POST /api/security/nginx/apply` (алиас `/admin/api/security/nginx/apply`) — безопасное применение Nginx Overflow Shield.
  - `POST /api/security/sysctl/apply` (алиас `/admin/api/security/sysctl/apply`) — наложение сетевых параметров ядра.
- **UI карточка в `security.html`**:
  - «Защита от переполнения буферов (Nginx & Network Shield)» с динамическими индикаторами статуса, счётчиками 414/400/413/429 за 24ч, интерактивными кнопками наложения с подтверждением и терминальным блоком лога проверки `nginx -t`.

### 12.5. Интеграция телеметрии Nginx в Docker-окружение
- **Проброс системных директорий хоста в `docker-compose.yml`**:
  ```yaml
  volumes:
    - .:/app/project
    - /var/log/nginx:/var/log/nginx:ro
    - /etc/nginx/conf.d:/etc/nginx/conf.d:ro
  ```
- **Изоляция и безопасность (`:ro`)**:
  - Все хостовые каталоги монтируются строго в режиме только для чтения (`:ro`), предотвращая несанкционированную модификацию конфигурации или журналов хоста из контейнера.
- **Graceful Fallback & ENV-конфигурируемость**:
  - Пути конфигурируются через переменные окружения (`NGINX_CONF_DIR`, `CONF_D_DIR`, `HARDENING_CONF_PATH`, `NGINX_ACCESS_LOG`, `NGINX_ERROR_LOG`) со стандартными дефолтами.
  - При отсутствии каталогов или журналов сервис логирует понятное предупреждение `logger.warning` и возвращает базовые безопасные метрики без падения приложения.
  - Запросы на изменение настроек изнутри изолированного контейнера формируют триггеры `run_nginx_harden.trigger` / `run_sysctl_harden.trigger` для хостового демона.


