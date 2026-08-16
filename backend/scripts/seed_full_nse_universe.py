"""Bulk seed: every NSE cash-equity symbol Angel One's scrip master knows
about (~2660, see angel_one_scrip_master.list_nse_equity_symbols).

Supersedes seed_universe.py's curated ~77-symbol list for the "full
universe" backtest slice (docs/engine.md's liquidity filter narrows this
down at query time via app.data.universe.get_universe, not here — this
script seeds broadly, the filter runs on read). Idempotent, safe to rerun.

No `name`/`sector` metadata beyond the bare symbol — Angel One's scrip
master doesn't carry sector classification, unlike seed_universe.py's
hand-curated list. sector stays NULL for symbols seeded here only;
relative_strength.py's sector matching degrades gracefully for those
(see that module's fallback-to-NIFTY-only behavior).

Run: cd backend && source .venv/bin/activate && python scripts/seed_full_nse_universe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.data.angel_one_scrip_master import list_nse_equity_symbols
from app.db.models import Stock
from app.db.session import SessionLocal


def seed() -> None:
    symbols = list_nse_equity_symbols()
    print(f"scrip master lists {len(symbols)} NSE cash-equity symbols")

    db = SessionLocal()
    inserted = 0
    already_present = 0
    try:
        existing_symbols = {row[0] for row in db.execute(select(Stock.symbol)).all()}
        for symbol in symbols:
            if symbol in existing_symbols:
                already_present += 1
                continue
            db.add(Stock(symbol=symbol, name=None, sector=None, listing_status="ACTIVE"))
            inserted += 1
        db.commit()
        print(f"seeded: {inserted} inserted, {already_present} already present, {len(symbols)} total")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
