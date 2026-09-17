import sys
import types
import unittest
from unittest.mock import patch

import auto_scanner_v5 as scanner
from market_data_v5 import MarketDataSession, MarketDataSnapshot


class FakeAnalysis:
    def __init__(self, recommendation, indicators):
        self.summary = {"RECOMMENDATION": recommendation}
        self.indicators = indicators


class FakeProvider:
    name = "BYBIT"

    def get_klines(self, symbol, interval, limit):
        if interval == "1h":
            return [{"high": 101.0, "low": 99.0, "close": 100.0}] * (scanner.STRUCTURE_ATR_PERIOD + 1)
        return [{"high": 102.0, "low": 98.0, "close": 100.0}]


class TestScoringGateBeforeAstra(unittest.TestCase):
    def test_score_below_threshold_never_calls_astra_or_telegram(self):
        provider = FakeProvider()
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
        fake_tv = types.SimpleNamespace(
            Interval=types.SimpleNamespace(
                INTERVAL_4_HOURS="4h",
                INTERVAL_1_HOUR="1h",
                INTERVAL_15_MINUTES="15m",
            ),
            get_multiple_analysis=lambda **kwargs: {"BINANCE:BTCUSDT.P": analysis},
        )
        plan = {
            "entry": 100.0,
            "sl": 98.0,
            "sl_pct": 2.0,
            "structure_stop": 98.5,
            "atr": 1.0,
            "tp1": 103.0,
            "tp1_pct": 3.0,
            "rr_tp1": 1.5,
            "tp2": 105.0,
            "tp2_pct": 5.0,
            "rr_tp2": 2.5,
            "tp3": 107.0,
            "tp3_pct": 7.0,
            "rr_tp3": 3.5,
            "invalidation": "invalid",
        }
        low_score = {
            "valid": True,
            "score": scanner.SETUP_MIN_SCORE - 1,
            "reason": "OK",
            "components": {"trend": 14, "strength": 8, "structure": 20, "market": 12, "funding": 9, "timing": 8},
        }

        with patch.object(scanner, "create_market_data_session", return_value=session), \
             patch.object(scanner, "build_structure_trade_plan", return_value=plan), \
             patch.object(scanner, "score_setup", return_value=low_score) as scoring, \
             patch.object(scanner, "analyze_with_ai", side_effect=AssertionError("Astra must not be called")), \
             patch.object(scanner, "send_telegram_alert", side_effect=AssertionError("Telegram must not be called")), \
             patch.dict(sys.modules, {"tradingview_ta": fake_tv}):
            result = scanner.run_scan_cycle(limit=1, recent_signals={})

        self.assertEqual(result, {})
        scoring.assert_called_once()

    def test_astra_wait_never_sends_telegram(self):
        provider = FakeProvider()
        snapshot = MarketDataSnapshot(
            provider="BYBIT",
            symbols=["BTCUSDT"],
            funding_pct={"BTCUSDT": 0.01},
            last_price={"BTCUSDT": 100.0},
        )
        session = MarketDataSession(provider=provider, snapshot=snapshot)
        indicators = {
            "close": 100.0, "EMA200": 90.0, "RSI": 50.0, "ADX": 35.0,
            "ADX+DI": 45.0, "ADX-DI": 10.0, "change": 1.0,
        }
        analysis = FakeAnalysis("STRONG_BUY", indicators)
        fake_tv = types.SimpleNamespace(
            Interval=types.SimpleNamespace(
                INTERVAL_4_HOURS="4h", INTERVAL_1_HOUR="1h", INTERVAL_15_MINUTES="15m",
            ),
            get_multiple_analysis=lambda **kwargs: {"BINANCE:BTCUSDT.P": analysis},
        )
        plan = {
            "entry": 100.0, "sl": 98.0, "sl_pct": 2.0, "structure_stop": 98.5, "atr": 1.0,
            "tp1": 104.0, "tp1_pct": 4.0, "rr_tp1": 2.0,
            "tp2": 106.0, "tp2_pct": 6.0, "rr_tp2": 3.0,
            "tp3": 108.0, "tp3_pct": 8.0, "rr_tp3": 4.0, "invalidation": "invalid",
        }
        high_score = {
            "valid": True, "score": 88, "reason": "OK",
            "components": {"trend": 20, "strength": 14, "structure": 25, "market": 15, "funding": 7, "timing": 7},
        }
        wait = {"confidence": 90, "verdict": "WAIT", "reason": "รอแท่งยืนยัน", "risk": "โมเมนตัมชะลอ"}

        with patch.object(scanner, "create_market_data_session", return_value=session), \
             patch.object(scanner, "build_structure_trade_plan", return_value=plan), \
             patch.object(scanner, "score_setup", return_value=high_score), \
             patch.object(scanner, "analyze_with_ai", return_value=wait) as astra, \
             patch.object(scanner, "send_telegram_alert", side_effect=AssertionError("WAIT must not alert")), \
             patch.dict(sys.modules, {"tradingview_ta": fake_tv}):
            result = scanner.run_scan_cycle(limit=1, recent_signals={})

        self.assertEqual(result, {})
        astra.assert_called_once()


if __name__ == "__main__":
    unittest.main()
