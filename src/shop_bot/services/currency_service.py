"""Currency Quotation & Conversion Service for NeoVPN ShopBot.
Base currency: USD ($).
Supports fiat (USD, EUR, UAH, RUB, KZT) and crypto (USDT, USDC, BTC, ETH, XRP, XMR, LTC, TON).
Strict 24-hour caching policy (expires_at = updated_at + 86400).
"""
import time
import logging
import urllib.request
import json
from typing import Dict, Tuple, Optional

from shop_bot.data_manager import database

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 86400  # Strict 24 hours

CURRENCY_SYMBOLS: Dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "UAH": "₴",
    "RUB": "₽",
    "KZT": "₸",
    "USDT": "USDT",
    "USDC": "USDC",
    "BTC": "BTC",
    "ETH": "ETH",
    "XRP": "XRP",
    "XMR": "XMR",
    "LTC": "LTC",
    "TON": "TON",
}

DEFAULT_FALLBACK_RATES: Dict[str, float] = {
    "USD_USD": 1.0,
    "USD_EUR": 0.92,
    "USD_UAH": 41.50,
    "USD_RUB": 96.00,
    "USD_KZT": 495.00,
    "USD_USDT": 1.0,
    "USD_USDC": 1.0,
    "USD_BTC": 0.000015,
    "USD_ETH": 0.00038,
    "USD_XRP": 1.80,
    "USD_XMR": 0.0065,
    "USD_LTC": 0.014,
    "USD_TON": 0.19,
}

COINGECKO_MAP: Dict[str, str] = {
    "USDT": "tether",
    "USDC": "usd-coin",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "XRP": "ripple",
    "XMR": "monero",
    "LTC": "litecoin",
    "TON": "the-open-network",
}


def get_currency_symbol(currency: str) -> str:
    """Return currency glyph or code."""
    c = (currency or "USD").strip().upper()
    return CURRENCY_SYMBOLS.get(c, c)


def _fetch_fiat_rates_from_api() -> Dict[str, float]:
    """Fetch fiat exchange rates with USD base from open.er-api.com."""
    url = "https://open.er-api.com/v6/latest/USD"
    req = urllib.request.Request(url, headers={"User-Agent": "NeoVPN-ShopBot/1.3.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result") == "success" and "rates" in data:
                rates = data["rates"]
                return {
                    f"USD_{cur}": float(rates[cur])
                    for cur in ("EUR", "UAH", "RUB", "KZT", "USD")
                    if cur in rates
                }
    except Exception as e:
        logger.warning(f"Could not fetch fiat rates from {url}: {e}")
    return {}


def _fetch_crypto_rates_from_api() -> Dict[str, float]:
    """Fetch cryptocurrency rates against USD."""
    # Try CoinGecko simple price
    ids = ",".join(COINGECKO_MAP.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
    req = urllib.request.Request(url, headers={"User-Agent": "NeoVPN-ShopBot/1.3.0"})
    rates: Dict[str, float] = {}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for symbol, cg_id in COINGECKO_MAP.items():
                if cg_id in data and "usd" in data[cg_id]:
                    usd_price = float(data[cg_id]["usd"])
                    if usd_price > 0:
                        # rate = coins per 1 USD
                        rates[f"USD_{symbol}"] = round(1.0 / usd_price, 8)
    except Exception as e:
        logger.warning(f"Could not fetch crypto rates from CoinGecko: {e}")
    return rates


def get_rate(target_currency: str) -> float:
    """
    Get exchange rate for USD to target_currency (fiat or crypto).
    Strict 24-hour cache: external API calls are prohibited if cache is valid.
    """
    target = (target_currency or "USD").strip().upper()
    if target == "USD":
        return 1.0

    pair = f"USD_{target}"
    now = int(time.time())

    # 1. Check persistent database cache
    cached = database.get_exchange_rate_from_db(pair)
    if cached and cached.get("expires_at", 0) > now:
        return float(cached["rate"])

    # 2. 24h expired or pair missing: query external provider
    new_rate: Optional[float] = None
    if target in ("EUR", "UAH", "RUB", "KZT"):
        fiat_map = _fetch_fiat_rates_from_api()
        if pair in fiat_map:
            new_rate = fiat_map[pair]
            # Also populate all returned fiat rates
            for p, r in fiat_map.items():
                database.save_exchange_rate_to_db(p, r, now, now + CACHE_TTL_SECONDS)
    elif target in COINGECKO_MAP:
        crypto_map = _fetch_crypto_rates_from_api()
        if pair in crypto_map:
            new_rate = crypto_map[pair]
            for p, r in crypto_map.items():
                database.save_exchange_rate_to_db(p, r, now, now + CACHE_TTL_SECONDS)

    # 3. If fresh rate obtained, return it
    if new_rate and new_rate > 0:
        database.save_exchange_rate_to_db(pair, new_rate, now, now + CACHE_TTL_SECONDS)
        return new_rate

    # 4. If old cached rate exists (network down), extend TTL slightly or return it
    if cached and float(cached.get("rate", 0)) > 0:
        logger.info(f"Using stale cached rate for {pair} due to network unavailability")
        return float(cached["rate"])

    # 5. Ultimate fallback to standard default rates
    fallback = DEFAULT_FALLBACK_RATES.get(pair, 1.0)
    database.save_exchange_rate_to_db(pair, fallback, now, now + CACHE_TTL_SECONDS)
    return fallback


def convert_from_usd(amount_usd: float, target_currency: str) -> Tuple[float, float]:
    """
    Convert amount from USD base to target_currency.
    Returns (converted_amount, rate).
    """
    target = (target_currency or "USD").strip().upper()
    rate = get_rate(target)
    if target in ("BTC", "ETH", "XMR"):
        converted = round(float(amount_usd) * rate, 6)
    elif target in ("XRP", "TON", "LTC"):
        converted = round(float(amount_usd) * rate, 4)
    else:
        # Standard fiat / stablecoins: 2 decimals
        converted = round(float(amount_usd) * rate, 2)
    return converted, rate


def convert_to_usd(amount_target: float, from_currency: str) -> Tuple[float, float]:
    """
    Convert amount from target_currency back to USD base.
    Returns (amount_usd, rate).
    """
    from_curr = (from_currency or "USD").strip().upper()
    rate = get_rate(from_curr)
    if rate <= 0:
        rate = 1.0
    amount_usd = round(float(amount_target) / rate, 2)
    return amount_usd, rate


def format_price(amount_usd: float, currency: str = "USD") -> str:
    """Format USD price converted to given currency with proper symbol."""
    curr = (currency or "USD").strip().upper()
    converted, _ = convert_from_usd(amount_usd, curr)
    sym = get_currency_symbol(curr)
    if curr == "USD":
        return f"${converted:.2f}"
    elif curr == "EUR":
        return f"€{converted:.2f}"
    elif curr in ("UAH", "RUB", "KZT"):
        return f"{converted:.2f} {sym}"
    else:
        return f"{converted} {sym}"
