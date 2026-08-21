"""Phase 2 Definition of Done check: run the rules table over the batch and print a
confusion count against ground truth.

For human eyes only — the pipeline itself never reads ground truth outside
report/metrics.py. Safe to delete once Phase 2 is confirmed.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import detect, diagnose

if __name__ == "__main__":
    records = detect.load_failures()
    truth = detect.load_ground_truth()

    leaked = [f for r in records for f in detect.EVAL_ONLY_FIELDS if f in r]
    print(f"Records loaded: {len(records)}")
    print(f"Eval-only fields leaked into the pipeline: {len(leaked)} (must be 0)\n")

    by_rule = Counter()
    resolved_correct, resolved_wrong, deferred = [], [], []

    for record in records:
        category, rule = diagnose._match(record)
        by_rule[rule] += 1
        expected = truth[record["id"]]["category"]

        if category == diagnose.NEEDS_LLM:
            deferred.append((record["id"], expected))
        elif category == expected:
            resolved_correct.append(record["id"])
        else:
            resolved_wrong.append((record["id"], expected, category))

    resolved = len(resolved_correct) + len(resolved_wrong)
    print(f"Resolved by rules: {resolved}")
    print(f"  correct:        {len(resolved_correct)}")
    print(f"  MISCLASSIFIED:  {len(resolved_wrong)}")
    print(f"Deferred to LLM (needs_llm): {len(deferred)}\n")

    print("Which rule fired:")
    for rule, count in by_rule.most_common():
        print(f"  {rule}: {count}")

    if resolved_wrong:
        print("\nMisclassifications (id, expected, got):")
        for row in resolved_wrong:
            print(f"  {row[0]}: expected {row[1]}, got {row[2]}")

    print("\nDeferred records and their (unseen) true categories:")
    for pid, expected in deferred:
        print(f"  {pid}: {expected}")

    ok = not resolved_wrong and not leaked and len(deferred) == 4
    print(f"\nPhase 2 DoD: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
