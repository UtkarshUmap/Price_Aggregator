"""
SQLite-backed price cache + tracked-products registry.

Why SQLite and not Redis? For a single-store admin tool the write volume is
tiny, SQLite needs zero extra infrastructure, and the data survives restarts.
Swap this module for Redis/Postgres later without touching the API layer —
the interface is just get/put/list functions.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_cache (
    platform    TEXT NOT NULL,
    query       TEXT NOT NULL,
    pincode     TEXT NOT NULL,
    results     TEXT NOT NULL,   -- JSON array of product dicts
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (platform, query, pincode)
);
CREATE TABLE IF NOT EXISTS tracked_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT NOT NULL,
    pincode     TEXT NOT NULL,
    platforms   TEXT NOT NULL,   -- JSON array, e.g. ["blinkit","zepto"]
    created_at  REAL NOT NULL,
    UNIQUE(query, pincode)
);
CREATE TABLE IF NOT EXISTS scrape_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    query       TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    duration_s  REAL,
    at          REAL NOT NULL
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)


@dataclass
class CachedResult:
    platform: str
    query: str
    pincode: str
    results: list
    fetched_at: float

    @property
    def age(self) -> float:
        return time.time() - self.fetched_at

    @property
    def is_fresh(self) -> bool:
        return self.age <= settings.CACHE_TTL

    @property
    def is_usable(self) -> bool:
        return self.age <= settings.STALE_MAX_AGE

    def to_api(self) -> dict:
        d = asdict(self)
        d["age_seconds"] = int(self.age)
        d["stale"] = not self.is_fresh
        return d


def get_cached(platform: str, query: str, pincode: str) -> CachedResult | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM price_cache WHERE platform=? AND query=? AND pincode=?",
            (platform, query.lower().strip(), pincode),
        ).fetchone()
    if not row:
        return None
    return CachedResult(
        platform=row["platform"],
        query=row["query"],
        pincode=row["pincode"],
        results=json.loads(row["results"]),
        fetched_at=row["fetched_at"],
    )


def put_cached(platform: str, query: str, pincode: str, results: list):
    with db() as conn:
        conn.execute(
            """INSERT INTO price_cache (platform, query, pincode, results, fetched_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(platform, query, pincode)
               DO UPDATE SET results=excluded.results, fetched_at=excluded.fetched_at""",
            (platform, query.lower().strip(), pincode, json.dumps(results), time.time()),
        )


def log_scrape(platform: str, query: str, ok: bool, error: str | None, duration: float):
    row = (platform, query, int(ok), error, duration, time.time())
    sql = "INSERT INTO scrape_log (platform, query, ok, error, duration_s, at) VALUES (?,?,?,?,?,?)"
    try:
        with db() as conn:
            conn.execute(sql, row)
    except sqlite3.OperationalError:
        init_db()  # first run / missing table — create and retry once
        with db() as conn:
            conn.execute(sql, row)


# ---- tracked products (for the background auto-refresh scheduler) ----

def track_product(query: str, pincode: str, platforms: list[str]):
    with db() as conn:
        conn.execute(
            """INSERT INTO tracked_products (query, pincode, platforms, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(query, pincode) DO UPDATE SET platforms=excluded.platforms""",
            (query.lower().strip(), pincode, json.dumps(platforms), time.time()),
        )


def untrack_product(query: str, pincode: str):
    with db() as conn:
        conn.execute(
            "DELETE FROM tracked_products WHERE query=? AND pincode=?",
            (query.lower().strip(), pincode),
        )


def list_tracked() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tracked_products ORDER BY id").fetchall()
    return [
        {
            "id": r["id"],
            "query": r["query"],
            "pincode": r["pincode"],
            "platforms": json.loads(r["platforms"]),
        }
        for r in rows
    ]


def recent_health(limit: int = 50) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM scrape_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
