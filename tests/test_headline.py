"""The published numbers, pinned.

Every figure in README.md, SCENARIOS.md and the UI derives from one 50-record run, and
nothing asserted them. `metrics.json` is gitignored, so there was no golden file either —
a change that quietly moved the money would have been caught only by someone re-reading
the README against a fresh run. For a project whose thesis is honest reporting, the
reported numbers are worth a test.

Runs the real pipeline over the committed fixture with explanations off: no network, and
no writes — `process()` is called with `persist` at its default no-op and no audit path.
"""

import hashlib
import json
from pathlib import Path

import run_batch
from core import detect, diagnose, gate, llm
from report import metrics

# Fixed so the run is reproducible; the gate measures cooling-off windows against it.
NOW = 1_787_380_244

# The published headline. If one of these changes, the README is now wrong.
EXPECTED = {
    "total": 50,
    "recoverable": 32,
    "dead": 18,
    "recovered": 21,
    "escalated": 6,
    "refused": 0,
    "afa_gated": 4,
    "false_intervention": 0,
    "impossible_recoveries": 0,
    "misclassified": 1,
    "recoverable_paise": 46_097_907,
    "recovered_paise": 14_180_184,
    "afa_paise": 23_930_177,
}


def _entries():
    state = gate.new_state()
    return [
        run_batch.process(record, state, NOW, use_llm_explain=False)
        for record in detect.load_failures()
    ]


def _report():
    return metrics.report(_entries(), detect.load_ground_truth())


def test_the_published_numbers_have_not_moved():
    report = _report()
    assert {key: report[key] for key in EXPECTED} == EXPECTED


def test_the_headline_rupee_figure():
    # The one number on the README, the UI and the demo: Rs 141,801.84.
    assert _report()["recovered_paise"] == 14_180_184


def test_the_rate_is_recovered_over_recoverable_not_over_total():
    # The honesty claim itself. Dividing by 50 would read 42%, and would be the easy lie.
    report = _report()
    assert report["recovery_rate"] == report["recovered"] / report["recoverable"]
    assert round(report["recovery_rate"], 4) == 0.6562


def test_diagnosis_accuracy_and_its_split_by_source():
    report = _report()
    assert report["diagnosis_accuracy"] == 0.98
    assert report["diagnosis_by_source"]["rules"] == {"total": 46, "correct": 46}
    assert report["diagnosis_by_source"]["llm"] == {"total": 4, "correct": 3}


def test_the_llm_tail_is_exactly_four_records():
    # If the rules table ever resolves these, the LLM stops being exercised and the
    # "3 of 4" figure silently describes a different set.
    assert sum(e["source"].startswith("llm") for e in _entries()) == 4


def test_the_classify_prompt_has_not_drifted():
    """The frozen prompt, actually frozen.

    CLAUDE.md and the README both argue that one character of drift voids the four
    production cache entries, so `complete()` raises on a keyless clone,
    `classify_failure()` falls back to `exhausted`, two payments stop retrying, and the
    headline changes — silently. eval_llm.py prints this hash; nothing asserted it.

    Changing the prompt deliberately: regenerate the cache with live calls, re-run the
    keyless fresh-clone gate, and update this hash in the same commit.
    """
    digest = hashlib.sha256(llm._CLASSIFY_SYSTEM.encode("utf-8")).hexdigest()
    assert digest == "6ea4b525de0678ac55eee3ba88b7dcd8a0242eae8c113ee99f73d820600a750d"


def test_every_llm_call_the_batch_needs_is_already_cached():
    # What makes the demo hermetic. A miss means a keyless clone silently defaults that
    # record to `exhausted` instead of classifying it.
    cache = llm._load_cache()
    ambiguous = [r for r in detect.load_failures() if diagnose.diagnose(r) == diagnose.NEEDS_LLM]
    assert ambiguous, "no ambiguous records — the LLM path is not being exercised at all"
    for record in ambiguous:
        key = llm._cache_key(llm._CLASSIFY_SYSTEM, llm._classify_prompt(record))
        assert key in cache, f"{record['id']} would require a live API call"


def test_the_fixture_file_itself_has_not_changed():
    # The numbers above mean nothing if the batch underneath them was regenerated.
    path = Path(__file__).resolve().parent.parent / "fixtures" / "failed_payments.json"
    records = json.loads(path.read_text())
    assert len(records) == 50
    assert sum(r["_recoverable"] for r in records) == 32
