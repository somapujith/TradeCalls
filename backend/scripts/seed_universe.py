"""One-time/rerunnable seed: populates `stocks` with a curated liquid NSE
universe. Idempotent (upserts by symbol) — safe to rerun to add names.

v1 bootstrap uses a hand-picked list of large/mid-cap NIFTY constituents
rather than pulling NSE's full ~2000-symbol equity list — smaller,
faster first run, expand later (see docs/engine.md's universe scope).
Symbols are stored bare (no .NS suffix) — app.data.yfinance_client's
_to_yf_symbol appends it, matching every other module's convention.

Run: cd backend && source .venv/bin/activate && python scripts/seed_universe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.models import Stock
from app.db.session import SessionLocal

# (symbol, name, sector) — sector is best-effort/free-text, not yet consumed
# by relative_strength.py (see that module's docstring), fine to be coarse.
UNIVERSE = [
    ("RELIANCE", "Reliance Industries", "ENERGY"),
    ("TCS", "Tata Consultancy Services", "IT"),
    ("HDFCBANK", "HDFC Bank", "BANK"),
    ("ICICIBANK", "ICICI Bank", "BANK"),
    ("INFY", "Infosys", "IT"),
    ("HINDUNILVR", "Hindustan Unilever", "FMCG"),
    ("ITC", "ITC", "FMCG"),
    ("SBIN", "State Bank of India", "BANK"),
    ("BHARTIARTL", "Bharti Airtel", "TELECOM"),
    ("BAJFINANCE", "Bajaj Finance", "FINANCE"),
    ("KOTAKBANK", "Kotak Mahindra Bank", "BANK"),
    ("LT", "Larsen & Toubro", "INFRA"),
    ("HCLTECH", "HCL Technologies", "IT"),
    ("AXISBANK", "Axis Bank", "BANK"),
    ("ASIANPAINT", "Asian Paints", "FMCG"),
    ("MARUTI", "Maruti Suzuki", "AUTO"),
    ("SUNPHARMA", "Sun Pharmaceutical", "PHARMA"),
    ("TITAN", "Titan Company", "FMCG"),
    ("ULTRACEMCO", "UltraTech Cement", "CEMENT"),
    ("NESTLEIND", "Nestle India", "FMCG"),
    ("WIPRO", "Wipro", "IT"),
    ("ADANIENT", "Adani Enterprises", "CONGLOMERATE"),
    ("ADANIPORTS", "Adani Ports", "INFRA"),
    ("ADANIPOWER", "Adani Power", "ENERGY"),
    ("ONGC", "Oil & Natural Gas Corp", "ENERGY"),
    ("NTPC", "NTPC", "ENERGY"),
    ("POWERGRID", "Power Grid Corp", "ENERGY"),
    ("COALINDIA", "Coal India", "ENERGY"),
    ("TATASTEEL", "Tata Steel", "METAL"),
    ("JSWSTEEL", "JSW Steel", "METAL"),
    ("HINDALCO", "Hindalco Industries", "METAL"),
    # TATAMOTORS: no match in Angel One's scrip master under this or any
    # searched variant (TATAMOTOR, "TATA MOTOR") as of 2026-08-17 — likely a
    # post-demerger ticker change not yet chased down. Omitted rather than
    # guessed; re-add once the correct current ticker is confirmed.
    ("M&M", "Mahindra & Mahindra", "AUTO"),
    ("BAJAJ-AUTO", "Bajaj Auto", "AUTO"),
    ("EICHERMOT", "Eicher Motors", "AUTO"),
    ("HEROMOTOCO", "Hero MotoCorp", "AUTO"),
    ("DRREDDY", "Dr Reddy's Labs", "PHARMA"),
    ("CIPLA", "Cipla", "PHARMA"),
    ("DIVISLAB", "Divi's Laboratories", "PHARMA"),
    ("APOLLOHOSP", "Apollo Hospitals", "HEALTHCARE"),
    ("BRITANNIA", "Britannia Industries", "FMCG"),
    ("TATACONSUM", "Tata Consumer Products", "FMCG"),
    ("GRASIM", "Grasim Industries", "CEMENT"),
    ("SHREECEM", "Shree Cement", "CEMENT"),
    ("BPCL", "Bharat Petroleum", "ENERGY"),
    ("INDUSINDBK", "IndusInd Bank", "BANK"),
    ("BAJAJFINSV", "Bajaj Finserv", "FINANCE"),
    ("SBILIFE", "SBI Life Insurance", "FINANCE"),
    ("HDFCLIFE", "HDFC Life Insurance", "FINANCE"),
    ("PIDILITIND", "Pidilite Industries", "CHEMICALS"),
    ("DABUR", "Dabur India", "FMCG"),
    ("GODREJCP", "Godrej Consumer Products", "FMCG"),
    ("SIEMENS", "Siemens", "INDUSTRIALS"),
    ("DLF", "DLF", "REALTY"),
    ("LODHA", "Macrotech Developers (Lodha)", "REALTY"),
    ("VEDL", "Vedanta", "METAL"),
    ("AMBUJACEM", "Ambuja Cements", "CEMENT"),
    ("HAVELLS", "Havells India", "INDUSTRIALS"),
    ("PNB", "Punjab National Bank", "BANK"),
    ("CANBK", "Canara Bank", "BANK"),
    ("BANKBARODA", "Bank of Baroda", "BANK"),
    ("ETERNAL", "Eternal (formerly Zomato)", "CONSUMER_TECH"),
    ("TRENT", "Trent", "RETAIL"),
    ("NAUKRI", "Info Edge (Naukri)", "CONSUMER_TECH"),
    ("PAYTM", "One97 Communications (Paytm)", "CONSUMER_TECH"),
    ("DMART", "Avenue Supermarts (DMart)", "RETAIL"),
    ("PIIND", "PI Industries", "CHEMICALS"),
    ("SRF", "SRF", "CHEMICALS"),
    ("MUTHOOTFIN", "Muthoot Finance", "FINANCE"),
    ("CHOLAFIN", "Cholamandalam Investment", "FINANCE"),
    # LTIM (LTIMindtree): same issue as TATAMOTORS above — no scrip-master
    # match found under LTIM, LTI, or MINDTREE as of 2026-08-17. Omitted.
    ("TECHM", "Tech Mahindra", "IT"),
    ("PERSISTENT", "Persistent Systems", "IT"),
    ("POLYCAB", "Polycab India", "INDUSTRIALS"),
    ("ABB", "ABB India", "INDUSTRIALS"),
    ("BEL", "Bharat Electronics", "DEFENSE"),
    ("HAL", "Hindustan Aeronautics", "DEFENSE"),
]


def seed() -> None:
    db = SessionLocal()
    inserted = 0
    updated = 0
    try:
        for symbol, name, sector in UNIVERSE:
            existing = db.scalar(select(Stock).where(Stock.symbol == symbol))
            if existing is None:
                db.add(Stock(symbol=symbol, name=name, sector=sector, listing_status="ACTIVE"))
                inserted += 1
            else:
                existing.name = name
                existing.sector = sector
                existing.listing_status = "ACTIVE"
                updated += 1
        db.commit()
        print(f"seeded: {inserted} inserted, {updated} updated, {len(UNIVERSE)} total")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
