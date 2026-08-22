# CLAUDE.md — Build Progress & State

## Project
Razorpay Buildathon Track 03 — bounded failed-payment recovery agent.
See plan.md for the full phase plan. This file tracks STATE.

## Current status
- Active phase: 7 — DONE. All phases complete, 12 days before the Sept 3 target.
- **Fresh-clone gate PASSES (verified twice, 2026-08-22).** Cloned into an empty dir
  with NO `.env` and no API keys: `pip install -r requirements.txt && python
  run_batch.py` reproduces Rs 141,801.84 / 21 of 32 / 98.0% exactly, and all tests pass.
  Works keyless because `llm.complete()` checks the committed cache before it ever
  looks for a provider — so the demo is hermetic by construction, not by luck.
- Every figure in README.md was checked against metrics.json programmatically before
  being written. If the fixtures or success rates ever change, re-verify them.
- Previous phase note: Minimal UI (Phase 6)
- Last session ended: 2026-08-21 — Next.js 16.3.2 / React 19.2.8 single page in `app/`.
  Server Component reads `metrics.json` + `audit_log.jsonl` from the repo root at
  request time; headline number, summary strip, and the audit table with refusals /
  conversions / escalations colour-coded. Verified: typecheck clean, production build
  clean, HTTP 200, correct figures rendered (Rs 1,41,801.84 in en-IN grouping), 50
  rows, stylesheet served with all rules. On `--resume` all 50 rows render as refused
  with their reason codes visible. **Visually confirmed by the user on 2026-08-22**
  after fixing a rationale-column clipping bug that only screenshots revealed —
  HTML-level checks passed while every explanation was cut off mid-sentence on screen.
  Lesson: for UI work, structural verification is not sufficient; get eyes on it.
- Next action: **all seven phases are complete.** Remaining work is the human's:
  record the demo (running order at the top of SCENARIOS.md) and submit.

## Phase checklist
- [x] Phase 0 — Scaffold & config
- [x] Phase 1 — Fixtures
- [x] Phase 2 — Detect + Diagnose (rules, LLM stubbed)
- [x] Phase 3 — Decide + Gate (the spine)
- [x] Phase 4 — Execute + Explain + Audit
- [x] Phase 5 — LLM diagnosis tail + Orchestrate + Metrics
- [x] Phase 6 — Minimal UI
- [x] Phase 7 — Demo, README, polish

## Decisions log (append-only — never rewrite history)
- 2026-08-20: Built directly at the repo root (rev-recovery-agent/) rather than
  nesting an extra recovery-agent/ folder — the repo root already is the project.
- 2026-08-20: LLM provider defaults to Groq per plan recommendation; core/llm.py
  dispatches on LLM_PROVIDER and supports groq or gemini, lazy-importing each SDK so
  only the chosen provider's package needs to be installed.
- 2026-08-20: requirements.txt pins `groq` as the default SDK; `google-generativeai`
  is left commented for anyone switching LLM_PROVIDER=gemini.
- 2026-08-20: GROQ_MODEL defaults to `openai/gpt-oss-20b` (fast/cheap, plenty for
  short classification + one-sentence rationale outputs). `llama-3.3-70b-versatile`
  (the plan's assumed default) is no longer served on this Groq account as of
  2026-08-20 — queried `GET /openai/v1/models` and confirmed current chat-capable
  options: openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.6-27b, groq/compound(-mini).
  Override via GROQ_MODEL in .env if a different one is preferred.
- Pre-seeded: AFA threshold gate at ₹15,000 (1_500_000 paise) — real RBI E-mandate
  Framework 2026 rule; retries above it convert to requires_afa, not silent retry.
- Pre-seeded: cooling-off windows (insufficient_funds T+24h) reflect processor practice
  (24–48h), NOT a hard RBI rule. bank_downtime shorter (transient).
- Pre-seeded: pre-debit notification is trace-level only (one audit field), no real infra.
- 2026-08-21: `detect.py` exposes TWO functions rather than the plan's single
  `load_failures(path) -> list[dict]`: `load_failures()` (eval fields stripped, for the
  pipeline) and `load_ground_truth()` (for report/metrics.py ONLY). Keeping them
  separate honors the plan's stated signature while still giving metrics its data —
  a single function returning a tuple would have changed the documented signature.
- 2026-08-21: Diagnosis rule order is retry_cap → error_reason → signature → keyword
  → needs_llm. `retry_count >= MAX_RETRY_ATTEMPTS → exhausted` is rule #1 because the
  plan defines `exhausted` as "already at retry cap"; the gate still independently
  enforces the cap (defense in depth), so this is not a substitute for it.
- 2026-08-21: The keyword layer currently fires 0 times — error_reason and signature
  resolve every clean record first. Kept as a narrow, high-precision fallback (the
  plan names keywords as part of the rules table), NOT broadened: loose free-text
  matching is the LLM tail's job and doing it here would silently mislabel records.
  If it still never fires by Phase 7, consider deleting it as dead code.
- 2026-08-21: **plan.md self-conflict resolved in favour of the design rule.** Line 267
  says gate.py "Reads/writes run_state.json"; lines 70–76 say the decision core must
  have NO I/O and call that the whole scalability story. Chose the design rule:
  `gate.evaluate()` is pure over (record, category, intervention, state, now) and
  `gate.commit()` mutates only an in-memory dict. run_state.json load/save belongs to
  run_batch.py (Phase 5). `now` is passed in, never read from the clock, so the gate
  stays deterministic. A test asserts evaluate() does not mutate state.
- 2026-08-21: Gate rule order is dead_instrument → retry cap → AFA convert → idempotency
  → cooling-off → global budget. Safety invariants first (no remaining quota makes a
  dead-instrument retry valid); the action type is settled by the AFA conversion BEFORE
  the idempotency key is derived, so a converted action dedupes as the auth link it
  became. Idempotency is checked before cooling-off because it is the more specific
  diagnosis of the same situation.
- 2026-08-21: AFA is modelled as decision=CONVERT (not a plain refusal) — the retry is
  refused but an auth link still goes out, so the customer is not abandoned. Metrics
  counts afa_gated off decision==CONVERT + reason==AFA_REQUIRED_ABOVE_THRESHOLD.
  Boundary is strictly `>` AFA_THRESHOLD_PAISE; exactly ₹15,000 still retries.
- 2026-08-21: Added `config.DEFAULT_COOLING_OFF_HOURS = 24` for categories absent from
  COOLING_OFF_HOURS (dead_instrument, exhausted) — needed so their actions still get an
  idempotency window rather than being re-firable at will.
- 2026-08-22: **The gate could not catch a misdiagnosis.** Rule 1 keyed off the diagnosed
  category, so a wrong label bypassed it entirely — verified by injecting one. Added rule
  1b, which re-derives dead-instrument evidence from the RECORD. Never say "the gate
  catches misdiagnosis": it re-derives the machine-readable evidence only, and cannot see
  evidence that exists solely in error_description. That limit is asserted by
  test_evidence_check_cannot_see_prose_only_evidence and shown live in demo beat 6.
- 2026-08-22: `config.HARD_DEAD_INSTRUMENT_REASONS` **duplicates** diagnose.REASON_RULES
  deliberately. Do NOT refactor it into an import — the regression it guards against is
  someone editing REASON_RULES, and importing would delete the check at the same moment.
  A drift test in test_gate.py keeps them in step on purpose rather than by accident.
- 2026-08-22: Fault-injected runs are demonstrations, never measurements. Containment is
  layered: separate audit file, no metrics computed, no state persisted, source
  "injected_fault", a per-row injected_fault field that is null on honest rows, loud
  banners, and metrics.report() raises on tampered input so the guarantee does not depend
  on run_batch staying correct. Injection with --real-link or --resume is refused.
- 2026-08-22: **eval_llm.py has no accuracy threshold, on purpose.** It exits non-zero on
  integrity violations only. A pass/fail bar on accuracy is the pressure that produces
  tuning against the eval set. It writes no results file either — stdout only — so no
  figure can go stale in an artifact while the README says something else.
- 2026-08-22: **The classify prompt is frozen.** Primary reason: editing it to fix a known
  miss is tuning against the test set. Mechanical reason, equally binding: llm.complete()
  keys the cache on sha256(system + user), so one character voids all four production
  cache entries — on a keyless clone complete() then raises, classify_failure() falls back
  to exhausted, two payments stop retrying, and the headline silently changes. eval_llm.py
  prints the prompt hash so any change visibly invalidates every quoted figure.
- 2026-08-22: Known LLM failure mode, measured not guessed: it reads a business decision
  to stop pursuing ("collections has suspended billing") as dead_instrument rather than
  exhausted. Seen in pay_EVAL00025 and pay_TEST00036. Both degrade safely — escalate
  becomes send_link — so unsafe errors are 0. Characterised and contained, not fixed.
- 2026-08-22: **"dead — not pursued" was factually wrong and is now "never retried"**
  (UI strip + metrics.render + SCENARIOS beat 1). All 18 non-recoverable payments ARE
  actioned — verified from the audit log: 12 dead_instrument → send_link, 6 exhausted →
  escalate, 0 with action_taken None. The old label contradicted the table printed
  directly beneath it. This departs from plan.md's literal phrase ("correctly not
  pursued", lines 58 and 570), deliberately: the plan's actual rule is about the
  DENOMINATOR — dead failures are excluded from the rate and never hidden — and "never
  retried" states that precisely while "not pursued" claims something the trail
  disproves. plan.md is left unedited as the historical spec.
- 2026-08-21: UI is Next.js **16.3.2** / React **19.2.8** (checked against npm, not
  assumed). Scaffolded with `create-next-app --empty --no-tailwind --src-dir`. NOTE:
  Next 16 generates a global `LayoutProps<"/">` type for the root layout — do not
  hand-write `{children: React.ReactNode}`, and read `app/AGENTS.md` before changing
  Next code, since much of it postdates the training cutoff.
- 2026-08-21: No Tailwind — the page is one headline plus one table, so ~80 lines of
  plain CSS in globals.css beats another dependency and build step.
- 2026-08-21: The page is a Server Component with `export const dynamic =
  "force-dynamic"`, reading metrics.json + audit_log.jsonl from the repo root per
  request. Without force-dynamic Next would prerender the numbers at build time and
  the page would go stale after a new batch run. Build output confirms route `/` is
  `ƒ (Dynamic)`.
- 2026-08-21: run_batch now also writes `metrics.json` (gitignored, like
  audit_log.jsonl) so the UI renders what the agent decided rather than re-deriving
  anything in the browser. No orchestration happens client-side.
- 2026-08-21: `app/CLAUDE.md` is a create-next-app artifact containing only
  `@AGENTS.md`; `next dev` regenerates it. **It is NOT this progress tracker** — the
  tracker is the CLAUDE.md at the repo root.
- 2026-08-21: LLM diagnosis lives in `llm.classify_failure()`, NOT in diagnose.py —
  the plan says needs_llm records "go to core/llm.py", and it keeps diagnose.py pure.
  The four labels are passed in by the caller so llm.py stays domain-agnostic. A
  label outside the four is discarded and defaults to escalate, recorded as source
  `llm_invalid_defaulted` so metrics counts it rather than hiding it.
- 2026-08-21: **The LLM prompt is NOT tuned to fix its one miss** (pay_TEST00036,
  said dead_instrument, truth exhausted). With n=4 ambiguous records, editing the
  prompt until that case passes is tuning on the test set — the exact leakage the
  plan warns about. 3/4 is reported honestly instead. Note the miss degraded safely:
  send_link, not a retry, so false_intervention stayed 0.
- 2026-08-21: metrics.py reports `refused_by_reason` and a `budget_exhausted` flag.
  Found via the 500-record run: 400 refusals from GLOBAL_ATTEMPT_BUDGET dropped the
  rate to 12.2%, which reads as agent failure unless the report says the cap was hit.
  A bound doing its job must not look like a bad number.
- 2026-08-21: AFA-gated payments are broken out of the recovery rate rather than
  buried in it. They ARE recoverable and were deliberately not retried, so counting
  them as plain misses would misreport compliance as failure.
- 2026-08-21: `--n` builds the scaled batch IN MEMORY rather than overwriting the
  committed 50-record fixture, so a volume demo never disturbs the reproducible run.
- 2026-08-21: **run_batch.py built in Phase 4, not Phase 5.** Phase 4's DoD requires "a
  full batch run writes a complete audit_log.jsonl", which needs the loop. Ordering
  shift only; Phase 5 still owns the LLM diagnosis tail, metrics and --n.
- 2026-08-21: Simulated outcomes hash the payment id rather than drawing from a running
  rng, so the recovered figure is independent of processing order and stable across
  runs. `simulate()` structurally refuses to let an unrecoverable category succeed —
  a test proves this holds even with a 100% success rate configured for dead_instrument.
- 2026-08-21: Batch runs start from FRESH state by default (--resume to carry state
  forward) so the rupee figure is reproducible. --resume is also the idempotency demo.
- 2026-08-21: The real Razorpay call is opt-in per payment (`--real-link <id>`), not
  on by default — otherwise every dead_instrument record would create a real object and
  the batch would be slow and non-hermetic. Notifications hard-disabled in the payload:
  these are synthetic customers and nothing should ever be sent to anyone.
- 2026-08-21: explain.py builds its prompt from stable fields only (NO timestamp) so
  re-runs hit the LLM cache. Any LLM failure falls back to a deterministic template
  sentence rather than aborting the run or leaving a blank rationale.
- 2026-08-21: The ambiguous `exhausted` fixture carries an explicit retry_count=1
  override. Without it, rule #1 would resolve it deterministically and the LLM tail
  would shrink to 3 records. Its description refers to out-of-band dunning outreach,
  not payment retries, so retry_count=1 is honest rather than contradictory.

## Module status
| Module               | State        | Notes                                             |
|----------------------|--------------|----------------------------------------------------|
| llm.py               | implemented  | complete() + cache; live-verified against Groq    |
| generate_fixtures.py | implemented  | 50 seeded records; --n/--seed args for scaling    |
| detect.py            | implemented  | load_failures() + load_ground_truth() (metrics)   |
| diagnose.py          | implemented  | rules table; returns needs_llm for the tail       |
| decide.py            | implemented  | 4-category table; fails closed on unknown         |
| gate.py              | implemented  | 6 bounds, structured reasons; pure, 33 tests pass |
| execute.py           | implemented  | seeded outcomes + real Razorpay test-mode path    |
| explain.py           | implemented  | LLM rationale, deterministic template fallback    |
| audit.py             | implemented  | JSONL trail, 14 fields per decision               |
| metrics.py           | implemented  | honest denominator + safety + budget flag         |
| run_batch.py         | implemented  | full loop incl. LLM tail, --n, metrics             |
| config.py            | implemented  | all Phase 0 constants + runtime file paths        |
| app/ (UI)            | implemented  | Next 16 server component; reads metrics + audit   |

## Known issues / TODO carried forward
- Any script under `fixtures/` or `scripts/` needs its own `sys.path` bootstrap (see
  `fixtures/generate_fixtures.py` top) to import top-level `config`/`core` when run
  directly as `python fixtures/generate_fixtures.py` — Python only puts the script's
  own directory on `sys.path`, not the repo root. `run_batch.py` doesn't need this
  since it already lives at the repo root.
- Razorpay test-mode key was added to `.env` but not yet exercised (that's Phase 4's
  real test-mode payment-link call, not a Phase 0/1 concern).
- LLM accuracy is now measured on a held-out set: 22/22, 95% CI [85.1%, 100%]; 29/30
  across all splits. Quote the INTERVAL, never the bare 100% — 22 observations cannot
  support a claim stronger than "at least 85%". The 98% headline figure remains dominated
  by the 46 rules-resolved records and still should not be read as an LLM result.
- The eval set may be EASIER than production: it contains nothing as hard as
  pay_TEST00036, the one production record still misclassified. Worth saying aloud rather
  than letting 22/22 imply the classifier is solved. Adding harder cases would be the
  next honest step — but author and label them before running anything, as before.
- The simulated success rates (bank_downtime 0.75, insufficient_funds 0.45) are
  plausible guesses, NOT measured from real data. The recovery rate inherits that
  assumption — say so in the README rather than implying it is an empirical result.
- Several gate bounds are backstops that never fire in a fresh run, by design:
  DEAD_INSTRUMENT_NEVER_RETRIED (decide() never proposes a retry for a dead card) and
  GLOBAL_BUDGET_EXHAUSTED (50 payments vs a 100 budget). RETRY_CAP_EXCEEDED also does
  not fire fresh, because diagnose() routes an at-cap record to `exhausted` first. All
  are covered by tests; see SCENARIOS.md "Refusals: which ones fire, and when".
- Two real test-mode payment links exist in the Razorpay account, not one:
  `plink_TSNNNlcJUtCEV6` (the intended demo object, recorded in SCENARIOS.md) and
  `plink_TSNNdtB2PiYw2R`, created accidentally while testing the missing-credentials
  path — load_dotenv() re-read .env and restored the keys the test had removed. Both
  are test mode with notifications disabled, so harmless. The fallback is now tested
  properly (empty-but-present env vars) in tests/test_execute.py.

## How to run (keep current as it changes)
- `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Copy `.env.example` to `.env` and fill in keys.
- Phase 0 sanity check (once keys are set): a short script calling
  `core.llm.complete("You are terse.", "Say OK")` twice — second call should hit
  `llm_cache.json` with no API call.
- Regenerate fixtures: `python fixtures/generate_fixtures.py` (add `--n 500` to scale).
- Phase 2 check: `python scripts/check_diagnose.py` — prints the confusion count and
  exits non-zero if any record is misclassified, any eval field leaks, or the
  needs_llm tail is not exactly 4.
- Tests: `pytest tests/ -q` (132 tests: decide, seven gate bounds, execution honesty,
  LLM containment + caching + explain fallback, fault injection, metrics arithmetic,
  Wilson intervals, eval-set integrity).
- LLM evaluation: `python scripts/eval_llm.py --split heldout` (or `dev` / `all`).
  Exits non-zero only if the SET is invalid, never on low accuracy.
- Fault injection demo: `python run_batch.py --inject-misdiagnosis pay_TEST00011=bank_downtime`
  (refused) vs `pay_TEST00024=bank_downtime` (retry fires — the documented limit).
- Full batch: `python run_batch.py` (fresh state, LLM from cache, prints the report).
  - `--no-explain` skips LLM rationales (template fallback) for fast dev runs.
  - `--no-llm` skips the LLM entirely (rules only; needs_llm records escalate).
  - `--n 500` runs a scaled in-memory batch — the volume demo.
  - `--resume` carries run_state.json forward — this is the idempotency demo.
  - `--real-link pay_TEST00011` makes ONE real Razorpay test-mode payment link.
- UI: `cd app && npm install` (once), then `npm run dev` → http://localhost:3000.
  Run the batch FIRST — with no metrics.json the page shows an empty-state prompt
  rather than an error. The page re-reads on refresh, so no restart is needed after
  a new run.
