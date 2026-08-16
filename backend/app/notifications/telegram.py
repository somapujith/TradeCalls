"""Telegram bot alerts — free, Bot API. Sends the alert format from the
original planning doc's section 38 ("BREAKOUT ALERT" / entry-SL-targets-
score-confirmation) whenever a new trade_setup is CONFIRMED.

Failure here must never break the scheduler's job chain — a Telegram
outage shouldn't stop ingestion/scoring/state-advance from completing and
persisting. Callers should use send_trade_setup_alert (catches and logs,
never raises) rather than the raw send_message for that reason.
"""
from __future__ import annotations

import json
import logging

import requests

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 15


class TelegramNotConfiguredError(RuntimeError):
    """Raised when telegram_bot_token/telegram_chat_id are blank."""


def _configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_message(text: str) -> None:
    """Raises TelegramNotConfiguredError or requests.RequestException on
    failure — callers that must not be blocked by a Telegram outage should
    use send_trade_setup_alert instead, which wraps this."""
    if not _configured():
        raise TelegramNotConfiguredError(
            "Telegram not configured — set telegram_bot_token/telegram_chat_id in backend/.env"
        )

    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram sendMessage rejected: {body}")


SETUP_TYPE_LABEL = {"BREAKOUT": "BREAKOUT ALERT", "DIP_BUY": "DIP-BUY ALERT"}


def format_trade_setup_alert(
    *,
    symbol: str,
    setup_type: str,
    entry_price: float | None,
    stop_loss: float,
    target_1r: float,
    target_1_5r: float,
    target_2r: float,
    target_3r: float,
    nearest_structural_target: float | None,
    score: float,
    tier: str,
    event_caution: str | None = None,
) -> str:
    """Terse "research call" format — user-specified, matched to a
    reference Telegram channel's style (compact single-block message, not
    the original doc's boxier section-38 layout): category line, then
    "<SYMBOL> Looks good above X SL Y" on one line, targets condensed to
    one comma-separated line rather than one per line."""
    label = "POSITIONAL RESEARCH"
    trigger_price = entry_price if entry_price is not None else nearest_structural_target
    targets = [t for t in [nearest_structural_target, target_1r, target_1_5r, target_2r, target_3r] if t is not None]
    targets_line = "  ".join(f"₹{t:,.2f}" for t in sorted(set(targets)))

    lines = [label, ""]
    trigger_text = f"₹{trigger_price:,.2f}" if trigger_price is not None else "current level"
    lines.append(f"<b>{symbol}</b>  Looks good above {trigger_text}  SL ₹{stop_loss:,.2f}")
    lines.append("")
    lines.append(f"🎯 Targets: {targets_line}")
    lines.append(f"Score: {score:.0f}/100 ({tier})")

    if event_caution:
        lines.append(f"⚠️ {event_caution}")

    return "\n".join(lines)


def format_new_high_update(symbol: str, price: float, *, rockets: int = 3) -> str:
    """Follow-up sent when an open call makes a fresh intraday/since-signal
    high — "<SYMBOL> made a high of X🚀🚀🚀" per the reference channel
    style. Caller (the continuous LTP-monitoring loop) decides when a move
    is significant enough to fire this — this function only formats."""
    return f"✨<b>{symbol}</b> made a high of ₹{price:,.2f}{'🚀' * rockets}"


def format_target_hit_update(symbol: str, entry_price: float, hit_price: float) -> str:
    """Follow-up sent when a target is hit — narrative style matching the
    reference channel ("In morning we shared research... today it hit a
    high of...")."""
    return (
        f"✅<b>{symbol}</b> 🔥 - In morning we shared research that it looks good above ₹{entry_price:,.2f}\n\n"
        f"Today only, it hit a high of ₹{hit_price:,.2f}🎯🎯"
    )


def send_trade_setup_alert(**kwargs) -> bool:
    """Formats + sends a trade_setup alert. Never raises — logs and
    returns False on any failure (config missing, network error, Telegram
    API rejection), True on success. See module docstring for why this
    must not break the scheduler's job chain."""
    try:
        text = format_trade_setup_alert(**kwargs)
        send_message(text)
        return True
    except Exception:
        logger.exception("Telegram alert failed for %s", kwargs.get("symbol"))
        return False
