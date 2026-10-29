#!/usr/bin/env python3
"""
Автономный скрипт проверки контрольной подписи AnyPay на сервере AnyPay (https://anypay.io/merchant).
Тестирует различные варианты передачи параметров и алгоритмов хеширования (MD5, SHA256).
"""
import os
import sys
import hashlib
import urllib.request
import urllib.parse
from typing import Dict, Any

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

try:
    from shop_bot.data_manager.database import get_setting
except Exception as e:
    print(f"Warning: Could not import database.get_setting ({e}), using env vars")
    def get_setting(k, default=""):
        return os.getenv(k.upper(), default)


def mask_str(s: str) -> str:
    s = str(s or "").strip()
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}...{s[-4:]}"


def test_variant(name: str, params: Dict[str, Any], sign_formula: str, raw_string: str):
    print("=" * 80)
    print(f"ТЕСТ: {name}")
    print(f"  Формула подписи: {sign_formula}")
    print(f"  Строка подписи:  {raw_string}")
    print(f"  Sign (хеш):      {params.get('sign')}")

    url = f"https://anypay.io/merchant?{urllib.parse.urlencode(params)}"
    print(f"  URL: {url[:120]}...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [Исключение сети]: {e}")
        return False

    print(f"  HTTP Статус: {status}")

    # Анализ текста ответа
    lower_body = body.lower()
    has_bad_sign = any(err in lower_body for err in [
        "неверная контрольная подпись",
        "неверная подпись",
        "invalid sign",
        "некорректная подпись"
    ])
    has_success_signs = any(ok in lower_body for ok in [
        "оплата заказа",
        "выберите способ оплаты",
        "способ оплаты",
        "банковские карты",
        "сбп",
        "anypay",
        "merchant-pay"
    ])

    if has_bad_sign:
        print("  -> РЕЗУЛЬТАТ: ❌ НЕВЕРНАЯ ПОДПИСЬ (Error: Invalid Sign)")
    elif has_success_signs and not ("ошибка" in lower_body and not "оплата" in lower_body):
        print("  -> РЕЗУЛЬТАТ: ✅ УСПЕХ! Форма оплаты AnyPay открылась корректно!")
    else:
        # Извлекаем краткий сниппет текста
        import re
        clean_text = re.sub(r'<[^>]+>', ' ', body)
        clean_text = ' '.join(clean_text.split())[:300]
        print(f"  -> РЕЗУЛЬТАТ: ⚠️ НЕОПРЕДЕЛЕННО (Сниппет: {clean_text})")

    return not has_bad_sign and has_success_signs


def main():
    project_id = str(get_setting("anypay_project_id") or os.getenv("ANYPAY_PROJECT_ID", "")).strip()
    secret_key = str(get_setting("anypay_secret_key") or os.getenv("ANYPAY_SECRET_KEY", "")).strip()
    currency = str(get_setting("anypay_currency", "RUB") or "RUB").strip().upper()

    print("\n" + "#" * 80)
    print("ДИАГНОСТИКА КОНТРОЛЬНОЙ ПОДПИСИ ANYPAY")
    print(f"Project ID: {project_id or 'НЕ НАСТРОЕН'}")
    print(f"Secret Key: {mask_str(secret_key) if secret_key else 'НЕ НАСТРОЕН'}")
    print(f"Currency:   {currency}")
    print("#" * 80 + "\n")

    if not project_id or not secret_key:
        print("ОШИБКА: Project ID или Secret Key не настроены в БД или переменных окружения.")
        sys.exit(1)

    currency = "RUB"
    desc = "Test Payment"
    secret_masked = mask_str(secret_key)

    test_cases = []

    # 1. Вариант А1: merchant_id, pay_id='1001', amount='100.00', SHA256 (как сейчас в коде: currency:amount:secret:merchant_id:pay_id)
    raw = f"{currency}:100.00:{secret_key}:{project_id}:1001"
    raw_m = f"{currency}:100.00:{secret_masked}:{project_id}:1001"
    sign = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант А1: merchant_id, SHA-256 (currency:amount:secret:merchant_id:pay_id), сумма 100.00",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "sha256(currency:amount:secret:merchant_id:pay_id)",
        "raw": raw_m
    })

    # 2. Вариант А2: merchant_id, pay_id='1001', amount='100.00', MD5 (по официальной документации для короткой формы!)
    raw = f"{currency}:100.00:{secret_key}:{project_id}:1001"
    raw_m = f"{currency}:100.00:{secret_masked}:{project_id}:1001"
    sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант А2: merchant_id, MD5 (currency:amount:secret:merchant_id:pay_id), сумма 100.00",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "md5(currency:amount:secret:merchant_id:pay_id)",
        "raw": raw_m
    })

    # 3. Вариант А3: merchant_id, pay_id='1001', amount='100.00', SHA256 (по официальной документации AnyPay SCI!)
    # Склеиваются: merchant_id, pay_id, amount, currency, desc, success_url, fail_url, secret_key
    raw = f"{project_id}:1001:100.00:{currency}:{desc}:::{secret_key}"
    raw_m = f"{project_id}:1001:100.00:{currency}:{desc}:::{secret_masked}"
    sign = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант А3: merchant_id, SHA-256 по ДОКУМЕНТАЦИИ (merchant_id:pay_id:amount:currency:desc:success_url:fail_url:secret_key)",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "success_url": "",
            "fail_url": "",
            "sign": sign
        },
        "formula": "sha256(merchant_id:pay_id:amount:currency:desc:success_url:fail_url:secret_key)",
        "raw": raw_m
    })

    # 4. Вариант А4: merchant_id, pay_id='1001', amount='100.00', SHA256 по документации без success/fail в параметрах
    raw = f"{project_id}:1001:100.00:{currency}:{desc}:::{secret_key}"
    raw_m = f"{project_id}:1001:100.00:{currency}:{desc}:::{secret_masked}"
    sign = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант А4: merchant_id, SHA-256 по ДОКУМЕНТАЦИИ (только обязательные параметры в URL)",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "sha256(merchant_id:pay_id:amount:currency:desc:::secret_key)",
        "raw": raw_m
    })

    # 5. Вариант Б1: project_id вместо merchant_id в GET-параметрах (MD5)
    raw = f"{currency}:100.00:{secret_key}:{project_id}:1001"
    raw_m = f"{currency}:100.00:{secret_masked}:{project_id}:1001"
    sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант Б1: project_id в GET, MD5 (currency:amount:secret:project_id:pay_id)",
        "params": {
            "project_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "md5(currency:amount:secret:project_id:pay_id)",
        "raw": raw_m
    })

    # 6. Вариант Б2: project_id вместо merchant_id в GET-параметрах (SHA-256 текущий)
    raw = f"{currency}:100.00:{secret_key}:{project_id}:1001"
    raw_m = f"{currency}:100.00:{secret_masked}:{project_id}:1001"
    sign = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант Б2: project_id в GET, SHA-256 текущий",
        "params": {
            "project_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "sha256(currency:amount:secret:project_id:pay_id)",
        "raw": raw_m
    })

    # 7. Вариант В1: целая сумма (100) вместо дробной (100.00) с MD5
    raw = f"{currency}:100:{secret_key}:{project_id}:1001"
    raw_m = f"{currency}:100:{secret_masked}:{project_id}:1001"
    sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант В1: Целая сумма (100), MD5",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "md5(currency:100:secret:merchant_id:pay_id)",
        "raw": raw_m
    })

    # 8. Вариант В2: целая сумма (100) с SHA256 по документации
    raw = f"{project_id}:1001:100:{currency}:{desc}:::{secret_key}"
    raw_m = f"{project_id}:1001:100:{currency}:{desc}:::{secret_masked}"
    sign = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант В2: Целая сумма (100), SHA-256 по документации",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100",
            "currency": currency,
            "desc": desc,
            "success_url": "",
            "fail_url": "",
            "sign": sign
        },
        "formula": "sha256(project_id:pay_id:100:currency:desc:success:fail:secret)",
        "raw": raw_m
    })

    # 9. Вариант Г: строковый pay_id (UUID 'test-uuid-123') vs числовой ('1001') с MD5
    raw = f"{currency}:100.00:{secret_key}:{project_id}:test-uuid-123"
    raw_m = f"{currency}:100.00:{secret_masked}:{project_id}:test-uuid-123"
    sign = hashlib.md5(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант Г: Строковый pay_id ('test-uuid-123') вместо числа, MD5",
        "params": {
            "merchant_id": project_id,
            "pay_id": "test-uuid-123",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "md5(currency:100.00:secret:merchant_id:pay_id)",
        "raw": raw_m
    })

    # 10. Вариант Д: короткий SHA-256 (project_id:pay_id:amount:currency:secret_key)
    raw = f"{project_id}:1001:100.00:{currency}:{secret_key}"
    raw_m = f"{project_id}:1001:100.00:{currency}:{secret_masked}"
    sign = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    test_cases.append({
        "name": "Вариант Д: Короткий SHA-256 (project_id:pay_id:amount:currency:secret_key)",
        "params": {
            "merchant_id": project_id,
            "pay_id": "1001",
            "amount": "100.00",
            "currency": currency,
            "desc": desc,
            "sign": sign
        },
        "formula": "sha256(project_id:pay_id:amount:currency:secret_key)",
        "raw": raw_m
    })

    success_variants = []
    for tc in test_cases:
        ok = test_variant(tc["name"], tc["params"], tc["formula"], tc["raw"])
        if ok:
            success_variants.append(tc["name"])

    print("\n" + "=" * 80)
    print("ИТОГИ ТЕСТИРОВАНИЯ:")
    if success_variants:
        print(f"🎉 НАЙДЕНО РАБОЧИХ ВАРИАНТОВ: {len(success_variants)}")
        for sv in success_variants:
            print(f"  👉 {sv}")
    else:
        print("❌ Ни один из вариантов не дал успеха. Проверьте правильность Secret Key в личном кабинете AnyPay!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
