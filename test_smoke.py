"""
Smoke test — runs WITHOUT a real browser by stubbing the scrapers.
Verifies: auth, cache write/read, stale flags, in-flight dedup,
track/untrack, health log, and stale-fallback when a scrape fails.

Run: python test_smoke.py
"""
import asyncio
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["API_KEYS"] = "test-key"
os.environ["REFRESH_INTERVAL"] = "0"

import httpx  # noqa: E402
from app import main, cache  # noqa: E402
from scrapers.base import ScrapeError  # noqa: E402

CALLS = {"count": 0}


class FakeScraper:
    platform = "blinkit"

    async def search(self, query, pincode):
        CALLS["count"] += 1
        await asyncio.sleep(0.2)  # simulate slow scrape (lets dedup kick in)
        return [{"platform": "blinkit", "name": "Amul Butter 100g",
                 "price": 62.0, "mrp": 65.0, "quantity": "100 g",
                 "in_stock": True, "image": None, "url": "https://blinkit.com/s/?q=amul"}]


class FailingScraper:
    platform = "zepto"

    async def search(self, query, pincode):
        raise ScrapeError("zepto", "blocked (simulated)")


async def run():
    cache.init_db()
    main.SCRAPERS = {"blinkit": FakeScraper(), "zepto": FailingScraper()}

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        H = {"X-API-Key": "test-key"}

        # 1. auth required
        r = await c.get("/prices", params={"query": "amul butter"})
        assert r.status_code == 401, r.text
        print("PASS auth: rejects missing API key")

        # 2. first call: no cache yet -> queued note, background refresh fires
        r = await c.get("/prices", params={"query": "amul butter", "platforms": "blinkit"}, headers=H)
        body = r.json()
        assert body["data"]["blinkit"]["results"] == []
        assert "blinkit" in body["refreshing"]
        print("PASS cold cache: instant empty response + refresh queued")

        await asyncio.sleep(0.5)  # let the background scrape finish

        # 3. second call: served from cache, fresh
        r = await c.get("/prices", params={"query": "amul butter", "platforms": "blinkit"}, headers=H)
        d = r.json()["data"]["blinkit"]
        assert d["results"][0]["price"] == 62.0
        assert d["stale"] is False
        print(f"PASS cache hit: price ₹{d['results'][0]['price']}, stale={d['stale']}")

        # 4. in-flight dedup: 5 concurrent refreshes for a NEW query -> 1 scrape
        before = CALLS["count"]
        await asyncio.gather(*[
            c.post("/refresh", params={"query": "tata salt", "platforms": "blinkit"}, headers=H)
            for _ in range(5)
        ])
        assert CALLS["count"] == before + 1, f"expected 1 scrape, got {CALLS['count'] - before}"
        print("PASS dedup: 5 concurrent requests -> 1 scrape")

        # 5. failing platform returns error but doesn't crash the API
        r = await c.post("/refresh", params={"query": "maggi", "platforms": "zepto"}, headers=H)
        body = r.json()
        assert "zepto" in body["errors"]
        print(f"PASS failure isolation: error surfaced -> {body['errors']['zepto'][:50]}")

        # 6. stale fallback: seed old cache for zepto, then fail a refresh
        cache.put_cached("zepto", "maggi", "413001", [{"name": "Maggi", "price": 14.0}])
        r = await c.post("/refresh", params={"query": "maggi", "platforms": "zepto"}, headers=H)
        body = r.json()
        assert body["results"]["zepto"][0]["price"] == 14.0
        print("PASS stale-if-error: last known price served when platform blocks us")

        # 7. track / list / untrack
        await c.post("/track", json={"query": "amul butter", "platforms": ["blinkit"]}, headers=H)
        r = await c.get("/tracked", headers=H)
        assert len(r.json()) == 1
        await c.request("DELETE", "/track", params={"query": "amul butter"}, headers=H)
        r = await c.get("/tracked", headers=H)
        assert len(r.json()) == 0
        print("PASS tracked products: add/list/remove")

        # 8. health endpoint reflects scrape log
        r = await c.get("/health", headers=H)
        assert r.json()["recent"] is not None
        print("PASS health endpoint")

        # 9. bulk job: submit 3 products, poll to completion, read bulk cache
        r = await c.post("/bulk", json={
            "queries": ["parle g", "tata salt", "maggi noodles"],
            "platforms": ["blinkit"], "track": True,
        }, headers=H)
        job = r.json()
        assert job["state"] == "running" and job["progress"]["total"] == 3
        for _ in range(50):
            await asyncio.sleep(0.2)
            r = await c.get(f"/bulk/{job['job_id']}", headers=H)
            if r.json()["state"] == "finished":
                break
        st = r.json()
        assert st["state"] == "finished" and st["progress"]["done"] == 3, st
        print(f"PASS bulk job: 3 products scraped, {st['progress']['percent']}% done")

        r = await c.get("/prices/bulk",
                        params={"queries": "parle g|tata salt|maggi noodles",
                                "platforms": "blinkit"}, headers=H)
        data = r.json()["data"]
        assert all(data[q]["blinkit"] is not None for q in data), data
        print("PASS bulk read: all 3 products served from cache in one call")

        r = await c.get("/tracked", headers=H)
        assert len(r.json()) == 3
        print("PASS bulk track: all 3 auto-refresh entries created")

        # 10. bulk job errors are isolated (failing platform doesn't kill job)
        r = await c.post("/bulk", json={"queries": ["oreo"], "platforms": ["zepto"]}, headers=H)
        jid = r.json()["job_id"]
        for _ in range(80):
            await asyncio.sleep(0.2)
            st = (await c.get(f"/bulk/{jid}", headers=H)).json()
            if st["state"] == "finished":
                break
        assert st["state"] == "finished" and st["errors"], st
        print("PASS bulk failure isolation: job finishes, error recorded per product")

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(run())
