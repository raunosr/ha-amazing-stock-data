"""Public Avanza market history. No account, trading, or credential endpoints."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import math
import re
import time

import aiohttp

from .const import PERIODS

BASE_URL = "https://www.avanza.se/_api"
MAX_BYTES = 4_000_000
MAX_POINTS = 20_000


class HistoryError(Exception):
    """A safe error code for clients; never contains upstream response bodies."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def finite_number(value):
    """JSON booleans and strings are not prices or timestamps."""
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def parse_chart(payload: dict, metadata: dict, period: str, now: float) -> dict:
    """Normalize actual source samples, retaining explicit missing closes as gaps."""
    rows = payload.get("ohlc")
    if not isinstance(rows, list) or len(rows) > MAX_POINTS:
        raise HistoryError("invalid_response")
    points = {}
    for row in rows:
        if not isinstance(row, dict):
            raise HistoryError("invalid_response")
        timestamp, value = row.get("timestamp"), row.get("close")
        if not finite_number(timestamp) or not 0 < timestamp <= now * 1000:
            continue
        points[timestamp] = {"time": timestamp, "value": value if finite_number(value) else None}
    ordered = sorted(points.values(), key=lambda point: point["time"])
    valid = [point for point in ordered if point["value"] is not None]
    details = payload.get("metadata")
    details = details.get("resolution") if isinstance(details, dict) else None
    resolution = details.get("chartResolution", "") if isinstance(details, dict) else ""
    if not isinstance(resolution, str):
        resolution = ""
    if resolution not in {"minute", "two_minutes", "five_minutes", "ten_minutes", "thirty_minutes", "hour", "day", "week", "month", "quarter"}:
        resolution = ""
    # Chart endpoints are actual samples, never a synthetic current-price point.
    start = ordered[0]["time"] if ordered else now * 1000
    end = ordered[-1]["time"] if ordered else now * 1000
    days = {"week": 7, "month": 30, "year": 365, "five_years": 1826, "ten_years": 3653}.get(period)
    tolerance = 7 if resolution == "week" else 3
    partial = bool(days and valid and valid[0]["time"] > (now - (days - tolerance) * 86400) * 1000)
    first, last = (valid[0]["value"], valid[-1]["value"]) if len(valid) >= 2 else (None, None)
    change = last - first if first is not None else None
    return {
        "provider": "avanza", "source": "Avanza", "period": period,
        "points": ordered, "start": start, "end": end,
        "fetched_at": int(now * 1000), "partial": partial,
        "resolution": resolution, "adjustment": "source_unspecified",
        "change": {"value": change, "percent": change / first * 100 if first else None},
        "instrument": metadata,
    }


def parse_instrument(payload: dict, instrument_id: str) -> dict:
    listing = payload.get("listing")
    if not isinstance(listing, dict) or str(payload.get("orderbookId")) != instrument_id:
        raise HistoryError("invalid_instrument")
    def text(value):
        return value[:200] if isinstance(value, str) else ""
    currency = text(listing.get("currency"))
    if not currency:
        raise HistoryError("invalid_instrument")
    return {
        "id": instrument_id, "name": text(payload.get("name")),
        "symbol": text(listing.get("tickerSymbol") or listing.get("shortName")),
        "currency": currency, "market": text(listing.get("marketPlaceName")),
        "kind": text(payload.get("type")).lower(),
    }


class AvanzaHistory:
    """Shared bounded cache with request coalescing, timeouts and rate backoff."""

    def __init__(self, session: aiohttp.ClientSession, clock=time.monotonic):
        self._session = session
        self._clock = clock
        self._cache = OrderedDict()
        self._pending = {}
        self._semaphore = asyncio.Semaphore(3)
        self._blocked_until = 0
        self._closed = False

    async def close(self):
        self._closed = True
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._cache.clear()

    async def _cached(self, key, ttl, factory):
        if self._closed:
            raise HistoryError("not_loaded")
        cached = self._cache.get(key)
        if cached and cached[0] > self._clock():
            self._cache.move_to_end(key)
            if isinstance(cached[1], HistoryError):
                raise HistoryError(cached[1].code)
            return cached[1]
        if key not in self._pending:
            if len(self._pending) >= 16:
                raise HistoryError("busy")
            async def run():
                try:
                    try:
                        value = await factory()
                        expiry = ttl
                    except HistoryError as error:
                        value, expiry = error, 60
                    self._cache[key] = (self._clock() + expiry, value)
                    self._cache.move_to_end(key)
                    while len(self._cache) > 256:
                        self._cache.popitem(last=False)
                    return value
                finally:
                    self._pending.pop(key, None)
            self._pending[key] = asyncio.create_task(run())
        value = await asyncio.shield(self._pending[key])
        if isinstance(value, HistoryError):
            raise HistoryError(value.code)
        return value

    async def _json(self, path, params=None):
        async with self._semaphore:
            if self._clock() < self._blocked_until:
                raise HistoryError("rate_limited")
            try:
                async with self._session.get(
                    f"{BASE_URL}/{path}", params=params, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=20),
                    headers={"Accept": "application/json", "User-Agent": "AmazingStockData/0.1.0"},
                ) as response:
                    if response.status == 429:
                        self._blocked_until = self._clock() + 300
                        raise HistoryError("rate_limited")
                    if response.status != 200:
                        raise HistoryError("source_unavailable")
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise HistoryError("invalid_response")
                    payload = json.loads(data)
                    if not isinstance(payload, dict):
                        raise HistoryError("invalid_response")
                    return payload
            except (aiohttp.ClientError, TimeoutError) as error:
                raise HistoryError("source_unavailable") from error
            except (ValueError, UnicodeError) as error:
                raise HistoryError("invalid_response") from error

    async def history(self, instrument_id: str, period: str) -> dict:
        if not re.fullmatch(r"[0-9]{1,12}", instrument_id) or period not in PERIODS:
            raise HistoryError("invalid_request")
        async def fetch():
            async def instrument():
                return parse_instrument(await self._json(f"market-guide/stock/{instrument_id}"), instrument_id)
            metadata = await self._cached((instrument_id, "metadata"), 86400, instrument)
            window, resolution = PERIODS[period]
            params = {"timePeriod": window}
            if resolution:
                params["resolution"] = resolution
            payload = await self._json(f"price-chart/stock/{instrument_id}", params)
            return parse_chart(payload, metadata, period, time.time())
        ttl = 3600 if period in {"year", "five_years", "ten_years", "max"} else 300
        return await self._cached((instrument_id, period), ttl, fetch)
