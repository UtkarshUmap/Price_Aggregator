"""
Central configuration. Everything tunable lives here, driven by environment
variables so you can change behavior in production without code changes.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- API ---
    API_KEYS: set[str] = set(
        k.strip() for k in os.getenv("API_KEYS", "dev-key-change-me").split(",") if k.strip()
    )
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # --- Cache ---
    DB_PATH: str = os.getenv("DB_PATH", "prices.db")
    # How long a cached price is considered "fresh" (seconds). 6h default —
    # quick-commerce prices don't change minute to minute.
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", str(6 * 3600)))
    # Serve stale data (with a stale flag) if a refresh fails, up to this age.
    STALE_MAX_AGE: int = int(os.getenv("STALE_MAX_AGE", str(48 * 3600)))

    # --- Scraping ---
    # Comma-separated proxy URLs, e.g. "http://user:pass@host:port,..."
    # Residential proxies strongly recommended for these platforms.
    PROXIES: list[str] = [p.strip() for p in os.getenv("PROXIES", "").split(",") if p.strip()]
    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
    NAV_TIMEOUT_MS: int = int(os.getenv("NAV_TIMEOUT_MS", "30000"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    # Minimum delay between scrape jobs per platform (be polite, avoid bans)
    PER_PLATFORM_COOLDOWN: float = float(os.getenv("PER_PLATFORM_COOLDOWN", "5"))
    # Max concurrent browser contexts
    MAX_CONCURRENCY: int = int(os.getenv("MAX_CONCURRENCY", "2"))

    # --- Scheduler ---
    # Auto-refresh tracked products every N seconds (0 disables)
    REFRESH_INTERVAL: int = int(os.getenv("REFRESH_INTERVAL", str(6 * 3600)))

    DEFAULT_PINCODE: str = os.getenv("DEFAULT_PINCODE", "413001")  # Solapur


settings = Settings()
