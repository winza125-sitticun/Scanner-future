import sys
import types
import unittest
from unittest.mock import patch

import auto_scanner_v5 as scanner
from market_data_v5 import MarketDataSession, MarketDataSnapshot


def candle(open_, high, low, close, volume=100.0):
    return {
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


class FakeAnalysis:
    def __init__(self, recommendation="NEUTRAL"):
        self.summary = {"RECOMMENDATION": recommendation}
        self.indicators = {
            "close": 115.1,
            "EMA200": 115.0,
            "RSI": 50.0,
            "ADX": 18.0,
            "ADX+DI": 20.0,
            "ADX-DI": 20.0,
            "change": 0.1,
        }


class FakeProvider:
    name = "OKX"

    def get_klines(self, symbol, interval, limit):
        if interval == "4h":
            return [
                candle(110, 115, 105, 110), candle(110, 120, 108, 118),
                candle(118, 119, 100, 104), candle(108, 125, 107, 123),
                candle(123, 124, 105, 110), candle(112, 130, 110, 128),
                candle(128, 129, 108, 115), candle(115, 119, 112, 118),
            ]
        if interval == "1h":
            return [
                candle(105, 108, 102, 106), candle(106, 112, 104, 110),
                candle(110, 111, 100, 103), candle(106, 115, 105, 113),
                candle(113, 114, 103, 106), candle(109, 116, 108, 114),
            ]
        if interval == "15m":
            return [
                candle(113, 114, 112, 113, 80), candle(113, 114.5, 111, 112, 90),
                candle(112, 115.5, 112, 115.2, 180),
                candle(115.2, 116, 114.95, 115.5, 150),
                candle(115.5, 116.2, 115.3, 115.8, 120),
            ]
        return []


class TestScannerAlwaysSendsTopFive(unittest.TestCase):
    def test_one_symbol_data_error_does_not_cancel_the_five_coin_digest(self):
        class PartialFailureProvider(FakeProvider):
            def get_klines(self, symbol, interval, limit):
                if symbol == "BTCUSDT" and interval == "15m":
                    raise RuntimeError("temporary provider failure")
                return super().get_klines(symbol, interval, limit)

        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]
        snapshot = MarketDataSnapshot(
            provider="OKX",
            symbols=symbols,
            funding_pct={symbol: 0.01 for symbol in symbols},
            last_price={symbol: 115.1 for symbol in symbols},
        )
        session = MarketDataSession(provider=PartialFailureProvider(), snapshot=snapshot)

        watchlist = scanner.build_retest_watchlist(
            session,
            symbols,
            candidate_limit=5,
            watchlist_size=5,
        )

        self.assertEqual(len(watchlist), 5)
        failed = next(item for item in watchlist if item["symbol"] == "BTCUSDT")
        self.assertEqual(failed["status"], "DATA_UNAVAILABLE")

    def test_wait_only_cycle_still_sends_all_five_ranked_coins(self):
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]
        snapshot = MarketDataSnapshot(
            provider="OKX",
            symbols=symbols,
            funding_pct={symbol: 0.01 for symbol in symbols},
            last_price={symbol: 115.1 for symbol in symbols},
        )
        session = MarketDataSession(provider=FakeProvider(), snapshot=snapshot)
        analyses = {f"BINANCE:{symbol}.P": FakeAnalysis() for symbol in symbols}
        fake_tv = types.SimpleNamespace(
            Interval=types.SimpleNamespace(
                INTERVAL_4_HOURS="4h",
                INTERVAL_1_HOUR="1h",
                INTERVAL_15_MINUTES="15m",
            ),
            get_multiple_analysis=lambda **kwargs: analyses,
        )

        with patch.object(scanner, "create_market_data_session", return_value=session), \
             patch.object(scanner, "send_telegram_alert", return_value=True) as telegram, \
             patch.dict(sys.modules, {"tradingview_ta": fake_tv}):
            result = scanner.run_scan_cycle(limit=5, recent_signals={"BTCUSDT_LONG": 9999999999.0})

        self.assertEqual(result, {"BTCUSDT_LONG": 9999999999.0})
        telegram.assert_called_once()
        message = telegram.call_args.args[0]
        self.assertIn("TOP 5 RETEST WATCHLIST", message)
        for symbol in symbols:
            self.assertIn(symbol, message)


if __name__ == "__main__":
    unittest.main()
