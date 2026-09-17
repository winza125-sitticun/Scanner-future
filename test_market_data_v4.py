import unittest

from market_data_v5 import (
    MarketDataSnapshot,
    parse_bybit_tickers,
    parse_bybit_klines,
    parse_okx_klines,
    parse_okx_tickers,
    price_basis_within_tolerance,
    select_market_data_session,
)


class FakeProvider:
    def __init__(self, name, snapshot=None, probe_candles=None):
        self.name = name
        self._snapshot = snapshot
        self._probe_candles = probe_candles if probe_candles is not None else []
        self.snapshot_calls = 0
        self.kline_calls = []

    def get_snapshot(self, limit):
        self.snapshot_calls += 1
        return self._snapshot

    def get_klines(self, symbol, interval, limit):
        self.kline_calls.append((symbol, interval, limit))
        return self._probe_candles


class TestBybitNormalization(unittest.TestCase):
    def test_tickers_normalize_universe_funding_and_last_price(self):
        payload = {
            "retCode": 0,
            "result": {
                "category": "linear",
                "list": [
                    {"symbol": "SOLUSDT", "turnover24h": "900", "fundingRate": "0.0002", "lastPrice": "150"},
                    {"symbol": "BTCUSDT", "turnover24h": "5000", "fundingRate": "0.0001", "lastPrice": "60000"},
                    {"symbol": "ETHUSDT", "turnover24h": "2000", "fundingRate": "-0.00005", "lastPrice": "3000"},
                    {"symbol": "BTCUSDC", "turnover24h": "99999", "fundingRate": "0.0001", "lastPrice": "60001"},
                ],
            },
        }
        snap = parse_bybit_tickers(payload, limit=2)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.provider, "BYBIT")
        self.assertEqual(snap.symbols, ["BTCUSDT", "ETHUSDT"])
        self.assertAlmostEqual(snap.funding_pct["BTCUSDT"], 0.01)
        self.assertAlmostEqual(snap.funding_pct["ETHUSDT"], -0.005)
        self.assertEqual(snap.last_price["BTCUSDT"], 60000.0)

    def test_malformed_ticker_payload_fails_closed(self):
        self.assertIsNone(parse_bybit_tickers({"retCode": 10001}, limit=2))
        self.assertIsNone(parse_bybit_tickers({"retCode": 0, "result": {"list": []}}, limit=2))


class TestBybitKlines(unittest.TestCase):
    def test_reverse_sorted_rows_are_normalized_oldest_first_and_open_candle_is_dropped(self):
        # 60-minute candles. now=10,800,000; candle starting at 7,200,000 is still open until 10,800,000.
        payload = {
            "retCode": 0,
            "result": {
                "list": [
                    ["7200000", "102", "108", "101", "107", "11", "1100"],
                    ["3600000", "100", "105", "95", "102", "10", "1000"],
                    ["0", "98", "103", "97", "100", "9", "900"],
                ]
            },
        }
        candles = parse_bybit_klines(payload, interval="1h", now_ms=10_799_999)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["open_time"], 0)
        self.assertEqual(candles[1]["open_time"], 3_600_000)
        self.assertEqual(candles[1]["close"], 102.0)

    def test_unsupported_interval_or_bad_payload_fails_closed(self):
        self.assertEqual(parse_bybit_klines({"retCode": 0, "result": {"list": []}}, "7h", now_ms=1), [])
        self.assertEqual(parse_bybit_klines({"retCode": 1}, "1h", now_ms=1), [])


class TestOkxNormalization(unittest.TestCase):
    def test_usdt_swaps_are_ranked_by_quote_volume_with_funding(self):
        payload = {
            "code": "0",
            "data": [
                {"instId": "ETH-USDT-SWAP", "last": "3000", "volCcy24h": "2"},
                {"instId": "BTC-USDT-SWAP", "last": "60000", "volCcy24h": "1"},
                {"instId": "BTC-USD-SWAP", "last": "60001", "volCcy24h": "999"},
                {"instId": "USDC-USDT-SWAP", "last": "1", "volCcy24h": "99999"},
            ],
        }
        funding = {
            "BTC-USDT-SWAP": "0.0001",
            "ETH-USDT-SWAP": "-0.00005",
        }

        snapshot = parse_okx_tickers(payload, funding, limit=2)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.provider, "OKX")
        self.assertEqual(snapshot.symbols, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(snapshot.last_price["BTCUSDT"], 60000.0)
        self.assertAlmostEqual(snapshot.funding_pct["BTCUSDT"], 0.01)
        self.assertAlmostEqual(snapshot.funding_pct["ETHUSDT"], -0.005)

    def test_missing_funding_or_malformed_payload_fails_closed(self):
        payload = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "last": "60000", "volCcy24h": "1"}],
        }
        self.assertIsNone(parse_okx_tickers(payload, {}, limit=1))
        self.assertIsNone(parse_okx_tickers({"code": "1", "data": []}, {}, limit=1))


class TestOkxKlines(unittest.TestCase):
    def test_reverse_sorted_rows_are_normalized_and_unconfirmed_candle_is_dropped(self):
        payload = {
            "code": "0",
            "data": [
                ["7200000", "102", "108", "101", "107", "11", "1", "1100", "0"],
                ["3600000", "100", "105", "95", "102", "10", "1", "1000", "1"],
                ["0", "98", "103", "97", "100", "9", "1", "900", "1"],
            ],
        }

        candles = parse_okx_klines(payload, interval="1h")

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["open_time"], 0)
        self.assertEqual(candles[1]["open_time"], 3_600_000)
        self.assertEqual(candles[1]["close"], 102.0)


class TestPriceBasisGuard(unittest.TestCase):
    def test_accepts_small_difference_and_rejects_large_or_missing_prices(self):
        self.assertTrue(price_basis_within_tolerance(100.0, 100.5, max_diff_pct=1.0))
        self.assertFalse(price_basis_within_tolerance(100.0, 102.0, max_diff_pct=1.0))
        self.assertFalse(price_basis_within_tolerance(None, 100.0, max_diff_pct=1.0))
        self.assertFalse(price_basis_within_tolerance(100.0, None, max_diff_pct=1.0))


class TestProviderSelection(unittest.TestCase):
    def _snapshot(self, provider):
        return MarketDataSnapshot(
            provider=provider,
            symbols=["BTCUSDT", "ETHUSDT"],
            funding_pct={"BTCUSDT": 0.01, "ETHUSDT": 0.01},
            last_price={"BTCUSDT": 60000.0, "ETHUSDT": 3000.0},
        )

    def test_falls_back_as_a_whole_when_primary_probe_has_insufficient_history(self):
        primary = FakeProvider("BINANCE", snapshot=self._snapshot("BINANCE"), probe_candles=[{"close": 1}] * 5)
        fallback = FakeProvider("BYBIT", snapshot=self._snapshot("BYBIT"), probe_candles=[{"close": 1}] * 15)

        session = select_market_data_session(
            [primary, fallback], limit=2, probe_kline_limit=20, min_probe_candles=15
        )

        self.assertIsNotNone(session)
        self.assertEqual(session.provider.name, "BYBIT")
        self.assertEqual(session.snapshot.provider, "BYBIT")
        self.assertEqual(primary.kline_calls, [("BTCUSDT", "1h", 20)])
        self.assertEqual(fallback.kline_calls, [("BTCUSDT", "1h", 20)])

    def test_no_healthy_provider_returns_none(self):
        broken_a = FakeProvider("BINANCE", snapshot=None, probe_candles=[])
        broken_b = FakeProvider("BYBIT", snapshot=self._snapshot("BYBIT"), probe_candles=[])
        self.assertIsNone(select_market_data_session([broken_a, broken_b], limit=2, probe_kline_limit=20, min_probe_candles=15))


if __name__ == "__main__":
    unittest.main()
