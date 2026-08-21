"""decide() maps each category to the intervention the four-category table specifies."""

import pytest

import config
from core import decide
from core.diagnose import NEEDS_LLM


def test_bank_downtime_retries_after_the_transient_window():
    intervention = decide.decide("bank_downtime")
    assert intervention.action == decide.RETRY
    assert intervention.delay_hours == config.COOLING_OFF_HOURS["bank_downtime"]
    assert intervention.touches_live_instrument


def test_insufficient_funds_retries_later_and_nudges():
    intervention = decide.decide("insufficient_funds")
    assert intervention.action == decide.RETRY
    assert intervention.delay_hours == config.COOLING_OFF_HOURS["insufficient_funds"]
    assert intervention.nudge


def test_insufficient_funds_waits_longer_than_bank_downtime():
    # A transient outage clears in hours; an empty account does not. Retrying an
    # underfunded account too soon just burns an attempt.
    assert (
        decide.decide("insufficient_funds").delay_hours
        > decide.decide("bank_downtime").delay_hours
    )


def test_dead_instrument_sends_a_link_and_never_retries():
    intervention = decide.decide("dead_instrument")
    assert intervention.action == decide.SEND_LINK
    assert intervention.action != decide.RETRY
    assert not intervention.touches_live_instrument


def test_exhausted_escalates_to_a_human():
    intervention = decide.decide("exhausted")
    assert intervention.action == decide.ESCALATE
    assert not intervention.touches_live_instrument


def test_unresolved_category_fails_closed():
    # The needs_llm sentinel must be resolved before deciding — inventing an
    # intervention for an unclassified record is how an agent retries blindly.
    with pytest.raises(ValueError):
        decide.decide(NEEDS_LLM)


def test_unknown_category_fails_closed():
    with pytest.raises(ValueError):
        decide.decide("something_invented")


def test_interventions_are_immutable():
    # Shared table entries: a mutable Intervention could be edited by one record and
    # silently affect every later one.
    intervention = decide.decide("bank_downtime")
    with pytest.raises(Exception):
        intervention.action = decide.ESCALATE
