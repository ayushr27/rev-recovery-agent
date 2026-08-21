"""Orchestrates detect -> diagnose (rules -> llm) -> decide -> gate -> execute ->
explain -> audit over a batch of failed payments, then prints the metrics report.
This is the single source of truth for the agent loop — keep it readable.

State persistence lives here rather than in gate.py on purpose: the gate is a pure
function of (record + state), and keeping the file I/O out here is what preserves that
property. See CLAUDE.md's decisions log.

Runs start from a fresh state by default so the recovered figure is reproducible. Pass
--resume to carry run_state.json forward, which is how the idempotency refusals become
visible: run twice and the second pass refuses every action as a duplicate.
"""

import argparse
import json
import time
from pathlib import Path

import config
from core import audit, decide, detect, diagnose, execute, explain, gate, llm
from report import metrics

STATE_PATH = Path(__file__).resolve().parent / config.RUN_STATE_PATH


def load_state(resume):
    if resume and STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return gate.new_state()


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def process(record, state, now, use_real_api=False, use_llm_explain=None, use_llm_diagnose=True):
    """One payment through the whole loop. Returns the audit entry."""
    category = diagnose.diagnose(record)
    source = "rules"

    if category == diagnose.NEEDS_LLM:
        # The rules table declined to classify this one. Hand it to the model,
        # constrained to the four labels; anything else falls back to escalation so an
        # out-of-spec answer becomes a human's problem, never a wrong action.
        if use_llm_diagnose:
            verdict = llm.classify_failure(record, diagnose.CATEGORIES)
            category = verdict["category"]
            source = "llm" if verdict["valid"] else "llm_invalid_defaulted"
        else:
            category = "exhausted"
            source = "unresolved"

    intervention = decide.decide(category)
    gate_result = gate.evaluate(record, category, intervention, state, now)

    # Commit before executing: reserving the idempotency key first means a crash
    # between the decision and the action cannot let that action fire twice.
    if gate_result.allowed:
        gate.commit(state, record["id"], gate_result, now)

    execution_result = execute.execute(record, category, gate_result, use_real_api=use_real_api)

    rationale = explain.explain(
        {
            "payment_id": record["id"],
            "payment_type": record.get("payment_type"),
            "amount": record.get("amount"),
            "category": category,
            "source": source,
            "intervention": intervention.action,
            "gate_result": gate_result.decision,
            "action": gate_result.action,
            "refusal_reason": gate_result.reason,
            "execution_status": execution_result["status"],
        },
        use_llm=use_llm_explain,
    )

    return audit.entry(
        record=record,
        category=category,
        source=source,
        intervention=intervention,
        gate_result=gate_result,
        execution_result=execution_result,
        rationale=rationale,
        timestamp=now,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="carry run_state.json forward")
    parser.add_argument("--no-explain", action="store_true", help="skip LLM rationales")
    parser.add_argument("--no-llm", action="store_true", help="skip LLM entirely (rules only)")
    parser.add_argument(
        "--n",
        type=int,
        help="generate a scaled batch of N records in memory instead of reading fixtures",
    )
    parser.add_argument(
        "--real-link",
        metavar="PAYMENT_ID",
        help="create ONE real Razorpay test-mode payment link, for this payment id",
    )
    args = parser.parse_args()

    if args.n:
        # Same seeded mix, scaled. Kept in memory so a volume run never overwrites the
        # committed 50-record fixture.
        from fixtures.generate_fixtures import generate

        raw = generate(n=args.n)
        records = detect.strip_eval_fields(raw)
        ground_truth = detect.ground_truth_from(raw)
    else:
        records = detect.load_failures()
        ground_truth = detect.load_ground_truth()

    state = load_state(args.resume)
    audit.reset()
    now = int(time.time())

    entries = []
    for record in records:
        entry = process(
            record,
            state,
            now,
            use_real_api=(record["id"] == args.real_link),
            use_llm_explain=False if (args.no_explain or args.no_llm) else None,
            use_llm_diagnose=not args.no_llm,
        )
        audit.append(entry)
        entries.append(entry)

    save_state(state)

    metrics.render(metrics.report(entries, ground_truth))
    print(f"Audit trail: {config.AUDIT_LOG_PATH}  |  state: {config.RUN_STATE_PATH}")


if __name__ == "__main__":
    main()
