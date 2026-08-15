"""Fills against NEXT bar, applies slippage/brokerage/STT — docs/engine.md#cost-model.

Entry fills happen on the bar after the signal bar, never the signal bar
itself. If next-day open gaps past the stop-loss, the trade is not filled —
logged as INVALIDATED_GAP, not silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class FillResult:
    filled: bool
    fill_price: float | None
    reason: str | None  # "INVALIDATED_GAP" if not filled


def attempt_entry_fill(next_bar_open: float, next_bar_high: float, next_bar_low: float, planned_entry: float, stop_loss: float, atr: float | None) -> FillResult:
    """Signal confirmed on day T; this evaluates the fill on day T+1's bar.

    If the open already gapped below the stop-loss, the setup is invalidated
    before it could ever be filled at a sane risk — this is not a fill, it's
    a rejection, logged distinctly to avoid survivorship bias in stats.
    """
    if next_bar_open <= stop_loss:
        return FillResult(filled=False, fill_price=None, reason="INVALIDATED_GAP")

    # Fill at max(open, planned_entry) if the level was cleared intrabar,
    # otherwise at the open itself (gapped straight through).
    fill_price = max(next_bar_open, planned_entry) if next_bar_high >= planned_entry else next_bar_open
    if fill_price > next_bar_high:
        fill_price = next_bar_high  # can't fill above the day's high

    slippage = _slippage(fill_price, atr)
    return FillResult(filled=True, fill_price=fill_price + slippage, reason=None)


def _slippage(price: float, atr: float | None) -> float:
    """Slippage in basis points off next-day open, scaled by ATR."""
    base_bps = settings.slippage_bps / 10_000
    atr_scale = (atr / price) if (atr and price > 0) else 0.0
    return price * base_bps * (1 + atr_scale)


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float
    stt: float
    total: float


def trade_costs(buy_value: float, sell_value: float) -> CostBreakdown:
    """STT is 0.1% on both buy and sell side for delivery/equity trades
    (not intraday's 0.025% sell-side-only rate) — docs/engine.md#cost-model.
    """
    brokerage = settings.brokerage_flat * 2  # buy + sell leg
    stt = (buy_value + sell_value) * settings.stt_rate
    return CostBreakdown(brokerage=brokerage, stt=stt, total=brokerage + stt)
