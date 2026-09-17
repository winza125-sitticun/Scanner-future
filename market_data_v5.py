from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Dict, Iterable, Optional, Protocol

import requests


DEFAULT_BLACKLIST_PREFIXES = ("USDC", "FDUSD", "BUSD", "TUSD", "EUR", "DAI")


@dataclass(frozen=True)
class MarketDataSnapshot:
    provider: str
    symbols: list[str]
    funding_pct: Dict[str, float]
    last_price: Dict[str, float]


@dataclass(frozen=True)
class MarketDataSession:
    provider: Any
    snapshot: MarketDataSnapshot


class MarketDataProvider(Protocol):
    name: str

    def get_snapshot(self, limit: int) -> Optional[MarketDataSnapshot]: ...

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Dict[str, Any]]: ...


def _valid_candle(candle: Dict[str, Any]) -> bool:
    try:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
    except (KeyError, TypeError, ValueError):
        return False
    return high >= low and low <= close <= high


def _eligible_usdt_symbol(symbol: Any) -> bool:
    if not isinstance(symbol, str) or not symbol.endswith("USDT"):
        return False
    return not any(symbol.startswith(prefix) for prefix in DEFAULT_BLACKLIST_PREFIXES)


def parse_bybit_tickers(payload: Any, limit: int) -> Optional[MarketDataSnapshot]:
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        return None
    result = payload.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows:
        return None

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not _eligible_usdt_symbol(symbol):
            continue
        try:
            turnover = float(row.get("turnover24h"))
            funding_pct = float(row.get("fundingRate")) * 100.0
            last_price = float(row.get("lastPrice"))
        except (TypeError, ValueError):
            continue
        if turnover < 0 or last_price <= 0:
            continue
        normalized.append((symbol, turnover, funding_pct, last_price))

    if not normalized:
        return None

    normalized.sort(key=lambda item: item[1], reverse=True)
    selected = normalized[: max(1, int(limit))]
    symbols = [item[0] for item in selected]
    funding = {item[0]: item[2] for item in selected}
    prices = {item[0]: item[3] for item in selected}
    return MarketDataSnapshot(provider="BYBIT", symbols=symbols, funding_pct=funding, last_price=prices)


def _interval_ms(interval: str) -> Optional[int]:
    mapping = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
    }
    return mapping.get(interval)


def _bybit_interval(interval: str) -> Optional[str]:
    mapping = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "120",
        "4h": "240",
        "6h": "360",
        "12h": "720",
        "1d": "D",
    }
    return mapping.get(interval)


def parse_bybit_klines(payload: Any, interval: str, now_ms: Optional[int] = None) -> list[Dict[str, Any]]:
    interval_ms = _interval_ms(interval)
    if interval_ms is None:
        return []
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        return []
    result = payload.get("result")
    rows = result.get("list") if isinstance(result, dict) else None
    if not isinstance(rows, list) or not rows:
        return []
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    candles = []
    try:
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                return []
            open_time = int(row[0])
            close_time = open_time + interval_ms
            if close_time > now_ms:
                continue
            candle = {
                "open_time": open_time,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": close_time,
            }
            if not _valid_candle(candle):
                return []
            candles.append(candle)
    except (TypeError, ValueError, IndexError):
        return []

    candles.sort(key=lambda candle: candle["open_time"])
    return candles


def parse_binance_tickers_and_funding(tickers: Any, premium_index: Any, limit: int) -> Optional[MarketDataSnapshot]:
    if not isinstance(tickers, list) or not isinstance(premium_index, list):
        return None

    funding_map: Dict[str, float] = {}
    for row in premium_index:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not _eligible_usdt_symbol(symbol):
            continue
        try:
            funding_map[symbol] = float(row.get("lastFundingRate")) * 100.0
        except (TypeError, ValueError):
            continue

    normalized = []
    for row in tickers:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not _eligible_usdt_symbol(symbol) or symbol not in funding_map:
            continue
        try:
            quote_volume = float(row.get("quoteVolume"))
            last_price = float(row.get("lastPrice"))
        except (TypeError, ValueError):
            continue
        if quote_volume < 0 or last_price <= 0:
            continue
        normalized.append((symbol, quote_volume, last_price))

    if not normalized:
        return None
    normalized.sort(key=lambda item: item[1], reverse=True)
    selected = normalized[: max(1, int(limit))]
    symbols = [item[0] for item in selected]
    return MarketDataSnapshot(
        provider="BINANCE",
        symbols=symbols,
        funding_pct={symbol: funding_map[symbol] for symbol in symbols},
        last_price={item[0]: item[2] for item in selected},
    )


def parse_binance_klines(raw: Any, now_ms: Optional[int] = None) -> list[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    candles = []
    try:
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                return []
            close_time = int(row[6])
            if close_time >= now_ms:
                continue
            candle = {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": close_time,
            }
            if not _valid_candle(candle):
                return []
            candles.append(candle)
    except (TypeError, ValueError, IndexError):
        return []
    candles.sort(key=lambda candle: candle["open_time"])
    return candles


def price_basis_within_tolerance(
    tradingview_price: Optional[float],
    provider_price: Optional[float],
    max_diff_pct: float,
) -> bool:
    try:
        tv = float(tradingview_price)
        provider = float(provider_price)
        tolerance = float(max_diff_pct)
    except (TypeError, ValueError):
        return False
    if tv <= 0 or provider <= 0 or tolerance < 0:
        return False
    diff_pct = abs(provider - tv) / tv * 100.0
    return diff_pct <= tolerance


def select_market_data_session(
    providers: Iterable[MarketDataProvider],
    limit: int,
    probe_kline_limit: int = 20,
    min_probe_candles: int = 1,
) -> Optional[MarketDataSession]:
    for provider in providers:
        try:
            snapshot = provider.get_snapshot(limit)
            if snapshot is None or not snapshot.symbols or "BTCUSDT" not in snapshot.symbols:
                continue
            probe = provider.get_klines("BTCUSDT", "1h", probe_kline_limit)
            if len(probe) < max(1, int(min_probe_candles)):
                continue
            return MarketDataSession(provider=provider, snapshot=snapshot)
        except Exception:
            continue
    return None


class BinanceMarketDataProvider:
    name = "BINANCE"

    def __init__(self, timeout: float = 8.0, base_url: str = "https://fapi.binance.com"):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_snapshot(self, limit: int) -> Optional[MarketDataSnapshot]:
        try:
            tickers = self._get_json("/fapi/v1/ticker/24hr")
            premium = self._get_json("/fapi/v1/premiumIndex")
            return parse_binance_tickers_and_funding(tickers, premium, limit)
        except (requests.RequestException, ValueError, TypeError):
            return None

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Dict[str, Any]]:
        try:
            raw = self._get_json(
                "/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            return parse_binance_klines(raw)
        except (requests.RequestException, ValueError, TypeError):
            return []


class BybitMarketDataProvider:
    name = "BYBIT"

    def __init__(self, timeout: float = 8.0, base_url: str = "https://api.bybit.com"):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")

    def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_snapshot(self, limit: int) -> Optional[MarketDataSnapshot]:
        try:
            payload = self._get_json("/v5/market/tickers", params={"category": "linear"})
            return parse_bybit_tickers(payload, limit)
        except (requests.RequestException, ValueError, TypeError):
            return None

    def get_klines(self, symbol: str, interval: str, limit: int) -> list[Dict[str, Any]]:
        api_interval = _bybit_interval(interval)
        if api_interval is None:
            return []
        try:
            payload = self._get_json(
                "/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": symbol,
                    "interval": api_interval,
                    "limit": limit,
                },
            )
            return parse_bybit_klines(payload, interval=interval)
        except (requests.RequestException, ValueError, TypeError):
            return []
