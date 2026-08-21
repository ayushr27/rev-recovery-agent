"""All tunable constants for the recovery agent live here — nowhere else."""

# Retry / attempt bounds
MAX_RETRY_ATTEMPTS = 3       # total attempts including the original retry_count
GLOBAL_ATTEMPT_BUDGET = 100  # hard cap on total actions across a batch run

# Per-category cooling-off window before the same payment can be retried again.
# insufficient_funds (24h) reflects standard payment-processor retry practice, not a
# hard RBI rule. bank_downtime is shorter because it's a transient outage, not an
# auth/mandate issue.
COOLING_OFF_HOURS = {
    "bank_downtime": 2,
    "insufficient_funds": 24,
}

# Applied to categories with no entry above (dead_instrument, exhausted). Their
# interventions are a payment link or a human escalation rather than a retry, but they
# still must not re-fire at will — don't re-send the same link to a customer twice in
# a day. Also the window used to bucket idempotency keys for those actions.
DEFAULT_COOLING_OFF_HOURS = 24

# RBI Digital Payments E-mandate Framework (2026): recurring transactions above this
# amount require additional-factor authentication and cannot be silently reattempted.
AFA_THRESHOLD_PAISE = 1_500_000  # ₹15,000

# error_reason values that are explicit, machine-readable proof the instrument is dead.
# The gate re-derives dead-instrument evidence from these, independently of whatever the
# classifier concluded. Three things about this list:
#
#   1. It DELIBERATELY duplicates the dead_instrument entries in core/diagnose.py's
#      REASON_RULES. Do not replace it with an import. The regression it exists to catch
#      is someone editing REASON_RULES — importing that table would delete the safety
#      check at the same moment the mistake is made. A drift test in tests/test_gate.py
#      keeps the two in step deliberately rather than accidentally.
#   2. It is a domain fact, not a tuning dial. Widening it is a design decision.
#   3. It covers explicit reason codes ONLY, never free text. Evidence that exists only
#      in error_description is invisible here, by design — reading prose is the LLM
#      tail's job, and doing it badly in the safety layer would be worse than not at all.
HARD_DEAD_INSTRUMENT_REASONS = frozenset({"card_expired", "card_blocked", "invalid_card"})

# Simulated execution. Outcomes are derived from a hash of the payment id rather than
# a running rng, so the recovered figure is identical no matter what order the batch is
# processed in — and stays identical across runs.
SIMULATION_SEED = 0

# Probability that a gated retry converts to `captured`, per category. A transient bank
# outage has usually cleared by the time we retry; an underfunded account has often not
# been topped up. Recoverable categories only — execute.simulate() structurally refuses
# to let an unrecoverable category succeed, whatever is configured here.
SIMULATED_RETRY_SUCCESS_RATE = {
    "bank_downtime": 0.75,
    "insufficient_funds": 0.45,
}

# Set False (or pass --no-explain) to skip LLM rationale generation on dev runs.
EXPLAIN_WITH_LLM = True

# Runtime file locations (kept here so no module hardcodes a path).
RUN_STATE_PATH = "run_state.json"
AUDIT_LOG_PATH = "audit_log.jsonl"
METRICS_PATH = "metrics.json"  # written each run; the UI reads it

# Fault-injected runs write here instead, so a demonstration can never overwrite the
# honest trail the UI pairs with metrics.json.
INJECTED_AUDIT_LOG_PATH = "audit_log.injected.jsonl"
LLM_CACHE_PATH = "llm_cache.json"
FIXTURES_PATH = "fixtures/failed_payments.json"
