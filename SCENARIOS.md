# SCENARIOS.md — demo beats

## Demo running order

Roughly four minutes. Every step runs from the committed cache — **zero live LLM calls**,
so there is no rate-limit or outage risk on stage.

1. **The number.** `python run_batch.py` → the report. Lead with the honest denominator:
   21 of **32 recoverable**, not 21 of 50. Point at "18 truly dead — correctly never
   retried" and say why inflating the denominator would be the easy lie. Be precise if
   asked: those 18 are excluded from the *rate*, not abandoned — 12 get a recovery link
   and 6 escalate to a human. Every one is actioned; none is retried.
2. **Say the baseline out loud.** Without the agent, 0 recovered and all ₹4,60,979.07
   stays failed. That is what the recovery is measured against.
3. **Diagnosis accuracy, in the same breath.** 98% overall, rules 46/46, LLM 3 of 4.
   Volunteer the miss before anyone finds it — and note it degraded safely to a link
   rather than a retry, so false interventions stayed 0.
4. **Refusal #1 — the AFA threshold** (`pay_TEST00003`, ₹87,970 recurring). The gate
   refuses the silent retry and converts it to an authentication link. This is the
   strongest beat: a real RBI rule, and it only applies to *recurring* debits — the
   one-time records above ₹15,000 correctly pass straight through.
5. **Refusal #2 — graceful escalation** (`pay_TEST00006`). At the retry cap, handed to a
   human rather than retried.
6. **Both sides of the safety net.** The strongest two minutes of the demo. Say first
   that the gate trusts the diagnosis, so a wrong label is the real risk — then show
   what happens when you force one:

   ```bash
   python run_batch.py --inject-misdiagnosis pay_TEST00011=bank_downtime   # REFUSED
   python run_batch.py --inject-misdiagnosis pay_TEST00024=bank_downtime   # retry FIRES
   ```

   Same forced fault, same category, two genuinely dead cards. `pay_TEST00011` carries
   `error_reason: invalid_card`, so the gate re-derives that from the record and refuses
   with `DEAD_INSTRUMENT_EVIDENCE_IN_RECORD` no matter what it was labelled.
   `pay_TEST00024` says it only in prose — and it gets retried and reports `captured`.

   Show the second one. It is the boundary of the safety net, stated by us rather than
   discovered by a judge, and the honest framing is: *the gate independently re-derives
   the evidence that is machine-readable; it cannot read prose, so that residual risk is
   measured instead — `false_intervention` after the fact, and `eval_llm.py` for how
   often the classifier is wrong to begin with.*

   Both runs are visibly demonstrations: separate audit file, no metrics computed, no
   state persisted, and `metrics.report()` refuses to score them at all.

7. **How good is the LLM, actually?** `python scripts/eval_llm.py --split heldout` →
   22/22 on the held-out set, 95% CI [85.1%, 100%], zero unsafe errors. Contrast with
   3/4 on production, whose interval is [30%, 95%] — a number worth nothing. Volunteer
   the one miss across all 30 and its named failure mode: the classifier reads
   "collections has stopped pursuing this" as a dead instrument. It degrades safely.

8. **Idempotency.** `python run_batch.py --resume` → all 50 refused, 0 actioned. Same
   payments, same window, nothing double-fired. This is the scalability primitive.
9. **Volume.** `python run_batch.py --n 500` → the global budget halts the batch at 100
   actions and the report says so explicitly, rather than letting the lower rate read as
   failure.
10. **The real object.** Show `plink_TSNNNlcJUtCEV6` (below) — a genuine Razorpay
    test-mode payment link. Pre-captured; do not depend on a live call on stage.
11. **The UI**, if there is time: `cd app && npm run dev`. Refusals are colour-coded.

If asked "what about high load?": the decision core is pure and idempotent, so it
parallelises and replays safely; scaling is an I/O-layer swap, deliberately not built.


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

`DEAD_INSTRUMENT_EVIDENCE_IN_RECORD` also never fires on honest data — verified at
n=50, 500 and 5000 — because any record carrying an explicit dead-instrument reason is
already routed to `send_link` before the gate sees it. It exists for the case where the
classification path is wrong, and `--inject-misdiagnosis` is how you show it working
(beat 6).

## Real Razorpay test-mode object

Created 2026-08-21 via `python run_batch.py --real-link pay_TEST00011`:

- Payment link id: `plink_TSNNNlcJUtCEV6`
- URL: https://rzp.io/rzp/pjEekd81
- Amount: 1056863 paise (₹10,568.63), status `created`

Test mode only, notifications disabled. Pre-captured here as the demo backup so a stalled
live call at demo time is not a problem — per the plan's belt-and-suspenders note.
