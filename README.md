# Revenue Recovery Agent

A bounded agent that recovers failed Razorpay payments — and, just as importantly,
**refuses to act when a rule says stop**.

It runs over a batch of synthetic failed-payment records and, for each one, diagnoses the
root cause, decides an intervention, checks that intervention against stateful stopping
rules, executes it, explains it in one plain sentence, and writes an auditable trace.

```
Recovered Rs 141,801.84 of Rs 460,979.07 recoverable across 50 failed payments.
21 of 32 recoverable payments (65.6%)  ·  diagnosis accuracy 98.0%  ·  false interventions 0
Baseline without the agent: 0 recovered, all Rs 460,979.07 stays failed.
```

---

## The loop

```
                  ┌──────────── decision core: pure, no I/O ────────────┐
                  │                                                     │
fixtures          │  diagnose  ───▶  decide  ───▶  gate                  │
   │              │  rules, else     category→     caps · cooling-off ·  │
   ▼              │  LLM tail        intervention  idempotency · AFA ·   │
 detect ─────────▶│                                global budget         │──▶ execute ──▶ explain ──▶ audit
  (I/O)           │                                                     │     (I/O)       (LLM)      (I/O)
                  └─────────────────────────────────────────────────────┘                              │
                                                                                                       ▼
                                                                                             report/metrics.py
                                                                                    (the only reader of ground truth)
```

`run_batch.py` is the whole agent in one screen. Read it first.

## The four recovery categories

| Category             | Recoverable? | Intervention                                 |
|----------------------|--------------|----------------------------------------------|
| `bank_downtime`      | yes          | retry at T+2h (transient outage)             |
| `insufficient_funds` | yes          | retry at T+24h + nudge                       |
| `dead_instrument`    | **no**       | send update-payment-method link, NEVER retry |
| `exhausted`          | **no**       | STOP, escalate to a human                    |

Rules resolve the deterministic majority — 46 of 50 records, all correct. Only the
genuinely ambiguous tail goes to an LLM: 4 records whose error codes are misleading and
whose real signal sits in the free-text description. We do not use a model where a lookup
table is exact.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_batch.py
```

**No API key is needed.** The committed `llm_cache.json` holds every LLM response this
batch produces, so a clean clone reproduces the numbers above with zero network calls —
verified by cloning into an empty directory with no `.env` present.

Optional, for live LLM calls or the real Razorpay path: `cp .env.example .env` and fill in
a Groq (or Gemini) key and Razorpay **test-mode** credentials.

### Other runs

| Command | What it shows |
|---|---|
| `python run_batch.py` | the headline run — recovery, AFA conversions, escalations |
| `python run_batch.py --resume` | **the idempotency proof** — every action refused as a duplicate |
| `python run_batch.py --n 500` | volume, and the global budget correctly halting the batch |
| `python run_batch.py --real-link pay_TEST00011` | creates one **real** Razorpay test-mode payment link |
| `python run_batch.py --no-llm` | rules only, no model involved |
| `pytest tests/ -q` | 87 tests — every stopping rule, execution honesty, LLM containment, metric arithmetic |

### The UI

```bash
cd app && npm install && npm run dev     # http://localhost:3000
```

Requires Node 20+ (Next.js 16). The Python agent has no Node dependency — if the UI is
inconvenient, `python run_batch.py` prints the same numbers.

A single page: the headline number, a summary strip, and the full audit table with
refusals, AFA conversions and escalations colour-coded. Run the batch first — the page
reads `metrics.json` and `audit_log.jsonl`, and re-reads them on every refresh.

---

## Why the number is honest

This is the part worth scrutinising, so here is exactly how it is computed.

**The denominator is `recoverable`, not `total`.** Recovery rate is
`recovered ÷ recoverable` = 21 ÷ 32. Dividing by all 50 payments would take credit for
correctly *not* chasing 18 dead cards. Those 18 are reported separately as "correctly not
pursued" — never hidden, never quietly folded into the denominator to flatter the rate.

**Diagnosis accuracy is always printed next to the recovery rate.** A recovery rate alone
is untrustworthy: misclassifying one payment silently moves it between the recoverable and
dead buckets and distorts the denominator itself. The two numbers only mean something
together.

**AFA-gated payments are broken out, not buried.** Four recoverable payments were
deliberately *not* retried, because regulation requires authentication first. Counting them
as plain misses would report compliance as failure; counting them as recoveries would be a
lie. They appear as ₹2,39,301.77 awaiting authentication — not written off.

**A dead payment can never be simulated into a recovery.** `execute.simulate()` refuses to
let an unrecoverable category succeed — structurally, not probabilistically. A test proves
this holds even with a 100% success rate configured for `dead_instrument`.

**The baseline is stated explicitly.** Without the agent nothing is recovered and the full
₹4,60,979.07 stays failed. The recovery is measured against that, not invented.

**What is assumed rather than measured.** Retry outcomes are simulated, with success rates
(75% for a transient bank outage, 45% for insufficient funds) chosen as plausible values —
**not** derived from real data. The recovery figure inherits that assumption. What the
prototype demonstrates is the decision logic; the success rates stand in for a production
feedback loop. Likewise the LLM resolved 3 of 4 ambiguous records — a sample of four, far
too small to quote as an accuracy rate, and reported as a count for that reason.

---

## Bounded by construction

Every action passes a stateful gate before it fires, and each refusal returns a structured
reason — the trail never shows a bare "blocked".

| Rule | Refusal reason |
|---|---|
| Never retry a dead instrument | `DEAD_INSTRUMENT_NEVER_RETRIED` |
| Max attempts, counting the processor's own prior tries | `RETRY_CAP_EXCEEDED` |
| Recurring debit above ₹15,000 → authentication, not a silent retry | `AFA_REQUIRED_ABOVE_THRESHOLD` |
| Same payment + action inside its window | `DUPLICATE_ACTION_IN_WINDOW` |
| Payment actioned too recently | `COOLING_OFF_ACTIVE` |
| Batch-wide action budget spent | `GLOBAL_BUDGET_EXHAUSTED` |

Every bound is a constant in `config.py` — one file, no magic numbers scattered through the
modules. "Can you change the retry cap?" is a one-line answer.

Some of these are backstops that never fire on a clean run, because `decide()` already
routes away from them. That is defence in depth, not dead code, and each is proven by a
test. `SCENARIOS.md` documents which refusals appear under which run.

### Idempotency is a real key, not a boolean

Each action carries `hash(payment_id + action_type + attempt_window)`, shown in the audit
trail. A boolean "already tried" cannot distinguish a legitimate later attempt from a
duplicate of the current one; a windowed key can. Run the batch twice and the second pass
refuses all 50 actions — 38 as duplicates, 12 on the retry cap — with nothing double-fired.

---

## Regulatory grounding

> Recovery actions model RBI Digital Payments E-mandate Framework (2026) behaviour:
> retries above ₹15,000 are gated behind additional-factor authentication rather than
> silently reattempted, and retry actions carry a pre-debit notification step.
> Cooling-off windows reflect standard payment-processor retry practice. This is a
> synthetic test-mode prototype, not a production-compliant system.

Scoping that claim precisely matters. The ₹15,000 AFA threshold is a real, current RBI rule
and applies **only to recurring auto-debits** — the gate checks `payment_type` before
applying it, because applying it to one-time checkout failures would be factually wrong.
The cooling-off windows are processor practice, not regulation. The pre-debit notification
is recorded as one field in the audit trace to show correct sequencing; no notification
infrastructure was built, and none is claimed.

---

## Scalability & robustness

**Scalability.** The decision core (`diagnose` → `decide` → `gate`) is stateless and
idempotent: every action carries an idempotency key, so it is safe to retry, parallelise,
and replay without double-charging. Because the logic is a pure function of
(record + state), it scales horizontally without modification. The decision core is cleanly
separated from the I/O layer (`detect` / `execute` / `audit`). Moving from prototype to
production means swapping the I/O layer — fixtures → Razorpay webhook ingestion, JSON files
→ Postgres for state/audit, the synchronous loop → a job queue with workers for real
scheduled retries — while the decision logic carries over unchanged. The prototype
validates the hard part (correct bounded logic); the scaling is well-understood
infrastructure, architected for but intentionally not built here.

**Robustness.** The agent is bounded by construction — a global attempt budget, per-payment
retry caps, cooling-off windows, and dead-instrument rules make runaway or infinite-loop
behaviour impossible. Every run is deterministic and reproducible (seeded fixtures, seeded
outcomes, cached LLM responses). Failure paths fail closed: a missing API key degrades to
simulation with a logged notice; an out-of-spec LLM label defaults to human escalation
rather than a wrong action. Every decision is fully auditable.

That the gate is I/O-free is not tidiness — it is what makes the claim checkable. A test
asserts `gate.evaluate()` never mutates state, and `now` is passed in rather than read from
the clock, so the gate is deterministic and replayable.

---

## Why now

Payments are moving toward agent-initiated commerce — AP2, x402, NPCI's UAP. As software
begins initiating payments on a user's behalf, the hard question stops being "can the agent
act?" and becomes "can it be *stopped*, and can it prove what it did?" This is a small,
complete answer to the second question: a bounded decision engine with an audit trail,
where refusing is a first-class outcome rather than an error path.

---

## Layout

```
config.py              every tunable bound, in one place
run_batch.py           the whole loop, in one screen
core/
  detect.py            load fixtures, strip eval-only fields    (I/O)
  diagnose.py          rules table → category, or "needs_llm"   (pure)
  decide.py            category → intervention                  (pure)
  gate.py              the stopping rules                       (pure)
  execute.py           simulate, or one real test-mode call     (I/O)
  explain.py           LLM → one-sentence rationale             (I/O)
  audit.py             append-only JSONL trail                  (I/O)
  llm.py               provider interface + response cache      (I/O)
report/metrics.py      the honest report; only reader of ground truth
fixtures/              seeded generator + the committed 50-record batch
app/                   Next.js single-page audit view
tests/                 87 tests
SCENARIOS.md           demo beats mapped to payment ids
CLAUDE.md              build state and the decisions log
```

Generated at runtime and gitignored: `audit_log.jsonl`, `run_state.json`, `metrics.json`.
Committed on purpose: `fixtures/failed_payments.json` and `llm_cache.json`, so the run is
reproducible and hermetic.

## Future work

Diagnosis is the obvious place a trained scorer would replace hand-written rules, once
there is labelled outcome data to train on. Two things would need to come first: real
retry-outcome data to replace the assumed success rates, and a labelled ambiguous set far
larger than four records. Neither is a change to the gate, which is the part worth keeping.
