import unittest

from auto_scanner_v5 import create_market_data_session, market_price_basis_ok
from market_data_v5 import MarketDataSnapshot


class FakeProvider:
    def __init__(self, name, snapshot, candles):
        self.name = name
        self.snapshot = snapshot
        self.candles = candles

    def get_snapshot(self, limit):
        return self.snapshot

    def get_klines(self, symbol, interval, limit):
        return list(self.candles)


class TestScannerMarketDataIntegration(unittest.TestCase):
    def _snapshot(self, provider):
        return MarketDataSnapshot(
            provider=provider,
            symbols=["BTCUSDT", "SOLUSDT"],
            funding_pct={"BTCUSDT": 0.01, "SOLUSDT": 0.005},
            last_price={"BTCUSDT": 60000.0, "SOLUSDT": 150.0},
        )

    def test_create_session_uses_fallback_provider_without_mixing(self):
        primary = FakeProvider("BINANCE", self._snapshot("BINANCE"), [])
        fallback = FakeProvider("BYBIT", self._snapshot("BYBIT"), [{"close": 1.0}] * 15)
        session = create_market_data_session(limit=2, providers=[primary, fallback], probe_kline_limit=20)
        self.assertIsNotNone(session)
        self.assertEqual(session.provider.name, "BYBIT")
        self.assertEqual(session.snapshot.provider, "BYBIT")

    def test_price_basis_uses_selected_session_price_and_fails_closed(self):
        provider = FakeProvider("BYBIT", self._snapshot("BYBIT"), [{"close": 1.0}] * 15)
        session = create_market_data_session(limit=2, providers=[provider], probe_kline_limit=20)
        self.assertTrue(market_price_basis_ok(session, "SOLUSDT", 150.5, max_diff_pct=1.0))
        self.assertFalse(market_price_basis_ok(session, "SOLUSDT", 154.0, max_diff_pct=1.0))
        self.assertFalse(market_price_basis_ok(session, "UNKNOWNUSDT", 1.0, max_diff_pct=1.0))


if __name__ == "__main__":
    unittest.main()

class FakeAnalysis:
    def __init__(self, recommendation, indicators):
        self.summary = {"RECOMMENDATION": recommendation}
        self.indicators = indicators


class RecordingProvider:
    name = "BYBIT"

    def __init__(self):
        self.calls = []

    def get_klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        return []


class TestRunCycleUsesSelectedProvider(unittest.TestCase):
    def test_run_cycle_does_not_call_legacy_direct_binance_fetchers(self):
        import sys
        import types
        from unittest.mock import patch
        import auto_scanner_v5 as scanner
        from market_data_v5 import MarketDataSession

        provider = RecordingProvider()
        snapshot = MarketDataSnapshot(
            provider="BYBIT",
            symbols=["BTCUSDT"],
            funding_pct={"BTCUSDT": 0.01},
            last_price={"BTCUSDT": 100.0},
        )
        session = MarketDataSession(provider=provider, snapshot=snapshot)

        indicators = {
            "close": 100.0,
            "EMA200": 90.0,
            "RSI": 50.0,
            "ADX": 30.0,
            "ADX+DI": 40.0,
            "ADX-DI": 10.0,
            "change": 1.0,
        }
        analysis = FakeAnalysis("BUY", indicators)

        fake_tv = types.SimpleNamespace()
        fake_tv.Interval = types.SimpleNamespace(
            INTERVAL_4_HOURS="4h",
            INTERVAL_1_HOUR="1h",
            INTERVAL_15_MINUTES="15m",
        )
        fake_tv.get_multiple_analysis = lambda **kwargs: {"BINANCE:BTCUSDT.P": analysis}

        with patch.object(scanner, "create_market_data_session", return_value=session), \
             patch.object(scanner, "get_target_futures_symbols", side_effect=AssertionError("legacy universe fetch used")), \
             patch.object(scanner, "get_funding_rates", side_effect=AssertionError("legacy funding fetch used")), \
             patch.dict(sys.modules, {"tradingview_ta": fake_tv}):
            result = scanner.run_scan_cycle(limit=1, recent_signals={})

        self.assertEqual(result, {})
        self.assertEqual(
            provider.calls,
            [
                ("BTCUSDT", "4h", scanner.STRUCTURE_KLINE_LIMIT),
                ("BTCUSDT", "1h", scanner.STRUCTURE_KLINE_LIMIT),
                ("BTCUSDT", "15m", scanner.STRUCTURE_KLINE_LIMIT),
            ],
        )
