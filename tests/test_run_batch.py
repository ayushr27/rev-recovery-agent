"""Fault injection must be visible, contained, and actually exercise the gate.

`process()` is called with `persist` left at its default no-op, so nothing here writes
run_state.json, and no test writes an audit file — assertions are made on the returned
entry rather than on disk.
"""

import json
from pathlib import Path

import pytest

import run_batch
from core import decide, detect, diagnose, gate
from report import metrics

NOW = 1_755_000_000
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "failed_payments.json"


@pytest.fixture(scope="module")
def records():
    return {r["id"]: r for r in detect.strip_eval_fields(json.loads(FIXTURES.read_text()))}


def test_an_honest_run_marks_every_row_as_untampered(records):
    entry = run_batch.process(records["pay_TEST00004"], gate.new_state(), NOW, use_llm_explain=False)
    assert entry["injected_fault"] is None


def test_injection_records_the_real_verdict_alongside_the_forced_one(records):
    entry = run_batch.process(
        records["pay_TEST00011"], gate.new_state(), NOW,
        use_llm_explain=False, inject_category="bank_downtime",
    )
    fault = entry["injected_fault"]
    assert entry["source"] == "injected_fault"
    assert fault["forced_category"] == "bank_downtime"
    # What the agent genuinely concluded is preserved, so the tampering is auditable.
    assert fault["actual_category"] == "dead_instrument"
    assert fault["actual_source"] == "rules"


def test_the_gate_catches_a_misdiagnosis_backed_by_hard_evidence(records):
    # pay_TEST00011 carries error_reason=invalid_card. Relabelling it does not change
    # what the record says, so the evidence rule refuses the retry.
    entry = run_batch.process(
        records["pay_TEST00011"], gate.new_state(), NOW,
        use_llm_explain=False, inject_category="bank_downtime",
    )
    assert entry["gate_result"] == gate.REFUSE
    assert entry["refusal_reason"] == gate.DEAD_INSTRUMENT_EVIDENCE_IN_RECORD
    assert entry["action_taken"] is None


def test_the_gate_cannot_catch_a_misdiagnosis_evidenced_only_in_prose(records):
    # pay_TEST00024 is equally dead, but only its description says so. This asserts the
    # documented limit: the retry fires. Shown live in the demo rather than hidden.
    entry = run_batch.process(
        records["pay_TEST00024"], gate.new_state(), NOW,
        use_llm_explain=False, inject_category="bank_downtime",
    )
    assert entry["gate_result"] == gate.ALLOW
    assert entry["action_taken"] == decide.RETRY


def test_metrics_refuses_to_score_an_injected_run(records):
    entries = [
        run_batch.process(records["pay_TEST00011"], gate.new_state(), NOW,
                          use_llm_explain=False, inject_category="bank_downtime")
    ]
    truth = {"pay_TEST00011": {"category": "dead_instrument", "recoverable": False}}
    with pytest.raises(ValueError, match="fault-injected"):
        metrics.report(entries, truth)


# --- the CLI's fail-closed validations ----------------------------------------


def test_injection_specs_are_parsed():
    assert run_batch._parse_injections(["pay_TEST00011=bank_downtime"]) == {
        "pay_TEST00011": "bank_downtime"
    }


@pytest.mark.parametrize("bad", ["garbage", "=bank_downtime", "pay_TEST00011=", ""])
def test_malformed_specs_are_rejected(bad):
    # A typo must never yield a clean-looking run that then gets presented as evidence.
    with pytest.raises(SystemExit):
        run_batch._parse_injections([bad])


def test_an_unknown_category_is_rejected():
    with pytest.raises(SystemExit):
        run_batch._parse_injections(["pay_TEST00011=not_a_category"])


def test_every_valid_category_is_accepted():
    for category in diagnose.CATEGORIES:
        assert run_batch._parse_injections([f"pay_X={category}"]) == {"pay_X": category}
