# Техническое задание для Antigravity: Интеграция платёжных шлюзов PayOK и Lava.top (Релиз v1.4.0)

## Общий контекст задачи
В рамках разработки ветки **v1.4.0** для проекта `3xui-shopbot` необходимо реализовать нативную интеграцию двух платёжных провайдеров, ориентированных на работу с физическими лицами (без обязательного оформления ИП или юридического лица):
1. **PayOK (payok.io)** — приём банковских карт, СБП и P2P-платежей с валидацией через контрольную MD5-подпись.
2. **Lava.top (gate.lava.top)** — приём карт РФ, зарубежных карт и СБП для цифровых услуг через REST API v3 и валидацию вебхуков по HMAC-SHA256.

Оба провайдера должны быть бесшовно встроены в мультивалютное ядро системы: базовая стоимость тарифа хранится в **USD ($)**, при создании счёта производится пересчёт в целевую валюту шлюза (`RUB` или `USD`) по зафиксированному курсу, а после успешного подтверждения автоматически выдаётся или продлевается VLESS-подписка.

---

## Блок 1. Спецификация шлюза PayOK

### 1.1. Параметры конфигурации (`config.json` и `config.example.json`)
```json
{
  "payok_enabled": false,
  "payok_api_id": "",
  "payok_api_key": "",
  "payok_shop_id": "",
  "payok_secret_key": "",
  "payok_currency": "RUB"
}
```

### 1.2. Механизм генерации платёжной ссылки
1. **Эндпоинт шлюза**: `https://payok.io/pay`
2. **Параметры запроса (GET/POST)**:
   - `amount`: сумма платежа в формате `0.00` (строго 2 знака после запятой).
   - `payment`: уникальный ID транзакции в базе данных магазина (`order_id`).
   - `shop`: идентификатор магазина из конфигурации (`payok_shop_id`).
   - `currency`: валюта транзакции (`RUB` по умолчанию или `USD`).
   - `desc`: описание платежа в кодировке UTF-8 (например, `Оплата подписки VLESS #${order_id}`).
   - `email`: email покупателя (если отсутствует, передавать `user_{telegram_id}@shop.local` или опускать).
   - `sign`: цифровая подпись MD5, вычисляемая конкатенацией параметров через символ пайпа `|`:
     $$\text{sign} = \text{MD5}(\text{amount} \mid \text{payment} \mid \text{shop} \mid \text{currency} \mid \text{desc} \mid \text{payok\_secret\_key})$$

### 1.3. Обработка входящего уведомления (`POST /webhook/payok`)
PayOK отправляет уведомление `application/x-www-form-urlencoded` со следующими полями:
- `payment_id`: номер транзакции магазина, переданный при создании.
- `payok_id`: внутренний идентификатор платежа в системе PayOK.
- `amount`: фактически оплаченная сумма.
- `currency`: валюта оплаты.
- `sign`: контрольная цифровая подпись вебхука.

**Алгоритм валидации и исполнения:**
1. Проверка наличия всех обязательных полей.
2. Проверка подписи вебхука:
   $$\text{expected\_sign} = \text{MD5}(\text{payok\_secret\_key} \mid \text{desc} \mid \text{currency} \mid \text{shop} \mid \text{payment\_id} \mid \text{amount})$$
   *(Примечание: валидировать строго по актуальному порядку параметров в документации PayOK; при несовпадении хеша возвращать `HTTP 403 Forbidden`).*
3. Поиск транзакции в базе данных по `payment_id`.
4. Идемпотентность: если статус транзакции уже равен `PAID`, возвращать ответ `200 OK` (текст `"OK"`), повторную активацию не производить.
5. Если статус `PENDING`:
   - Сверить сумму `amount` с допустимой погрешностью конвертации (не менее ожидаемой суммы счёта).
   - Обновить статус транзакции на `PAID`, записать `external_id = payok_id`.
   - Вызвать сервис активации ключей (`SubscriptionService.activate_or_extend(...)`).
   - Отправить клиенту пуш-сообщение в Telegram с деталями подписки и инструкцией подключения.
   - Вернуть серверу PayOK ответ: HTTP-код `200` с телом `"OK"`.

---

## Блок 2. Спецификация шлюза Lava.top

### 2.1. Параметры конфигурации
```json
{
  "lavatop_enabled": false,
  "lavatop_api_key": "",
  "lavatop_offer_id": "",
  "lavatop_webhook_key": "",
  "lavatop_currency": "RUB"
}
```

### 2.2. Создание счёта через API (`POST https://gate.lava.top/api/v3/invoice`)
Счета выставляются к цифровому офферу с динамической ценой:
- **Заголовки запроса (HTTP Headers)**:
  ```http
  X-Api-Key: <lavatop_api_key>
  Content-Type: application/json
  Accept: application/json
  ```
- **Тело запроса (JSON)**:
  ```json
  {
    "email": "user_{telegram_id}@shop.local",
    "offerId": "<lavatop_offer_id>",
    "currency": "RUB",
    "price": 250.00,
    "orderId": "<unique_order_id>",
    "customFields": {
      "telegram_id": 123456789,
      "tariff_id": 2,
      "keys_count": 1
    }
  }
  ```
- **Обработка ответа API**:
  - Из успешного ответа извлечь `paymentUrl` (ссылка для редиректа клиента) и `invoiceId` (идентификатор инвойса в Lava.top).
  - Сохранить `invoiceId` в поле `external_invoice_id` таблицы транзакций.

### 2.3. Обработка входящего уведомления (`POST /webhook/lavatop`)
- **Проверка авторизации**:
  Сверить сигнатуру из заголовка `X-Signature` или `Authorization`:
  $$\text{expected\_signature} = \text{HMAC-SHA256}(\text{raw\_body}, \text{lavatop\_webhook\_key})$$
  Если подпись не совпадает — отдавать `HTTP 401 Unauthorized` / `HTTP 403 Forbidden`.
- **События вебхука**:
  Обрабатывать тип события `payment.success` или статус инвойса `PAID`.
- **Логика исполнения**:
  1. Извлечь `orderId` и фактическую сумму из полезной нагрузки (`payload`).
  2. Заблокировать транзакцию в БД от параллельного изменения (idempotency check).
  3. Если транзакция уже оплачена — сразу вернуть `{"status": "ok"}` с кодом `200`.
  4. При статусе `PENDING` активировать пакет ключей в 3X-UI и уведомить пользователя в Telegram.

### 2.4. Резервный механизм проверки статуса (Fallback Polling)
В карточку инвойса в Telegram-боте добавить инлайн-кнопку ручной проверки оплаты:
`[ 🔄 Проверить оплату ]` с `callback_data="check_lava_<order_id>"`.
- При клике бот обращается к API Lava.top:
  `GET https://gate.lava.top/api/v1/invoices/{invoice_id}` (с заголовком `X-Api-Key`).
- Если статус инвойса `PAID`, а вебхук задержался в очереди, бот немедленно выполняет выдачу ключей и обновляет интерфейс пользователя.

---

## Блок 3. Архитектурные изменения в кодовой базе

1. **Модель базы данных (`app/database.py` / `src/db.py`)**:
   - Расширить перечисление способов оплаты (`payment_method`): добавить значения `'payok'` и `'lavatop'`.
   - Обеспечить хранение `external_invoice_id`, `pay_currency` и зафиксированного курса обмена `exchange_rate_used`.

2. **Клиентский Telegram-бот (Интерфейс оплаты)**:
   - В клавиатуру выбора методов платежа динамически подтягивать активные шлюзы:
     - `💳 Банковская карта / СБП (PayOK)` — активно, если `payok_enabled == true`.
     - `⚡ Карты и СБП (Lava.top)` — активно, если `lavatop_enabled == true`.
   - При выборе генерировать индивидуальный инвойс с валидной ссылкой на оплату и кнопками:
     - `[ 🔗 Оплатить заказ ] (URL-кнопка)`
     - `[ 🔄 Проверить оплату ] (Callback)`
     - `[ ❌ Отмена ] (Callback)`

3. **Веб-панель управления администратора (`app/admin/templates/settings.html`)**:
   - Создать блок **«Настройки PayOK»**:
     - Тумблер активации шлюза (`payok_enabled`).
     - Поля ввода: `API ID`, `API Key`, `Shop ID`, `Secret Key`, выбор валюты (`RUB`/`USD`).
     - Поле «URL вебхука для PayOK»: `https://<DOMAIN>/webhook/payok` (с кнопкой быстрого копирования).
   - Создать блок **«Настройки Lava.top»**:
     - Тумблер активации шлюза (`lavatop_enabled`).
     - Поля ввода: `API Key`, `Offer ID`, `Webhook Secret Key`, выбор валюты (`RUB`/`USD`).
     - Поле «URL вебхука для Lava.top»: `https://<DOMAIN>/webhook/lavatop` (с кнопкой быстрого копирования).
   - Обновить обработчик сохранения настроек во Flask (`/admin/settings`).

4. **Файл шаблона конфигурации (`config.example.json`)**:
   - Добавить секции PayOK и Lava.top с пустыми значениями по умолчанию.

---

## Блок 4. Инструкция по выполнению для Antigravity

Antigravity должен последовательно выполнить следующие действия:

```bash
# 1. Создание новой функциональной ветки для релиза v1.4.0
git checkout -b feat/v1.4.0-payok-lavatop

# 2. Реализация кода:
#    - Создать модуль app/services/payments/payok.py
#    - Создать модуль app/services/payments/lavatop.py
#    - Добавить маршруты /webhook/payok и /webhook/lavatop во Flask-приложение
#    - Добавить кнопки и обработчики в Telegram-бота
#    - Встроить поля конфигурации в веб-интерфейс настроек
#    - Обновить config.example.json

# 3. Валидация синтаксиса Python (0 ошибок)
python3 -m compileall src app

# 4. Проверка исключения секретов через git status:
#    Ни в коем случае не коммитить боевой config.json или базы данных!
git status

# 5. Фиксация изменений и отправка ветки в удалённый репозиторий:
git add app/ src/ config.example.json
git commit -m "feat(payments): implement PayOK and Lava.top payment providers with webhook verification"
git push origin feat/v1.4.0-payok-lavatop
```