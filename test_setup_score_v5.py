import unittest

from setup_score_v5 import score_setup, score_passes_threshold


class TestSetupScoreV5(unittest.TestCase):
    def _high_quality(self):
        return dict(
            side="LONG",
            r4="STRONG_BUY", r1="STRONG_BUY", r15="STRONG_BUY",
            adx=42.0, di_plus=45.0, di_minus=10.0,
            rr_tp1=3.2, rr_tp2=4.0, rr_tp3=5.0,
            btc_bias="BULLISH",
            tradingview_price=100.0, provider_price=100.05,
            funding=-0.0100,
            rsi=48.0,
            max_basis_diff_pct=1.0,
            max_long_funding=0.03,
            min_short_funding=-0.04,
        )

    def test_high_quality_setup_can_score_100(self):
        result = score_setup(**self._high_quality())
        self.assertTrue(result["valid"])
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["components"], {
            "trend": 20,
            "strength": 15,
            "structure": 30,
            "market": 15,
            "funding": 10,
            "timing": 10,
        })

    def test_high_quality_short_is_scored_symmetrically(self):
        data = self._high_quality()
        data.update(
            side="SHORT",
            r4="STRONG_SELL", r1="STRONG_SELL", r15="STRONG_SELL",
            di_plus=10.0, di_minus=45.0,
            btc_bias="BEARISH",
            funding=0.0100,
            rsi=52.0,
        )
        result = score_setup(**data)
        self.assertTrue(result["valid"])
        self.assertEqual(result["score"], 100)

    def test_regular_buy_alignment_scores_lower_than_strong_buy(self):
        strong = score_setup(**self._high_quality())
        data = self._high_quality()
        data.update(r4="BUY", r1="BUY", r15="BUY")
        regular = score_setup(**data)
        self.assertTrue(regular["valid"])
        self.assertLess(regular["components"]["trend"], strong["components"]["trend"])

    def test_rr_1_5_scores_lower_than_rr_3(self):
        high = score_setup(**self._high_quality())
        data = self._high_quality()
        data.update(rr_tp1=1.5, rr_tp2=2.0, rr_tp3=2.5)
        low = score_setup(**data)
        self.assertTrue(low["valid"])
        self.assertLess(low["components"]["structure"], high["components"]["structure"])

    def test_basis_outside_tolerance_fails_closed(self):
        data = self._high_quality()
        data["provider_price"] = 102.0
        result = score_setup(**data)
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["reason"], "PRICE_BASIS_MISMATCH")

    def test_missing_critical_data_fails_closed(self):
        data = self._high_quality()
        data["adx"] = None
        result = score_setup(**data)
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["reason"], "MISSING_DATA")

    def test_threshold_gate(self):
        self.assertTrue(score_passes_threshold({"valid": True, "score": 75}, 75))
        self.assertFalse(score_passes_threshold({"valid": True, "score": 74}, 75))
        self.assertFalse(score_passes_threshold({"valid": False, "score": 99}, 75))
        self.assertFalse(score_passes_threshold(None, 75))


if __name__ == "__main__":
    unittest.main()
