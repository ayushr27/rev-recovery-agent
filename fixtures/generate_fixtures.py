"""Generates a reproducible, deliberately-mixed batch of synthetic failed Razorpay
payments (failed_payments.json) covering all four recovery categories plus an
ambiguous tail for the LLM diagnosis path.

Each record mirrors Razorpay's real failed-payment shape, plus two eval-only fields
(`_ground_truth_category`, `_recoverable`) that the pipeline must never read outside
`report/metrics.py`.
"""

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

SEED = 42
BASE_TIMESTAMP = 1755000000  # fixed reference point so created_at stays reproducible
OUTPUT_PATH = Path(__file__).resolve().parent.parent / config.FIXTURES_PATH

METHODS = ["upi", "card", "netbanking"]

RECOVERABLE = {
    "bank_downtime": True,
    "insufficient_funds": True,
    "dead_instrument": False,
    "exhausted": False,
}

# Fixed (error_code, error_source, error_step) signature per deterministic category —
# this is what Phase 2's rules table keys off. Kept distinct across categories on
# purpose (different error_step for insufficient_funds vs dead_instrument, etc.).
CATEGORY_SIGNATURE = {
    "bank_downtime": ("GATEWAY_ERROR", "bank", "payment_authorization"),
    "insufficient_funds": ("BAD_REQUEST_ERROR", "customer", "payment_authorization"),
    "dead_instrument": ("BAD_REQUEST_ERROR", "customer", "payment_authentication"),
    "exhausted": ("BAD_REQUEST_ERROR", "business", "payment_authorization"),
}

# (error_reason, error_description) variants per category, for realistic variety.
CATEGORY_VARIANTS = {
    "bank_downtime": [
        ("payment_failed", "Your payment could not be completed due to a temporary issue at the bank. Please try again."),
        ("payment_failed", "The bank's servers are currently experiencing downtime. Please retry shortly."),
        ("payment_failed", "Payment declined due to a temporary connectivity issue with the issuing bank."),
    ],
    "insufficient_funds": [
        ("insufficient_funds", "You do not have sufficient balance in your account to complete this transaction."),
        ("insufficient_funds", "The payment failed because the linked account does not have enough funds."),
        ("insufficient_funds", "Insufficient balance to process this payment. Please add funds and try again."),
    ],
    "dead_instrument": [
        ("card_expired", "Your card has expired. Please use a different payment method."),
        ("card_blocked", "This card has been blocked by the issuing bank and cannot be used."),
        ("invalid_card", "The card number entered is invalid."),
    ],
    "exhausted": [
        ("payment_failed", "This payment has failed after the maximum number of retry attempts and requires manual review."),
        ("payment_failed", "All automatic retry attempts for this payment have been exhausted."),
    ],
}

# Deliberately messy cases. Each case's (error_code, error_source, error_step) is
# chosen to NOT match any CATEGORY_SIGNATURE above, and its error_reason is a generic
# one the reason table doesn't key on — so the rules table cannot cleanly resolve it.
# Only the free-text description carries the real signal, which is exactly what the
# LLM tail exists to read.
#
# `retry_count` overrides the category default when set. The exhausted case needs one:
# without it the record would carry retry_count == MAX_RETRY_ATTEMPTS and the rules
# table would resolve it deterministically via the retry-cap rule, leaving nothing
# ambiguous about it. At retry_count=1 the record is genuinely judgment-requiring —
# its description is about out-of-band dunning outreach ("tried reaching out"), not
# payment retries, so the two signals honestly disagree.
AMBIGUOUS_CASES = [
    {
        "category": "insufficient_funds",
        "error_code": "GATEWAY_ERROR",
        "error_source": "bank",
        "error_step": "payment_processing",
        "error_reason": "payment_failed",
        "error_description": "The transaction did not go through because the available balance was too low to cover this payment.",
        "retry_count": None,
    },
    {
        "category": "bank_downtime",
        "error_code": "SERVER_ERROR",
        "error_source": "gateway",
        "error_step": "payment_processing",
        "error_reason": "timeout",
        "error_description": "We experienced a brief service interruption while contacting your bank; this usually clears up within a couple of hours.",
        "retry_count": None,
    },
    {
        "category": "dead_instrument",
        "error_code": "GATEWAY_ERROR",
        "error_source": "bank",
        "error_step": "payment_authentication",
        "error_reason": "authentication_failed",
        "error_description": "The card used for this transaction is no longer valid and has been permanently deactivated by the issuer.",
        "retry_count": None,
    },
    {
        "category": "exhausted",
        "error_code": "BAD_REQUEST_ERROR",
        "error_source": "customer",
        "error_step": "payment_processing",
        "error_reason": "payment_failed",
        "error_description": "We've tried reaching out multiple times without success; no further automatic attempts will be made on this payment.",
        "retry_count": 1,
    },
]

# Category mix, expressed as fractions of the ~50-record baseline so --n scales the
# same proportions (Phase 5 wants a scaled batch for the volume demo).
DETERMINISTIC_FRACTIONS = {
    "bank_downtime": 18 / 50,
    "insufficient_funds": 12 / 50,
    "dead_instrument": 10 / 50,
    "exhausted": 6 / 50,
}
ONE_TIME_FRACTION = 7 / 50   # ~6-8 one_time records at n=50
AFA_FRACTION = 4 / 50        # ~3-4 high-amount recurring records at n=50

AFA_LOW_PAISE = (9_900, 1_499_900)      # ₹99 – ₹14,999 (below the AFA threshold)
AFA_HIGH_PAISE = (1_800_000, 9_000_000)  # ₹18,000 – ₹90,000 (above it)


def _method_for(rng, category):
    if category == "dead_instrument":
        return rng.choices(METHODS, weights=[1, 4, 1])[0]  # mostly card
    return rng.choice(METHODS)


def _retry_count_for(rng, category):
    if category == "exhausted":
        return config.MAX_RETRY_ATTEMPTS
    if category == "dead_instrument":
        return rng.randint(0, 1)
    return rng.randint(0, config.MAX_RETRY_ATTEMPTS - 1)


def _amount_paise(rng, high_afa):
    lo, hi = AFA_HIGH_PAISE if high_afa else AFA_LOW_PAISE
    return rng.randint(lo, hi)


def _build_record(rng, idx, slot, payment_type, high_afa):
    retry_override = None
    if slot["ambiguous"] is not None:
        case = slot["ambiguous"]
        category = case["category"]
        error_code = case["error_code"]
        error_source = case["error_source"]
        error_step = case["error_step"]
        error_reason = case["error_reason"]
        description = case["error_description"]
        retry_override = case["retry_count"]
    else:
        category = slot["category"]
        error_code, error_source, error_step = CATEGORY_SIGNATURE[category]
        error_reason, description = rng.choice(CATEGORY_VARIANTS[category])

    record = {
        "id": f"pay_TEST{idx:05d}",
        "amount": _amount_paise(rng, high_afa),
        "currency": "INR",
        "status": "failed",
        "method": _method_for(rng, category),
        "payment_type": payment_type,
        "error_code": error_code,
        "error_source": error_source,
        "error_step": error_step,
        "error_reason": error_reason,
        "error_description": description,
        "created_at": BASE_TIMESTAMP - rng.randint(0, 30 * 24 * 3600),
        "retry_count": _retry_count_for(rng, category),
        "_ground_truth_category": category,
        "_recoverable": RECOVERABLE[category],
    }

    # Applied after the fact, never in place of the _retry_count_for call above, so
    # the rng stream stays identical whether or not an override exists — otherwise
    # overriding one record would shift every record generated after it.
    if retry_override is not None:
        record["retry_count"] = retry_override
    return record


def generate(n=50, seed=SEED):
    rng = random.Random(seed)

    counts = {cat: round(n * frac) for cat, frac in DETERMINISTIC_FRACTIONS.items()}
    ambiguous_count = max(1, n - sum(counts.values()))

    slots = []
    for category, count in counts.items():
        slots += [{"category": category, "ambiguous": None}] * count
    for i in range(ambiguous_count):
        slots.append({"category": None, "ambiguous": AMBIGUOUS_CASES[i % len(AMBIGUOUS_CASES)]})
    rng.shuffle(slots)

    # one_time: only from the clean bank_downtime / insufficient_funds / dead_instrument
    # slots — exhausted and the hand-crafted ambiguous cases stay recurring so those
    # two demo beats aren't muddied by an extra dimension.
    one_time_pool = [
        i for i, s in enumerate(slots)
        if s["category"] in ("bank_downtime", "insufficient_funds", "dead_instrument")
    ]
    one_time_target = min(len(one_time_pool), max(1, round(n * ONE_TIME_FRACTION)))
    one_time_idx = set(rng.sample(one_time_pool, one_time_target))

    # AFA-eligible high amounts: only recoverable, recurring, clean-signature records.
    afa_pool = [
        i for i, s in enumerate(slots)
        if s["category"] in ("bank_downtime", "insufficient_funds") and i not in one_time_idx
    ]
    afa_target = min(len(afa_pool), max(3, round(n * AFA_FRACTION)))
    afa_idx = set(rng.sample(afa_pool, afa_target))

    records = []
    for i, slot in enumerate(slots):
        payment_type = "one_time" if i in one_time_idx else "recurring"
        records.append(_build_record(rng, idx=i + 1, slot=slot, payment_type=payment_type, high_afa=(i in afa_idx)))
    return records


def _is_clean_signature(record):
    signature = CATEGORY_SIGNATURE.get(record["_ground_truth_category"])
    return signature == (record["error_code"], record["error_source"], record["error_step"])


def _print_summary(records):
    cat_counts = Counter(r["_ground_truth_category"] for r in records)
    type_counts = Counter(r["payment_type"] for r in records)
    atypical = [r for r in records if not _is_clean_signature(r)]
    afa_eligible = [
        r for r in records
        if r["payment_type"] == "recurring" and r["_recoverable"] and r["amount"] > config.AFA_THRESHOLD_PAISE
    ]

    print(f"Wrote {len(records)} records to {OUTPUT_PATH}")
    print("By ground-truth category:")
    for cat in ("bank_downtime", "insufficient_funds", "dead_instrument", "exhausted"):
        print(f"  {cat}: {cat_counts.get(cat, 0)}")
    print(f"By payment_type: {dict(type_counts)}")
    print(f"Atypical signature (rules can't cleanly resolve -> needs_llm): {len(atypical)}")
    print(f"AFA-eligible (recurring, recoverable, > AFA_THRESHOLD_PAISE): {len(afa_eligible)}")

    print("\nCandidate SCENARIOS.md ids:")
    dead = next((r for r in records if r["_ground_truth_category"] == "dead_instrument"), None)
    exhausted = next((r for r in records if r["_ground_truth_category"] == "exhausted"), None)
    print(f"  dead-card retry refused:  {dead['id'] if dead else 'NONE FOUND'}")
    print(f"  AFA-threshold refusal:    {afa_eligible[0]['id'] if afa_eligible else 'NONE FOUND'}")
    print(f"  graceful escalation:      {exhausted['id'] if exhausted else 'NONE FOUND'}")
    print(f"  LLM-resolved ambiguous:   {atypical[0]['id'] if atypical else 'NONE FOUND'}")
    print("  successful recovery:      TBD — needs Phase 4's seeded execute() outcome")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=50, help="number of records to generate")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    records = generate(n=args.n, seed=args.seed)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(records, indent=2) + "\n")

    _print_summary(records)


if __name__ == "__main__":
    main()
