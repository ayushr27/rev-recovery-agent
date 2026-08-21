"""The gate is the agent's stopping behaviour, so each bound gets an explicit test.

Every test drives the gate the way the pipeline does — decide() first, then
evaluate() — so a change to the four-category table cannot leave these passing
against an intervention the agent would never actually produce.
"""

import copy

import pytest

import config
from core import decide, diagnose, gate

NOW = 1_755_000_000
HOUR = 3600


def record(**overrides):
    """A recoverable, recurring failure below the AFA threshold. Override per test."""
    base = {
        "id": "pay_TEST00001",
        "amount": 49_900,  # ₹499
        "payment_type": "recurring",
        "retry_count": 0,
    }
    base.update(overrides)
    return base


def run(rec, category, state=None, now=NOW):
    """decide -> evaluate, the way run_batch.py wires it."""
    state = gate.new_state() if state is None else state
    return gate.evaluate(rec, category, decide.decide(category), state, now)


# --- the happy path, so the refusals below mean something ----------------------


def test_transient_failure_is_allowed_to_retry():
    result = run(record(), "bank_downtime")
    assert result.decision == gate.ALLOW
    assert result.action == decide.RETRY
    assert result.reason is None
    assert result.idempotency_key


def test_allowed_retry_carries_the_pre_debit_notification():
    # The framework's 24h pre-debit alert: recorded as preceding the debit, not built.
    assert run(record(), "bank_downtime").pre_debit_notified


def test_non_debit_actions_carry_no_pre_debit_notification():
    assert not run(record(), "dead_instrument").pre_debit_notified
    assert not run(record(), "exhausted").pre_debit_notified


# --- dead instrument: never retried -------------------------------------------


def test_dead_instrument_is_never_retried():
    # Forcing a retry intervention onto a dead instrument — the gate must refuse it
    # even though decide() would never have produced this pairing itself.
    forced_retry = decide.Intervention(
        action=decide.RETRY, delay_hours=2, touches_live_instrument=True
    )
    result = gate.evaluate(record(), "dead_instrument", forced_retry, gate.new_state(), NOW)
    assert result.decision == gate.REFUSE
    assert result.reason == gate.DEAD_INSTRUMENT_NEVER_RETRIED
    assert result.action is None


def test_dead_instrument_still_gets_a_payment_link():
    # Refusing the retry must not mean abandoning the customer.
    result = run(record(), "dead_instrument")
    assert result.allowed
    assert result.action == decide.SEND_LINK


def test_dead_instrument_retry_refused_even_with_budget_to_spare():
    # A safety invariant, not a budget: remaining quota never makes it valid.
    state = gate.new_state()
    state["global_actions"] = 0
    forced_retry = decide.Intervention(decide.RETRY, 2, True)
    result = gate.evaluate(record(), "dead_instrument", forced_retry, state, NOW)
    assert result.reason == gate.DEAD_INSTRUMENT_NEVER_RETRIED


# --- dead instrument: evidence in the record, independent of the diagnosis -----


@pytest.mark.parametrize("reason", sorted(config.HARD_DEAD_INSTRUMENT_REASONS))
def test_hard_evidence_in_the_record_refuses_a_retry(reason):
    result = run(record(error_reason=reason), "bank_downtime")
    assert result.decision == gate.REFUSE
    assert result.reason == gate.DEAD_INSTRUMENT_EVIDENCE_IN_RECORD


def test_evidence_rule_holds_whatever_the_diagnosis_says():
    # The point of the rule. Rule 1 trusts the category and so cannot catch this; the
    # record says the card is invalid no matter what it was labelled.
    for wrong_label in ("bank_downtime", "insufficient_funds"):
        result = run(record(error_reason="invalid_card"), wrong_label)
        assert result.reason == gate.DEAD_INSTRUMENT_EVIDENCE_IN_RECORD, wrong_label


def test_evidence_rule_is_an_invariant_not_a_quota():
    # Fresh state, no attempts used, full budget — none of that makes it permissible.
    state = gate.new_state()
    state["global_actions"] = 0
    result = run(record(error_reason="card_expired", retry_count=0), "bank_downtime", state)
    assert result.decision == gate.REFUSE
    assert result.reason == gate.DEAD_INSTRUMENT_EVIDENCE_IN_RECORD


def test_evidence_rule_does_not_block_serving_the_customer():
    # Refusing the retry must not also refuse the payment link that fixes the problem.
    result = run(record(error_reason="card_expired"), "dead_instrument")
    assert result.allowed
    assert result.action == decide.SEND_LINK


def test_evidence_check_cannot_see_prose_only_evidence():
    # The documented limit, asserted so it cannot quietly change. This record is a dead
    # instrument, but only its description says so — and the gate does not read prose.
    # A misdiagnosis here WILL be retried. That residual risk is measured by
    # report/metrics.py's false_intervention count and scripts/eval_llm.py, not prevented.
    prose_only = record(
        error_reason="authentication_failed",
        error_description="The card is no longer valid and has been permanently deactivated.",
    )
    result = run(prose_only, "bank_downtime")
    assert result.allowed
    assert result.action == decide.RETRY


def test_missing_or_unknown_error_reason_does_not_raise():
    assert run(record(), "bank_downtime").allowed
    assert run(record(error_reason=None), "bank_downtime").allowed
    assert run(record(error_reason="something_new"), "bank_downtime").allowed


def test_evidence_reasons_have_not_drifted_from_the_rules_table():
    # config.HARD_DEAD_INSTRUMENT_REASONS deliberately duplicates diagnose.REASON_RULES
    # rather than importing it, so that editing the classifier cannot silently delete the
    # safety check. This test is what keeps the two in step on purpose instead of by
    # accident: if either side changes, it fails and forces a conscious decision.
    for reason in config.HARD_DEAD_INSTRUMENT_REASONS:
        assert diagnose.REASON_RULES.get(reason) == "dead_instrument", reason


# --- retry cap ----------------------------------------------------------------


def test_payment_already_at_the_cap_is_refused():
    result = run(record(retry_count=config.MAX_RETRY_ATTEMPTS), "bank_downtime")
    assert result.decision == gate.REFUSE
    assert result.reason == gate.RETRY_CAP_EXCEEDED


def test_cap_counts_processor_attempts_plus_our_own():
    # The record arrives one below the cap; this run has already fired once. Counting
    # only one of those two would let a payment past the cap.
    state = gate.new_state()
    state["payments"]["pay_TEST00001"] = {"attempts": 1, "actions": []}
    result = run(record(retry_count=config.MAX_RETRY_ATTEMPTS - 1), "bank_downtime", state)
    assert result.reason == gate.RETRY_CAP_EXCEEDED


def test_cap_does_not_block_non_retry_actions():
    # An exhausted payment is at the cap by definition; escalating it is still allowed.
    result = run(record(retry_count=config.MAX_RETRY_ATTEMPTS), "exhausted")
    assert result.allowed
    assert result.action == decide.ESCALATE


# --- RBI AFA threshold --------------------------------------------------------


def test_large_recurring_retry_is_converted_not_silently_retried():
    result = run(record(amount=config.AFA_THRESHOLD_PAISE + 1), "bank_downtime")
    assert result.decision == gate.CONVERT
    assert result.action == decide.REQUIRES_AFA
    assert result.reason == gate.AFA_REQUIRED_ABOVE_THRESHOLD
    assert result.action != decide.RETRY


def test_converted_action_does_not_claim_a_pre_debit_notification():
    # An authentication link is not a debit.
    result = run(record(amount=config.AFA_THRESHOLD_PAISE + 1), "bank_downtime")
    assert not result.pre_debit_notified


def test_amount_exactly_at_the_threshold_is_not_gated():
    # The rule is "above ₹15,000", so the boundary itself still retries normally.
    result = run(record(amount=config.AFA_THRESHOLD_PAISE), "bank_downtime")
    assert result.decision == gate.ALLOW
    assert result.action == decide.RETRY


def test_one_time_payment_above_the_threshold_is_not_gated():
    # The AFA rule covers recurring auto-debits only. Applying it to one-time
    # checkout failures would be factually wrong about the regulation.
    result = run(
        record(payment_type="one_time", amount=config.AFA_THRESHOLD_PAISE * 5),
        "bank_downtime",
    )
    assert result.decision == gate.ALLOW
    assert result.action == decide.RETRY


# --- idempotency and cooling-off ----------------------------------------------


def test_replaying_the_same_action_in_the_window_is_refused():
    state = gate.new_state()
    first = run(record(), "bank_downtime", state)
    gate.commit(state, "pay_TEST00001", first, NOW)

    replay = run(record(), "bank_downtime", state)
    assert replay.decision == gate.REFUSE
    assert replay.reason == gate.DUPLICATE_ACTION_IN_WINDOW
    assert replay.idempotency_key == first.idempotency_key


def test_idempotency_key_is_stable_for_the_same_payment_action_and_window():
    a = run(record(), "bank_downtime")
    b = run(record(), "bank_downtime")
    assert a.idempotency_key == b.idempotency_key


def test_idempotency_key_differs_across_payments():
    a = run(record(id="pay_TEST00001"), "bank_downtime")
    b = run(record(id="pay_TEST00002"), "bank_downtime")
    assert a.idempotency_key != b.idempotency_key


def test_a_recent_different_action_trips_the_cooling_off_window():
    # A link went out moments ago; a retry is a different action (so not a duplicate)
    # but the payment is still inside its cooling window.
    state = gate.new_state()
    state["payments"]["pay_TEST00001"] = {
        "attempts": 0,
        "actions": [{"idempotency_key": "idem_other", "action": decide.SEND_LINK, "at": NOW}],
    }
    result = run(record(), "bank_downtime", state)
    assert result.decision == gate.REFUSE
    assert result.reason == gate.COOLING_OFF_ACTIVE


def test_action_is_allowed_once_the_cooling_window_has_passed():
    hours = gate.cooling_off_hours("bank_downtime")
    later = NOW + hours * HOUR
    state = gate.new_state()
    state["payments"]["pay_TEST00001"] = {
        "attempts": 0,
        "actions": [{"idempotency_key": "idem_other", "action": decide.SEND_LINK, "at": NOW}],
    }
    assert run(record(), "bank_downtime", state, now=later).allowed


# --- global budget ------------------------------------------------------------


def test_global_budget_halts_a_runaway():
    state = gate.new_state()
    state["global_actions"] = config.GLOBAL_ATTEMPT_BUDGET
    result = run(record(), "bank_downtime", state)
    assert result.decision == gate.REFUSE
    assert result.reason == gate.GLOBAL_BUDGET_EXHAUSTED


def test_budget_allows_the_final_action_then_stops():
    state = gate.new_state()
    state["global_actions"] = config.GLOBAL_ATTEMPT_BUDGET - 1
    assert run(record(), "bank_downtime", state).allowed

    state["global_actions"] = config.GLOBAL_ATTEMPT_BUDGET
    assert not run(record(), "bank_downtime", state).allowed


def test_a_runaway_loop_cannot_exceed_the_budget():
    # Hammer the gate far past the budget and confirm the bound holds.
    state = gate.new_state()
    allowed = 0
    for i in range(config.GLOBAL_ATTEMPT_BUDGET * 2):
        rec = record(id=f"pay_TEST{i:05d}")
        result = run(rec, "bank_downtime", state)
        if result.allowed:
            gate.commit(state, rec["id"], result, NOW)
            allowed += 1
    assert allowed == config.GLOBAL_ATTEMPT_BUDGET
    assert state["global_actions"] == config.GLOBAL_ATTEMPT_BUDGET


# --- the purity claim the README will make ------------------------------------


def test_evaluate_does_not_mutate_state():
    # The scalability story depends on evaluate() being a pure function of
    # (record + state); a hidden mutation here would quietly break replay safety.
    state = gate.new_state()
    state["payments"]["pay_TEST00001"] = {"attempts": 1, "actions": []}
    before = copy.deepcopy(state)
    run(record(), "bank_downtime", state)
    assert state == before


def test_commit_records_the_action_and_spends_budget():
    state = gate.new_state()
    result = run(record(), "bank_downtime", state)
    gate.commit(state, "pay_TEST00001", result, NOW)

    payment_state = state["payments"]["pay_TEST00001"]
    assert payment_state["attempts"] == 1
    assert payment_state["actions"][0]["idempotency_key"] == result.idempotency_key
    assert state["global_actions"] == 1


def test_commit_ignores_refused_actions():
    # A refusal must not consume budget or count as an attempt.
    state = gate.new_state()
    refused = run(record(retry_count=config.MAX_RETRY_ATTEMPTS), "bank_downtime")
    gate.commit(state, "pay_TEST00001", refused, NOW)
    assert state["global_actions"] == 0
    assert state["payments"] == {}


def test_non_retry_actions_do_not_consume_retry_attempts():
    state = gate.new_state()
    result = run(record(), "dead_instrument")
    gate.commit(state, "pay_TEST00001", result, NOW)
    assert state["payments"]["pay_TEST00001"]["attempts"] == 0
    assert state["global_actions"] == 1
