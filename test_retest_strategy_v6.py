import unittest

try:
    import retest_strategy_v6 as strategy
except ImportError:
    strategy = None


def candle(open_, high, low, close, volume=100.0):
    return {
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


class TestRetestStrategy(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(strategy, "retest_strategy_v6 must exist")

    @staticmethod
    def bullish_4h():
        return [
            candle(110, 115, 105, 110),
            candle(110, 120, 108, 118),
            candle(118, 119, 100, 104),
            candle(108, 125, 107, 123),
            candle(123, 124, 105, 110),
            candle(112, 130, 110, 128),
            candle(128, 129, 108, 115),
            candle(115, 119, 112, 118),
        ]

    @staticmethod
    def bullish_1h():
        return [
            candle(105, 108, 102, 106),
            candle(106, 112, 104, 110),
            candle(110, 111, 100, 103),
            candle(106, 115, 105, 113),
            candle(113, 114, 103, 106),
            candle(109, 116, 108, 114),
        ]

    @staticmethod
    def bullish_15m_confirmed():
        return [
            candle(113, 114, 112, 113, 80),
            candle(113, 114.5, 111, 112, 90),
            candle(112, 115.5, 112, 115.2, 180),
            candle(115.2, 116, 114.95, 115.5, 150),
            candle(115.5, 116.2, 115.3, 115.8, 120),
        ]

    def test_long_requires_breakout_then_confirmed_retest(self):
        result = strategy.analyze_retest_setup(
            candles_4h=self.bullish_4h(),
            candles_1h=self.bullish_1h(),
            candles_15m=self.bullish_15m_confirmed(),
            current_price=115.10,
            atr_period=3,
            swing_window=1,
        )

        self.assertEqual(result["status"], "LONG_CONFIRMED")
        self.assertEqual(result["side"], "LONG")
        self.assertEqual(result["bias_15m"], "BULLISH")
        self.assertAlmostEqual(result["breakout_level"], 115.0)
        self.assertLessEqual(result["entry_low"], 115.0)
        self.assertGreaterEqual(result["entry_high"], 115.0)
        self.assertLess(result["sl"], result["entry"])
        self.assertEqual([result["tp1"], result["tp2"], result["tp3"]], [120.0, 125.0, 130.0])
        self.assertGreaterEqual(result["score"], 90)

    def test_breakout_without_retest_stays_waiting(self):
        candles_15m = [
            candle(113, 114, 112, 113, 80),
            candle(113, 114.5, 111, 112, 90),
            candle(112, 115.5, 112, 115.2, 180),
            candle(115.4, 116.2, 115.35, 115.9, 140),
            candle(115.9, 116.4, 115.5, 116.0, 120),
        ]

        result = strategy.analyze_retest_setup(
            candles_4h=self.bullish_4h(),
            candles_1h=self.bullish_1h(),
            candles_15m=candles_15m,
            current_price=115.70,
            atr_period=3,
            swing_window=1,
        )

        self.assertEqual(result["status"], "WAIT_RETEST")
        self.assertFalse(result["retest_confirmed"])

    def test_confirmed_retest_is_not_chased_beyond_half_atr(self):
        result = strategy.analyze_retest_setup(
            candles_4h=self.bullish_4h(),
            candles_1h=self.bullish_1h(),
            candles_15m=self.bullish_15m_confirmed(),
            current_price=116.25,
            atr_period=3,
            swing_window=1,
            max_chase_atr=0.5,
        )

        self.assertEqual(result["status"], "WAIT_NO_CHASE")
        self.assertTrue(result["retest_confirmed"])

    def test_short_uses_mirrored_breakdown_and_retest_rules(self):
        candles_4h = [
            candle(120, 122, 118, 120),
            candle(120, 125, 116, 123),
            candle(118, 119, 105, 108),
            candle(108, 120, 107, 118),
            candle(113, 114, 100, 103),
            candle(103, 115, 102, 113),
            candle(113, 114, 95, 99),
            candle(99, 108, 97, 101),
        ]
        candles_1h = [
            candle(120, 122, 118, 120),
            candle(120, 125, 118, 123),
            candle(118, 119, 115, 117),
            candle(117, 120, 116, 119),
            candle(119, 119.5, 110, 112),
            candle(112, 115, 111, 114),
            candle(114, 114.5, 108, 109),
        ]
        candles_15m = [
            candle(112, 113, 111, 112, 80),
            candle(112, 114, 111.5, 113, 90),
            candle(113, 113.2, 109.5, 109.8, 180),
            candle(109.8, 110.05, 109.2, 109.5, 150),
            candle(109.5, 109.8, 109.0, 109.2, 120),
        ]

        result = strategy.analyze_retest_setup(
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_15m=candles_15m,
            current_price=109.90,
            atr_period=3,
            swing_window=1,
        )

        self.assertEqual(result["status"], "SHORT_CONFIRMED")
        self.assertEqual(result["side"], "SHORT")
        self.assertAlmostEqual(result["breakout_level"], 110.0)
        self.assertGreater(result["sl"], result["entry"])
        self.assertEqual([result["tp1"], result["tp2"], result["tp3"]], [105.0, 100.0, 95.0])

    def test_ranking_returns_five_best_even_when_none_is_confirmed(self):
        items = [
            {"symbol": "AUSDT", "score": 30, "status": "WAIT_BREAKOUT", "distance_atr": 0.4, "liquidity_rank": 1},
            {"symbol": "BUSDT", "score": 70, "status": "WAIT_RETEST", "distance_atr": 0.2, "liquidity_rank": 2},
            {"symbol": "CUSDT", "score": 55, "status": "WAIT_RETEST", "distance_atr": 0.1, "liquidity_rank": 3},
            {"symbol": "DUSDT", "score": 10, "status": "NO_SETUP", "distance_atr": 9.0, "liquidity_rank": 4},
            {"symbol": "EUSDT", "score": 45, "status": "WAIT_BREAKOUT", "distance_atr": 0.3, "liquidity_rank": 5},
            {"symbol": "FUSDT", "score": 60, "status": "WAIT_RETEST", "distance_atr": 0.5, "liquidity_rank": 6},
        ]

        ranked = strategy.rank_watchlist(items, limit=5)

        self.assertEqual([item["symbol"] for item in ranked], ["BUSDT", "FUSDT", "CUSDT", "EUSDT", "AUSDT"])
        self.assertTrue(all(item["status"] != "LONG_CONFIRMED" for item in ranked))

    def test_digest_lists_all_five_with_waiting_levels(self):
        items = [
            {
                "symbol": f"COIN{i}USDT",
                "score": 70 - i,
                "status": "WAIT_RETEST",
                "side": "LONG",
                "bias_4h": "BULLISH",
                "bias_1h": "BULLISH",
                "entry_low": 99.5,
                "entry_high": 100.5,
                "sl": 98.0,
                "tp1": 102.0,
                "tp2": 104.0,
                "tp3": 106.0,
                "rr_tp1": 1.0,
                "rr_tp2": 2.0,
                "rr_tp3": 3.0,
            }
            for i in range(1, 6)
        ]

        message = strategy.format_watchlist_digest(items, provider="OKX", scan_time="2026-09-18 12:00")

        for i in range(1, 6):
            self.assertIn(f"COIN{i}USDT", message)
        self.assertIn("WAIT_RETEST", message)
        self.assertIn("15m", message)
        self.assertIn("Entry", message)
        self.assertIn("TP1/TP2/TP3", message)

    def test_latest_breakout_replaces_an_invalidated_old_retest(self):
        candles_15m = [
            candle(113, 114, 112, 113, 80),
            candle(113, 115.6, 112, 115.3, 180),
            candle(115.3, 116, 114.95, 115.6, 150),
            candle(115.6, 115.8, 113.5, 114.0, 170),
            candle(114.0, 115.5, 113.8, 115.2, 160),
            candle(115.4, 116.2, 115.35, 115.9, 140),
            candle(115.9, 116.4, 115.5, 116.0, 120),
        ]

        result = strategy.analyze_retest_setup(
            candles_4h=self.bullish_4h(),
            candles_1h=self.bullish_1h(),
            candles_15m=candles_15m,
            current_price=115.70,
            atr_period=3,
            swing_window=1,
        )

        self.assertEqual(result["status"], "WAIT_RETEST")
        self.assertFalse(result["retest_confirmed"])

    def test_breakout_older_than_twenty_15m_candles_is_not_reused(self):
        candles_15m = self.bullish_15m_confirmed() + [
            candle(115.6, 116.2, 115.3, 115.8, 100)
            for _ in range(25)
        ]

        result = strategy.analyze_retest_setup(
            candles_4h=self.bullish_4h(),
            candles_1h=self.bullish_1h(),
            candles_15m=candles_15m,
            current_price=115.70,
            atr_period=3,
            swing_window=1,
        )

        self.assertEqual(result["status"], "WAIT_BREAKOUT")
        self.assertFalse(result["breakout_confirmed"])


if __name__ == "__main__":
    unittest.main()
