# CLAUDE.md — Build Progress & State

## Project
Razorpay Buildathon Track 03 — bounded failed-payment recovery agent.
See plan.md for the full phase plan. This file tracks STATE.

## Current status
- Active phase: 3 — Decide + Gate (DONE, pending user confirmation to advance)
- Last session ended: 2026-08-21 — `core/decide.py` (four-category → Intervention,
  fails closed on unknown/needs_llm) and `core/gate.py` (the spine) implemented.
  Gate enforces: dead-instrument-never-retried, retry cap (record.retry_count +
  state attempts), RBI AFA conversion, idempotency key, cooling-off, global budget —
  each returning a structured reason. 33 pytest tests pass. Whole decision core
  (diagnose/decide/gate) verified I/O-free by grep. LLM still not called anywhere.
- Next action: get user confirmation that Phase 3's Definition of Done passes, then
  start Phase 4 (Execute + Explain + Audit). Phase 4 must prove the real Razorpay
  test-mode payment-link call works and degrades cleanly when keys are absent — the
  plan warns explicitly not to leave API auth until the final days.

## Phase checklist
- [x] Phase 0 — Scaffold & config
- [x] Phase 1 — Fixtures
- [x] Phase 2 — Detect + Diagnose (rules, LLM stubbed)
- [x] Phase 3 — Decide + Gate (the spine)
- [ ] Phase 4 — Execute + Explain + Audit
- [ ] Phase 5 — LLM diagnosis tail + Orchestrate + Metrics
- [ ] Phase 6 — Minimal UI
- [ ] Phase 7 — Demo, README, polish

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
| execute.py           | stub         | docstring only                                    |
| explain.py           | stub         | docstring only                                    |
| audit.py             | stub         | docstring only                                    |
| metrics.py           | stub         | docstring only                                    |
| run_batch.py         | stub         | docstring only                                    |
| config.py            | implemented  | all Phase 0 constants + runtime file paths        |
| app/ (UI)            | not started  | Phase 6 — do not start early                      |

## Known issues / TODO carried forward
- Any script under `fixtures/` or `scripts/` needs its own `sys.path` bootstrap (see
  `fixtures/generate_fixtures.py` top) to import top-level `config`/`core` when run
  directly as `python fixtures/generate_fixtures.py` — Python only puts the script's
  own directory on `sys.path`, not the repo root. `run_batch.py` doesn't need this
  since it already lives at the repo root.
- Razorpay test-mode key was added to `.env` but not yet exercised (that's Phase 4's
  real test-mode payment-link call, not a Phase 0/1 concern).
- SCENARIOS.md "successful recovery" row is still TBD — needs Phase 4's seeded
  execute() outcome before a payment id can be picked.
- Idempotency never fires naturally within a single batch run (50 unique payment ids,
  each seen once). It fires on a SECOND run against a warm run_state.json — which is
  itself a strong demo beat: run the batch twice, watch the second run refuse
  everything as DUPLICATE_ACTION_IN_WINDOW. Worth showing in Phase 7.
- run_state.json load/save is NOT written yet — it lands in run_batch.py in Phase 5.
  Until then the gate is exercised only by tests, which build state dicts inline.

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
- Tests: `pytest tests/ -q` (33 tests: decide table + all six gate bounds).
- Later phases: `python run_batch.py`.
