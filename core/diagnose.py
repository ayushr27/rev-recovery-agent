"""Rules-first categorization of a failed payment into one of the four recovery
categories, falling back to the sentinel "needs_llm" for the ambiguous tail.

Pure logic over a single record — no I/O, no LLM call. Records this table cannot
resolve cleanly are handed to the LLM tail by the caller (Phase 5), never guessed at
here. Guessing is what produces a wrong recoverable/dead denominator downstream.
"""

import config

CATEGORIES = ("bank_downtime", "insufficient_funds", "dead_instrument", "exhausted")

# Sentinel: the rules table declined to classify. Not a category.
NEEDS_LLM = "needs_llm"

# Razorpay's error_reason is the single most direct signal when it is specific.
# Generic reasons ("payment_failed", "timeout", "authentication_failed") are
# deliberately absent — they carry no category information on their own.
REASON_RULES = {
    "insufficient_funds": "insufficient_funds",
    "card_expired": "dead_instrument",
    "card_blocked": "dead_instrument",
    "invalid_card": "dead_instrument",
}

# (error_code, error_source, error_step) → category. The structured triple is the
# reliable machine signal; this is the workhorse rule.
SIGNATURE_RULES = {
    ("GATEWAY_ERROR", "bank", "payment_authorization"): "bank_downtime",
    ("BAD_REQUEST_ERROR", "customer", "payment_authorization"): "insufficient_funds",
    ("BAD_REQUEST_ERROR", "customer", "payment_authentication"): "dead_instrument",
    ("BAD_REQUEST_ERROR", "business", "payment_authorization"): "exhausted",
}

# Narrow, high-precision phrases only, checked against error_description as a last
# resort before deferring to the LLM. These are unambiguous statements of cause, not
# general keyword matching — loose matching on free text is exactly the fuzzy work
# the LLM tail exists to do, and doing it here badly would silently mislabel records.
KEYWORD_RULES = (
    ("insufficient balance", "insufficient_funds"),
    ("card has expired", "dead_instrument"),
    ("has been blocked", "dead_instrument"),
)


def _match(record):
    """Return (category, rule_name). category is NEEDS_LLM when nothing resolves."""
    # A payment already at the attempt cap is exhausted regardless of why it failed.
    if record.get("retry_count", 0) >= config.MAX_RETRY_ATTEMPTS:
        return "exhausted", "retry_cap"

    reason = record.get("error_reason")
    if reason in REASON_RULES:
        return REASON_RULES[reason], "error_reason"

    signature = (
        record.get("error_code"),
        record.get("error_source"),
        record.get("error_step"),
    )
    if signature in SIGNATURE_RULES:
        return SIGNATURE_RULES[signature], "signature"

    description = (record.get("error_description") or "").lower()
    for phrase, category in KEYWORD_RULES:
        if phrase in description:
            return category, "keyword"

    return NEEDS_LLM, "unresolved"


def diagnose(record):
    """Return one of CATEGORIES, or NEEDS_LLM if the rules cannot resolve the record."""
    category, _rule = _match(record)
    return category
