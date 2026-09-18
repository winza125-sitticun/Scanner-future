import argparse
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from setup_score_v5 import score_passes_threshold, score_setup
from retest_strategy_v6 import analyze_retest_setup, format_watchlist_digest, rank_watchlist

from market_data_v5 import (
    BinanceMarketDataProvider,
    BybitMarketDataProvider,
    MarketDataSession,
    OkxMarketDataProvider,
    price_basis_within_tolerance,
    select_market_data_session,
)

# ==========================================================
# CONFIGURATION
# Secrets are loaded from environment variables only.
# ==========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

ADX_MIN_THRESHOLD = float(os.getenv("ADX_MIN_THRESHOLD", "20.0"))
MAX_LONG_FUNDING = float(os.getenv("MAX_LONG_FUNDING", "0.0300"))
MIN_SHORT_FUNDING = float(os.getenv("MIN_SHORT_FUNDING", "-0.0400"))
AI_MIN_CONFIDENCE = int(os.getenv("AI_MIN_CONFIDENCE", "75"))
SETUP_MIN_SCORE = int(os.getenv("SETUP_MIN_SCORE", "75"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "3600"))
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
STRUCTURE_KLINE_LIMIT = int(os.getenv("STRUCTURE_KLINE_LIMIT", "120"))
STRUCTURE_SWING_WINDOW = int(os.getenv("STRUCTURE_SWING_WINDOW", "2"))
STRUCTURE_ATR_PERIOD = int(os.getenv("STRUCTURE_ATR_PERIOD", "14"))
STRUCTURE_ATR_BUFFER_MULT = float(os.getenv("STRUCTURE_ATR_BUFFER_MULT", "0.25"))
MIN_STRUCTURE_RR = float(os.getenv("MIN_STRUCTURE_RR", "1.5"))
MARKET_PRICE_BASIS_MAX_DIFF_PCT = float(os.getenv("MARKET_PRICE_BASIS_MAX_DIFF_PCT", "1.0"))
MARKET_PROVIDER_PROBE_KLINE_LIMIT = int(os.getenv("MARKET_PROVIDER_PROBE_KLINE_LIMIT", "20"))
WATCHLIST_SIZE = int(os.getenv("WATCHLIST_SIZE", "5"))
WATCHLIST_CANDIDATE_LIMIT = int(os.getenv("WATCHLIST_CANDIDATE_LIMIT", "12"))

BUY_RECS = {"BUY", "STRONG_BUY"}
SELL_RECS = {"SELL", "STRONG_SELL"}

DEFAULT_TOP_FUTURES = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
    "SUIUSDT", "NEARUSDT", "AVAXUSDT", "LINKUSDT", "PEPEUSDT", "WIFUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "TAOUSDT", "FETUSDT", "RENDERUSDT", "INJUSDT", "TIAUSDT",
    "SEIUSDT", "SHIBUSDT", "DOTUSDT", "LTCUSDT", "UNIUSDT", "FILUSDT", "ATOMUSDT",
    "TRXUSDT", "FTMUSDT", "AAVEUSDT", "KASUSDT", "STXUSDT", "ORDIUSDT", "RUNEUSDT",
]


def create_market_data_session(
    limit: int,
    providers=None,
    probe_kline_limit: int = MARKET_PROVIDER_PROBE_KLINE_LIMIT,
    min_probe_candles: int = STRUCTURE_ATR_PERIOD + 1,
):
    if providers is None:
        providers = [
            BinanceMarketDataProvider(timeout=HTTP_TIMEOUT_SECONDS),
            BybitMarketDataProvider(timeout=HTTP_TIMEOUT_SECONDS),
            OkxMarketDataProvider(timeout=HTTP_TIMEOUT_SECONDS),
        ]
    return select_market_data_session(
        providers,
        limit=limit,
        probe_kline_limit=probe_kline_limit,
        min_probe_candles=min_probe_candles,
    )


def market_price_basis_ok(
    session: Optional[MarketDataSession],
    symbol: str,
    tradingview_price: Optional[float],
    max_diff_pct: float = MARKET_PRICE_BASIS_MAX_DIFF_PCT,
) -> bool:
    if session is None:
        return False
    provider_price = session.snapshot.last_price.get(symbol)
    return price_basis_within_tolerance(tradingview_price, provider_price, max_diff_pct)


def format_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.5f}"
    return f"{p:.8f}"


def _valid_candle(candle: Dict[str, Any]) -> bool:
    try:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
    except (KeyError, TypeError, ValueError):
        return False
    return high >= low and low <= close <= high


def calculate_atr(candles, period=14):
    """Simple ATR over the most recent `period` true ranges."""
    if period <= 0 or len(candles) < period + 1:
        return None
    if not all(_valid_candle(c) for c in candles[-(period + 1):]):
        return None

    true_ranges = []
    start = len(candles) - period
    for i in range(start, len(candles)):
        current = candles[i]
        previous = candles[i - 1]
        high = float(current["high"])
        low = float(current["low"])
        prev_close = float(previous["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    return sum(true_ranges) / period if true_ranges else None


def find_confirmed_swings(candles, window=2):
    """Return confirmed local highs/lows, excluding edge candles."""
    if window < 1 or len(candles) < (window * 2 + 1):
        return {"highs": [], "lows": []}
    if not all(_valid_candle(c) for c in candles):
        return {"highs": [], "lows": []}

    highs = []
    lows = []
    for i in range(window, len(candles) - window):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        neighbors = candles[i - window:i] + candles[i + 1:i + window + 1]
        if all(high > float(c["high"]) for c in neighbors):
            highs.append({"index": i, "price": high})
        if all(low < float(c["low"]) for c in neighbors):
            lows.append({"index": i, "price": low})
    return {"highs": highs, "lows": lows}


def _dedupe_levels(levels, tolerance):
    result = []
    for level in sorted(float(v) for v in levels if v is not None):
        if not result or abs(level - result[-1]) > tolerance:
            result.append(level)
    return result


def build_structure_trade_plan(
    side,
    entry_price,
    candles_1h,
    candles_4h=None,
    pivot_levels=None,
    min_rr=1.5,
    atr_period=14,
    atr_buffer_mult=0.25,
    swing_window=2,
):
    """Build a fail-closed structure plan from confirmed swings + ATR buffer."""
    side = str(side).upper()
    if side not in {"LONG", "SHORT"} or entry_price is None or entry_price <= 0:
        return None
    if min_rr <= 0 or atr_buffer_mult < 0:
        return None

    candles_4h = candles_4h or []
    pivot_levels = pivot_levels or []
    atr = calculate_atr(candles_1h, period=atr_period)
    if atr is None or atr <= 0:
        return None

    swings_1h = find_confirmed_swings(candles_1h, window=swing_window)
    swings_4h = find_confirmed_swings(candles_4h, window=swing_window) if candles_4h else {"highs": [], "lows": []}
    buffer_dist = atr * atr_buffer_mult

    if side == "LONG":
        stop_candidates = [s for s in swings_1h["lows"] if s["price"] < entry_price]
        if not stop_candidates:
            return None
        structure_stop = stop_candidates[-1]["price"]
        sl = structure_stop - buffer_dist
        if sl >= entry_price:
            return None
        target_levels = [s["price"] for s in swings_1h["highs"] if s["price"] > entry_price]
        target_levels += [s["price"] for s in swings_4h["highs"] if s["price"] > entry_price]
        target_levels += [float(v) for v in pivot_levels if v is not None and float(v) > entry_price]
    else:
        stop_candidates = [s for s in swings_1h["highs"] if s["price"] > entry_price]
        if not stop_candidates:
            return None
        structure_stop = stop_candidates[-1]["price"]
        sl = structure_stop + buffer_dist
        if sl <= entry_price:
            return None
        target_levels = [s["price"] for s in swings_1h["lows"] if s["price"] < entry_price]
        target_levels += [s["price"] for s in swings_4h["lows"] if s["price"] < entry_price]
        target_levels += [float(v) for v in pivot_levels if v is not None and float(v) < entry_price]

    risk_dist = abs(entry_price - sl)
    if risk_dist <= 0:
        return None

    tolerance = max(entry_price * 0.0001, atr * 0.05)
    targets = _dedupe_levels(target_levels, tolerance)
    if side == "SHORT":
        targets = list(reversed(targets))
    if not targets:
        return None

    rr_values = [abs(target - entry_price) / risk_dist for target in targets]
    if rr_values[0] < min_rr:
        return None

    selected = targets[:3]
    selected_rr = rr_values[:3]
    result = {
        "entry": float(entry_price),
        "sl": sl,
        "sl_pct": risk_dist / entry_price * 100,
        "structure_stop": structure_stop,
        "atr": atr,
        "atr_buffer": buffer_dist,
        "target_source": "STRUCTURE",
        "invalidation": (
            "แท่ง 1H ปิดต่ำกว่า swing low ที่ใช้เป็นโครงสร้างหยุดขาดทุน"
            if side == "LONG"
            else "แท่ง 1H ปิดเหนือ swing high ที่ใช้เป็นโครงสร้างหยุดขาดทุน"
        ),
    }
    for idx in range(3):
        key = idx + 1
        if idx < len(selected):
            target = selected[idx]
            result[f"tp{key}"] = target
            result[f"tp{key}_pct"] = abs(target - entry_price) / entry_price * 100
            result[f"rr_tp{key}"] = selected_rr[idx]
        else:
            result[f"tp{key}"] = None
            result[f"tp{key}_pct"] = None
            result[f"rr_tp{key}"] = None
    return result


def parse_futures_klines(raw, now_ms=None):
    """Parse Binance-style kline rows and keep closed candles only."""
    import time as _time

    if not isinstance(raw, list):
        return []
    if now_ms is None:
        now_ms = int(_time.time() * 1000)

    candles = []
    try:
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                return []
            close_time = int(row[6])
            if close_time >= now_ms:
                continue
            candle = {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": close_time,
            }
            if not _valid_candle(candle):
                return []
            candles.append(candle)
    except (TypeError, ValueError, IndexError):
        return []
    return candles


def extract_classic_pivot_targets(indicators, side):
    """Extract TradingView monthly Classic pivot targets for the trade direction."""
    if not isinstance(indicators, dict):
        return []
    side = str(side).upper()
    prefix = "R" if side == "LONG" else "S" if side == "SHORT" else None
    if prefix is None:
        return []

    result = []
    for level in (1, 2, 3):
        value = indicators.get(f"Pivot.M.Classic.{prefix}{level}")
        if isinstance(value, (int, float)):
            result.append(float(value))
    return result


def normalize_ai_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Validate Astra output. WAIT is valid, but only APPROVED may pass the final gate."""
    if not isinstance(result, dict):
        raise ValueError("AI result must be an object")
    confidence = int(result.get("confidence"))
    verdict = str(result.get("verdict", "")).strip().upper()
    if not 0 <= confidence <= 100 or verdict not in {"APPROVED", "WAIT", "REJECT"}:
        raise ValueError("Invalid AI confidence/verdict")
    return {
        "confidence": confidence,
        "verdict": verdict,
        "reason": str(result.get("reason", "")).strip(),
        "risk": str(result.get("risk", "")).strip(),
    }


def ai_decision_is_approved(result: Optional[Dict[str, Any]], min_confidence: int) -> bool:
    """Fail closed: malformed/missing AI output can never approve a setup."""
    if not isinstance(result, dict):
        return False
    try:
        normalized = normalize_ai_result(result)
    except (TypeError, ValueError):
        return False
    return normalized["verdict"] == "APPROVED" and normalized["confidence"] >= min_confidence


def determine_btc_bias(
    r4: Optional[str],
    close4: Optional[float],
    ema200_4: Optional[float],
    r1: Optional[str],
    close1: Optional[float],
    ema200_1: Optional[float],
) -> str:
    """4H defines the regime; 1H must confirm it using its own EMA200."""
    critical = (close4, ema200_4, close1, ema200_1)
    if any(v is None for v in critical):
        return "NEUTRAL"

    if r4 in BUY_RECS and r1 in BUY_RECS and close4 > ema200_4 and close1 > ema200_1:
        return "BULLISH"
    if r4 in SELL_RECS and r1 in SELL_RECS and close4 < ema200_4 and close1 < ema200_1:
        return "BEARISH"
    return "NEUTRAL"


def setup_passes_filters(
    *,
    side: str,
    symbol: str,
    r4: Optional[str],
    r1: Optional[str],
    r15: Optional[str],
    btc_bias: str,
    price: Optional[float],
    ema200: Optional[float],
    adx: Optional[float],
    di_plus: Optional[float],
    di_minus: Optional[float],
    funding: Optional[float],
    adx_min: float,
    max_long_funding: float,
    min_short_funding: float,
) -> bool:
    """Deterministic gate. Critical missing data rejects the setup."""
    if any(v is None for v in (price, ema200, adx, di_plus, di_minus, funding)):
        return False

    side = side.upper()
    is_btc = symbol == "BTCUSDT"

    if side == "LONG":
        if not (r4 in BUY_RECS and r1 in BUY_RECS and r15 in BUY_RECS):
            return False
        if not is_btc and btc_bias == "BEARISH":
            return False
        if adx < adx_min or di_plus <= di_minus:
            return False
        if price <= ema200:
            return False
        if funding > max_long_funding:
            return False
        return True

    if side == "SHORT":
        if not (r4 in SELL_RECS and r1 in SELL_RECS and r15 in SELL_RECS):
            return False
        if not is_btc and btc_bias == "BULLISH":
            return False
        if adx < adx_min or di_minus <= di_plus:
            return False
        if price >= ema200:
            return False
        if funding < min_short_funding:
            return False
        return True

    return False


def send_telegram_alert(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [!] Telegram credentials missing; alert not sent.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"  [!] Telegram error: {exc}")
        return False


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI response does not contain a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI response JSON is not an object")
    return parsed


def analyze_with_ai(
    symbol: str,
    side: str,
    price: float,
    r4: str,
    r1: str,
    r15: str,
    rsi: float,
    adx: float,
    funding: float,
    btc_trend: str,
    tags: list[str],
    plan: Optional[Dict[str, Any]] = None,
    setup_score: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Devil's Advocate only. AI outage or malformed output rejects the setup."""
    if not GEMINI_API_KEY:
        return {
            "confidence": 0,
            "verdict": "REJECT",
            "reason": "AI validation unavailable",
            "risk": "ไม่พบ GEMINI_API_KEY จึงไม่อนุมัติสัญญาณ",
        }

    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)
        plan = plan or {}
        structure_text = (
            f"SL={plan.get('sl')} | TP1={plan.get('tp1')} (RR={plan.get('rr_tp1')}) | "
            f"TP2={plan.get('tp2')} (RR={plan.get('rr_tp2')}) | "
            f"TP3={plan.get('tp3')} (RR={plan.get('rr_tp3')}) | ATR={plan.get('atr')}"
        )
        setup_score = setup_score or {}
        score_text = f"{setup_score.get('score', 'N/A')}/100 | components={setup_score.get('components', {})}"
        prompt = f"""
คุณเป็น Risk Manager ของกองทุนเทรด Crypto Futures และทำหน้าที่ Devil's Advocate เท่านั้น
ห้ามสร้างสัญญาณใหม่ หน้าที่คือจับความเสี่ยงของ setup ที่ผ่าน deterministic filters มาแล้ว

- คู่เหรียญ: {symbol} (ทิศทาง: {side})
- ราคา: {price}
- BTC Bias: {btc_trend}
- แนวโน้ม: 4H={r4} / 1H={r1} / 15m={r15}
- RSI 1H: {rsi:.1f}
- ADX 1H: {adx:.1f}
- Funding Rate: {funding:+.4f}%
- จุดสังเกต: {', '.join(tags)}
- Structure Plan: {structure_text}
- Deterministic Setup Score: {score_text}

คำตัดสิน:
- APPROVED = setup ยังแข็งแรงและไม่มีความเสี่ยงใหม่ที่ควรหยุด
- WAIT = setup ผ่านคะแนนแล้ว แต่ควรรอ confirmation เพิ่มก่อนเข้า
- REJECT = พบความเสี่ยงที่ทำให้ setup ไม่ควรถูกแจ้งเป็นสัญญาณเข้า
ห้ามเปลี่ยน LONG เป็น SHORT หรือ SHORT เป็น LONG และห้ามสร้างสัญญาณใหม่

ตอบ JSON เท่านั้น:
{{
  "confidence": <จำนวนเต็ม 0-100>,
  "verdict": "APPROVED" หรือ "WAIT" หรือ "REJECT",
  "reason": "เหตุผลภาษาไทยสั้นๆ 1 ประโยค",
  "risk": "ความเสี่ยงสำคัญที่สุดภาษาไทย 1 ประโยค"
}}
"""
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        result = _extract_json_object(getattr(response, "text", ""))
        return normalize_ai_result(result)
    except Exception as exc:
        print(f"  [!] AI error: {exc}")
        return {
            "confidence": 0,
            "verdict": "REJECT",
            "reason": "AI validation unavailable",
            "risk": "AI error หรือผลลัพธ์ไม่ถูกต้อง จึง fail-closed",
        }


def get_target_futures_symbols(limit: int = 40) -> list[str]:
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            blacklist = ("USDC", "FDUSD", "BUSD", "TUSD", "EUR", "DAI")
            volume_by_symbol = {
                item.get("symbol"): float(item.get("quoteVolume", 0) or 0)
                for item in data
                if isinstance(item, dict) and item.get("symbol")
            }
            usdt_pairs = [
                symbol
                for symbol in volume_by_symbol
                if symbol.endswith("USDT") and not any(symbol.startswith(b) for b in blacklist)
            ]
            usdt_pairs.sort(key=lambda symbol: volume_by_symbol[symbol], reverse=True)
            if len(usdt_pairs) >= limit:
                return usdt_pairs[:limit]
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"  [!] Futures universe fallback: {exc}")
    return DEFAULT_TOP_FUTURES[:limit]


def get_funding_rates() -> Dict[str, float]:
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return {
                item["symbol"]: float(item.get("lastFundingRate", 0)) * 100
                for item in data
                if isinstance(item, dict) and item.get("symbol")
            }
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"  [!] Funding data unavailable: {exc}")
    return {}


def get_futures_klines(symbol: str, interval: str, limit: int = STRUCTURE_KLINE_LIMIT) -> list[Dict[str, Any]]:
    """Fetch closed USD-M futures klines used only for structure/risk planning."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        candles = parse_futures_klines(resp.json())
        if len(candles) < STRUCTURE_ATR_PERIOD + 1:
            return []
        return candles
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"  [!] Structure kline data unavailable for {symbol} {interval}: {exc}")
        return []


def _rec(analysis) -> Optional[str]:
    return analysis.summary.get("RECOMMENDATION") if analysis else None


def _indicator(analysis, name: str) -> Optional[float]:
    if not analysis:
        return None
    value = analysis.indicators.get(name)
    return value if isinstance(value, (int, float)) else None


def build_retest_watchlist(
    market_session: MarketDataSession,
    target_symbols: list[str],
    *,
    candidate_limit: int = WATCHLIST_CANDIDATE_LIMIT,
    watchlist_size: int = WATCHLIST_SIZE,
    candle_cache: Optional[Dict[tuple[str, str], list[Dict[str, Any]]]] = None,
) -> list[Dict[str, Any]]:
    """Analyze liquid candidates and rank the five closest high-quality retest plans."""
    if candle_cache is None:
        candle_cache = {}

    candidates = target_symbols[: max(watchlist_size, candidate_limit)]
    analyzed = []
    for liquidity_rank, symbol in enumerate(candidates, start=1):
        try:
            series = {}
            for interval in ("4h", "1h", "15m"):
                key = (symbol, interval)
                if key not in candle_cache:
                    candle_cache[key] = market_session.provider.get_klines(
                        symbol,
                        interval,
                        STRUCTURE_KLINE_LIMIT,
                    )
                series[interval] = candle_cache[key]

            result = analyze_retest_setup(
                candles_4h=series["4h"],
                candles_1h=series["1h"],
                candles_15m=series["15m"],
                current_price=market_session.snapshot.last_price.get(symbol),
                atr_period=STRUCTURE_ATR_PERIOD,
                swing_window=STRUCTURE_SWING_WINDOW,
                entry_zone_atr=0.10,
                stop_buffer_atr=0.15,
                max_chase_atr=0.50,
            )
        except Exception as exc:
            print(f"  [!] {symbol}: retest watchlist data unavailable: {exc}")
            result = analyze_retest_setup(
                candles_4h=[],
                candles_1h=[],
                candles_15m=[],
                current_price=market_session.snapshot.last_price.get(symbol),
            )
        result.update(
            {
                "symbol": symbol,
                "liquidity_rank": liquidity_rank,
                "funding": market_session.snapshot.funding_pct.get(symbol),
                "data_provider": market_session.snapshot.provider,
            }
        )
        analyzed.append(result)

    return rank_watchlist(analyzed, limit=watchlist_size)


def run_scan_cycle(limit: int = 40, recent_signals: Optional[Dict[str, float]] = None):
    if recent_signals is None:
        recent_signals = {}

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 120)
    print(f" 🔍 SCAN CYCLE START : {now_str}")
    print("=" * 120)

    market_session = create_market_data_session(limit=limit)
    if market_session is None:
        print("[x] No healthy market-data provider. Fail-closed: skipping this scan cycle.")
        return recent_signals

    target_symbols = list(market_session.snapshot.symbols)
    funding_rates = market_session.snapshot.funding_pct
    provider_name = market_session.snapshot.provider
    print(f"[*] Market Data Provider: [{provider_name}] (locked for this scan cycle)")

    candle_cache: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
    watchlist = build_retest_watchlist(
        market_session,
        target_symbols,
        candle_cache=candle_cache,
    )
    digest = format_watchlist_digest(watchlist, provider=provider_name, scan_time=now_str)
    if send_telegram_alert(digest):
        print(f"[*] Top {len(watchlist)} retest watchlist sent to Telegram.")
    else:
        print("[x] Retest watchlist delivery failed; signal scan will continue.")

    tv_symbols = [f"BINANCE:{s}.P" for s in target_symbols]

    try:
        from tradingview_ta import Interval, get_multiple_analysis

        a4 = get_multiple_analysis(screener="crypto", interval=Interval.INTERVAL_4_HOURS, symbols=tv_symbols)
        a1 = get_multiple_analysis(screener="crypto", interval=Interval.INTERVAL_1_HOUR, symbols=tv_symbols)
        a15 = get_multiple_analysis(screener="crypto", interval=Interval.INTERVAL_15_MINUTES, symbols=tv_symbols)
    except Exception as exc:
        print(f"[x] TradingView query failed. Fail-closed: {exc}")
        return recent_signals

    btc_4h = a4.get("BINANCE:BTCUSDT.P")
    btc_1h = a1.get("BINANCE:BTCUSDT.P")
    if not market_price_basis_ok(market_session, "BTCUSDT", _indicator(btc_1h, "close")):
        print("[x] BTC TradingView/provider price basis exceeded tolerance or data is missing. Fail-closed.")
        return recent_signals

    btc_bias = determine_btc_bias(
        _rec(btc_4h),
        _indicator(btc_4h, "close"),
        _indicator(btc_4h, "EMA200"),
        _rec(btc_1h),
        _indicator(btc_1h, "close"),
        _indicator(btc_1h, "EMA200"),
    )
    print(f"[*] 👑 Bitcoin Market Regime: [{btc_bias}] (4H={_rec(btc_4h) or 'N/A'} / 1H={_rec(btc_1h) or 'N/A'})")

    qualified_setups = []
    for symbol in target_symbols:
        tv_sym = f"BINANCE:{symbol}.P"
        an4, an1, an15 = a4.get(tv_sym), a1.get(tv_sym), a15.get(tv_sym)
        if not an4 or not an1 or not an15:
            continue

        r4, r1, r15 = _rec(an4), _rec(an1), _rec(an15)
        price = _indicator(an1, "close")
        chg = _indicator(an1, "change") or 0.0
        rsi1 = _indicator(an1, "RSI")
        adx1 = _indicator(an1, "ADX")
        di_plus = _indicator(an1, "ADX+DI")
        di_minus = _indicator(an1, "ADX-DI")
        ema200 = _indicator(an1, "EMA200")
        funding = funding_rates.get(symbol)

        if not market_price_basis_ok(market_session, symbol, price):
            print(f"  [x] {symbol}: TradingView/{provider_name} price basis mismatch -> REJECT")
            continue

        side = None
        if r4 in BUY_RECS and r1 in BUY_RECS and r15 in BUY_RECS:
            side = "LONG"
        elif r4 in SELL_RECS and r1 in SELL_RECS and r15 in SELL_RECS:
            side = "SHORT"
        else:
            continue

        if not setup_passes_filters(
            side=side,
            symbol=symbol,
            r4=r4,
            r1=r1,
            r15=r15,
            btc_bias=btc_bias,
            price=price,
            ema200=ema200,
            adx=adx1,
            di_plus=di_plus,
            di_minus=di_minus,
            funding=funding,
            adx_min=ADX_MIN_THRESHOLD,
            max_long_funding=MAX_LONG_FUNDING,
            min_short_funding=MIN_SHORT_FUNDING,
        ):
            continue

        tags = [f"ADX={adx1:.1f}", f"4H={r4}", f"1H={r1}", f"15m={r15}"]
        tags.append("Price>EMA200" if side == "LONG" else "Price<EMA200")
        if rsi1 is not None:
            if side == "LONG" and rsi1 <= 35:
                tags.append("RSI_OVERSOLD")
            if side == "SHORT" and rsi1 >= 65:
                tags.append("RSI_OVERBOUGHT")

        candles_1h = candle_cache.get((symbol, "1h"))
        if candles_1h is None:
            candles_1h = market_session.provider.get_klines(symbol, "1h", STRUCTURE_KLINE_LIMIT)
            candle_cache[(symbol, "1h")] = candles_1h
        candles_4h = candle_cache.get((symbol, "4h"))
        if candles_4h is None:
            candles_4h = market_session.provider.get_klines(symbol, "4h", STRUCTURE_KLINE_LIMIT)
            candle_cache[(symbol, "4h")] = candles_4h
        if len(candles_1h) < STRUCTURE_ATR_PERIOD + 1 or not candles_4h:
            print(f"  [x] {symbol}: missing closed {provider_name} kline history -> structure REJECT")
            continue

        pivot_levels = extract_classic_pivot_targets(an4.indicators, side)
        plan = build_structure_trade_plan(
            side=side,
            entry_price=price,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            pivot_levels=pivot_levels,
            min_rr=MIN_STRUCTURE_RR,
            atr_period=STRUCTURE_ATR_PERIOD,
            atr_buffer_mult=STRUCTURE_ATR_BUFFER_MULT,
            swing_window=STRUCTURE_SWING_WINDOW,
        )
        if not plan:
            print(f"  [x] {symbol}: structure/ATR/R:R gate rejected setup")
            continue

        setup_score_result = score_setup(
            side=side,
            r4=r4,
            r1=r1,
            r15=r15,
            adx=adx1,
            di_plus=di_plus,
            di_minus=di_minus,
            rr_tp1=plan.get("rr_tp1"),
            rr_tp2=plan.get("rr_tp2"),
            rr_tp3=plan.get("rr_tp3"),
            btc_bias=btc_bias,
            tradingview_price=price,
            provider_price=market_session.snapshot.last_price.get(symbol),
            funding=funding,
            rsi=rsi1,
            max_basis_diff_pct=MARKET_PRICE_BASIS_MAX_DIFF_PCT,
            max_long_funding=MAX_LONG_FUNDING,
            min_short_funding=MIN_SHORT_FUNDING,
        )
        if not score_passes_threshold(setup_score_result, SETUP_MIN_SCORE):
            score_value = setup_score_result.get("score", 0) if isinstance(setup_score_result, dict) else 0
            reason = setup_score_result.get("reason", "INVALID_SCORE") if isinstance(setup_score_result, dict) else "INVALID_SCORE"
            print(f"  ⏳ {symbol}: Setup Score {score_value}/100 < {SETUP_MIN_SCORE} or invalid ({reason}) -> WAIT; Astra skipped")
            continue

        tags.append(f"StructureRR1={plan['rr_tp1']:.2f}")
        tags.append(f"ATR={plan['atr']:.6g}")
        tags.append(f"SetupScore={setup_score_result['score']}")
        qualified_setups.append(
            {
                "side": side,
                "symbol": symbol,
                "price": price,
                "chg": chg,
                "funding": funding,
                "r4": r4,
                "r1": r1,
                "r15": r15,
                "rsi": rsi1,
                "adx": adx1,
                "tags": tags,
                "plan": plan,
                "setup_score": setup_score_result,
                "data_provider": provider_name,
            }
        )

    print(f"\n[*] Setup Score Gate Passed (>= {SETUP_MIN_SCORE}): {len(qualified_setups)} setups.")
    print("-" * 120)
    print("{:<6} {:<10} {:<12} {:<16} {:<16} {:<8} {:<8} {:<10}".format(
        "Side", "Symbol", "Entry", "Stop Loss", "TP1", "RSI", "ADX", "Funding%"
    ))
    print("-" * 120)

    curr_t = time.time()
    for item in qualified_setups:
        p = item["plan"]
        rsi_val = f"{item['rsi']:.1f}" if item["rsi"] is not None else "N/A"
        sl_sign = "-" if item["side"] == "LONG" else "+"
        tp_sign = "+" if item["side"] == "LONG" else "-"
        sl_str = f"{format_price(p['sl'])} ({sl_sign}{p['sl_pct']:.1f}%)"
        tp1_str = f"{format_price(p['tp1'])} ({tp_sign}{p['tp1_pct']:.1f}%)"

        print("{:<6} {:<10} {:<12} {:<16} {:<16} {:<8} {:<8.1f} {:<+10.4f}".format(
            item["side"], item["symbol"], format_price(p["entry"]), sl_str, tp1_str,
            rsi_val, item["adx"], item["funding"]
        ))

        key = f"{item['symbol']}_{item['side']}"
        if curr_t - recent_signals.get(key, 0) <= ALERT_COOLDOWN_SECONDS:
            continue

        print(f"\n  🧠 Requesting Devil's Advocate AI review for #{item['symbol']}...")
        ai_res = analyze_with_ai(
            item["symbol"], item["side"], p["entry"], item["r4"], item["r1"], item["r15"],
            item["rsi"] if item["rsi"] is not None else 50.0,
            item["adx"], item["funding"], btc_bias, item["tags"], item["plan"], item["setup_score"],
        )
        print(f"     -> Astra Confidence: {ai_res.get('confidence', 0)}% | Verdict: {ai_res.get('verdict', 'REJECT')}")

        if not ai_decision_is_approved(ai_res, AI_MIN_CONFIDENCE):
            verdict = ai_res.get("verdict", "REJECT")
            print(f"     [x] Astra {verdict}/fail-closed: {ai_res.get('risk', 'unknown risk')}")
            continue

        icon = "🟢 *[STRUCTURE LONG]*" if item["side"] == "LONG" else "🔴 *[STRUCTURE SHORT]*"
        target_lines = [
            f"💰 *TP1:* `{format_price(p['tp1'])}` ({tp_sign}{p['tp1_pct']:.2f}% | R:R 1:{p['rr_tp1']:.2f})"
        ]
        if p.get("tp2") is not None:
            target_lines.append(
                f"🚀 *TP2:* `{format_price(p['tp2'])}` ({tp_sign}{p['tp2_pct']:.2f}% | R:R 1:{p['rr_tp2']:.2f})"
            )
        if p.get("tp3") is not None:
            target_lines.append(
                f"🏁 *TP3:* `{format_price(p['tp3'])}` ({tp_sign}{p['tp3_pct']:.2f}% | R:R 1:{p['rr_tp3']:.2f})"
            )
        targets_text = "\n".join(target_lines)
        score = item["setup_score"]
        comp = score["components"]
        msg = (
            f"{icon} `#{item['symbol']}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 *Setup Score:* `{score['score']}/100`\n"
            f"Trend `{comp['trend']}/20` | Strength `{comp['strength']}/15` | Structure `{comp['structure']}/30`\n"
            f"Market `{comp['market']}/15` | Funding `{comp['funding']}/10` | Timing `{comp['timing']}/10`\n"
            f"🤖 *Astra:* `APPROVED` | *Confidence:* `{ai_res['confidence']}%`\n"
            f"✅ *Final Decision:* `{item['side']}`\n"
            f"💡 *เหตุผล:* {ai_res.get('reason', '')}\n"
            f"⚠️ *จุดเสี่ยง:* {ai_res.get('risk', '')}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👑 *BTC Bias:* `{btc_bias}` | *ADX:* `{item['adx']:.1f}`\n"
            f"🛰 *Market Data:* `{item['data_provider']}` | *Signal:* `TradingView BINANCE`\n"
            f"🎯 *Entry:* `{format_price(p['entry'])}`\n"
            f"🛑 *Stop Loss:* `{format_price(p['sl'])}` ({sl_sign}{p['sl_pct']:.2f}%)\n"
            f"🧱 *Structure Stop:* `{format_price(p['structure_stop'])}` | *ATR:* `{format_price(p['atr'])}`\n"
            f"{targets_text}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Trend:* 4H=`{item['r4']}` / 1H=`{item['r1']}` / 15m=`{item['r15']}` | *RSI:* `{rsi_val}`\n"
            f"💡 *Invalidation:* {p['invalidation']}"
        )

        if send_telegram_alert(msg):
            # Cooldown starts only after a successful Telegram delivery.
            recent_signals[key] = curr_t
            print(f"🔥 [ALERT SENT] -> #{item['symbol']}")
        else:
            print(f"[x] Alert delivery failed; cooldown not started for #{item['symbol']}")

    print("-" * 120)
    return recent_signals


def main(interval: int = 5, limit: int = 40):
    print(f"🤖 Analysis Engine V5 started. Scanning every {interval} min(s).")
    print("🔒 Mode: decision-support only; no order execution code is present.")
    signals: Dict[str, float] = {}
    while True:
        try:
            signals = run_scan_cycle(limit=limit, recent_signals=signals)
        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as exc:
            print(f"[x] Scan cycle error (fail-closed): {exc}")
        time.sleep(interval * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto Futures Analysis Engine V5 (setup scoring + Astra final decision)")
    parser.add_argument("--interval", type=int, default=5, help="Scan interval in minutes")
    parser.add_argument("--limit", type=int, default=40, help="Number of futures symbols to scan")
    args = parser.parse_args()
    main(interval=args.interval, limit=args.limit)
