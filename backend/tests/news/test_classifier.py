"""Tests for app.news.classifier — pure function, no I/O."""
from __future__ import annotations

from app.news.classifier import (
    EVENT_BUYBACK,
    EVENT_DIVIDEND,
    EVENT_EARNINGS_NEGATIVE,
    EVENT_EARNINGS_POSITIVE,
    EVENT_LEGAL,
    EVENT_ORDER_WIN,
    EVENT_RATING_DOWNGRADE,
    EVENT_UNCLASSIFIED,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    classify,
)


def test_legal_court_case_classifies_negative_high_severity():
    result = classify("Company X faces court case over land dispute")

    assert result.event_type == EVENT_LEGAL
    assert result.sentiment == NEGATIVE
    assert result.severity == 4
    assert result.confidence > 0


def test_lawsuit_keyword_also_matches_legal():
    result = classify("Investors file lawsuit against management")

    assert result.event_type == EVENT_LEGAL
    assert result.sentiment == NEGATIVE


def test_order_win_classifies_positive():
    result = classify("Company X wins order worth Rs 500 crore from NHAI")

    assert result.event_type == EVENT_ORDER_WIN
    assert result.sentiment == POSITIVE


def test_earnings_positive_beats_estimates():
    result = classify("Company X profit surges 40%, beats estimates")

    assert result.event_type == EVENT_EARNINGS_POSITIVE
    assert result.sentiment == POSITIVE


def test_earnings_negative_profit_falls():
    result = classify("Company X Q2 net profit falls 20% YoY")

    assert result.event_type == EVENT_EARNINGS_NEGATIVE
    assert result.sentiment == NEGATIVE


def test_rating_downgrade_classifies_negative():
    result = classify("CRISIL downgrades rating on Company X debt")

    assert result.event_type == EVENT_RATING_DOWNGRADE
    assert result.sentiment == NEGATIVE


def test_dividend_classifies_positive_low_severity():
    result = classify("Company X declares dividend of Rs 5 per share")

    assert result.event_type == EVENT_DIVIDEND
    assert result.sentiment == POSITIVE
    assert result.severity <= 2


def test_buyback_classifies_positive():
    result = classify("Company X announces buyback of shares worth Rs 1000 crore")

    assert result.event_type == EVENT_BUYBACK
    assert result.sentiment == POSITIVE


def test_unrelated_headline_is_unclassified_with_zero_confidence():
    result = classify("Company X opens new office in Bangalore")

    assert result.event_type == EVENT_UNCLASSIFIED
    assert result.sentiment == NEUTRAL
    assert result.severity == 0
    assert result.confidence == 0.0


def test_legal_checked_before_order_win_when_both_present():
    """Doc'd priority: more severe/specific categories checked first."""
    result = classify("Company X wins order but also faces lawsuit over contract terms")

    assert result.event_type == EVENT_LEGAL


def test_classify_uses_summary_too():
    result = classify("Company X in the news", summary="Company faces investigation by regulators")

    assert result.event_type == EVENT_LEGAL


def test_severity_always_between_0_and_5():
    for headline in [
        "Company X insider trading probe launched",
        "Company X wins order",
        "Company X declares dividend",
        "Company X opens new office",
    ]:
        result = classify(headline)
        assert 0 <= result.severity <= 5


def test_confidence_always_between_0_and_1():
    for headline in ["Company X faces court case", "Company X opens new office"]:
        result = classify(headline)
        assert 0.0 <= result.confidence <= 1.0
