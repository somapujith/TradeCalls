"""Angel One instrument-token lookup — resolves a bare NSE symbol (or the
NIFTY 50 index) to the numeric symboltoken get_ltp/get_candle_data require.

Was the "known v1 limitation" flagged in docs/engine.md and
app.data.angel_one_client's docstrings: those functions require a
symbol_token but had no lookup wired in. This closes that gap using Angel
One's own published scrip master (public, no auth, ~155k instruments
across all exchanges/segments as of 2026-08-17).

Fetched once per process and cached in memory — not persisted to disk or
committed to the repo (it's a ~35MB file that changes as instruments are
added/removed; fetching fresh each process start is simpler than cache
invalidation for a file this size, and the fetch only happens on first
lookup, not at import time).
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
FETCH_TIMEOUT_SECONDS = 60

# NIFTY 50 index's Angel One token doesn't follow the "<SYMBOL>-EQ" cash-
# equity convention scrip lookup below assumes — hardcoded here since it's
# a single well-known constant. Angel One's scrip master actually lists two
# NIFTY-named NSE entries: token "26000" (name="NIFTY", instrumenttype="")
# and token "99926000" (symbol="Nifty 50", instrumenttype="AMXIDX"). Only
# "99926000" returns data from getCandleData (verified 2026-08-17 — "26000"
# returns status=SUCCESS with an empty data array, silently, no error) —
# use that one.
NIFTY_50_INDEX_TOKEN = "99926000"

_cache: dict[str, str] | None = None  # name (bare NSE symbol) -> token, NSE cash-equity only


class ScripMasterUnavailableError(RuntimeError):
    """Raised when the scrip master can't be fetched/parsed — distinct from
    a resolved-but-symbol-not-found case (that returns None, this raises,
    since a fetch failure means we can't answer any lookup at all)."""


def _load() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache

    try:
        response = requests.get(SCRIP_MASTER_URL, timeout=FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        raise ScripMasterUnavailableError(f"Failed to fetch Angel One scrip master: {exc}") from exc

    mapping: dict[str, str] = {}
    for row in rows:
        if (
            row.get("exch_seg") == "NSE"
            and row.get("instrumenttype") == ""
            and str(row.get("symbol", "")).endswith("-EQ")
        ):
            mapping[row["name"]] = row["token"]

    logger.info("Angel One scrip master loaded: %d NSE cash-equity symbols indexed", len(mapping))
    _cache = mapping
    return _cache


def resolve_equity_token(symbol: str) -> str | None:
    """symbol: bare NSE symbol (e.g. "RELIANCE", "M&M") — same form stored
    in Stock.symbol. Returns Angel One's numeric token, or None if not
    found in the NSE cash-equity segment (e.g. a typo, a delisted symbol,
    or a symbol this project only knows by a different alias)."""
    return _load().get(symbol)


def list_nse_equity_symbols() -> list[str]:
    """All bare NSE cash-equity symbols known to Angel One's scrip master
    (~2660 as of 2026-08-17 — includes some ETFs that share the "-EQ"
    suffix/blank instrumenttype convention with true equities; not
    separately filtered out, acceptable for universe-seeding purposes).
    Used by scripts/seed_full_nse_universe.py."""
    return sorted(_load().keys())


def reset_cache_for_tests() -> None:
    global _cache
    _cache = None
