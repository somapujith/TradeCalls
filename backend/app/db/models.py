"""SQLAlchemy ORM models for the v1 daily-bar backtest schema — see docs/db.md."""
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

# Postgres gets a real BIGINT PK; SQLite gets plain INTEGER so it keeps its
# rowid-aliasing autoincrement behavior (a bare BigInteger PK on SQLite maps
# to BIGINT, which breaks that aliasing and autoincrement silently fails —
# only matters for the sqlite-backed test suite, prod is Neon/Postgres).
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class Stock(Base):
    """NSE symbol master — docs/db.md `stocks`."""

    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    listing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    daily_bars: Mapped[list["DailyOHLCV"]] = relationship(back_populates="stock")


class DailyOHLCV(Base):
    """Daily OHLCV, adjusted for splits/dividends. No forward-fill — docs/db.md."""

    __tablename__ = "daily_ohlcv"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_daily_ohlcv_stock_date"),
        Index("ix_daily_ohlcv_stock_date", "stock_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    adjusted_close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)

    stock: Mapped["Stock"] = relationship(back_populates="daily_bars")


class TechnicalIndicator(Base):
    """EMA/SMA/RSI/MACD/ATR/Bollinger per symbol per date — docs/db.md."""

    __tablename__ = "technical_indicators"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_indicators_stock_date"),
        Index("ix_indicators_stock_date", "stock_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ema9: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    ema20: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    ema50: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    sma100: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    sma200: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    rsi: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    macd: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    macd_signal: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    atr: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    bb_upper: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    bb_lower: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)


class SupportResistanceLevel(Base):
    """Computed levels + resistance clusters per symbol per date — docs/db.md."""

    __tablename__ = "support_resistance_levels"
    __table_args__ = (Index("ix_srlevels_stock_date", "stock_id", "trade_date"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    level_type: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. PRIOR_DAY_HIGH, SWING_LOW, RESISTANCE_CLUSTER
    price: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    strength: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)  # cluster strength / touch count


class BreakoutCandidate(Base):
    """Symbols currently in a non-terminal state, either setup type — docs/db.md."""

    __tablename__ = "breakout_candidates"
    __table_args__ = (
        UniqueConstraint("stock_id", "setup_type", name="uq_candidate_stock_setup"),
        Index("ix_candidates_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(16), nullable=False)  # BREAKOUT | DIP_BUY
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BreakoutEvent(Base):
    """State-machine transition log, append-only — docs/db.md."""

    __tablename__ = "breakout_events"
    __table_args__ = (Index("ix_events_stock_setup_date", "stock_id", "setup_type", "trade_date"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(16), nullable=False)  # BREAKOUT | DIP_BUY
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TradeSetup(Base):
    """Entry/SL/targets/score on CONFIRMED, tagged strategy_version and setup_type — docs/db.md."""

    __tablename__ = "trade_setups"
    __table_args__ = (
        Index("ix_setups_stock_version", "stock_id", "strategy_version"),
        Index("ix_setups_setup_type", "setup_type"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(16), nullable=False)  # BREAKOUT | DIP_BUY
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    stop_loss: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    target_1r: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    target_1_5r: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    target_2r: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    target_3r: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    nearest_structural_target: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    score_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded component -> points
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    outcome: Mapped["TradeOutcome | None"] = relationship(back_populates="trade_setup", uselist=False)


class TradeOutcome(Base):
    """MFE/MAE/target-hit/SL-hit/holding-days per closed trade_setup — docs/db.md."""

    __tablename__ = "trade_outcomes"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    trade_setup_id: Mapped[int] = mapped_column(ForeignKey("trade_setups.id"), unique=True, nullable=False)
    mfe: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    mae: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    target_hit: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 1R/1.5R/2R/3R/STRUCTURAL, or null
    sl_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # TARGET_HIT/INVALIDATED/SESSION_END/INVALIDATED_GAP
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    trade_setup: Mapped["TradeSetup"] = relationship(back_populates="outcome")


class BacktestResult(Base):
    """Aggregated metrics per backtest run, keyed by strategy_version — docs/db.md."""

    __tablename__ = "backtest_results"
    __table_args__ = (Index("ix_backtest_results_version", "strategy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # null = combined, else BREAKOUT/DIP_BUY
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    avg_mfe: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    avg_mae: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    avg_holding_days: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    breakdown_by_score_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    breakdown_by_sector: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    breakdown_by_day_of_week: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    breakdown_by_regime: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsArticle(Base):
    """Persisted, classified news — see app/news/classifier.py and
    app/news/persist.py. Previously news was fetched fresh per-call and
    discarded (docs/db.md listed `news` as a deferred table); this is that
    table, added once a frontend News tab needed real history to show.
    """

    __tablename__ = "news_articles"
    __table_args__ = (
        Index("ix_news_articles_symbol_published", "symbol", "published_at"),
        Index("ix_news_articles_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # null = untagged, no known symbol match
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    # "rss:" + full feed URL (see scraper_rss_fallback.py) can exceed 64
    # chars easily (e.g. the Economic Times feed URL alone is 70+ chars) —
    # sized generously rather than truncating a real source string.
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)  # dedup key
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. LEGAL, EARNINGS, ORDER_WIN — see classifier.py
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)  # POSITIVE | NEGATIVE | NEUTRAL
    severity: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-5, see classifier.py's doc-section-27 scale
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)  # 0-1, rule-match strength not a calibrated probability
