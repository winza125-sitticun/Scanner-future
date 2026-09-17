import unittest

from auto_scanner_v5 import (
    calculate_atr,
    find_confirmed_swings,
    build_structure_trade_plan,
    parse_futures_klines,
    extract_classic_pivot_targets,
)


def candle(high, low, close, open_=None):
    if open_ is None:
        open_ = close
    return {"open": float(open_), "high": float(high), "low": float(low), "close": float(close)}


class TestATR(unittest.TestCase):
    def test_calculate_atr_uses_true_range_and_requires_enough_history(self):
        candles = [
            candle(10, 8, 9),
            candle(11, 9, 10),
            candle(14, 10, 13),
            candle(15, 12, 14),
        ]
        # TRs after first candle: 2, 4, 3. Last 3 average = 3.0
        self.assertAlmostEqual(calculate_atr(candles, period=3), 3.0)
        self.assertIsNone(calculate_atr(candles[:3], period=3))


class TestSwingDetection(unittest.TestCase):
    def test_detects_only_confirmed_local_highs_and_lows(self):
        candles = [
            candle(10, 8, 9),
            candle(11, 7, 10),
            candle(15, 9, 14),   # swing high
            candle(12, 8, 10),
            candle(11, 5, 7),    # swing low
            candle(13, 8, 12),
            candle(12, 9, 10),
        ]
        swings = find_confirmed_swings(candles, window=1)
        self.assertEqual([x["index"] for x in swings["highs"]], [2, 5])
        self.assertEqual([x["index"] for x in swings["lows"]], [1, 4])
        self.assertEqual(swings["highs"][0]["price"], 15.0)
        self.assertEqual(swings["lows"][-1]["price"], 5.0)


class TestStructurePlan(unittest.TestCase):
    def _long_1h(self):
        # Enough candles for ATR(3), with most recent confirmed swing low at 96
        return [
            candle(100, 98, 99),
            candle(101, 97, 100),
            candle(102, 99, 101),
            candle(101, 98, 99),
            candle(100, 96, 98),    # latest confirmed swing low below entry
            candle(104, 99, 103),
            candle(112, 101, 108),  # swing high target
            candle(109, 102, 105),
            candle(120, 103, 110),  # later swing high target
            candle(111, 104, 106),
        ]

    def _long_4h(self):
        return [
            candle(105, 94, 100),
            candle(108, 96, 104),
            candle(112, 99, 110),  # HTF target
            candle(109, 101, 105),
            candle(111, 102, 108),
        ]

    def test_long_stop_uses_recent_swing_low_plus_atr_buffer_and_targets_are_structure_levels(self):
        plan = build_structure_trade_plan(
            side="LONG",
            entry_price=102.0,
            candles_1h=self._long_1h(),
            candles_4h=self._long_4h(),
            pivot_levels=[115.0, 125.0],
            min_rr=1.0,
            atr_period=3,
            atr_buffer_mult=0.25,
            swing_window=1,
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["structure_stop"], 96.0)
        self.assertLess(plan["sl"], 96.0)
        self.assertGreater(plan["tp1"], 102.0)
        self.assertGreaterEqual(plan["rr_tp1"], 1.0)
        self.assertEqual(plan["target_source"], "STRUCTURE")

    def test_short_plan_is_mirrored(self):
        candles_1h = [
            candle(101, 98, 100),
            candle(104, 99, 103),
            candle(108, 101, 106),  # swing high
            candle(105, 100, 102),
            candle(104, 99, 103),
            candle(103, 97, 99),
            candle(101, 94, 96),    # swing low target
            candle(100, 95, 98),
            candle(99, 92, 94),     # later swing low target
            candle(100, 93, 95),
        ]
        candles_4h = [
            candle(110, 96, 104),
            candle(112, 94, 100),
            candle(108, 90, 96),
            candle(106, 92, 98),
            candle(104, 88, 92),
        ]
        plan = build_structure_trade_plan(
            side="SHORT",
            entry_price=100.0,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            pivot_levels=[90.0, 85.0],
            min_rr=0.5,
            atr_period=3,
            atr_buffer_mult=0.25,
            swing_window=1,
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["structure_stop"], 108.0)
        self.assertGreater(plan["sl"], 108.0)
        self.assertLess(plan["tp1"], 100.0)
        self.assertGreaterEqual(plan["rr_tp1"], 0.5)

    def test_rejects_when_nearest_structure_target_has_insufficient_room(self):
        plan = build_structure_trade_plan(
            side="LONG",
            entry_price=102.0,
            candles_1h=self._long_1h(),
            candles_4h=self._long_4h(),
            pivot_levels=[103.0, 115.0],
            min_rr=2.0,
            atr_period=3,
            atr_buffer_mult=0.25,
            swing_window=1,
        )
        self.assertIsNone(plan)

    def test_rejects_missing_structure_or_atr(self):
        flat = [candle(101, 99, 100) for _ in range(5)]
        self.assertIsNone(
            build_structure_trade_plan(
                side="LONG",
                entry_price=100.0,
                candles_1h=flat,
                candles_4h=[],
                pivot_levels=[],
                min_rr=1.5,
                atr_period=14,
                atr_buffer_mult=0.25,
                swing_window=1,
            )
        )


class TestKlineParsing(unittest.TestCase):
    def test_parses_only_closed_futures_klines(self):
        raw = [
            [1000, "100", "105", "95", "102", "10", 1999, "0", 5, "0", "0", "0"],
            [2000, "102", "108", "101", "107", "11", 2999, "0", 6, "0", "0", "0"],
            [3000, "107", "109", "106", "108", "12", 4999, "0", 7, "0", "0", "0"],
        ]
        candles = parse_futures_klines(raw, now_ms=4000)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["open"], 100.0)
        self.assertEqual(candles[1]["close"], 107.0)

    def test_malformed_kline_payload_fails_closed(self):
        self.assertEqual(parse_futures_klines({"code": -1}, now_ms=4000), [])
        self.assertEqual(parse_futures_klines([[1, "x"]], now_ms=4000), [])


class TestPivotTargets(unittest.TestCase):
    def test_extracts_directional_classic_pivots(self):
        indicators = {
            "Pivot.M.Classic.R1": 110.0,
            "Pivot.M.Classic.R2": 120.0,
            "Pivot.M.Classic.R3": 130.0,
            "Pivot.M.Classic.S1": 90.0,
            "Pivot.M.Classic.S2": 80.0,
            "Pivot.M.Classic.S3": 70.0,
        }
        self.assertEqual(extract_classic_pivot_targets(indicators, "LONG"), [110.0, 120.0, 130.0])
        self.assertEqual(extract_classic_pivot_targets(indicators, "SHORT"), [90.0, 80.0, 70.0])


if __name__ == "__main__":
    unittest.main()
