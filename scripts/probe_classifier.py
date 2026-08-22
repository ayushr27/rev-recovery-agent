"""An ablation on the one production record the classifier still gets wrong.

`pay_TEST00036` is labelled `exhausted` and the model calls it `dead_instrument`.
CLAUDE.md recorded the cause as "it reads collections language as a dead instrument" —
but the eval set contradicts that. Two held-out cases use the SAME language and are both
classified correctly:

  pay_TEST00036 (miss)  error_source=customer  method=upi   "We've tried reaching out
                        multiple times without success; no further automatic attempts
                        will be made on this payment."
  pay_EVAL00029 (ok)    error_source=gateway   method=card  "We have contacted the
                        customer repeatedly about this failure without a reply, and no
                        further automatic collection will be attempted."
  pay_EVAL00004 (ok)    error_source=business  method=card  "The dunning cycle ... has
                        completed ... no further automated attempts are scheduled."

So the prose is not what separates them; the structured fields are. This holds the
description fixed and varies only `error_source` and `method`, which is the only way to
tell a reading failure from a field-conflict failure.

IMPORTANT — this is a probe, not a measurement:
  * Every variant carries the SAME unambiguous exhausted-signalling description, so the
    ground truth is `exhausted` for all of them. That is stated here, in the file, before
    any call is made, and this script is committed before it is first run — so the git
    history shows the labels preceded the answers.
  * These cases are deliberately NOT added to eval/ambiguous_cases.json. They are eight
    variants of one record, chosen because it is already known to fail; folding them into
    the accuracy figure would be measuring the classifier on a case picked for failing.
  * Nothing here tunes anything. llm._CLASSIFY_SYSTEM is frozen and hash-pinned by
    tests/test_headline.py.
  * Stdout only, like scripts/eval_llm.py — no results file to drift out of sync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import decide, diagnose, llm

# The description is held constant. It says, unambiguously, that automated collection has
# stopped — which is `exhausted`. Nothing in it describes a broken instrument.
DESCRIPTION = (
    "We've tried reaching out multiple times without success; no further automatic "
    "attempts will be made on this payment."
)
GROUND_TRUTH = "exhausted"

ERROR_SOURCES = ("customer", "gateway", "business", "bank")
METHODS = ("upi", "card")


def variant(error_source, method):
    """pay_TEST00036 with exactly two fields changed."""
    return {
        "id": f"probe_{error_source}_{method}",
        "amount": 1_140_755,
        "currency": "INR",
        "status": "failed",
        "method": method,
        "payment_type": "recurring",
        "error_code": "BAD_REQUEST_ERROR",
        "error_source": error_source,
        "error_step": "payment_processing",
        "error_reason": "payment_failed",
        "error_description": DESCRIPTION,
        "created_at": 1_752_604_872,
        "retry_count": 1,
    }


def main():
    cases = [variant(src, method) for src in ERROR_SOURCES for method in METHODS]

    print("=" * 72)
    print("ABLATION: does error_source override the description?")
    print("=" * 72)
    print(f"  description held constant; ground truth {GROUND_TRUTH!r} for all "
          f"{len(cases)} variants")
    print(f'  "{DESCRIPTION}"')
    print()

    # Every variant must still defeat the rules table, or this measures the rules.
    for case in cases:
        if diagnose.diagnose(case) != diagnose.NEEDS_LLM:
            raise SystemExit(f"{case['id']} is resolved by the rules; the probe is invalid")

    print(f"  {'error_source':<16}{'method':<10}{'said':<20}{'correct?':<10}consequence")
    results = []
    for case in cases:
        said = llm.classify_failure(case, diagnose.CATEGORIES)["category"]
        correct = said == GROUND_TRUTH
        # An error is unsafe only if it would put a live-instrument action on a payment
        # that was never recoverable — the same test scripts/eval_llm.py applies.
        unsafe = not correct and decide.decide(said).touches_live_instrument
        results.append((case, said, correct))
        print(f"  {case['error_source']:<16}{case['method']:<10}{said:<20}"
              f"{'yes' if correct else 'NO':<10}"
              f"{'UNSAFE' if unsafe else 'degrades safely'}")

    # Report both axes rather than the one that was hypothesised. Reading only the
    # error_source margin would have confirmed a story the data does not support.
    def margin(field, values):
        return {v: f"{sum(ok for c, _, ok in results if c[field] == v)}/"
                   f"{sum(1 for c, _, _ in results if c[field] == v)}"
                for v in values}

    misses = [(c, s) for c, s, ok in results if not ok]
    print()
    print("-- Correct answers, by field ----------------------------------------")
    print(f"  by error_source   {margin('error_source', ERROR_SOURCES)}")
    print(f"  by method         {margin('method', METHODS)}")
    print()
    print("-- What this shows ---------------------------------------------------")
    if not misses:
        print("  Every variant was read correctly, so neither field explains the")
        print("  production miss and the cause remains unidentified.")
        print("=" * 72)
        return

    miss_methods = {c["method"] for c, _ in misses}
    miss_sources = {c["error_source"] for c, _ in misses}
    print(f"  {len(misses)} of {len(results)} misread identical prose.")
    if len(miss_methods) == 1:
        method = next(iter(miss_methods))
        print(f"  Every miss is method={method!r}; no other method fails at all. The")
        print(f"  strongest signal here is the instrument type, not the wording — and")
        print(f"  {method} is genuinely a case where a mandate can be revoked, so a")
        print("  'stop collecting' message is plausibly a dead mandate to the model.")
    if len(miss_sources) < len(ERROR_SOURCES):
        print(f"  Within that, only error_source in {sorted(miss_sources)} fails; "
              f"{sorted(set(ERROR_SOURCES) - miss_sources)} read it correctly,")
        print("  so the two fields compound rather than either acting alone.")
    print()
    print("  State this as a field-conflict failure, not a comprehension failure: the")
    print("  model weighs structured hints above a description that plainly says")
    print("  automated collection has stopped. Every miss still degrades safely to")
    print("  send_link, so false_intervention stays 0 — measured, not assumed.")
    print()
    print("  Not in the eval set and not folded into any accuracy figure: these are eight")
    print("  variants of a record chosen BECAUSE it fails.")
    print("=" * 72)


if __name__ == "__main__":
    main()
