"""Measure the LLM classifier on the held-out ambiguous set.

The agent's headline diagnosis accuracy is dominated by the rules table, which resolves
46 of 50 records perfectly. That figure says almost nothing about the part that can
actually be wrong. This measures only the ambiguous tail — the records the rules decline
to classify — which is the LLM's real job and, because the gate cannot catch a
misdiagnosis, the project's genuine residual risk.

Calls llm.classify_failure(), the same function the agent uses, never a reimplementation.

Two deliberate refusals:
  * No accuracy threshold. This exits non-zero on integrity violations only. A pass/fail
    bar on accuracy is exactly the pressure that produces tuning against the eval set.
  * No results file. Stdout only, so no number here can drift out of sync with the
    README by sitting in a stale artifact.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import decide, detect, diagnose, llm
from report import stats

CASES_PATH = Path(__file__).resolve().parent.parent / "eval" / "ambiguous_cases.json"
RECOVERABLE = {"bank_downtime": True, "insufficient_funds": True,
               "dead_instrument": False, "exhausted": False}


def check_integrity(cases):
    """Return a list of problems that make the SET invalid — not the model's answers."""
    problems = []
    stripped = {c["id"]: s for c, s in zip(cases, detect.strip_eval_fields(cases))}

    for case in cases:
        cid = case["id"]
        if diagnose.diagnose(stripped[cid]) != diagnose.NEEDS_LLM:
            problems.append(f"{cid}: resolved by the rules table, so it is not ambiguous")
        if case.get("_ground_truth_category") not in diagnose.CATEGORIES:
            problems.append(f"{cid}: label {case.get('_ground_truth_category')!r} is not a category")
        elif case.get("_recoverable") != RECOVERABLE[case["_ground_truth_category"]]:
            problems.append(f"{cid}: _recoverable disagrees with its label")
        if case.get("_split") not in ("dev", "heldout"):
            problems.append(f"{cid}: missing or unknown _split")
        for field in ("_label_rationale", "_provenance"):
            if not case.get(field):
                problems.append(f"{cid}: missing {field}")

    for cid, count in Counter(c["id"] for c in cases).items():
        if count > 1:
            problems.append(f"{cid}: duplicate id")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "heldout", "all"), default="heldout")
    args = parser.parse_args()

    all_cases = json.loads(CASES_PATH.read_text())
    problems = check_integrity(all_cases)

    cases = [c for c in all_cases if args.split == "all" or c.get("_split") == args.split]
    stripped = {c["id"]: s for c, s in zip(all_cases, detect.strip_eval_fields(all_cases))}

    # Provenance, so a changed prompt visibly invalidates any figure quoted elsewhere.
    prompt_hash = hashlib.sha256(llm._CLASSIFY_SYSTEM.encode("utf-8")).hexdigest()[:12]
    cached_before = len(llm._load_cache())

    print("=" * 72)
    print(f"LLM CLASSIFIER — {args.split.upper()} SPLIT")
    print("=" * 72)
    print(f"  cases            {len(cases)} of {len(all_cases)}")
    print(f"  classify prompt  sha256:{prompt_hash}")
    print(f"  model            {os.environ.get('GROQ_MODEL', 'openai/gpt-oss-20b')}")

    results = [(case, llm.classify_failure(stripped[case["id"]], diagnose.CATEGORIES))
               for case in cases]

    served_live = len(llm._load_cache()) - cached_before
    print(f"  cache            {len(cases) - served_live} served from cache, {served_live} live")
    print()

    correct = [(c, v) for c, v in results if v["category"] == c["_ground_truth_category"]]
    misses = [(c, v) for c, v in results if v["category"] != c["_ground_truth_category"]]
    invalid = [(c, v) for c, v in results if not v["valid"]]

    print("-- Accuracy on the ambiguous tail ------------------------------------")
    print(f"  {stats.format_interval(len(correct), len(cases))}")
    print(f"  out-of-spec answers defaulted to escalation: {len(invalid)}")
    print()

    print("-- Confusion (rows = truth, columns = predicted) ---------------------")
    print(f"  {'':<21}" + "".join(f"{c[:11]:>13}" for c in diagnose.CATEGORIES))
    for truth in diagnose.CATEGORIES:
        row = Counter(v["category"] for c, v in results if c["_ground_truth_category"] == truth)
        print(f"  {truth:<21}" + "".join(f"{row.get(p, 0):>13}" for p in diagnose.CATEGORIES))
    print()

    # Accuracy alone cannot say whether the errors matter. This can: an error is unsafe
    # only if it would put a live-instrument action on a payment that was never
    # recoverable. It is the false_intervention idea applied to the classifier.
    unsafe = [
        (c, v) for c, v in misses
        if not c["_recoverable"] and decide.decide(v["category"]).touches_live_instrument
    ]
    print("-- Are the errors dangerous? -----------------------------------------")
    print(f"  errors that would touch a live instrument on a dead payment: {len(unsafe)}")
    print(f"  errors that degrade safely (wrong, but harmless):            {len(misses) - len(unsafe)}")
    for case, verdict in unsafe:
        print(f"    UNSAFE {case['id']}: {case['_ground_truth_category']} -> {verdict['category']}")
    print()

    if misses:
        print("-- Every miss, with the reasoning behind its label --------------------")
        for case, verdict in misses:
            print(f"  {case['id']}: said {verdict['category']}, "
                  f"labelled {case['_ground_truth_category']}")
            print(f"      why that label: {case['_label_rationale']}")
        print()

    print("NOTE: this set is balanced across categories and does not match the")
    print("production mix, so it measures the classifier, not the batch. The cases and")
    print("their labels were authored by the same person, which is a real limitation;")
    print("_provenance and _label_rationale are recorded so both can be audited.")

    if problems:
        print("\nINTEGRITY FAILURES (the set itself is invalid):")
        for problem in problems:
            print(f"  {problem}")
    print("=" * 72)

    # Deliberately not gated on accuracy — see the module docstring.
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
