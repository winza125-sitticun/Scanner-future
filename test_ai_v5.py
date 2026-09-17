import unittest

from auto_scanner_v5 import ai_decision_is_approved, normalize_ai_result


class TestAstraDecisionV5(unittest.TestCase):
    def test_wait_is_valid_but_never_approved(self):
        result = normalize_ai_result({
            "confidence": 88,
            "verdict": "WAIT",
            "reason": "รอยืนยันแท่งถัดไป",
            "risk": "โมเมนตัมเริ่มอ่อน",
        })
        self.assertEqual(result["verdict"], "WAIT")
        self.assertFalse(ai_decision_is_approved(result, 75))

    def test_approved_requires_confidence_threshold(self):
        result = normalize_ai_result({
            "confidence": 74,
            "verdict": "APPROVED",
            "reason": "setup ผ่าน",
            "risk": "ปกติ",
        })
        self.assertFalse(ai_decision_is_approved(result, 75))

    def test_unknown_verdict_is_rejected_by_validator(self):
        with self.assertRaises(ValueError):
            normalize_ai_result({
                "confidence": 99,
                "verdict": "LONG",
                "reason": "invalid",
                "risk": "invalid",
            })


if __name__ == "__main__":
    unittest.main()
