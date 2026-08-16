"""Rule-based news classifier — event type, sentiment, severity, confidence.

No LLM/SLM exists in this codebase yet (the original plan's ai/ module was
never built) — this is keyword/phrase matching against headline+summary,
not language understanding. Confidence reflects rule-match specificity,
not a calibrated probability. Categories and the 0-5 severity scale follow
the original planning doc's sections 26-27 (News Engine / News Severity).

Rules are checked in order; first match wins. Order matters — more severe/
specific categories (legal, regulatory, debt distress) are checked before
generic ones so e.g. a headline combining "wins order" and "faces lawsuit"
classifies as the legal event, not the order-win one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EVENT_LEGAL = "LEGAL"
EVENT_REGULATORY = "REGULATORY"
EVENT_DEBT = "DEBT"
EVENT_RATING_DOWNGRADE = "RATING_DOWNGRADE"
EVENT_RATING_UPGRADE = "RATING_UPGRADE"
EVENT_MANAGEMENT = "MANAGEMENT"
EVENT_PROMOTER = "PROMOTER_ACTIVITY"
EVENT_INSIDER = "INSIDER_ACTIVITY"
EVENT_ORDER_LOSS = "ORDER_LOSS"
EVENT_ORDER_WIN = "ORDER_WIN"
EVENT_ACQUISITION = "ACQUISITION_MERGER"
EVENT_EARNINGS_NEGATIVE = "EARNINGS_NEGATIVE"
EVENT_EARNINGS_POSITIVE = "EARNINGS_POSITIVE"
EVENT_BUYBACK = "BUYBACK"
EVENT_DIVIDEND = "DIVIDEND"
EVENT_UNCLASSIFIED = "UNCLASSIFIED"

POSITIVE = "POSITIVE"
NEGATIVE = "NEGATIVE"
NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class Classification:
    event_type: str
    sentiment: str
    severity: int  # 0 (irrelevant) .. 5 (existential), per doc section 27
    confidence: float  # 0-1, rule-match strength


@dataclass(frozen=True)
class _Rule:
    event_type: str
    sentiment: str
    severity: int
    confidence: float
    patterns: tuple[str, ...]  # regex, matched case-insensitively, word-boundary wrapped by _compile


def _compile(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE)


_RULES: list[_Rule] = [
    _Rule(EVENT_INSIDER, NEGATIVE, 4, 0.75, ("insider trading",)),
    _Rule(
        EVENT_LEGAL,
        NEGATIVE,
        4,
        0.8,
        (
            "court case", "lawsuit", "litigation", "sued", "sues", "court order",
            "fraud", "cbi raid", "ed raid", "raided", "probe", "investigation",
            "chargesheet", "arrested", "summons",
        ),
    ),
    _Rule(
        EVENT_REGULATORY,
        NEGATIVE,
        3,
        0.7,
        ("sebi action", "show cause notice", "regulatory action", "banned by sebi", "trading ban", "sebi penalty"),
    ),
    _Rule(
        EVENT_DEBT,
        NEGATIVE,
        4,
        0.75,
        ("debt default", "loan default", "default on payment", "insolvency", "bankruptcy", "npa"),
    ),
    _Rule(
        EVENT_RATING_DOWNGRADE,
        NEGATIVE,
        3,
        0.7,
        ("rating downgrade", "downgrades? rating", "downgraded to", "credit rating cut"),
    ),
    _Rule(EVENT_RATING_UPGRADE, POSITIVE, 2, 0.7, ("rating upgrade", "upgrades? rating", "upgraded to")),
    _Rule(
        EVENT_MANAGEMENT,
        NEGATIVE,
        3,
        0.6,
        ("ceo resigns", "md resigns", "cfo resigns", "steps down", "resigns as"),
    ),
    _Rule(EVENT_PROMOTER, NEGATIVE, 3, 0.65, ("promoter pledge", "pledged shares", "promoter stake sale")),
    _Rule(EVENT_ORDER_LOSS, NEGATIVE, 3, 0.65, ("order cancelled", "contract cancelled", "loses order", "loses contract")),
    _Rule(EVENT_ORDER_WIN, POSITIVE, 3, 0.65, ("wins order", "bags order", "secures contract", "wins contract")),
    _Rule(EVENT_ACQUISITION, POSITIVE, 3, 0.6, ("to acquire", "acquisition of", "merger with", "to merge with")),
    _Rule(
        EVENT_EARNINGS_NEGATIVE,
        NEGATIVE,
        3,
        0.6,
        ("profit falls", "net loss", "misses estimates", "loss widens", "profit plunges", "profit declines"),
    ),
    _Rule(
        EVENT_EARNINGS_POSITIVE,
        POSITIVE,
        2,
        0.6,
        ("profit surges", "profit jumps", "beats estimates", "profit rises", "net profit up"),
    ),
    _Rule(EVENT_BUYBACK, POSITIVE, 2, 0.6, ("share buyback", "buyback of shares", "announces buyback")),
    _Rule(EVENT_DIVIDEND, POSITIVE, 1, 0.6, ("declares dividend", "announces dividend", "interim dividend")),
]

_COMPILED_RULES = [(rule, _compile(rule.patterns)) for rule in _RULES]


def classify(headline: str, summary: str | None = None) -> Classification:
    """Classifies a headline (+ optional summary) into event type, sentiment,
    severity, and confidence. Returns EVENT_UNCLASSIFIED/NEUTRAL/severity=0/
    confidence=0.0 when no rule matches — a deliberately low-confidence
    default rather than guessing a category."""
    text = " ".join(part for part in (headline, summary) if part)

    for rule, pattern in _COMPILED_RULES:
        if pattern.search(text):
            return Classification(
                event_type=rule.event_type, sentiment=rule.sentiment, severity=rule.severity, confidence=rule.confidence
            )

    return Classification(event_type=EVENT_UNCLASSIFIED, sentiment=NEUTRAL, severity=0, confidence=0.0)
