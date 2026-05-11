"""Long-lived Chromium for the Uber Eats adapter.

The original adapter launched a fresh Playwright + Chromium for every
/api/check request. With Chromium cold-start at ~3-5 s and the rest of
the navigation taking ~7 s, the UE branch dominated request latency.

This pool keeps one Chromium instance running for the lifetime of the
FastAPI app. Each request gets a fresh context (so the per-request
geolocation is honoured), but skips the browser launch and OS-process
spin-up. Measured savings: ~12 s → ~5-7 s per UE call.

Concurrency model: one request at a time per pool. Uber Eats' anti-bot
treats concurrent sessions from the same IP as suspicious. With ≤3
expected users and an idempotent 60 s cache covering bursts, a single
serialised lane is the right trade-off.

Lifecycle: `await pool.start()` on FastAPI startup,
`await pool.shutdown()` on shutdown. If the browser crashes, the next
call transparently relaunches it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import (
    Browser,
    Playwright,
    async_playwright,
)

logger = logging.getLogger(__name__)


CHROMIUM_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


class BrowserPool:
    """Singleton-style warm-browser pool. Use the module-level `pool`."""

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def start(self) -> None:
        """Launch Playwright and Chromium. Idempotent."""
        if self._playwright is not None:
            return
        self._playwright = await async_playwright().start()
        await self._launch_browser()
        self._ready.set()
        logger.info("BrowserPool: Chromium started, pool ready")

    async def shutdown(self) -> None:
        """Close Chromium and stop Playwright. Idempotent."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning("BrowserPool: browser close failed: %s", e)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("BrowserPool: playwright stop failed: %s", e)
            self._playwright = None
        self._ready.clear()
        logger.info("BrowserPool: shut down")

    async def _launch_browser(self) -> None:
        assert self._playwright is not None
        self._browser = await self._playwright.chromium.launch(
            headless=True, args=CHROMIUM_LAUNCH_ARGS,
        )

    def _is_alive(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def get_browser(self) -> Browser:
        """Return a connected Browser instance, relaunching if it crashed.

        Caller is expected to hold the pool's lock for the duration of any
        browser/context/page usage — see `acquire()`.
        """
        if not self._is_alive():
            logger.warning("BrowserPool: browser not alive, relaunching")
            await self._launch_browser()
        assert self._browser is not None
        return self._browser

    def acquire(self) -> "PoolSession":
        """Reserve the browser for one logical operation.

        Usage:
            async with pool.acquire() as browser:
                ctx = await browser.new_context(...)
                ...
        """
        return PoolSession(self)


class PoolSession:
    """Async-context-manager wrapper around the pool's lock + browser."""

    def __init__(self, pool: BrowserPool) -> None:
        self._pool = pool

    async def __aenter__(self) -> Browser:
        await self._pool._ready.wait()
        await self._pool._lock.acquire()
        try:
            return await self._pool.get_browser()
        except Exception:
            self._pool._lock.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._pool._lock.release()


# Module-level instance — there's exactly one Chromium in the process.
pool = BrowserPool()
