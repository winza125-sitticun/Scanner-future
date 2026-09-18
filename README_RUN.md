# Auto Scanner V6 - Multi-timeframe Retest Watchlist + V5 Signal Gate

Decision-support scanner only. This package does **not** place exchange orders.

## What changed in V6
V6 keeps the V5 signal gate, then adds a five-coin digest on every scan. It analyzes the 12 most liquid provider symbols, ranks them by setup quality and distance to entry, and sends the best five even when every item is still waiting.

The retest method is deterministic:

- 4H and 1H must agree through confirmed HH/HL or LH/LL structure.
- 15m must close through the latest 1H swing level.
- A confirmed entry requires a later 15m retest/rejection of that level.
- Entry is a zone of `breakout level ± 0.10 ATR(15m)`.
- Stop is beyond the latest opposite 15m swing plus `0.15 ATR(15m)`.
- TP1-TP3 come from the next 1H/4H swing levels.
- If price moves more than `0.50 ATR(15m)` beyond the entry zone, status becomes `WAIT_NO_CHASE`.
- Only breakouts within the latest 20 closed 15m candles are eligible.

Possible digest statuses are `LONG_CONFIRMED`, `SHORT_CONFIRMED`, `WAIT_RETEST`, `WAIT_BREAKOUT`, `WAIT_NO_CHASE`, `NO_SETUP`, and `DATA_UNAVAILABLE`.

The score is a **watchlist quality score, not a win-rate probability**.

## V5 individual signal gate
V5 keeps the V2-V4 fail-closed filters and Structure Engine, then adds a deterministic quality score **before** Astra/Gemini is called.

The score is a **setup quality score, not a win-rate probability**. A score of 84/100 does not mean an 84% chance of profit.

### V5 decision flow
```text
Market-data provider health check
  Binance USD-M -> fallback to Bybit Linear
        |
        v
TradingView 4H + 1H + 15m
        |
        v
Price-basis + deterministic safety filters
        |
        v
Structure Engine
Swing stop + ATR buffer + structure TP + minimum R:R
        |
        v
Setup Score 0-100
        |
        +-- score < 75 / invalid --> WAIT
        |                         Astra is NOT called
        |                         no individual signal alert
        |
        v
Astra Devil's Advocate
APPROVED / WAIT / REJECT
        |
        +-- WAIT / REJECT / low confidence --> no alert
        |
        v
APPROVED + confidence >= threshold
        |
        v
Telegram decision-support alert
```

## Setup Score 0-100
Default gate: `SETUP_MIN_SCORE=75`.

| Component | Max | What it rewards |
|---|---:|---|
| Trend | 20 | 4H/1H/15m alignment; STRONG signals score higher than regular BUY/SELL |
| Strength | 15 | ADX level + directional DI dominance |
| Structure | 30 | TP1 R:R quality plus valid farther TP2/TP3 structure |
| Market | 15 | BTC regime alignment + tight TradingView/provider price basis |
| Funding | 10 | Lower crowding risk for the selected side |
| Timing | 10 | RSI location suitable for a trend-following entry |

The scoring engine is intentionally downstream of the hard safety filters. It cannot rescue a setup that fails 4H/1H/15m alignment, EMA200, ADX/DI, funding, price basis, structure, ATR, or minimum R:R.

## Astra rules
Astra/Gemini is a **Devil's Advocate only**. It cannot:
- create a new setup,
- change LONG to SHORT or SHORT to LONG,
- increase the deterministic score,
- rescue a setup below `SETUP_MIN_SCORE`.

Valid Astra verdicts:
- `APPROVED` - no new blocking risk was found.
- `WAIT` - deterministic score passed, but more confirmation is recommended.
- `REJECT` - material risk invalidates the alert candidate.

Only `APPROVED` with `confidence >= AI_MIN_CONFIDENCE` can reach Telegram. AI errors, malformed JSON, missing API key, WAIT, REJECT, or low confidence all fail closed.

## Market-data layer retained from V4
TradingView remains the signal engine for 4H + 1H + 15m. Market-data providers are selected in this order:

1. Binance USD-M
2. Bybit V5 Linear fallback
3. OKX USDT Swap fallback

A provider must pass a BTC closed-candle health probe before the scan starts. Once selected, the same provider supplies universe, funding, provider last price, and 1H/4H candles for the entire scan cycle. Funding/candles are never silently mixed between exchanges in one cycle.

## Structure Engine retained from V3
- Confirmed swing high/low structure
- ATR stop buffer
- 1H/4H structure targets
- TradingView Classic pivot targets
- Minimum nearest-target R:R gate

## 1) Rotate previously exposed credentials
Any Telegram/Gemini credentials previously pasted into source or chat should be treated as exposed. Revoke/rotate them before running V5.

## 2) Install dependencies
```bash
python -m pip install -r requirements.txt
```

## 3) Set credentials as environment variables
```bash
export TELEGRAM_BOT_TOKEN="NEW_TOKEN"
export TELEGRAM_CHAT_ID="YOUR_CHAT_ID"
export GEMINI_API_KEY="NEW_GEMINI_KEY"
export GEMINI_MODEL="gemini-2.5-flash"
```

Do not put real credentials in source code.

## 4) V5 scoring and Astra settings
```bash
export SETUP_MIN_SCORE=75
export AI_MIN_CONFIDENCE=75
```

`SETUP_MIN_SCORE` controls the deterministic quality gate. `AI_MIN_CONFIDENCE` applies only after a setup has already passed that gate.

## 5) Deterministic / market-data settings
```bash
export ADX_MIN_THRESHOLD=20.0
export MAX_LONG_FUNDING=0.0300
export MIN_SHORT_FUNDING=-0.0400
export ALERT_COOLDOWN_SECONDS=3600
export HTTP_TIMEOUT_SECONDS=8

export MARKET_PRICE_BASIS_MAX_DIFF_PCT=1.0
export MARKET_PROVIDER_PROBE_KLINE_LIMIT=20
```

## 6) Structure settings
```bash
export STRUCTURE_KLINE_LIMIT=120
export STRUCTURE_SWING_WINDOW=2
export STRUCTURE_ATR_PERIOD=14
export STRUCTURE_ATR_BUFFER_MULT=0.25
export MIN_STRUCTURE_RR=1.5
export WATCHLIST_SIZE=5
export WATCHLIST_CANDIDATE_LIMIT=12
```

## 7) Run tests
```bash
python -m unittest discover -v
```

## 8) Run scanner
```bash
python auto_scanner_v5.py --interval 5 --limit 40
```

## Telegram output
Every scan sends the ranked retest watchlist. Approved individual alerts still include both deterministic and Astra layers, for example:

```text
Setup Score: 84/100
Trend      18/20
Strength   13/15
Structure  27/30
Market     14/15
Funding     6/10
Timing      6/10

Astra: APPROVED
Confidence: 82%
Final Decision: LONG
```

## Fail-closed examples
V6 does not send an individual trade signal when any of these occurs. The five-coin digest is still sent with a waiting/error status when provider data is sufficient to start the cycle:
- no healthy market-data provider,
- missing or stale critical provider data,
- TradingView/provider basis exceeds tolerance,
- 4H/1H/15m alignment fails,
- EMA200 / ADX / DI / funding hard filter fails,
- ATR, swing stop, structure target, or minimum R:R is unavailable,
- scoring input is missing/invalid,
- Setup Score is below the configured threshold,
- Astra returns WAIT or REJECT,
- Astra confidence is below threshold,
- Astra/API response is unavailable or malformed,
- Telegram delivery fails.

Cooldown starts only after a successful Telegram delivery.

## Intentionally excluded
V5 contains no live/testnet order execution, order placement, leverage selection, position sizing, automatic position management, or exchange trading credentials.
