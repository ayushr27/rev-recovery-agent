# SCENARIOS.md — demo beats

Filled in during Phase 1 after fixture generation, with the actual payment ids that
hit each named demo beat. If any beat has no matching id, fixtures get adjusted until
it does — this file is what keeps fixture generation honest.

| Beat                                                              | Payment id     | Notes |
|---------------------------------------------------------------------|----------------|-------|
| Successful recovery (recoverable → retry → captured)                | `pay_TEST00004`| `bank_downtime`, recurring, ₹4,492.68 → retried → `captured`. 20 of the batch recover. |
| Dead-card retry refused (`dead_instrument`)                         | `pay_TEST00011`| `invalid_card`, retry_count=0. Gate must refuse any retry. |
| AFA-threshold refusal (recoverable, `recurring`, > ₹15,000)          | `pay_TEST00003`| `insufficient_funds`, recurring, amount ₹87,970.25 (> 1,500,000 paise). 4 such records exist total. |
| Graceful escalation (`exhausted` / retry cap hit)                   | `pay_TEST00006`| retry_count == MAX_RETRY_ATTEMPTS (3) already. |
| LLM-resolved ambiguous case (rules returned `needs_llm`)             | `pay_TEST00024`| One of 4 atypical-signature records; ground truth `dead_instrument` (`authentication_failed`, source mismatched as `bank`), description-only signal. |

## Refusals: which ones fire, and when

Worth knowing before the demo — several gate bounds are deliberately *backstops* that
never trigger in a clean run, because `decide()` already routes away from them. That is
correct defence-in-depth, not dead code (every one is proven by a test in
`tests/test_gate.py`), but it means the visible refusals depend on how you run it.

**A single fresh run** (`python run_batch.py`) shows:
- 4 × `AFA_REQUIRED_ABOVE_THRESHOLD` — recurring debits above ₹15,000 converted to
  `requires_afa` instead of silently retried. The strongest beat: domain knowledge,
  not a generic guardrail.
- 6 × escalation — `exhausted` payments handed to a human rather than retried.

**A 500-record run** (`python run_batch.py --n 500 --no-llm`) shows the global budget
firing: 400 × `GLOBAL_BUDGET_EXHAUSTED` once 100 actions are spent. The report flags
this explicitly so the lower recovery rate reads as the bound working rather than the
agent failing. A good answer to "what happens at volume?" — it stops, on purpose, and
says so.

**Running it twice** (`python run_batch.py` then `python run_batch.py --resume`) shows
the whole batch refuse: 38 × `DUPLICATE_ACTION_IN_WINDOW` and 12 × `RETRY_CAP_EXCEEDED`,
0 actioned. This is the idempotency story made visible — the same payments, the same
window, nothing double-fires.

`DEAD_INSTRUMENT_NEVER_RETRIED` and `GLOBAL_BUDGET_EXHAUSTED` do not fire naturally:
`decide()` never proposes a retry for a dead instrument, and 50 payments never exhaust a
100-action budget. Both are covered by tests.

## Real Razorpay test-mode object

Created 2026-08-21 via `python run_batch.py --real-link pay_TEST00011`:

- Payment link id: `plink_TSNNNlcJUtCEV6`
- URL: https://rzp.io/rzp/pjEekd81
- Amount: 1056863 paise (₹10,568.63), status `created`

Test mode only, notifications disabled. Pre-captured here as the demo backup so a stalled
live call at demo time is not a problem — per the plan's belt-and-suspenders note.
