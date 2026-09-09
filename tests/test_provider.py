"""Offline contract and HTTP tests with synthetic data; no account or market calls."""

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

# Load the provider without importing Home Assistant on development machines.
package = types.ModuleType("stock_test")
package.__path__ = [str(Path(__file__).parents[1] / "custom_components/amazing_stock_data")]
sys.modules["stock_test"] = package
provider = importlib.import_module("stock_test.provider")

NOW = 1_800_000_000
INSTRUMENT = {"orderbookId": "123", "name": "Example", "listing": {"currency": "USD", "tickerSymbol": "EXM", "marketPlaceName": "Example market"}, "type": "STOCK"}


def chart():
    return {"ohlc": [{"timestamp": (NOW - 7 * 86400) * 1000, "close": 20}, {"timestamp": NOW * 1000, "close": 30}], "metadata": {"resolution": {"chartResolution": "week"}}}


class ParserTests(unittest.TestCase):
    def test_actual_endpoints_change_and_listing(self):
        result = provider.parse_chart(chart(), provider.parse_instrument(INSTRUMENT, "123"), "ten_years", NOW)
        self.assertEqual(result["change"], {"value": 10, "percent": 50})
        self.assertTrue(result["partial"])
        self.assertEqual(result["start"], (NOW - 7 * 86400) * 1000)
        self.assertEqual(result["instrument"]["symbol"], "EXM")
        self.assertEqual(result["adjustment"], "source_unspecified")
        self.assertEqual(provider.parse_chart(chart(), {}, "max", NOW)["partial"], False)

    def test_gaps_nonfinite_future_and_duplicates(self):
        data = chart()
        data["ohlc"] = [{"timestamp": 1000, "close": 0}, {"timestamp": 2000, "close": float("nan")}, {"timestamp": 3000, "close": True}, {"timestamp": 4000, "close": 9}, {"timestamp": 4000, "close": 10}, {"timestamp": (NOW + 1) * 1000, "close": 99}]
        result = provider.parse_chart(data, {}, "max", NOW)
        self.assertEqual([p["value"] for p in result["points"]], [0, None, None, 10])
        self.assertIsNone(result["change"]["percent"])
        self.assertEqual(result["change"]["value"], 10)

    def test_invalid_payload_and_instrument(self):
        for value in ({}, {"ohlc": "bad"}, {"ohlc": [None]}, {"ohlc": [{}] * 20001}):
            with self.assertRaises(provider.HistoryError):
                provider.parse_chart(value, {}, "week", NOW)
        for value in ({}, {**INSTRUMENT, "orderbookId": "999"}, {**INSTRUMENT, "listing": {}}):
            with self.assertRaises(provider.HistoryError):
                provider.parse_instrument(value, "123")


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.status = 200
        self.body = None
        self.release = None
        async def handler(request):
            self.requests.append((request.path, dict(request.query)))
            if self.release:
                await self.release.wait()
            if self.body is not None:
                return web.Response(body=self.body, status=self.status)
            return web.json_response(INSTRUMENT if "market-guide" in request.path else chart(), status=self.status)
        app = web.Application()
        app.router.add_get("/{path:.*}", handler)
        self.server = TestServer(app)
        await self.server.start_server()
        self.old_base = provider.BASE_URL
        provider.BASE_URL = str(self.server.make_url("/")).rstrip("/")
        self.session = aiohttp.ClientSession()
        self.clock = 10
        self.client = provider.AvanzaHistory(self.session, clock=lambda: self.clock)

    async def asyncTearDown(self):
        await self.client.close()
        await self.session.close()
        await self.server.close()
        provider.BASE_URL = self.old_base

    async def test_period_mapping_cache_and_metadata_reuse(self):
        result = await self.client.history("123", "ten_years")
        self.assertEqual(result["provider"], "avanza")
        self.assertEqual(self.requests[-1][1], {"timePeriod": "ten_years", "resolution": "week"})
        await self.client.history("123", "ten_years")
        self.assertEqual(len(self.requests), 2)
        await self.client.history("123", "max")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[-1][1]["timePeriod"], "infinity")
        self.clock += 3601
        await self.client.history("123", "max")
        self.assertEqual(len(self.requests), 4)

    async def test_coalescing_and_one_caller_cancellation(self):
        self.release = asyncio.Event()
        first = asyncio.create_task(self.client.history("123", "week"))
        second = asyncio.create_task(self.client.history("123", "week"))
        await asyncio.sleep(.02)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.release.set()
        result = await second
        self.assertEqual(result["instrument"]["id"], "123")
        self.assertEqual(len(self.requests), 2)

    async def test_allowlisted_request_and_negative_cache(self):
        for instrument in ("../123", "https://example.com", "123\n", "", "1" * 13):
            with self.assertRaises(provider.HistoryError):
                await self.client.history(instrument, "week")
        self.assertEqual(self.requests, [])
        self.status = 503
        for _ in range(2):
            with self.assertRaises(provider.HistoryError):
                await self.client.history("123", "week")
        self.assertEqual(len(self.requests), 1)

    async def test_rate_backoff_applies_across_instruments(self):
        self.status = 429
        with self.assertRaises(provider.HistoryError):
            await self.client.history("123", "week")
        self.status = 200
        with self.assertRaisesRegex(provider.HistoryError, "rate_limited"):
            await self.client.history("456", "max")
        self.assertEqual(len(self.requests), 1)

    async def test_response_limits_json_and_redirects(self):
        for body, status in ((b"not json", 200), (b"[]", 200), (b"x" * (provider.MAX_BYTES + 1), 200), (b"", 302)):
            self.body, self.status = body, status
            with self.assertRaises(provider.HistoryError):
                await self.client._json("test")

    async def test_cache_bound_and_unload(self):
        async def constant():
            return 1
        for i in range(270):
            await self.client._cached((i, "test"), 300, constant)
        self.assertEqual(len(self.client._cache), 256)
        self.release = asyncio.Event()
        task = asyncio.create_task(self.client.history("123", "max"))
        await asyncio.sleep(.02)
        await self.client.close()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.client._pending, {})
        with self.assertRaisesRegex(provider.HistoryError, "not_loaded"):
            await self.client.history("123", "max")


if __name__ == "__main__":
    unittest.main()
