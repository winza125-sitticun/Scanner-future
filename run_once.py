"""Run one scanner cycle and persist Telegram cooldown state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable


RecentSignals = dict[str, float]
ScanCycle = Callable[..., RecentSignals]


def load_recent_signals(state_path: str | Path) -> RecentSignals:
    """Load valid cooldown timestamps, or fail closed to an empty state."""
    path = Path(state_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    clean: RecentSignals = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, (int, float)) and value >= 0:
            clean[key] = float(value)
    return clean


def save_recent_signals(state_path: str | Path, recent_signals: RecentSignals) -> None:
    """Atomically persist cooldown timestamps for the next scheduled run."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(recent_signals, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_once(
    state_path: str | Path,
    limit: int = 40,
    scan_cycle: ScanCycle | None = None,
) -> RecentSignals:
    """Run exactly one scan cycle, then save and return its cooldown state."""
    if scan_cycle is None:
        from auto_scanner_v5 import run_scan_cycle

        scan_cycle = run_scan_cycle

    recent_signals = load_recent_signals(state_path)
    updated = scan_cycle(limit=limit, recent_signals=recent_signals)
    if not isinstance(updated, dict):
        raise TypeError("run_scan_cycle must return a recent-signals dictionary")
    save_recent_signals(state_path, updated)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        default=".scanner_state/recent_signals.json",
        help="JSON file used to persist Telegram cooldown timestamps",
    )
    parser.add_argument("--limit", type=int, default=40, help="Maximum symbols to scan")
    args = parser.parse_args()
    run_once(args.state_file, limit=args.limit)


if __name__ == "__main__":
    main()
