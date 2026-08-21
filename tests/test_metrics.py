"""The rupee number has to be honest, so the arithmetic behind it gets tested.

The denominator is the thing most easily fudged: dividing recoveries by *total*
payments instead of *recoverable* ones silently inflates the rate by counting dead
cards the agent correctly refused to chase.
"""

from core import decide, execute, gate
from report import metrics


def row(payment_id, category, status, action=decide.RETRY, amount=100_000,
        source="rules", gate_result=gate.ALLOW, reason=None):
    return {
        "payment_id": payment_id,
        "amount": amount,
        "category": category,
        "source": source,
        "intervention": action,
        "action_taken": action,
        "gate_result": gate_result,
        "refusal_reason": reason,
        "execution_result": {"status": status},
    }


def truth(**pairs):
    """pairs: payment_id -> (category, recoverable)."""
    return {pid: {"category": cat, "recoverable": rec} for pid, (cat, rec) in pairs.items()}


# --- the honest denominator ---------------------------------------------------


def test_rate_divides_by_recoverable_not_total():
    entries = [
        row("p1", "bank_downtime", execute.CAPTURED),
        row("p2", "bank_downtime", execute.FAILED),
        row("p3", "dead_instrument", execute.LINK_SENT, action=decide.SEND_LINK),
        row("p4", "dead_instrument", execute.LINK_SENT, action=decide.SEND_LINK),
    ]
    data = metrics.report(entries, truth(
        p1=("bank_downtime", True), p2=("bank_downtime", True),
        p3=("dead_instrument", False), p4=("dead_instrument", False),
    ))
    # 1 recovered of 2 recoverable = 50%. Dividing by the 4 total would say 25%.
    assert data["recoverable"] == 2
    assert data["dead"] == 2
    assert data["recovery_rate"] == 0.5


def test_dead_failures_are_reported_not_hidden():
    entries = [row("p1", "dead_instrument", execute.LINK_SENT, action=decide.SEND_LINK)]
    data = metrics.report(entries, truth(p1=("dead_instrument", False)))
    assert data["dead"] == 1
    assert data["total"] == 1


def test_money_uses_the_recoverable_denominator_too():
    entries = [
        row("p1", "bank_downtime", execute.CAPTURED, amount=50_000),
        row("p2", "bank_downtime", execute.FAILED, amount=30_000),
        row("p3", "dead_instrument", execute.LINK_SENT, action=decide.SEND_LINK, amount=90_000),
    ]
    data = metrics.report(entries, truth(
        p1=("bank_downtime", True), p2=("bank_downtime", True),
        p3=("dead_instrument", False),
    ))
    assert data["recovered_paise"] == 50_000
    # The dead card's 90,000 must not appear in the recoverable pool.
    assert data["recoverable_paise"] == 80_000


# --- safety counters ----------------------------------------------------------


def test_retrying_an_unrecoverable_payment_counts_as_a_false_intervention():
    # The failure mode that matters: a misdiagnosed dead card actually being charged.
    entries = [row("p1", "bank_downtime", execute.FAILED, action=decide.RETRY)]
    data = metrics.report(entries, truth(p1=("dead_instrument", False)))
    assert data["false_intervention"] == 1


def test_links_and_escalations_on_dead_payments_are_not_false_interventions():
    entries = [
        row("p1", "dead_instrument", execute.LINK_SENT, action=decide.SEND_LINK),
        row("p2", "exhausted", execute.ESCALATED, action=decide.ESCALATE),
    ]
    data = metrics.report(entries, truth(
        p1=("dead_instrument", False), p2=("exhausted", False),
    ))
    assert data["false_intervention"] == 0


def test_refused_actions_are_not_false_interventions():
    # Nothing fired, so nothing was done wrong.
    entries = [row("p1", "dead_instrument", execute.NOT_ATTEMPTED, action=None,
                   gate_result=gate.REFUSE, reason=gate.DEAD_INSTRUMENT_NEVER_RETRIED)]
    data = metrics.report(entries, truth(p1=("dead_instrument", False)))
    assert data["false_intervention"] == 0


def test_a_capture_on_an_unrecoverable_payment_is_flagged():
    # Structurally impossible via execute(), but checked from the output side too.
    entries = [row("p1", "bank_downtime", execute.CAPTURED)]
    data = metrics.report(entries, truth(p1=("dead_instrument", False)))
    assert data["impossible_recoveries"] == 1


# --- diagnosis accuracy, reported alongside recovery --------------------------


def test_accuracy_is_split_by_source():
    entries = [
        row("p1", "bank_downtime", execute.CAPTURED, source="rules"),
        row("p2", "bank_downtime", execute.FAILED, source="llm"),
        row("p3", "bank_downtime", execute.FAILED, source="llm"),
    ]
    data = metrics.report(entries, truth(
        p1=("bank_downtime", True),
        p2=("bank_downtime", True),
        p3=("insufficient_funds", True),  # the llm got this one wrong
    ))
    assert data["diagnosis_by_source"]["rules"] == {"total": 1, "correct": 1}
    assert data["diagnosis_by_source"]["llm"] == {"total": 2, "correct": 1}
    assert data["misclassified"] == 1


def test_afa_gated_payments_are_counted_and_broken_out():
    entries = [row("p1", "bank_downtime", execute.LINK_SENT, action=decide.REQUIRES_AFA,
                   amount=2_000_000, gate_result=gate.CONVERT,
                   reason=gate.AFA_REQUIRED_ABOVE_THRESHOLD)]
    data = metrics.report(entries, truth(p1=("bank_downtime", True)))
    assert data["afa_gated"] == 1
    assert data["afa_paise"] == 2_000_000
    # Recoverable but deliberately not retried — it must not read as a recovery.
    assert data["recovered"] == 0


# --- budget-limited runs must be legible --------------------------------------


def test_budget_exhaustion_is_surfaced():
    # Otherwise a bounded run looks like a low recovery rate rather than a cap.
    entries = [row("p1", "bank_downtime", execute.NOT_ATTEMPTED, action=None,
                   gate_result=gate.REFUSE, reason=gate.GLOBAL_BUDGET_EXHAUSTED)]
    data = metrics.report(entries, truth(p1=("bank_downtime", True)))
    assert data["budget_exhausted"] is True
    assert data["refused_by_reason"][gate.GLOBAL_BUDGET_EXHAUSTED] == 1


def test_budget_flag_is_false_on_a_clean_run():
    entries = [row("p1", "bank_downtime", execute.CAPTURED)]
    data = metrics.report(entries, truth(p1=("bank_downtime", True)))
    assert data["budget_exhausted"] is False


def test_empty_batch_does_not_divide_by_zero():
    data = metrics.report([], {})
    assert data["recovery_rate"] == 0.0
    assert data["diagnosis_accuracy"] == 0.0
