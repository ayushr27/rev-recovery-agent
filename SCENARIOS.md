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

6.5. **What is the AI actually worth, and what does the number rest on?**
   `python scripts/analyze_value.py` — the two questions a sharp judge asks.

   *"46 of 50 records never touch a model — is the AI decoration?"* Answer in rupees:
   **₹12,183.03, 8.6% of the headline**, is recovered only because the ambiguous tail was
   classified instead of escalated. Confirm live with `--no-llm`: 20 of 32, ₹1,29,618.81.
   Volunteer that this is small. The 98% accuracy is a rules result and saying otherwise
   claims a lookup table's work for the model.

   *"Your success rates are made up."* Correct — so they are swept. ±20% moves the
   headline between ₹1,28,518 and ₹1,73,183; the structural floor and ceiling are ₹0 and
   ₹2,21,677.30. The ceiling is set by the **gate**, not the success rate: only 28 records
   were ever allowed a retry. Say "read it as a point on a curve, not a measurement"
   before anyone else does.

7. **How good is the LLM, actually?** `python scripts/eval_llm.py --split heldout` →
   22/22 on the held-out set, 95% CI [85.1%, 100%], zero unsafe errors. Contrast with
   3/4 on production, whose interval is [30%, 95%] — a number worth nothing. Volunteer
   the one miss across all 30 and its named failure mode: the classifier reads
   "collections has stopped pursuing this" as a dead instrument. It degrades safely.

8. **Idempotency.** Pin the clock on BOTH commands so the beat is reproducible:

   ```bash
   python run_batch.py            --now 1787380244
   python run_batch.py --resume   --now 1787380244
   ```

   All 50 refused, 0 actioned — 38 duplicates, 12 on the retry cap. Same payments, same
   window, nothing double-fired. This is the scalability primitive. (Without `--now`, a
   gap of more than two hours reopens `bank_downtime`'s cooling-off window and 14 actions
   correctly fire — see "Stopping rules" below before anyone asks.)
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
| Successful recovery (recoverable → retry → captured)                | `pay_TEST00004`| `bank_downtime`, recurring, ₹4,492.68 → retried → `captured`. 21 of the batch recover. |
| Dead-card retry refused (`dead_instrument`)                         | `pay_TEST00011`| `invalid_card`, retry_count=0. Gate must refuse any retry. |
| AFA-threshold refusal (recoverable, `recurring`, > ₹15,000)          | `pay_TEST00003`| `insufficient_funds`, recurring, amount ₹87,970.25 (> 1,500,000 paise). 4 such records exist total. |
| Graceful escalation (`exhausted` / retry cap hit)                   | `pay_TEST00006`| retry_count == MAX_RETRY_ATTEMPTS (3) already. |
| LLM-resolved ambiguous case (rules returned `needs_llm`)             | `pay_TEST00024`| One of 4 atypical-signature records; ground truth `dead_instrument` (`authentication_failed`, source mismatched as `bank`), description-only signal. |

## Stopping rules: which ones fire, and when

**Say the three outcomes precisely — a judge will check.** The gate has three verdicts,
and only one of them is a refusal:

| Verdict | Meaning | On a fresh run |
|---|---|---|
| `REFUSE` | nothing fires | **0** — the report says `Refused by the gate  0` |
| `CONVERT` | the retry is refused, but an auth link goes out | 4 × `AFA_REQUIRED_ABOVE_THRESHOLD` |
| `ALLOW` | the action fires | 46, of which 6 are escalations to a human |

So do **not** say "the fresh run shows six refusals". It shows zero refusals, four
conversions and six escalations. AFA is a conversion precisely because the customer is not
abandoned — the retry is blocked and an authentication link is sent instead. Escalation is
an `ALLOW`: handing a payment to a human *is* the action.

Several bounds are deliberately *backstops* that never trigger in a clean run, because
`decide()` already routes away from them. That is defence in depth, not dead code — every
one is proven by a test in `tests/test_gate.py` — but it means the visible refusals depend
on how you run it.

**A 500-record run** (`python run_batch.py --n 500 --no-llm`) shows the global budget
firing: 400 × `GLOBAL_BUDGET_EXHAUSTED` once 100 actions are spent. The report flags this
explicitly so the lower recovery rate reads as the bound working rather than the agent
failing. A good answer to "what happens at volume?" — it stops, on purpose, and says so.
Use `--no-llm`: at n=500 there are 40 ambiguous records and only 6 are in the committed
cache, so a bare `--n 500` either makes 34 live API calls or silently defaults them.

**Running it twice, with the clock pinned:**

```bash
python run_batch.py            --now 1787380244
python run_batch.py --resume   --now 1787380244
```

38 × `DUPLICATE_ACTION_IN_WINDOW`, 12 × `RETRY_CAP_EXCEEDED`, 0 actioned. **Pass the same
`--now` to both** — that is what makes the beat reproducible. Without it the second run
uses the wall clock, and if more than two hours have passed, `bank_downtime`'s cooling-off
window has expired and 14 actions legitimately fire instead. That is the gate working
correctly (a new window is a new attempt, not a duplicate), but it is the opposite of the
beat you meant to show. If asked, the honest claim is: *within the attempt window nothing
double-fires, and the retry cap holds regardless of how long you wait.*

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
