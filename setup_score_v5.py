from __future__ import annotations

from typing import Any, Dict, Optional


BUY_RECS = {"BUY", "STRONG_BUY"}
SELL_RECS = {"SELL", "STRONG_SELL"}


def _invalid(reason: str) -> Dict[str, Any]:
    return {
        "valid": False,
        "score": 0,
        "reason": reason,
        "components": {
            "trend": 0,
            "strength": 0,
            "structure": 0,
            "market": 0,
            "funding": 0,
            "timing": 0,
        },
    }


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _trend_score(side: str, r4: str, r1: str, r15: str) -> Optional[int]:
    side = side.upper()
    if side == "LONG":
        expected = BUY_RECS
        strong = "STRONG_BUY"
    elif side == "SHORT":
        expected = SELL_RECS
        strong = "STRONG_SELL"
    else:
        return None

    recs = (r4, r1, r15)
    if any(rec not in expected for rec in recs):
        return None

    # Strong alignment earns the full timeframe weight; regular alignment earns
    # a smaller score so that deterministic filtering does not flatten all setups.
    weights = ((8, 6), (7, 5), (5, 3))
    return sum(full if rec == strong else regular for rec, (full, regular) in zip(recs, weights))


def _strength_score(side: str, adx: float, di_plus: float, di_minus: float) -> Optional[int]:
    if adx < 20 or di_plus < 0 or di_minus < 0:
        return None
    if side == "LONG":
        dominant, other = di_plus, di_minus
    elif side == "SHORT":
        dominant, other = di_minus, di_plus
    else:
        return None
    if dominant <= other:
        return None

    if adx >= 40:
        adx_points = 10
    elif adx >= 35:
        adx_points = 9
    elif adx >= 30:
        adx_points = 8
    elif adx >= 25:
        adx_points = 6
    else:
        adx_points = 4

    total_di = dominant + other
    if total_di <= 0:
        return None
    dominance = (dominant - other) / total_di
    if dominance >= 0.50:
        di_points = 5
    elif dominance >= 0.35:
        di_points = 4
    elif dominance >= 0.20:
        di_points = 3
    else:
        di_points = 2
    return adx_points + di_points


def _structure_score(rr1: float, rr2: Optional[float], rr3: Optional[float]) -> Optional[int]:
    if rr1 < 1.5:
        return None
    if rr1 >= 3.0:
        points = 22
    elif rr1 >= 2.5:
        points = 20
    elif rr1 >= 2.0:
        points = 17
    elif rr1 >= 1.75:
        points = 14
    else:
        points = 12

    if rr2 is not None and rr2 > rr1:
        points += 4
    if rr3 is not None and rr3 > (rr2 if rr2 is not None else rr1):
        points += 4
    return min(points, 30)


def _market_score(
    side: str,
    btc_bias: str,
    tradingview_price: float,
    provider_price: float,
    max_basis_diff_pct: float,
) -> Optional[int]:
    if tradingview_price <= 0 or provider_price <= 0 or max_basis_diff_pct < 0:
        return None
    diff_pct = abs(provider_price - tradingview_price) / tradingview_price * 100.0
    if diff_pct > max_basis_diff_pct:
        return None

    aligned_bias = "BULLISH" if side == "LONG" else "BEARISH"
    if btc_bias == aligned_bias:
        bias_points = 10
    elif btc_bias == "NEUTRAL":
        bias_points = 6
    else:
        bias_points = 0

    if diff_pct <= 0.10:
        basis_points = 5
    elif diff_pct <= 0.25:
        basis_points = 4
    elif diff_pct <= 0.50:
        basis_points = 3
    else:
        basis_points = 2
    return bias_points + basis_points


def _funding_score(side: str, funding: float, max_long_funding: float, min_short_funding: float) -> Optional[int]:
    if side == "LONG":
        if funding > max_long_funding:
            return None
        if funding <= 0:
            return 10
        if funding <= 0.005:
            return 9
        if funding <= 0.015:
            return 7
        if funding <= 0.025:
            return 5
        return 3

    if side == "SHORT":
        if funding < min_short_funding:
            return None
        if funding >= 0:
            return 10
        if funding >= -0.005:
            return 9
        if funding >= -0.015:
            return 7
        if funding >= -0.030:
            return 5
        return 3
    return None


def _timing_score(side: str, rsi: float) -> Optional[int]:
    if not 0 <= rsi <= 100:
        return None
    if side == "LONG":
        if 40 <= rsi <= 55:
            return 10
        if 35 <= rsi <= 60:
            return 8
        if 30 <= rsi <= 65:
            return 6
        if 25 <= rsi <= 70:
            return 4
        return 2
    if side == "SHORT":
        if 45 <= rsi <= 60:
            return 10
        if 40 <= rsi <= 65:
            return 8
        if 35 <= rsi <= 70:
            return 6
        if 30 <= rsi <= 75:
            return 4
        return 2
    return None


def score_setup(
    *,
    side: str,
    r4: str,
    r1: str,
    r15: str,
    adx: Any,
    di_plus: Any,
    di_minus: Any,
    rr_tp1: Any,
    rr_tp2: Any = None,
    rr_tp3: Any = None,
    btc_bias: str,
    tradingview_price: Any,
    provider_price: Any,
    funding: Any,
    rsi: Any,
    max_basis_diff_pct: float = 1.0,
    max_long_funding: float = 0.03,
    min_short_funding: float = -0.04,
) -> Dict[str, Any]:
    """Score an already-filtered setup from 0-100; missing/invalid critical data fails closed."""
    side = str(side).upper()
    critical = {
        "adx": _as_float(adx),
        "di_plus": _as_float(di_plus),
        "di_minus": _as_float(di_minus),
        "rr_tp1": _as_float(rr_tp1),
        "tradingview_price": _as_float(tradingview_price),
        "provider_price": _as_float(provider_price),
        "funding": _as_float(funding),
        "rsi": _as_float(rsi),
    }
    if side not in {"LONG", "SHORT"} or any(value is None for value in critical.values()):
        return _invalid("MISSING_DATA")

    trend = _trend_score(side, str(r4), str(r1), str(r15))
    if trend is None:
        return _invalid("TREND_MISALIGNMENT")

    strength = _strength_score(side, critical["adx"], critical["di_plus"], critical["di_minus"])
    if strength is None:
        return _invalid("STRENGTH_INVALID")

    rr2 = _as_float(rr_tp2) if rr_tp2 is not None else None
    rr3 = _as_float(rr_tp3) if rr_tp3 is not None else None
    structure = _structure_score(critical["rr_tp1"], rr2, rr3)
    if structure is None:
        return _invalid("STRUCTURE_INVALID")

    market = _market_score(
        side,
        str(btc_bias).upper(),
        critical["tradingview_price"],
        critical["provider_price"],
        float(max_basis_diff_pct),
    )
    if market is None:
        return _invalid("PRICE_BASIS_MISMATCH")

    funding_points = _funding_score(side, critical["funding"], float(max_long_funding), float(min_short_funding))
    if funding_points is None:
        return _invalid("FUNDING_INVALID")

    timing = _timing_score(side, critical["rsi"])
    if timing is None:
        return _invalid("TIMING_INVALID")

    components = {
        "trend": trend,
        "strength": strength,
        "structure": structure,
        "market": market,
        "funding": funding_points,
        "timing": timing,
    }
    total = int(sum(components.values()))
    return {
        "valid": True,
        "score": max(0, min(total, 100)),
        "reason": "OK",
        "components": components,
    }


def score_passes_threshold(result: Optional[Dict[str, Any]], threshold: int) -> bool:
    if not isinstance(result, dict) or result.get("valid") is not True:
        return False
    try:
        score = int(result.get("score"))
        threshold_value = int(threshold)
    except (TypeError, ValueError):
        return False
    return 0 <= score <= 100 and 0 <= threshold_value <= 100 and score >= threshold_value
