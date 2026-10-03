"""
Base scraper: everything that is NOT platform-specific lives here so the
per-platform files stay tiny and easy to fix when a site changes.

Responsibilities:
  - Playwright browser lifecycle (one shared browser, context per job)
  - Proxy rotation + realistic browser fingerprint
  - Retries with exponential backoff + jitter
  - Per-platform cooldown so we never hammer a site
  - Concurrency limiting
  - Uniform result shape + structured errors
"""
import asyncio
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from playwright.async_api import async_playwright, Browser, BrowserContext

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings
from app.cache import log_scrape

USER_AGENTS = [
    # A small pool of current, common mobile UAs. Mobile pages are usually
    # lighter and these platforms are mobile-first anyway.
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]


@dataclass
class ScrapeError(Exception):
    platform: str
    reason: str

    def __str__(self):
        return f"[{self.platform}] {self.reason}"


class BrowserPool:
    """One Chromium instance shared across scrapers; contexts are cheap."""

    _instance: "BrowserPool | None" = None

    def __init__(self):
        self._pw = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENCY)

    @classmethod
    def get(cls) -> "BrowserPool":
        if cls._instance is None:
            cls._instance = BrowserPool()
        return cls._instance

    async def browser(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._pw is None:
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=settings.HEADLESS,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
            return self._browser

    async def new_context(self) -> BrowserContext:
        browser = await self.browser()
        proxy = None
        if settings.PROXIES:
            url = random.choice(settings.PROXIES)
            proxy = {"server": url}
        ctx = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 390, "height": 844},  # phone-sized
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            geolocation=None,
            proxy=proxy,
        )
        # Basic stealth: hide webdriver flag
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        ctx.set_default_timeout(settings.NAV_TIMEOUT_MS)
        return ctx

    async def shutdown(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()


class BaseScraper(ABC):
    """Subclass this per platform. Implement only `platform` and `_scrape`."""

    platform: str = "base"
    _last_run: dict[str, float] = {}  # per-platform cooldown tracker

    async def search(self, query: str, pincode: str) -> list[dict]:
        """Public entry point: cooldown -> retries -> normalized results."""
        pool = BrowserPool.get()
        last_err: Exception | None = None

        for attempt in range(1, settings.MAX_RETRIES + 1):
            await self._respect_cooldown()
            start = time.time()
            async with pool.semaphore:
                ctx = await pool.new_context()
                try:
                    results = await self._scrape(ctx, query, pincode)
                    results = [self._normalize(r) for r in results if r.get("name")]
                    log_scrape(self.platform, query, True, None, time.time() - start)
                    return results
                except Exception as e:  # noqa: BLE001 — we log and retry
                    last_err = e
                    log_scrape(self.platform, query, False, str(e)[:300], time.time() - start)
                    # Exponential backoff with jitter: 2s, 4s, 8s (+/- 30%)
                    delay = (2 ** attempt) * random.uniform(0.7, 1.3)
                    await asyncio.sleep(delay)
                finally:
                    await ctx.close()

        raise ScrapeError(self.platform, f"failed after {settings.MAX_RETRIES} attempts: {last_err}")

    async def _respect_cooldown(self):
        last = BaseScraper._last_run.get(self.platform, 0)
        wait = settings.PER_PLATFORM_COOLDOWN - (time.time() - last)
        if wait > 0:
            await asyncio.sleep(wait + random.uniform(0, 1))
        BaseScraper._last_run[self.platform] = time.time()

    def _normalize(self, raw: dict) -> dict:
        """Uniform shape regardless of platform quirks."""
        return {
            "platform": self.platform,
            "name": raw.get("name", "").strip(),
            "price": _to_number(raw.get("price")),
            "mrp": _to_number(raw.get("mrp")),
            "quantity": (raw.get("quantity") or "").strip(),
            "in_stock": bool(raw.get("in_stock", True)),
            "image": raw.get("image"),
            "url": raw.get("url"),
        }

    @abstractmethod
    async def _scrape(self, ctx: BrowserContext, query: str, pincode: str) -> list[dict]:
        """Platform-specific logic. Return list of raw dicts with keys:
        name, price, mrp, quantity, in_stock, image, url."""
        ...


def _to_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("₹", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None
