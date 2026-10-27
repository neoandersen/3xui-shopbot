-- Migration: Update all plans and tariffs currency to USD
UPDATE plans SET price_currency = 'USD' WHERE price_currency IS NULL OR price_currency = 'RUB';
UPDATE bot_settings SET value = 'USD' WHERE key = 'base_currency';
UPDATE bot_settings SET value = '50' WHERE key = 'max_keys';
UPDATE bot_settings SET value = '2.0' WHERE key = 'minimum_withdrawal' AND value = '100';
