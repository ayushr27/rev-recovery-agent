# CLAUDE.md — Build Progress & State

## Project
Razorpay Buildathon Track 03 — bounded failed-payment recovery agent.
See plan.md for the full phase plan. This file tracks STATE.

## Current status
- Active phase: 1 — Fixtures (DONE, pending user confirmation to advance)
- Last session ended: 2026-08-20 — `fixtures/generate_fixtures.py` implemented and
  run: 50 records (bank_downtime 19, insufficient_funds 13, dead_instrument 11,
  exhausted 7 — includes the 4 ambiguous records' true categories), 7 one_time /
  43 recurring, 4 AFA-eligible, 4 atypical-signature (needs_llm) records. Verified
  reproducible (identical output across two runs with the same seed). SCENARIOS.md
  filled in with 4 of 5 confirmed payment ids; "successful recovery" stays TBD until
  Phase 4's seeded execute() exists.
- Next action: get user confirmation that Phase 1's Definition of Done passes, then
  start Phase 2 (Detect + Diagnose — rules table over error_code/source/step, LLM
  stubbed, sentinel "needs_llm" for the 4 atypical records).

## Phase checklist
- [x] Phase 0 — Scaffold & config
- [x] Phase 1 — Fixtures
- [ ] Phase 2 — Detect + Diagnose (rules, LLM stubbed)
- [ ] Phase 3 — Decide + Gate (the spine)
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

## Module status
| Module               | State        | Notes                                             |
|----------------------|--------------|----------------------------------------------------|
| llm.py               | implemented  | complete() + cache; live-verified against Groq    |
| generate_fixtures.py | implemented  | 50 seeded records; --n/--seed args for scaling    |
| detect.py            | stub         | docstring only                                    |
| diagnose.py          | stub         | docstring only                                    |
| decide.py            | stub         | docstring only                                    |
| gate.py              | stub         | docstring only                                    |
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

## How to run (keep current as it changes)
- `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Copy `.env.example` to `.env` and fill in keys.
- Phase 0 sanity check (once keys are set): a short script calling
  `core.llm.complete("You are terse.", "Say OK")` twice — second call should hit
  `llm_cache.json` with no API call.
- Later phases: `python fixtures/generate_fixtures.py`, `python run_batch.py`,
  `pytest`.
