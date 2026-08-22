"""The eval set is only worth something if it is genuinely ambiguous and well formed.

These tests make no LLM calls — they check the set itself, not the model's answers, so
a failure here means the measurement instrument is broken rather than the classifier.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

import config
from core import detect, diagnose
from scripts.eval_llm import check_integrity

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "eval" / "ambiguous_cases.json"
FIXTURES_PATH = ROOT / "fixtures" / "failed_payments.json"

RECOVERABLE = {"bank_downtime": True, "insufficient_funds": True,
               "dead_instrument": False, "exhausted": False}


@pytest.fixture(scope="module")
def cases():
    return json.loads(CASES_PATH.read_text())


def test_the_shipped_set_passes_its_own_integrity_check(cases):
    assert check_integrity(cases) == []


def test_every_case_defeats_the_rules_table(cases):
    # The point of the set. A case the rules can resolve is not ambiguous, and including
    # it would inflate the measured accuracy with work the LLM never actually does.
    for case, stripped in zip(cases, detect.strip_eval_fields(cases)):
        assert diagnose.diagnose(stripped) == diagnose.NEEDS_LLM, case["id"]


def test_no_case_leaks_an_eval_field_into_the_classifier(cases):
    # What the model sees must be what the pipeline would see.
    for stripped in detect.strip_eval_fields(cases):
        for field in detect.EVAL_ONLY_FIELDS:
            assert field not in stripped


def test_ids_are_unique_and_disjoint_from_the_production_batch(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    fixture_ids = {r["id"] for r in json.loads(FIXTURES_PATH.read_text())}
    assert not (fixture_ids & set(ids))


def test_every_label_is_a_real_category(cases):
    for case in cases:
        assert case["_ground_truth_category"] in diagnose.CATEGORIES, case["id"]


def test_recoverability_agrees_with_the_label(cases):
    # A disagreement here would corrupt the unsafe-error count, which is the number that
    # says whether the classifier's mistakes actually matter.
    for case in cases:
        assert case["_recoverable"] == RECOVERABLE[case["_ground_truth_category"]], case["id"]


def test_every_case_carries_an_auditable_label(cases):
    # A reader must be able to challenge any label without taking it on trust.
    for case in cases:
        assert case["_label_rationale"].strip(), case["id"]
        assert case["_provenance"].strip(), case["id"]


def test_all_four_categories_are_represented_in_both_splits(cases):
    for split in ("dev", "heldout"):
        present = {c["_ground_truth_category"] for c in cases if c["_split"] == split}
        assert present == set(diagnose.CATEGORIES), split


def test_the_set_is_not_dominated_by_one_category(cases):
    # A lopsided set would make the headline accuracy a measure of the majority class.
    counts = Counter(c["_ground_truth_category"] for c in cases)
    assert min(counts.values()) >= 5
    assert max(counts.values()) <= 2 * min(counts.values())


def test_retry_counts_stay_below_the_cap(cases):
    # At or above the cap the rules resolve the record as exhausted before the LLM is
    # ever consulted, which is the most likely way this set could silently stop testing
    # what it claims to test.
    for case in cases:
        assert case["retry_count"] < config.MAX_RETRY_ATTEMPTS, case["id"]


def test_the_integrity_check_actually_catches_a_bad_label(cases):
    # Guard against the check vacuously passing.
    broken = json.loads(json.dumps(cases[:1]))
    broken[0]["_ground_truth_category"] = "not_a_category"
    assert check_integrity(broken)


def test_the_integrity_check_catches_a_case_the_rules_can_resolve(cases):
    resolvable = json.loads(json.dumps(cases[:1]))
    resolvable[0]["error_reason"] = "card_expired"  # rules resolve this immediately
    problems = check_integrity(resolvable)
    assert any("not ambiguous" in problem for problem in problems)
