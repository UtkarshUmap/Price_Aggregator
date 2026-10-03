"""
Selector-maintenance helper. When a platform breaks, run:

    python -m scrapers.debug blinkit "amul butter" 413001

It runs the scraper NON-headless (visible browser if you have a display),
prints results, and always dumps `debug_<platform>.png` + `.html` so you can
inspect what the page actually looked like and fix SELECTORS quickly.
"""
import asyncio
import sys

from .base import BrowserPool
from .blinkit import BlinkitScraper
from .zepto import ZeptoScraper
from .instamart import InstamartScraper

SCRAPERS = {
    "blinkit": BlinkitScraper,
    "zepto": ZeptoScraper,
    "instamart": InstamartScraper,
}


async def main():
    from app.cache import init_db
    init_db()
    if len(sys.argv) < 3:
        print(f"usage: python -m scrapers.debug <{'|'.join(SCRAPERS)}> <query> [pincode]")
        sys.exit(1)
    platform, query = sys.argv[1], sys.argv[2]
    pincode = sys.argv[3] if len(sys.argv) > 3 else "413001"

    scraper = SCRAPERS[platform]()

    # Monkey-patch to also save artifacts
    orig = scraper._scrape

    async def wrapped(ctx, q, pc):
        try:
            return await orig(ctx, q, pc)
        finally:
            for page in ctx.pages:
                await page.screenshot(path=f"debug_{platform}.png", full_page=True)
                with open(f"debug_{platform}.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
                print(f"saved debug_{platform}.png / .html")

    scraper._scrape = wrapped

    try:
        results = await scraper.search(query, pincode)
        print(f"\n{len(results)} results:")
        for r in results:
            price = f"₹{r['price']:.2f}" if r["price"] is not None else "  n/a  "
            print(f"  {price:>9}  {r['name'][:60]}  ({r['quantity'] or '?'})")
    finally:
        await BrowserPool.get().shutdown()


if __name__ == "__main__":
    asyncio.run(main())
