"""
Zepto scraper — same maintenance rules as blinkit.py:
only SEARCH_URL and SELECTORS should ever need edits.
"""
from playwright.async_api import BrowserContext

from .base import BaseScraper
from .blinkit import _text, _attr

SEARCH_URL = "https://www.zeptonow.com/search?query={query}"

SELECTORS = {
    "card": "a[data-testid='product-card']",
    "name": "[data-testid='product-card-name']",
    "price": "[data-testid='product-card-price']",
    "mrp": "p[class*='line-through']",
    "quantity": "[data-testid='product-card-quantity']",
    "image": "img",
    "out_of_stock": "p:has-text('Out of Stock')",
}


class ZeptoScraper(BaseScraper):
    platform = "zepto"

    async def _scrape(self, ctx: BrowserContext, query: str, pincode: str) -> list[dict]:
        page = await ctx.new_page()

        await page.goto("https://www.zeptonow.com", wait_until="domcontentloaded")
        try:
            await page.locator("button:has-text('Select Location'), [data-testid='location-box']").first.click(timeout=8000)
            search = page.locator("input[placeholder*='Search']")
            await search.first.fill(pincode, timeout=8000)
            await page.wait_for_timeout(1500)
            await page.locator("[data-testid='address-search-item']").first.click(timeout=8000)
            try:
                await page.locator("button:has-text('Confirm')").first.click(timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        await page.goto(SEARCH_URL.format(query=query), wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        cards = page.locator(SELECTORS["card"])
        count = min(await cards.count(), 10)
        results = []
        for i in range(count):
            card = cards.nth(i)
            results.append({
                "name": await _text(card, SELECTORS["name"]),
                "price": await _text(card, SELECTORS["price"]),
                "mrp": await _text(card, SELECTORS["mrp"]),
                "quantity": await _text(card, SELECTORS["quantity"]),
                "in_stock": (await card.locator(SELECTORS["out_of_stock"]).count()) == 0,
                "image": await _attr(card, SELECTORS["image"], "src"),
                "url": page.url,
            })
        return results
