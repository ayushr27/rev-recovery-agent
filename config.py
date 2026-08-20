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

# RBI Digital Payments E-mandate Framework (2026): recurring transactions above this
# amount require additional-factor authentication and cannot be silently reattempted.
AFA_THRESHOLD_PAISE = 1_500_000  # ₹15,000

# Runtime file locations (kept here so no module hardcodes a path).
RUN_STATE_PATH = "run_state.json"
AUDIT_LOG_PATH = "audit_log.jsonl"
LLM_CACHE_PATH = "llm_cache.json"
FIXTURES_PATH = "fixtures/failed_payments.json"
