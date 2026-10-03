"""
Bulk scrape jobs.

Why a queue and not "scrape everything in parallel"?
Blasting 100+ searches at Blinkit/Zepto simultaneously is the fastest way to
get IP-banned. So a bulk job works through products SEQUENTIALLY per platform
(platforms run in parallel with each other — they're different sites), and
respects the same per-platform cooldown as single scrapes.

Rough timing: ~15–20s per product per platform. 100 products ≈ 30–40 min per
platform, all three platforms finishing around the same time since they run
side by side. Results are written to the price cache AS THEY COMPLETE, so
`GET /prices/bulk` shows partial data immediately — no need to wait for the
whole job.

Jobs are held in memory (fine for a single-process deployment). A restart
loses job *progress objects*, but never the scraped data — that's in SQLite.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class BulkJob:
    id: str
    queries: list[str]
    pincode: str
    platforms: list[str]
    created_at: float = field(default_factory=time.time)
    # (platform, query) -> "pending" | "ok" | "error: ..."
    status: dict = field(default_factory=dict)
    done: int = 0
    total: int = 0
    finished_at: float | None = None
    cancelled: bool = False

    def to_api(self) -> dict:
        errors = {f"{p}:{q}": s for (p, q), s in self.status.items()
                  if isinstance(s, str) and s.startswith("error")}
        return {
            "job_id": self.id,
            "state": ("cancelled" if self.cancelled
                      else "finished" if self.finished_at
                      else "running"),
            "progress": {"done": self.done, "total": self.total,
                         "percent": round(100 * self.done / self.total, 1) if self.total else 0},
            "eta_seconds": self._eta(),
            "errors": errors,
            "queries": self.queries,
            "platforms": self.platforms,
            "pincode": self.pincode,
        }

    def _eta(self) -> int | None:
        if self.finished_at or not self.done:
            return None
        elapsed = time.time() - self.created_at
        rate = self.done / elapsed
        return int((self.total - self.done) / rate) if rate > 0 else None


JOBS: dict[str, BulkJob] = {}
_MAX_KEPT = 50  # keep the last N job records around for status checks


def create_job(queries: list[str], pincode: str, platforms: list[str]) -> BulkJob:
    job = BulkJob(
        id=uuid.uuid4().hex[:12],
        queries=[q.lower().strip() for q in queries if q.strip()],
        pincode=pincode,
        platforms=platforms,
    )
    job.total = len(job.queries) * len(platforms)
    for p in platforms:
        for q in job.queries:
            job.status[(p, q)] = "pending"
    JOBS[job.id] = job
    # prune old finished jobs
    if len(JOBS) > _MAX_KEPT:
        for jid in sorted(JOBS, key=lambda j: JOBS[j].created_at)[: len(JOBS) - _MAX_KEPT]:
            if JOBS[jid].finished_at:
                JOBS.pop(jid, None)
    return job


async def run_job(job: BulkJob, scrape_one) -> None:
    """scrape_one(platform, query, pincode) -> results (the deduped scraper
    entry point from app.main). Platforms run in parallel; queries within a
    platform run sequentially — the polite pattern."""

    async def platform_worker(platform: str):
        for q in job.queries:
            if job.cancelled:
                return
            try:
                await scrape_one(platform, q, job.pincode)
                job.status[(platform, q)] = "ok"
            except Exception as e:  # noqa: BLE001 — record and continue
                job.status[(platform, q)] = f"error: {str(e)[:200]}"
            finally:
                job.done += 1

    await asyncio.gather(*(platform_worker(p) for p in job.platforms))
    job.finished_at = time.time()
