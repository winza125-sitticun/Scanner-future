import json
import tempfile
import unittest
from pathlib import Path

from run_once import load_recent_signals, run_once


class RunOnceTests(unittest.TestCase):
    def test_run_once_persists_cooldown_returned_by_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "recent_signals.json"
            state_path.write_text('{"BTCUSDT_LONG": 100.0}', encoding="utf-8")
            observed = {}

            def scan_cycle(*, limit, recent_signals):
                observed["limit"] = limit
                observed["recent_signals"] = dict(recent_signals)
                return {**recent_signals, "ETHUSDT_SHORT": 200.0}

            result = run_once(state_path=state_path, limit=25, scan_cycle=scan_cycle)

            self.assertEqual(25, observed["limit"])
            self.assertEqual({"BTCUSDT_LONG": 100.0}, observed["recent_signals"])
            self.assertEqual(
                {"BTCUSDT_LONG": 100.0, "ETHUSDT_SHORT": 200.0},
                result,
            )
            self.assertEqual(result, json.loads(state_path.read_text(encoding="utf-8")))

    def test_invalid_state_fails_closed_to_empty_cooldown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "recent_signals.json"
            state_path.write_text("not-json", encoding="utf-8")

            self.assertEqual({}, load_recent_signals(state_path))


if __name__ == "__main__":
    unittest.main()
