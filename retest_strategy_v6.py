"""Deterministic multi-timeframe breakout/retest watchlist strategy."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


CONFIRMED_STATUSES = {"LONG_CONFIRMED", "SHORT_CONFIRMED"}


def _valid_candle(candle: Dict[str, Any]) -> bool:
    try:
        open_ = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        volume = float(candle.get("volume", 0.0))
    except (KeyError, TypeError, ValueError):
        return False
    return high >= max(open_, close) and low <= min(open_, close) and volume >= 0


def calculate_atr(candles: list[Dict[str, Any]], period: int = 14) -> Optional[float]:
    if period <= 0 or len(candles) < period + 1:
        return None
    sample = candles[-(period + 1):]
    if not all(_valid_candle(candle) for candle in sample):
        return None

    ranges = []
    for index in range(1, len(sample)):
        current = sample[index]
        previous = sample[index - 1]
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges) / len(ranges) if ranges else None


def confirmed_swings(candles: list[Dict[str, Any]], window: int = 2) -> Dict[str, list[Dict[str, float]]]:
    if window < 1 or len(candles) < window * 2 + 1:
        return {"highs": [], "lows": []}
    if not all(_valid_candle(candle) for candle in candles):
        return {"highs": [], "lows": []}

    highs = []
    lows = []
    for index in range(window, len(candles) - window):
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        neighbors = candles[index - window:index] + candles[index + 1:index + window + 1]
        if all(high > float(candle["high"]) for candle in neighbors):
            highs.append({"index": index, "price": high})
        if all(low < float(candle["low"]) for candle in neighbors):
            lows.append({"index": index, "price": low})
    return {"highs": highs, "lows": lows}


def classify_structure(candles: list[Dict[str, Any]], window: int = 2) -> str:
    swings = confirmed_swings(candles, window=window)
    if len(swings["highs"]) < 2 or len(swings["lows"]) < 2:
        return "NEUTRAL"

    previous_high, latest_high = swings["highs"][-2:]
    previous_low, latest_low = swings["lows"][-2:]
    if latest_high["price"] > previous_high["price"] and latest_low["price"] > previous_low["price"]:
        return "BULLISH"
    if latest_high["price"] < previous_high["price"] and latest_low["price"] < previous_low["price"]:
        return "BEARISH"
    return "NEUTRAL"


def _dedupe_levels(levels: Iterable[float], tolerance: float) -> list[float]:
    result = []
    for level in sorted(float(value) for value in levels):
        if not result or abs(level - result[-1]) > tolerance:
            result.append(level)
    return result


def _empty_result(status: str = "NO_SETUP", reason: str = "โครงสร้างยังไม่สอดคล้องกัน") -> Dict[str, Any]:
    return {
        "status": status,
        "side": None,
        "score": 0,
        "bias_4h": "NEUTRAL",
        "bias_1h": "NEUTRAL",
        "bias_15m": "NEUTRAL",
        "breakout_level": None,
        "breakout_confirmed": False,
        "retest_confirmed": False,
        "entry": None,
        "entry_low": None,
        "entry_high": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "rr_tp1": None,
        "rr_tp2": None,
        "rr_tp3": None,
        "atr_15m": None,
        "distance_atr": 999.0,
        "reason": reason,
    }


def _breakout_index(candles: list[Dict[str, Any]], level: float, side: str) -> Optional[int]:
    latest = None
    for index in range(1, len(candles)):
        previous_close = float(candles[index - 1]["close"])
        close = float(candles[index]["close"])
        if side == "LONG" and previous_close <= level < close:
            latest = index
        if side == "SHORT" and previous_close >= level > close:
            latest = index
    return latest


def _retest_index(
    candles: list[Dict[str, Any]],
    breakout_index: int,
    level: float,
    zone: float,
    side: str,
) -> Optional[int]:
    for index in range(breakout_index + 1, len(candles)):
        candle = candles[index]
        open_ = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        if side == "LONG" and low <= level + zone and close >= level and close > open_:
            return index
        if side == "SHORT" and high >= level - zone and close <= level and close < open_:
            return index
    return None


def _distance_from_zone(price: float, low: float, high: float, atr: float) -> float:
    if low <= price <= high:
        return 0.0
    distance = low - price if price < low else price - high
    return distance / atr if atr > 0 else 999.0


def analyze_retest_setup(
    *,
    candles_4h: list[Dict[str, Any]],
    candles_1h: list[Dict[str, Any]],
    candles_15m: list[Dict[str, Any]],
    current_price: float,
    atr_period: int = 14,
    swing_window: int = 2,
    entry_zone_atr: float = 0.10,
    stop_buffer_atr: float = 0.15,
    max_chase_atr: float = 0.50,
    trigger_lookback: int = 20,
) -> Dict[str, Any]:
    """Return a fail-closed 4H/1H structure plus 15m breakout/retest plan."""
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return _empty_result(status="DATA_UNAVAILABLE", reason="ราคาปัจจุบันไม่ถูกต้อง")
    if price <= 0 or min(atr_period, swing_window, trigger_lookback) <= 0:
        return _empty_result(status="DATA_UNAVAILABLE", reason="ข้อมูลหรือพารามิเตอร์ไม่ถูกต้อง")
    if not all(_valid_candle(candle) for series in (candles_4h, candles_1h, candles_15m) for candle in series):
        return _empty_result(status="DATA_UNAVAILABLE", reason="แท่งเทียนไม่สมบูรณ์")

    atr_15m = calculate_atr(candles_15m, period=atr_period)
    if atr_15m is None or atr_15m <= 0:
        return _empty_result(status="DATA_UNAVAILABLE", reason="ข้อมูล 15m ไม่พอคำนวณ ATR")

    bias_4h = classify_structure(candles_4h, window=swing_window)
    bias_1h = classify_structure(candles_1h, window=swing_window)
    result = _empty_result()
    result.update({"bias_4h": bias_4h, "bias_1h": bias_1h, "atr_15m": atr_15m})

    if bias_4h == bias_1h == "BULLISH":
        side = "LONG"
    elif bias_4h == bias_1h == "BEARISH":
        side = "SHORT"
    else:
        result["reason"] = "4H และ 1H ยังไม่ไปทางเดียวกัน"
        return result

    swings_1h = confirmed_swings(candles_1h, window=swing_window)
    swings_4h = confirmed_swings(candles_4h, window=swing_window)
    swings_15m = confirmed_swings(candles_15m, window=swing_window)
    level_source = swings_1h["highs"] if side == "LONG" else swings_1h["lows"]
    if not level_source:
        result["reason"] = "ไม่พบ swing 1H สำหรับวางระดับ breakout"
        return result

    level = float(level_source[-1]["price"])
    zone = atr_15m * entry_zone_atr
    entry_low = level - zone
    entry_high = level + zone
    distance_atr = _distance_from_zone(price, entry_low, entry_high, atr_15m)
    trigger_candles = candles_15m[-(int(trigger_lookback) + 1):]
    breakout_index = _breakout_index(trigger_candles, level, side)
    retest_index = (
        _retest_index(trigger_candles, breakout_index, level, zone, side)
        if breakout_index is not None
        else None
    )

    if side == "LONG":
        stop_levels = [swing["price"] for swing in swings_15m["lows"] if swing["price"] < level]
        if not stop_levels:
            stop_levels = [swing["price"] for swing in swings_1h["lows"] if swing["price"] < level]
        structure_stop = stop_levels[-1] if stop_levels else None
        sl = structure_stop - atr_15m * stop_buffer_atr if structure_stop is not None else None
        targets = [swing["price"] for swing in swings_1h["highs"] + swings_4h["highs"] if swing["price"] > entry_high]
    else:
        stop_levels = [swing["price"] for swing in swings_15m["highs"] if swing["price"] > level]
        if not stop_levels:
            stop_levels = [swing["price"] for swing in swings_1h["highs"] if swing["price"] > level]
        structure_stop = stop_levels[-1] if stop_levels else None
        sl = structure_stop + atr_15m * stop_buffer_atr if structure_stop is not None else None
        targets = [swing["price"] for swing in swings_1h["lows"] + swings_4h["lows"] if swing["price"] < entry_low]

    tolerance = max(level * 0.0001, atr_15m * 0.05)
    targets = _dedupe_levels(targets, tolerance)
    if side == "SHORT":
        targets.reverse()
    targets = targets[:3]

    risk = abs(level - sl) if sl is not None else None
    rr_values = [abs(target - level) / risk for target in targets] if risk and risk > 0 else []
    breakout_confirmed = breakout_index is not None
    retest_confirmed = retest_index is not None
    bias_15m = (
        "BULLISH"
        if breakout_confirmed and side == "LONG"
        else "BEARISH"
        if breakout_confirmed and side == "SHORT"
        else classify_structure(candles_15m, window=swing_window)
    )

    proximity_points = max(0, round(10 * (1 - min(distance_atr / max_chase_atr, 1)))) if max_chase_atr > 0 else 0
    score = 30 + (20 if breakout_confirmed else 0) + (25 if retest_confirmed else 0)
    score += 15 if retest_confirmed else 0
    score += proximity_points

    if not breakout_confirmed:
        status = "WAIT_BREAKOUT"
        reason = "โครงสร้างตรงกัน แต่รอแท่ง 15m ปิดทะลุระดับสำคัญ"
    elif not retest_confirmed:
        status = "WAIT_RETEST"
        reason = "เกิด breakout/breakdown แล้ว แต่ยังไม่ retest ยืนยัน"
    elif distance_atr > max_chase_atr:
        status = "WAIT_NO_CHASE"
        reason = "Retest ยืนยันแล้วแต่ราคาวิ่งห่างเกิน 0.5 ATR ห้ามไล่ราคา"
    else:
        status = f"{side}_CONFIRMED"
        reason = "4H/1H ไปทางเดียวกัน และ 15m breakout ตามด้วย retest rejection"

    result.update(
        {
            "status": status,
            "side": side,
            "score": min(100, int(score)),
            "breakout_level": level,
            "breakout_confirmed": breakout_confirmed,
            "retest_confirmed": retest_confirmed,
            "bias_15m": bias_15m,
            "entry": level,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "sl": sl,
            "distance_atr": distance_atr,
            "reason": reason,
        }
    )
    for index in range(3):
        key = index + 1
        result[f"tp{key}"] = targets[index] if index < len(targets) else None
        result[f"rr_tp{key}"] = rr_values[index] if index < len(rr_values) else None
    return result


def rank_watchlist(items: list[Dict[str, Any]], limit: int = 5) -> list[Dict[str, Any]]:
    status_priority = {
        "LONG_CONFIRMED": 5,
        "SHORT_CONFIRMED": 5,
        "WAIT_RETEST": 4,
        "WAIT_NO_CHASE": 3,
        "WAIT_BREAKOUT": 2,
        "NO_SETUP": 1,
        "DATA_UNAVAILABLE": 0,
    }
    ranked = sorted(
        (dict(item) for item in items if isinstance(item, dict) and item.get("symbol")),
        key=lambda item: (
            -float(item.get("score", 0) or 0),
            -status_priority.get(str(item.get("status")), 0),
            float(item.get("distance_atr", 999) or 999),
            int(item.get("liquidity_rank", 999999) or 999999),
            str(item.get("symbol")),
        ),
    )
    return ranked[: max(0, int(limit))]


def _format_price(value: Any) -> str:
    if value is None:
        return "N/A"
    number = float(value)
    if number >= 1000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:.4f}"
    if number >= 0.01:
        return f"{number:.5f}"
    return f"{number:.8f}"


def _format_rr(value: Any) -> str:
    return "N/A" if value is None else f"1:{float(value):.2f}"


def format_watchlist_digest(items: list[Dict[str, Any]], provider: str, scan_time: str) -> str:
    lines = [
        "📡 *TOP 5 RETEST WATCHLIST*",
        f"🕒 `{scan_time}` | 🛰 `{provider}`",
        "ส่งทุกครั้ง แม้ยังไม่มีสัญญาณยืนยัน",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if not items:
        lines.append("ไม่พบข้อมูลเหรียญที่วิเคราะห์ได้ในรอบนี้")
        return "\n".join(lines)

    for rank, item in enumerate(items[:5], start=1):
        entry_low = _format_price(item.get("entry_low"))
        entry_high = _format_price(item.get("entry_high"))
        lines.extend(
            [
                f"{rank}. *#{item.get('symbol', 'UNKNOWN')}* — `{item.get('status', 'NO_SETUP')}` — `{int(item.get('score', 0) or 0)}/100`",
                f"   4H `{item.get('bias_4h', 'N/A')}` | 1H `{item.get('bias_1h', 'N/A')}` | 15m `{item.get('bias_15m', 'N/A')}` | Side `{item.get('side') or 'WAIT'}`",
                f"   Entry `{entry_low}–{entry_high}` | SL `{_format_price(item.get('sl'))}`",
                f"   TP1/TP2/TP3 `{_format_price(item.get('tp1'))}` / `{_format_price(item.get('tp2'))}` / `{_format_price(item.get('tp3'))}`",
                f"   R:R `{_format_rr(item.get('rr_tp1'))}` / `{_format_rr(item.get('rr_tp2'))}` / `{_format_rr(item.get('rr_tp3'))}`",
                f"   เหตุผล: {item.get('reason', 'ข้อมูลไม่พอ')}",
            ]
        )
    lines.append("⚠️ รอ Retest ยืนยันและไม่ไล่ราคา; ระบบนี้ไม่ส่งคำสั่งซื้อขาย")
    return "\n".join(lines)
