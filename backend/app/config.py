"""Pydantic settings loaded from environment / .env. No hardcoded secrets — see docs/backend.md#configpy."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg2://user:password@localhost/tradecalls"

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Universe filter (docs/engine.md, docs/db.md)
    universe_price_floor: float = 50.0
    universe_turnover_floor_cr: float = 10.0  # 20D avg turnover, crores INR

    # Cost model (docs/engine.md#cost-model)
    slippage_bps: float = 5.0
    brokerage_flat: float = 0.0  # many discount brokers: zero-brokerage delivery
    stt_rate: float = 0.001  # 0.1% delivery, both buy and sell side

    # Scheduler
    scheduler_timezone: str = "Asia/Kolkata"
    eod_ingestion_hour: int = 18
    eod_ingestion_minute: int = 0

    # Data client
    yfinance_max_retries: int = 3
    yfinance_backoff_seconds: float = 2.0

    # Angel One SmartAPI — now also the daily OHLCV ingestion source
    # (app/data/angel_one_ohlcv.py), not just LTP display. See
    # docs/engine.md#live-data-angel-one for why: yfinance started failing
    # wholesale (Yahoo blocking/empty responses) on 2026-08-17.
    angel_one_api_key: str = ""
    angel_one_client_code: str = ""
    angel_one_mpin: str = ""  # SmartAPI's generateSession takes MPIN in the password slot for MPIN-only accounts
    angel_one_totp_secret: str = ""  # base32 TOTP seed from SmartAPI portal 2FA setup
    # Angel One's per-second rate limit for historical candle requests isn't
    # publicly documented in detail — this delay between per-symbol
    # getCandleData calls is a conservative guess (~3 req/s), not a verified
    # SLA. Tighten once actual throttling behavior is observed.
    angel_one_candle_request_delay_seconds: float = 0.4

    # LTP cache TTL (seconds) — see docs/api.md GET /api/calls
    ltp_cache_ttl_seconds: int = 60

    # Telegram bot alerts (docs/engine.md#telegram-alerts) — free, Bot API.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # News collection module (app/news/ — free-tier only, docs/engine.md#zero-budget-constraint)
    # Not wired into scoring.py in v1 — standalone collector, see app/news/__init__.py.
    news_api_newsapi_key: str = ""  # newsapi.org free dev tier (100 req/day) — https://newsapi.org
    news_api_gnews_key: str = ""  # gnews.io free tier (100 req/day) — https://gnews.io
    news_fetch_limit: int = 50  # default per-source article cap for fetch_recent()
    news_request_timeout_seconds: float = 10.0
    news_scraper_delay_seconds: float = 1.0  # min delay between requests to a single scraped site

    # CNBC Awaaz scraper (no free official API — see app/news/scraper_cnbc_awaaz.py)
    news_cnbc_awaaz_base_url: str = "https://hindi.cnbctv18.com"
    news_cnbc_awaaz_section_path: str = "/share-market"

    # RSS fallback scraper (see app/news/scraper_rss_fallback.py)
    news_rss_feed_urls: str = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"  # comma-separated


settings = Settings()
