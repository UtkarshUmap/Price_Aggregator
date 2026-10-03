"""
Blinkit scraper.

╔══════════════════════════════════════════════════════════════════╗
║ MAINTENANCE ZONE — when Blinkit breaks, it breaks HERE.           ║
║ Update SEARCH_URL and the SELECTORS dict. Nothing else.           ║
║ To find new selectors: open the search page on your phone/desktop ║
║ devtools, inspect a product card, copy stable-looking attributes. ║
║ Verified against the live site: run `python -m scrapers.debug     ║
║ blinkit "amul butter"` and look at the saved screenshot + HTML.   ║
╚══════════════════════════════════════════════════════════════════╝
"""
from playwright.async_api import BrowserContext

from .base import BaseScraper

SEARCH_URL = "https://blinkit.com/s/?q={query}"

SELECTORS = {
    # Product cards on the search results page. Blinkit uses generated class
    # names, so prefer data-* attributes / structural selectors where possible.
    "card": "div[role='button'][id]",          # product tiles
    "name": "div.tw-text-300, div[class*='Product__ProductName']",
    "price": "div[class*='tw-font-semibold']:has-text('₹'), div[class*='Price']",
    "mrp": "div[class*='line-through']",
    "quantity": "div.tw-text-200, div[class*='Variant']",
    "image": "img",
    "out_of_stock": "div:has-text('Out of Stock')",
}


class BlinkitScraper(BaseScraper):
    platform = "blinkit"

    async def _scrape(self, ctx: BrowserContext, query: str, pincode: str) -> list[dict]:
        page = await ctx.new_page()

        # Blinkit gates content behind a delivery location. Simplest reliable
        # approach: set the location cookie-less way — load home, type the
        # pincode into the location search if the modal appears.
        await page.goto("https://blinkit.com", wait_until="domcontentloaded")
        try:
            loc_input = page.locator("input[name='select-locality'], input[placeholder*='delivery location']")
            await loc_input.first.fill(pincode, timeout=8000)
            await page.wait_for_timeout(1500)
            await page.locator("div[class*='LocationSearchList']").first.click(timeout=8000)
            await page.wait_for_timeout(1500)
        except Exception:
            pass  # location may already be set from a previous context/proxy geo

        await page.goto(SEARCH_URL.format(query=query), wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)  # let the JS app render

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


async def _text(card, selector: str) -> str | None:
    loc = card.locator(selector)
    if await loc.count():
        return (await loc.first.inner_text()).strip()
    return None


async def _attr(card, selector: str, attr: str) -> str | None:
    loc = card.locator(selector)
    if await loc.count():
        return await loc.first.get_attribute(attr)
    return None
