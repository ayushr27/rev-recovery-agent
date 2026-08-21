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
from core import audit, decide, detect, diagnose, execute, explain, gate

STATE_PATH = Path(__file__).resolve().parent / config.RUN_STATE_PATH


def load_state(resume):
    if resume and STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return gate.new_state()


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def process(record, state, now, use_real_api=False, use_llm_explain=None):
    """One payment through the whole loop. Returns the audit entry."""
    category = diagnose.diagnose(record)
    source = "rules"

    if category == diagnose.NEEDS_LLM:
        # PHASE 5 REPLACES THIS. Until the LLM diagnosis tail is wired, an
        # unresolvable record is escalated to a human rather than guessed at — the
        # same fail-closed choice decide() makes for an unknown label.
        category = "exhausted"
        source = "unresolved_pending_llm"

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
    parser.add_argument(
        "--real-link",
        metavar="PAYMENT_ID",
        help="create ONE real Razorpay test-mode payment link, for this payment id",
    )
    args = parser.parse_args()

    records = detect.load_failures()
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
            use_llm_explain=False if args.no_explain else None,
        )
        audit.append(entry)
        entries.append(entry)

    save_state(state)

    # Phase 5 replaces this with report/metrics.py and the honest denominator.
    allowed = [e for e in entries if e["gate_result"] in (gate.ALLOW, gate.CONVERT)]
    captured = [e for e in entries if e["execution_result"]["status"] == execute.CAPTURED]
    print(f"Processed {len(entries)} payments; {len(allowed)} actioned, {len(captured)} captured.")
    print(f"Audit trail: {config.AUDIT_LOG_PATH}  |  state: {config.RUN_STATE_PATH}")


if __name__ == "__main__":
    main()
