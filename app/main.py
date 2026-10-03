"""
Price API — the only thing your e-commerce site talks to.

Design principles:
  1. Your website NEVER waits on a live scrape unless it asks to.
     GET /prices returns cache instantly; refreshes happen in background.
  2. Scrapes are deduplicated: 10 users asking for "amul butter" at once
     trigger ONE scrape.
  3. Stale-if-error: if a platform blocks us, we serve the last known price
     with a `stale: true` flag instead of failing your page.

Endpoints (all require X-API-Key header):
  GET  /prices?query=amul+butter&pincode=413001&platforms=blinkit,zepto
       -> cached prices per platform (+ kicks off background refresh if stale)
  POST /refresh?query=...&pincode=...       force a live scrape, wait for it
  POST /track   {query, pincode, platforms} add product to auto-refresh list
  DELETE /track?query=...&pincode=...
  GET  /tracked                              list auto-refreshed products
  GET  /health                               recent scrape success/failures
"""
import asyncio
import contextlib
import time

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from pydantic import BaseModel

from .config import settings
from . import cache
from . import jobs
from scrapers.base import BrowserPool, ScrapeError
from scrapers.blinkit import BlinkitScraper
from scrapers.zepto import ZeptoScraper
from scrapers.instamart import InstamartScraper

SCRAPERS = {
    "blinkit": BlinkitScraper(),
    "zepto": ZeptoScraper(),
    "instamart": InstamartScraper(),
}

# in-flight dedup: (platform, query, pincode) -> Task
_inflight: dict[tuple, asyncio.Task] = {}


def require_key(x_api_key: str = Header(default="")):
    if x_api_key not in settings.API_KEYS:
        raise HTTPException(401, "invalid or missing X-API-Key")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    cache.init_db()
    refresher = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        refresher.cancel()
        await BrowserPool.get().shutdown()


app = FastAPI(title="Price Scraper API", dependencies=[Depends(require_key)],
              lifespan=lifespan)


# ---------- core scrape-with-dedup ----------

async def _scrape_one(platform: str, query: str, pincode: str) -> list:
    key = (platform, query.lower().strip(), pincode)
    if key in _inflight:
        return await _inflight[key]  # piggyback on the running scrape

    async def job():
        try:
            results = await SCRAPERS[platform].search(query, pincode)
            cache.put_cached(platform, query, pincode, results)
            return results
        finally:
            _inflight.pop(key, None)

    task = asyncio.create_task(job())
    _inflight[key] = task
    return await task


# ---------- endpoints ----------

@app.get("/prices")
async def get_prices(
    query: str,
    pincode: str = Query(default=settings.DEFAULT_PINCODE),
    platforms: str = "blinkit,zepto,instamart",
):
    """Instant response from cache. Background refresh if stale."""
    wanted = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    if not wanted:
        raise HTTPException(400, f"platforms must be from {list(SCRAPERS)}")

    out, refreshing = {}, []
    for p in wanted:
        hit = cache.get_cached(p, query, pincode)
        if hit and hit.is_usable:
            out[p] = hit.to_api()
            if not hit.is_fresh:
                refreshing.append(p)
        else:
            out[p] = {"platform": p, "results": [], "stale": True, "age_seconds": None,
                      "note": "no data yet — refresh queued"}
            refreshing.append(p)

    # fire-and-forget background refreshes (deduped)
    for p in refreshing:
        asyncio.ensure_future(_scrape_one(p, query, pincode))

    return {"query": query, "pincode": pincode, "data": out,
            "refreshing": refreshing}


@app.post("/refresh")
async def refresh(
    query: str,
    pincode: str = Query(default=settings.DEFAULT_PINCODE),
    platforms: str = "blinkit,zepto,instamart",
    timeout: int = 90,
):
    """Blocking live scrape — for the admin 'Refresh now' button."""
    wanted = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    results, errors = {}, {}

    async def run(p):
        try:
            results[p] = await asyncio.wait_for(_scrape_one(p, query, pincode), timeout)
        except (ScrapeError, asyncio.TimeoutError) as e:
            errors[p] = str(e)
            hit = cache.get_cached(p, query, pincode)
            if hit and hit.is_usable:
                results[p] = hit.results  # stale fallback

    await asyncio.gather(*(run(p) for p in wanted))
    return {"query": query, "pincode": pincode, "results": results, "errors": errors}


class TrackBody(BaseModel):
    query: str
    pincode: str = settings.DEFAULT_PINCODE
    platforms: list[str] = ["blinkit", "zepto", "instamart"]


# ---------- bulk scraping ----------

class BulkBody(BaseModel):
    queries: list[str]                    # e.g. ["amul butter", "tata salt", ...]
    pincode: str = settings.DEFAULT_PINCODE
    platforms: list[str] = ["blinkit", "zepto", "instamart"]
    track: bool = False                   # also add all to the auto-refresh list


@app.post("/bulk")
async def bulk_start(body: BulkBody):
    """Start a mass scrape. Returns immediately with a job_id; poll
    GET /bulk/{job_id} for progress. Results land in the cache as they
    complete, readable any time via GET /prices/bulk."""
    wanted = [p for p in body.platforms if p in SCRAPERS]
    if not wanted:
        raise HTTPException(400, f"platforms must be from {list(SCRAPERS)}")
    if not body.queries:
        raise HTTPException(400, "queries must be a non-empty list")
    if len(body.queries) > 500:
        raise HTTPException(400, "max 500 queries per job — split your catalog")

    job = jobs.create_job(body.queries, body.pincode, wanted)
    asyncio.ensure_future(jobs.run_job(job, _scrape_one))

    if body.track:
        for q in job.queries:
            cache.track_product(q, body.pincode, wanted)

    return job.to_api()


@app.get("/bulk/{job_id}")
async def bulk_status(job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job_id (jobs don't survive restarts; data does)")
    return job.to_api()


@app.delete("/bulk/{job_id}")
async def bulk_cancel(job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job_id")
    job.cancelled = True
    return {"ok": True}


@app.get("/prices/bulk")
async def get_prices_bulk(
    queries: str,                          # pipe-separated: amul butter|tata salt
    pincode: str = Query(default=settings.DEFAULT_PINCODE),
    platforms: str = "blinkit,zepto,instamart",
):
    """Read cached prices for MANY products in one call (no scraping).
    Perfect for rendering the whole admin price table at once."""
    wanted = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    qlist = [q.strip() for q in queries.split("|") if q.strip()]
    if len(qlist) > 500:
        raise HTTPException(400, "max 500 queries per call")

    out = {}
    for q in qlist:
        row = {}
        for p in wanted:
            hit = cache.get_cached(p, q, pincode)
            row[p] = hit.to_api() if hit and hit.is_usable else None
        out[q] = row
    return {"pincode": pincode, "count": len(qlist), "data": out}


@app.post("/track")
async def track(body: TrackBody):
    cache.track_product(body.query, body.pincode, body.platforms)
    return {"ok": True}


@app.delete("/track")
async def untrack(query: str, pincode: str = Query(default=settings.DEFAULT_PINCODE)):
    cache.untrack_product(query, pincode)
    return {"ok": True}


@app.get("/tracked")
async def tracked():
    return cache.list_tracked()


@app.get("/health")
async def health():
    log = cache.recent_health()
    ok = sum(1 for r in log if r["ok"])
    return {"recent_success_rate": (ok / len(log)) if log else None, "recent": log[:20]}


# ---------- background scheduler ----------

async def _refresh_loop():
    """Periodically re-scrape all tracked products, slowly and politely."""
    while True:
        await asyncio.sleep(settings.REFRESH_INTERVAL or 3600)
        if not settings.REFRESH_INTERVAL:
            continue
        for item in cache.list_tracked():
            for p in item["platforms"]:
                if p in SCRAPERS:
                    with contextlib.suppress(Exception):
                        await _scrape_one(p, item["query"], item["pincode"])
            await asyncio.sleep(5)  # spread jobs out



