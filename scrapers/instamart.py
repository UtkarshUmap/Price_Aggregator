"""
Swiggy Instamart scraper — same maintenance rules:
only SEARCH_URL and SELECTORS should ever need edits.

Note: Instamart is the most aggressive of the three about location and
bot detection. Expect this one to need proxies soonest at any real volume.
"""
from playwright.async_api import BrowserContext

from .base import BaseScraper
from .blinkit import _text, _attr

SEARCH_URL = "https://www.swiggy.com/instamart/search?custom_back=true&query={query}"

SELECTORS = {
    "card": "div[data-testid='default_container_ux4']",
    "name": "div.novMV, div[class*='sc-'][class*='Name']",
    "price": "div[data-testid='item-offer-price'], div[data-testid='itemOfferPrice']",
    "mrp": "div[data-testid='item-mrp-price']",
    "quantity": "div[data-testid='item-quantity']",
    "image": "img",
    "out_of_stock": "div:has-text('Sold Out')",
}


class InstamartScraper(BaseScraper):
    platform = "instamart"

    async def _scrape(self, ctx: BrowserContext, query: str, pincode: str) -> list[dict]:
        page = await ctx.new_page()

        await page.goto("https://www.swiggy.com/instamart", wait_until="domcontentloaded")
        try:
            await page.locator("div[data-testid='set-gps-button'], span:has-text('Setup your delivery location')").first.click(timeout=8000)
            search = page.locator("input[placeholder*='search for area']")
            await search.first.fill(pincode, timeout=8000)
            await page.wait_for_timeout(1500)
            await page.locator("div[data-testid='address-item'], div[class*='icon-search']").first.click(timeout=8000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        await page.goto(SEARCH_URL.format(query=query), wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)

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
