import unittest

from auto_scanner_v5 import (
    ai_decision_is_approved,
    determine_btc_bias,
    setup_passes_filters,
)


class TestFailClosedAI(unittest.TestCase):
    def test_ai_error_or_missing_result_never_approves(self):
        self.assertFalse(ai_decision_is_approved(None, 75))
        self.assertFalse(ai_decision_is_approved({}, 75))
        self.assertFalse(ai_decision_is_approved({"confidence": 99, "verdict": "REJECT"}, 75))
        self.assertFalse(ai_decision_is_approved({"confidence": 74, "verdict": "APPROVED"}, 75))
        self.assertTrue(ai_decision_is_approved({"confidence": 75, "verdict": "APPROVED"}, 75))


class TestBTCBias(unittest.TestCase):
    def test_4h_close_is_compared_with_4h_ema200_and_1h_confirms(self):
        self.assertEqual(
            determine_btc_bias("BUY", 110.0, 100.0, "BUY", 105.0, 100.0),
            "BULLISH",
        )
        self.assertEqual(
            determine_btc_bias("SELL", 90.0, 100.0, "SELL", 95.0, 100.0),
            "BEARISH",
        )
        self.assertEqual(
            determine_btc_bias("BUY", 110.0, 100.0, "SELL", 95.0, 100.0),
            "NEUTRAL",
        )

    def test_missing_btc_data_fails_to_neutral(self):
        self.assertEqual(determine_btc_bias("BUY", None, 100.0, "BUY", 105.0, 100.0), "NEUTRAL")
        self.assertEqual(determine_btc_bias("BUY", 110.0, None, "BUY", 105.0, 100.0), "NEUTRAL")


class TestSetupFilters(unittest.TestCase):
    def _base(self, side="LONG"):
        if side == "LONG":
            return dict(
                side="LONG", symbol="SOLUSDT", r4="BUY", r1="BUY", r15="BUY",
                btc_bias="BULLISH", price=105.0, ema200=100.0,
                adx=25.0, di_plus=30.0, di_minus=15.0, funding=0.01,
                adx_min=20.0, max_long_funding=0.03, min_short_funding=-0.04,
            )
        return dict(
            side="SHORT", symbol="SOLUSDT", r4="SELL", r1="SELL", r15="SELL",
            btc_bias="BEARISH", price=95.0, ema200=100.0,
            adx=25.0, di_plus=15.0, di_minus=30.0, funding=-0.01,
            adx_min=20.0, max_long_funding=0.03, min_short_funding=-0.04,
        )

    def test_long_requires_all_three_timeframes(self):
        data = self._base("LONG")
        self.assertTrue(setup_passes_filters(**data))
        data["r15"] = "NEUTRAL"
        self.assertFalse(setup_passes_filters(**data))

    def test_long_requires_price_above_ema200(self):
        data = self._base("LONG")
        data["price"] = 99.0
        self.assertFalse(setup_passes_filters(**data))

    def test_short_requires_price_below_ema200(self):
        data = self._base("SHORT")
        self.assertTrue(setup_passes_filters(**data))
        data["price"] = 101.0
        self.assertFalse(setup_passes_filters(**data))

    def test_missing_critical_indicator_fails_closed(self):
        data = self._base("LONG")
        data["ema200"] = None
        self.assertFalse(setup_passes_filters(**data))
        data = self._base("LONG")
        data["adx"] = None
        self.assertFalse(setup_passes_filters(**data))


if __name__ == "__main__":
    unittest.main()
