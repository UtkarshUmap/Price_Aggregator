# Price Scraper API

A self-hosted API that scrapes live prices from Blinkit, Zepto, and Swiggy Instamart, caches them, and serves them to your e-commerce site. Built so your website **never waits on a scrape** and **never breaks when a platform blocks you**.

> ⚠️ Scraping these platforms is against their terms of service, and they actively fight bots. This project isolates that fragility: your site only ever talks to your own stable API, and platform breakage is fixed by editing one small `SELECTORS` dict per site.

## Architecture

```
Your website ──X-API-Key──> FastAPI (/prices, /refresh, /track)
                                │
                    SQLite cache (fresh? serve instantly)
                                │ stale/missing → background job
                    Scraper layer (Playwright, shared browser)
                      • retries + exponential backoff
                      • proxy rotation + mobile fingerprints
                      • per-platform cooldown, concurrency cap
                      • in-flight dedup (1 scrape per query, ever)
                                │
                  blinkit.py / zepto.py / instamart.py
                  (ONLY files you edit when a site changes)
```

Key behaviors:
- `GET /prices` — returns cached prices **instantly**; if stale, quietly refreshes in the background. Response includes `stale` and `age_seconds` so your UI can show "as of 2h ago".
- `POST /refresh` — blocking live scrape for the admin "Refresh now" button. If a platform blocks us, you still get the last known price plus an error message.
- `POST /track` — products on the tracked list are auto-refreshed every `REFRESH_INTERVAL` seconds, so admin pages are always warm.
- `POST /bulk` — mass-scrape your whole catalog. Send `{"queries": ["amul butter", "tata salt", ...], "track": true}` and get back a `job_id` instantly. The queue works through products politely (sequential per platform, platforms in parallel) so you don't get IP-banned. Poll `GET /bulk/{job_id}` for progress + ETA; cancel with `DELETE /bulk/{job_id}`. Results are cached as they complete.
- `GET /prices/bulk?queries=amul butter|tata salt|maggi` — read cached prices for hundreds of products in ONE call (pipe-separated). This is what renders your admin price table.
- `GET /health` — recent scrape success rate, so you notice breakage before your users do.

### Bulk workflow (recommended for a full catalog)

```bash
# 1. kick off a mass scrape of your catalog, and auto-track everything
curl -X POST -H "X-API-Key: k" -H "Content-Type: application/json" \
  -d '{"queries":["amul butter","tata salt","maggi noodles"],"track":true}' \
  http://localhost:8000/bulk
# -> {"job_id":"a1b2c3", "progress":{"done":0,"total":9}, ...}

# 2. poll progress (shows percent + ETA + per-product errors)
curl -H "X-API-Key: k" http://localhost:8000/bulk/a1b2c3

# 3. render the whole price table from cache in one call
curl -H "X-API-Key: k" \
  "http://localhost:8000/prices/bulk?queries=amul%20butter|tata%20salt|maggi%20noodles"
```

Timing expectation: ~15–20s per product per platform, so 100 products ≈ 30–40 min. Because `track: true` puts them on the auto-refresh schedule, this long scrape happens **once** — after that the cache is always warm and bulk reads are instant.


## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps

cp .env.example .env        # then EDIT .env: set a strong API_KEYS value!
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Try it:
```bash
curl -H "X-API-Key: your-key" "http://localhost:8000/prices?query=amul%20butter&pincode=413001"
```

## Integrating with your website (Node example)

```js
// server-side only — never expose the API key in browser JS
const r = await fetch(
  `http://scraper-host:8000/prices?query=${encodeURIComponent(name)}&pincode=413001`,
  { headers: { "X-API-Key": process.env.SCRAPER_KEY } }
);
const { data } = await r.json();
// data.blinkit.results[0].price, data.blinkit.stale, data.blinkit.age_seconds
```

Admin "Refresh now" button → `POST /refresh?query=...` (may take 30–90s).

## Deployment (cheapest sane option)

Any ₹400–800/month VPS (Hetzner/DigitalOcean/Oracle free tier) with 2 GB RAM
runs this fine at store scale. Docker:

```bash
docker build -t price-scraper .
docker run -d --restart=always -p 8000:8000 --env-file .env \
  -v $(pwd)/data:/data -e DB_PATH=/data/prices.db price-scraper
```

Put nginx/Caddy in front for HTTPS. Do **not** expose it without the API key set.

## When a platform breaks (it will)

1. `GET /health` shows the success rate dropping for one platform.
2. On a machine with a display run:
   `HEADLESS=false python -m scrapers.debug blinkit "amul butter" 413001`
3. Look at `debug_blinkit.png` / `.html`, open the real site in devtools,
   find the new product-card markup.
4. Update `SEARCH_URL` / `SELECTORS` in that one file. Done.

If you start getting blocked even with correct selectors (CAPTCHAs, empty
pages), add 2–3 **Indian residential proxies** to `PROXIES` in `.env`
(₹300–1000/month from most providers at this tiny volume). Instamart will
need this first.

## Keeping costs minimal

- Cache TTL of 6h + tracked-product auto-refresh means ~4 scrapes/product/day.
- 100 tracked products ≈ 1,200 page loads/day total — small enough that a
  single cheap VPS and (maybe) a starter proxy plan covers everything.
- Never call `/refresh` from customer-facing pages; only `/prices`.
