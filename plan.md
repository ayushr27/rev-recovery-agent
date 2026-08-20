# PLAN.md — Razorpay Buildathon Track 03: AI Revenue Recovery

## For Claude Code

This is a **phase-by-phase build plan**. Execute one phase at a time. Do not skip
ahead. At the end of each phase there is a **Definition of Done** — stop, confirm it
passes, then move to the next phase. Ask me before making architectural changes that
deviate from this document.

The whole system must stay **legible**: a person should be able to read `run_batch.py`
and understand the entire agent in one screen. If a phase pushes complexity, prefer the
simpler version.

---

## PRE-FLIGHT (human does this BEFORE starting Claude Code)

Do not start Phase 0 until these are done — they're the external dependencies that block:

1. **API keys in `.env`:** Groq (or Gemini) key, and Razorpay **test-mode** Key ID +
   Secret. Razorpay test mode is free — generate the key from the Razorpay dashboard now if
   you don't have it. Discovering a missing key mid-build is dead time.
2. **LLM provider chosen:** Groq recommended (fast, generous free tier, fine for 50 records
   with caching). Set `LLM_PROVIDER` accordingly so `llm.py` isn't ambiguous.
3. **Claude Code running** in the repo dir with this `plan.md` and an empty `CLAUDE.md`
   present.
4. **First instruction to Claude Code:** "Create CLAUDE.md first, then start Phase 0. Stop
   at each Definition of Done for my confirmation before proceeding."

Hard constraint: **wrap by Sept 3**, hard deadline Sept 5. If Sept 2 arrives with a working
gate + honest metrics + rough demo, SHIP and stop — do not chase UI polish into the buffer.

---

## What we are building (context — read fully before coding)

A **bounded agent that recovers failed Razorpay payments**. It runs over a batch of
synthetic failed-payment records and, for each one:

1. **Detects** the failure (loads from fixtures)
2. **Diagnoses** the root cause into one of four recovery categories (rules for the
   deterministic majority, an LLM for the ambiguous tail)
3. **Decides** the right intervention
4. **Gates** the action against stopping rules (caps, cooling-off, dead-instrument,
   idempotency) — the agent must visibly *refuse* when a rule says stop
5. **Executes** the action (simulated by default; one real Razorpay test-mode API call
   available as a credibility path)
6. **Explains** every decision in one plain-language line (LLM)
7. **Audits** — appends an auditable trace per payment
8. **Reports** batch metrics with an honest recoverable-vs-dead denominator

The judging bar (memorize this — every phase serves it):
> "Don't just identify the problem. Show measured money recovered across a batch, with
> compliant escalation, stopping rules, and an audit trail."

### Non-negotiable design rules
- **The rupee number must be honest.** Recovery rate = `recovered / recoverable`, NOT
  `recovered / total`. Truly-dead failures are reported as "correctly not pursued," never
  hidden.
- **Rules where rules win, LLM where it earns its place.** Do not LLM what a lookup table
  does deterministically. The LLM is for: (a) genuinely ambiguous failures, and (b)
  turning a decision trace into a human-readable rationale.
- **The gate is stateful.** Attempt counts and cooling windows persist across the batch
  run, not per-record in isolation. Idempotency lives here: same payment + same action
  inside a cooling window = refuse, do not double-fire.
- **No real training.** This is a decision system, not a learned model. There is no
  training set and no model fitting anywhere. Fixtures are for *running* and *reporting*,
  not training.
- **Decision core stays separate from I/O — enforce this throughout.** The decision core
  (`diagnose`, `decide`, `gate`) must be pure logic over (record + state) with no I/O
  inside it: no file reads, no API calls, no DB. All I/O lives in `detect` (input),
  `execute` (actions), `audit` (output). This is not just tidiness — it is what lets the
  project honestly claim scalability: swapping fixtures→webhooks or JSON→Postgres touches
  only the I/O modules while the logic is unchanged. Keep the seam clean in every phase.
  The decision core being stateless + idempotent is the whole scalability story; do not
  leak I/O into it.

### The four recovery categories
| Category             | Recoverable? | Intervention                                  |
|----------------------|--------------|-----------------------------------------------|
| `bank_downtime`      | yes          | retry at T+2h (transient)                     |
| `insufficient_funds` | yes          | retry at T+24h + nudge                        |
| `dead_instrument`    | **no**       | send update-payment-method link, NEVER retry  |
| `exhausted`          | **no**       | STOP, escalate to human                       |

`_recoverable` ground truth = `true` for the first two, `false` for the last two.

---

## Tech stack
- **Agent core:** Python 3.11+, FastAPI (only if we expose an endpoint; CLI-first is fine)
- **LLM:** Groq or Gemini API (whichever key is available; wrap behind one interface so it
  swaps trivially). Prompt-only — no fine-tuning.
- **UI (Phase 6, last):** Next.js + TypeScript — a single page showing the audit table and
  the headline recovery number. Keep it minimal.
- **State:** in-memory dict keyed by payment id, persisted to a small JSON run-state file.
  No database.

---

## Target directory layout
```
recovery-agent/
├── fixtures/
│   ├── generate_fixtures.py
│   └── failed_payments.json
├── core/
│   ├── __init__.py
│   ├── detect.py
│   ├── diagnose.py
│   ├── decide.py
│   ├── gate.py
│   ├── execute.py
│   ├── explain.py
│   ├── audit.py
│   └── llm.py            # single LLM interface (Groq/Gemini swap here) + response cache
├── config.py            # ALL tunable constants live here (caps, windows, thresholds)
├── report/
│   └── metrics.py
├── app/                  # Next.js — BUILD LAST
├── run_batch.py          # orchestrates the whole loop
├── run_state.json        # written at runtime (gitignore)
├── audit_log.jsonl       # written at runtime (gitignore)
├── llm_cache.json        # cached LLM outputs — COMMIT this (demo runs from cache)
├── SCENARIOS.md          # named demo beats mapped to specific fixture payment ids
├── .env.example
├── requirements.txt
└── README.md
```

---

# PHASE 0 — Scaffold & config
**Goal:** repo skeleton that runs, with LLM connectivity proven.

Tasks:
- Create the directory layout above (empty stubs for each module with a docstring naming
  its single responsibility).
- `requirements.txt`: `python-dotenv`, the chosen LLM SDK (`groq` or
  `google-generativeai`), `razorpay`, `fastapi` + `uvicorn` (optional), `pytest`.
- `.env.example` with `LLM_PROVIDER=groq`, `GROQ_API_KEY=`, `GEMINI_API_KEY=`,
  `RAZORPAY_KEY_ID=`, `RAZORPAY_KEY_SECRET=`.
- **`config.py` — ALL tunable constants in ONE place** (no magic numbers scattered in
  modules). At minimum:
  ```python
  MAX_RETRY_ATTEMPTS = 3            # total attempts incl. original retry_count
  GLOBAL_ATTEMPT_BUDGET = 100       # hard cap on total actions across the batch
  COOLING_OFF_HOURS = {            # per-category retry gap (processor practice)
      "bank_downtime": 2,
      "insufficient_funds": 24,
  }
  AFA_THRESHOLD_PAISE = 1_500_000   # ₹15,000 — real RBI E-mandate 2026 rule
  ```
  Every bound the gate enforces reads from here. This makes "bounded" concretely tunable —
  a judge asking "can you change the retry cap?" gets "yes, one constant."
- `core/llm.py`: one function `complete(system: str, user: str) -> str` that reads
  `LLM_PROVIDER` and dispatches to Groq or Gemini. Handle missing key with a clear error.
  **Add a response cache:** key each call by a hash of (system + user), persist to
  `llm_cache.json`. On a cache hit, return the cached response WITHOUT an API call. This
  (a) protects against rate limits, (b) speeds iteration, and (c) — critically — lets the
  **demo run entirely from cache with zero live API calls**. Commit `llm_cache.json` once
  warmed so the demo is hermetic.
- **`SCENARIOS.md`** — a living map of the specific demo beats you're engineering toward,
  each tied to a fixture payment id (filled in during Phase 1). Beats to cover:
  - a successful recovery (recoverable → retry → captured)
  - a dead-card retry **refused** (`dead_instrument`)
  - an **AFA-threshold refusal** (recoverable but > ₹15,000 → `requires_afa`)
  - a graceful **escalation** (`exhausted` / cap hit)
  - an **LLM-resolved ambiguous** case (rules couldn't classify → LLM did)
  This keeps fixture generation honest: you build toward named beats, not hope they emerge.
- `.gitignore`: `.env`, `run_state.json`, `audit_log.jsonl`, `node_modules/`, `__pycache__/`.
  (Do NOT gitignore `llm_cache.json` or `SCENARIOS.md` — those are committed.)

**Definition of Done:** a throwaway script calls `llm.complete("You are terse.", "Say
OK")` and prints a real model response; a second identical call returns from cache with no
API hit. `config.py` and an initial `SCENARIOS.md` skeleton exist. Commit.

---

# PHASE 1 — Fixtures (the foundation)
**Goal:** a deliberate, reproducible batch of ~50 synthetic failed payments.

`fixtures/generate_fixtures.py` writes `failed_payments.json`. Each record mirrors
Razorpay's real failed-payment shape, plus two `eval-only` fields the agent must NOT read
during diagnosis (only `report/metrics.py` may read them):

```json
{
  "id": "pay_TEST00001",
  "amount": 49900,
  "currency": "INR",
  "status": "failed",
  "method": "upi",
  "payment_type": "recurring",
  "error_code": "BAD_REQUEST_ERROR",
  "error_source": "bank",
  "error_step": "payment_authentication",
  "error_reason": "payment_failed",
  "error_description": "Your payment could not be completed due to a temporary issue at the bank.",
  "created_at": 1735900000,
  "retry_count": 0,
  "_ground_truth_category": "bank_downtime",
  "_recoverable": true
}
```

Conventions that must be correct (they signal real platform knowledge):
- `amount` in **paise** (₹499.00 → 49900).
- `method` ∈ {`upi`, `card`, `netbanking`}.
- `payment_type` ∈ {`recurring`, `one_time`}. **This matters:** the RBI AFA threshold rule
  applies ONLY to `recurring` auto-debits, not one-time checkout failures. The gate's
  AFA branch (Phase 3) must check `payment_type == "recurring"` before applying the
  ₹15,000 threshold. Make the batch mostly `recurring` (this is a recurring-payment
  recovery agent) but include ~6–8 `one_time` records — they give you an honest extra
  branch ("one-time failures above ₹15,000 do NOT hit the AFA rule; only recurring do").
  The 3–4 above-₹15,000 records that trigger the AFA path MUST be `recurring`.
- Use realistic Razorpay-style `error_code` / `error_source` / `error_step` values.

**Deliberate mix (engineer it, do not randomize blindly):**
- ~18 `bank_downtime` (recoverable — main win)
- ~12 `insufficient_funds` (recoverable — slower)
- ~10 `dead_instrument` (the "correctly refuses to retry" moment)
- ~6 `exhausted` / already at retry cap (the "escalate gracefully" moment)
- ~4 **deliberately ambiguous** — vague/misleading `error_description`, mismatched
  `error_source` — these MUST exist so the LLM tail has something real to resolve.

**Amount distribution (needed for the AFA threshold branch in Phase 3):** most amounts
below ₹15,000 (1_500_000 paise), but ensure **at least 3–4 recoverable, `recurring`
records have `amount > 1_500_000`** (e.g. ₹18,000–₹90,000). These trigger the AFA-required
refusal path in the gate. Without them, that regulation-grounded branch has nothing to
demonstrate.

After generating, **fill in `SCENARIOS.md`** with the actual payment ids that hit each demo
beat (successful recovery, dead-card refusal, AFA-threshold refusal, escalation,
LLM-resolved ambiguous). If any beat has no matching id, adjust the fixtures until it does.

Make generation seeded (fixed random seed) so the batch is reproducible.

**Definition of Done:** `python fixtures/generate_fixtures.py` produces
`failed_payments.json` with ~50 records in the mix above; counts print to console. Commit
the JSON for reproducibility.

---

# PHASE 2 — Detect + Diagnose (rules only, LLM stubbed)
**Goal:** correctly categorize the deterministic majority; flag the ambiguous tail.

- `core/detect.py`: `load_failures(path) -> list[dict]`. Strips `eval-only` fields into a
  separate ground-truth map so the pipeline literally cannot cheat by reading them.
- `core/diagnose.py`: a **rules table** mapping (`error_code`, `error_source`,
  `error_step`, keywords) → one of the four categories. For records the table cannot
  cleanly resolve, return the sentinel `"needs_llm"`. **Do not call the LLM yet** — stub
  it.

**Definition of Done:** running diagnosis over the batch categorizes the ~46
deterministic records correctly and returns `"needs_llm"` for the ~4 ambiguous ones. Print
a confusion count against ground truth (for your eyes only — the agent still doesn't read
ground truth in-pipeline). Commit.

---

# PHASE 3 — Decide + Gate (the spine — spend the most time here)
**Goal:** map categories to interventions and enforce stateful stopping rules.

- `core/decide.py`: `decide(category) -> Intervention`. Pure mapping per the four-category
  table. An `Intervention` carries: action type (`retry` | `send_link` | `escalate`),
  delay, and whether it touches a live instrument.
- `core/gate.py` — **the differentiator.** Stateful. Reads/writes `run_state.json` keyed
  by payment id. **All bounds read from `config.py`** (no inline magic numbers). Enforces:
  - **Max retry cap** (`config.MAX_RETRY_ATTEMPTS`, total attempts including original
    `retry_count`).
  - **Cooling-off window** (`config.COOLING_OFF_HOURS[category]`): no repeat action on the
    same payment inside its window.
  - **Dead-instrument rule**: never issue `retry` for `dead_instrument`.
  - **Global attempt budget** (`config.GLOBAL_ATTEMPT_BUDGET`) across the batch (guards
    runaway loops).
  - **Idempotency**: same payment + same action inside cooling window → refuse.
  - **Idempotency (use a real key, not a boolean):** generate an idempotency key per
    action as a hash of `(payment_id + action_type + attempt_window)`. Same key seen again
    inside the cooling window → refuse. Show the key in the audit trail. A boolean
    "already tried" is NOT enough — a payments audience treats idempotency keys as the
    real signal you understand the concept.
  - **AFA threshold rule (regulation-grounded — real RBI rule, encode it):** if
    `payment_type == "recurring"` AND `amount > config.AFA_THRESHOLD_PAISE` (₹15,000) AND
    the intervention is `retry`, the gate **refuses the silent retry** and converts the
    action to `requires_afa` → send an authentication link instead. Refusal reason:
    `AFA_REQUIRED_ABOVE_THRESHOLD`. This reflects the RBI Digital Payments E-mandate
    Framework (2026): *recurring* transactions above ₹15,000 require additional-factor
    authentication and cannot be auto-reattempted silently. **The `payment_type` check is
    essential** — this rule does NOT apply to one-time payments, and applying it to them
    would be factually wrong. This is a legitimate extra refusal path AND it makes both the
    `amount` and `payment_type` fields functionally meaningful.
  - Every refusal returns a structured reason (e.g. `RETRY_CAP_EXCEEDED`,
    `AFA_REQUIRED_ABOVE_THRESHOLD`) — this is the "one failure handled gracefully"
    evidence.

- **Pre-debit notification modeling (trace-level, do NOT build real notification infra):**
  any `retry` intervention carries a "notify 24h prior to debit" step recorded in its
  audit trace. In simulation you do not actually wait 24h — you just record that the
  notification precedes the debit, showing the correct sequence per the framework's
  24-hour pre-debit alert rule. This is ONE field in the audit object, not a subsystem.

- **Cooling-off windows are grounded, not arbitrary:** `insufficient_funds → retry at
  T+24h` aligns with standard payment-processor retry practice (processors commonly advise
  waiting 24–48h before reattempting, as retrying too soon can itself cause failure). Keep
  `bank_downtime` shorter (transient outage, not a mandate/auth issue) — that distinction
  is defensible. Note in comments that the 24–48h window reflects processor *practice*, not
  a hard RBI rule (the ₹15,000 AFA threshold IS the hard rule; the cooling window is
  practice).

**Definition of Done:** unit tests in `pytest` prove each rule fires: a capped payment is
refused, a dead instrument is never retried, a duplicate action inside the window is
blocked, the global budget halts a runaway, **and a recoverable payment above ₹15,000 is
refused for silent retry and converted to `requires_afa`**. Commit.

---

# PHASE 4 — Execute + Explain + Audit
**Goal:** carry out gated actions, log an auditable trace, explain each decision.

- `core/execute.py`:
  - Default `simulate(intervention, record) -> result` — returns a realistic fake
    success/failure and (for retries) may flip some previously-failed payments to
    `captured` per a fixed seeded outcome so the recovery number is deterministic and
    reproducible. **Simulated retry success must only be possible for recoverable
    categories** — a `dead_instrument` retry can never "succeed" (and should never reach
    here anyway, because the gate blocks it).
  - One real path `razorpay_testmode_create_link(record) -> link` that hits Razorpay
    **test mode** to create a real Payment Link, returning a real test object id. Used for
    the demo credibility moment. Guard it so a missing key degrades to simulate with a
    clear log line.
- `core/explain.py`: LLM turns a decision trace (record + category + intervention + gate
  result) into ONE plain-language sentence, e.g. *"Retried after 2h because the failure
  was a transient bank outage; instrument is valid."* This is where the LLM earns its
  place on the output side.
- `core/audit.py`: append-only `audit_log.jsonl`, one JSON object per decision:
  `{payment_id, payment_type, amount, input_summary, category, source (rules|llm),
  intervention, gate_result, refusal_reason, idempotency_key, pre_debit_notified,
  execution_result, rationale, timestamp}`. Must be human-readable and complete. The
  `idempotency_key` and `pre_debit_notified` fields are what make the bounded/compliant
  claims concrete in the trail.

**Definition of Done:** a full batch run writes a complete `audit_log.jsonl` where every
record has a readable rationale and a traceable decision path, including idempotency key
and (for retries) the pre-debit notification step. The one real test-mode call produces a
genuine Razorpay test object id when keys are present. Commit.

---

# PHASE 5 — Wire the LLM diagnosis tail + Orchestrate + Metrics
**Goal:** close the loop end to end with honest numbers.

- Replace the Phase 2 stub: `"needs_llm"` records now go to `core/llm.py` with a tight
  prompt that returns exactly one of the four category labels (constrain output; validate
  it's one of the four, else default to `escalate`).
- `run_batch.py`: orchestrate the full loop —
  `detect → diagnose (rules→llm) → decide → gate → execute → explain → audit` — over the
  batch, then call metrics. **Make batch size a parameter** (e.g. `--n 500` or an env var):
  `generate_fixtures.py` can produce N records (same seeded mix, scaled), and the loop runs
  over whatever size. This lets you demo a 500-record run to show it doesn't fall over at
  volume — a one-line honest flex, no infrastructure needed. Default stays ~50 for the main
  demo.
- `report/metrics.py`: batch report. Reads the `eval-only` ground truth ONLY here.
  Reports:
  - `total`, `recoverable`, `dead`
  - **`recovered` and recovery rate = recovered / recoverable** (the honest denominator)
  - `escalated` count, `false_intervention` count (any action taken on a `_recoverable ==
    false` record that wasn't the correct link/escalate — must be 0 if the gate works)
  - `afa_gated` count (recurring payments > ₹15,000 correctly routed to `requires_afa`)
  - **diagnosis accuracy reported ALONGSIDE recovery rate**: rules-correct, llm-correct,
    misclassified. Recovery rate without diagnosis accuracy is untrustworthy (a
    misclassification silently distorts the recoverable denominator) — the two numbers must
    be shown together, always.
  - a one-line money figure: *"Recovered ₹X of ₹Y recoverable across N failed payments."*
  - **the baseline for honesty**: also state what happens WITHOUT the agent — i.e. all
    recoverable payments stay failed (₹Y lost). Recovery is measured against that baseline,
    not invented.

**Definition of Done:** `python run_batch.py` runs the whole batch and prints the honest
report — recovery rate AND diagnosis accuracy AND the baseline, ending in the ₹ headline.
The rupee number is reproducible across runs (seeded), and running with a warm
`llm_cache.json` makes ZERO live LLM calls. Commit.

---

# PHASE 6 — Minimal UI (LAST — do not start early)
**Goal:** a single page that makes the demo legible. Minimal.

- Next.js + TS single page:
  - The headline recovery number (big).
  - The audit table: payment id, category, source (rules/llm badge), intervention, gate
    result, rationale — with the refusals/escalations visually distinct (this is the
    graceful-failure moment on screen).
  - A small summary strip: recoverable vs dead, recovery rate, escalations.
- Read from `audit_log.jsonl` / a metrics JSON the batch writes. No live orchestration in
  the browser needed — running the batch server-side and rendering results is enough.

**Definition of Done:** the page loads, shows the number, and renders the audit trail with
refusals clearly marked. Commit.

---

# PHASE 7 — Demo, README, polish
**Goal:** submission-ready.

- `README.md`: the problem, the loop diagram, how to run, the honest-metrics explanation
  (why recovery rate uses the recoverable denominator), and a short "future work" note
  (e.g. "the diagnosis step could be swapped for a trained scorer" — as a line, not a
  build).
- **Include the regulatory-grounding paragraph in the README** (this is what makes
  "compliant escalation" credible — use close to this wording):

  > Recovery actions model RBI Digital Payments E-mandate Framework (2026) behavior:
  > retries above ₹15,000 are gated behind additional-factor authentication rather than
  > silently reattempted, and retry actions carry a pre-debit notification step.
  > Cooling-off windows reflect standard payment-processor retry practice. This is a
  > synthetic test-mode prototype, not a production-compliant system.

  Note the honesty in that last line — scope the compliance claim, don't overclaim. The
  ₹15,000 AFA threshold is a real, current RBI rule; the cooling window is processor
  practice; nothing here is production-compliant. That honest scoping is exactly what the
  bar rewards.
- **Include a "Scalability & robustness" section in the README** — this pre-answers the
  near-certain judge/interview question "what if there's high load / too many requests?"
  The strong answer is NOT "we built for scale" (we deliberately didn't) — it's showing the
  architecture wouldn't need to change to get there. Use close to this:

  > **Scalability.** The decision core (`diagnose` → `decide` → `gate`) is stateless and
  > idempotent: every action carries an idempotency key, so it is safe to retry,
  > parallelize, and replay without double-charging. Because the logic is a pure function
  > of (record + state), it scales horizontally without modification. The decision core is
  > cleanly separated from the I/O layer (`detect` / `execute` / `audit`). Moving from
  > prototype to production means swapping the I/O layer — fixtures → Razorpay webhook
  > ingestion, JSON files → Postgres for state/audit, the synchronous loop → a job queue
  > with workers for real scheduled retries — while the decision logic carries over
  > unchanged. The prototype validates the hard part (correct bounded logic); the scaling
  > is well-understood infrastructure, architected for but intentionally not built here.
  >
  > **Robustness.** The agent is bounded by construction — a global attempt budget, per-
  > payment retry caps, cooling-off windows, and dead-instrument rules make runaway or
  > infinite-loop behavior impossible. Every run is deterministic and reproducible (seeded
  > fixtures, seeded outcomes, cached LLM responses). Failure paths fail closed: a missing
  > API key degrades to simulation with a logged notice; an out-of-spec LLM label defaults
  > to human escalation rather than a wrong action. Every decision is fully auditable.

  Key point to hold in live Q&A: the durable contribution is the **bounded, idempotent,
  stateless decision engine**. Idempotency is *the* scalability primitive for a payments
  system — it's what makes concurrency and retries safe — and we have it. "Too many
  requests" was never a logic problem; it's an I/O-layer problem the architecture already
  isolates. Do NOT build a queue/DB/load system for the hackathon — it costs the deadline
  and the gate (the actual differentiator) for infrastructure no one is grading.
- Batch size is a parameter (Phase 5) — optionally show a 500-record run in the demo to
  visually confirm it handles volume, without any added infrastructure.
- Record the demo: run the batch, show the number, then **deliberately show refusals** —
  ideally two kinds: (1) a dead-card retry blocked or a capped payment escalated, and
  (2) **the AFA-threshold refusal** (a recurring payment above ₹15,000 converted to
  `requires_afa` instead of silently retried). The second one is strong because it shows
  domain knowledge, not just generic guardrails. This is the "one failure handled
  gracefully" moment — you have two flavors of it. Then show the one real Razorpay
  test-mode object. Use `SCENARIOS.md` to jump straight to the payment ids for each beat.
- **Demo must be hermetic:** run from the committed `llm_cache.json` so there are ZERO live
  LLM calls during the demo (no rate-limit or outage risk on stage). For the real Razorpay
  object, **pre-capture the test-mode object id / a screenshot** as a backup so you can
  show "here's a real one I created" even if the live call stalls at demo time. Belt and
  suspenders — a broken live call in front of judges sinks an otherwise-good demo.
- Frame it against the why-now (agentic commerce / AP2 / x402 / NPCI UAP) in one or two
  sentences so it reads as a piece of the bigger shift, not just a retry bot.

**Definition of Done:** repo runs clean from a fresh clone per the README; demo recorded
including the AFA-threshold refusal; submission form ready.

---

## Timeline (solo)
- **Phase 0–1:** first session — scaffold + fixtures.
- **Phase 2–3:** core sessions — diagnosis + the stateful gate (most important).
- **Phase 4–5:** execute, explain, audit, orchestrate, metrics.
- **Phase 6:** UI, kept minimal.
- **Phase 7:** demo + README.
- **Target wrap: Sept 3.** Hard deadline: Sept 5. Protect the buffer.

## Reminders for Claude Code
- One phase at a time. Confirm each Definition of Done before proceeding.
- Keep `run_batch.py` readable as the single source of truth for the loop.
- Never let the pipeline read `eval-only` fields except in `report/metrics.py`.
- Prefer the simpler implementation when in doubt. Ask before deviating.

---

## PROGRESS TRACKING — maintain `CLAUDE.md` (mandatory, every session)

There is memory loss between sessions. To survive that, maintain a `CLAUDE.md` at the repo
root as the **single source of truth for what has been done and what's next**. This is not
optional and not a once-at-the-end task.

**Create `CLAUDE.md` in Phase 0** with this structure:

```markdown
# CLAUDE.md — Build Progress & State

## Project
Razorpay Buildathon Track 03 — bounded failed-payment recovery agent.
See plan.md for the full phase plan. This file tracks STATE.

## Current status
- Active phase: <n>
- Last session ended: <date> — <one line on where things stand>
- Next action: <the very next concrete thing to do>

## Phase checklist
- [ ] Phase 0 — Scaffold & config
- [ ] Phase 1 — Fixtures
- [ ] Phase 2 — Detect + Diagnose (rules, LLM stubbed)
- [ ] Phase 3 — Decide + Gate (the spine)
- [ ] Phase 4 — Execute + Explain + Audit
- [ ] Phase 5 — LLM diagnosis tail + Orchestrate + Metrics
- [ ] Phase 6 — Minimal UI
- [ ] Phase 7 — Demo, README, polish

## Decisions log (append-only — never rewrite history)
- <date>: <decision made and why> e.g. "Retry cap set to 3 total attempts."
- Pre-seeded: AFA threshold gate at ₹15,000 (1_500_000 paise) — real RBI E-mandate
  Framework 2026 rule; retries above it convert to requires_afa, not silent retry.
- Pre-seeded: cooling-off windows (insufficient_funds T+24h) reflect processor practice
  (24–48h), NOT a hard RBI rule. bank_downtime shorter (transient).
- Pre-seeded: pre-debit notification is trace-level only (one audit field), no real infra.

## Module status
| Module              | State        | Notes                                  |
|---------------------|--------------|----------------------------------------|
| llm.py              | not started  |                                        |
| generate_fixtures.py| not started  |                                        |
| detect.py           | not started  |                                        |
| diagnose.py         | not started  |                                        |
| decide.py           | not started  |                                        |
| gate.py             | not started  |                                        |
| execute.py          | not started  |                                        |
| explain.py          | not started  |                                        |
| audit.py            | not started  |                                        |
| metrics.py          | not started  |                                        |
| app/ (UI)           | not started  |                                        |

## Known issues / TODO carried forward
- <anything left unresolved that the next session must pick up>

## How to run (keep current as it changes)
- <commands to run the batch, the UI, tests>
```

**Update protocol — at the END of every phase (and before ending any session):**
1. Tick the phase checkbox only when its Definition of Done actually passed.
2. Update **Current status** (active phase, last session line, next action).
3. Update the **Module status** table for anything touched.
4. Append any architectural decision to the **Decisions log** — never edit past entries.
5. Record anything unfinished under **Known issues / TODO carried forward**.
6. Keep **How to run** accurate as commands change.

**At the START of every session:** read `CLAUDE.md` first, then `plan.md`. Resume from
"Next action." Do not re-derive decisions already in the Decisions log — honor them.

---

## THREAT MITIGATIONS — bake these in (do not skip)

These address the ways this build most often fails. Each maps to a phase.

- **Honest number (Phase 1, 4, 5):** `simulate()` outcomes are seeded and deterministic;
  retry success is possible ONLY for recoverable categories; a `dead_instrument` retry can
  never "succeed." Metrics report recovery rate against the **recoverable** denominator and
  list dead failures as "correctly not pursued." The README and demo must state the
  baseline explicitly ("without the agent, N stay dead").

- **Gate is the priority (Phase 3):** if the timeline slips, cut UI polish and explanation
  flourish — NEVER the gate. Ship a real stateful gate before anything visual.

- **LLM is visibly used (Phase 1, 2, 5):** confirm the ~4 ambiguous fixtures genuinely
  cannot be resolved by the rules table — run Phase 2 and verify exactly those route to
  `needs_llm`. If rules resolve them, make them more ambiguous.

- **LLM output is constrained (Phase 5):** diagnosis LLM must return exactly one of the
  four labels; validate and default to `escalate` on any violation. Log LLM
  misclassifications honestly rather than hiding them.

- **No false interventions (Phase 3, 4):** add a test that tries to force a dead-instrument
  retry and confirms the gate refuses. `false_intervention` count must be 0.

- **LLM caching (Phase 0/5):** cache LLM responses by input hash in `llm.py` so re-running
  the batch does not re-hit the API for unchanged records — protects against rate limits
  and speeds iteration. Make the explanation call toggleable for dev runs.

- **Real API done early (Phase 4):** prove the single Razorpay test-mode Payment Link call
  works in Phase 4 and then leave it alone. It must degrade to simulate with a clear log
  line if keys are absent. Do NOT leave API auth for the final days.

- **Runs from a fresh clone (Phase 7):** commit fixtures, provide `.env.example`, keep
  README run steps current. Treat "clean run from fresh clone" as a hard gate, not polish.

- **Hard stop Sept 3:** wrap by Sept 3 regardless of "one more feature." A polished-enough
  submission plus resumed placement prep beats a perfect submission plus lost prep time.
