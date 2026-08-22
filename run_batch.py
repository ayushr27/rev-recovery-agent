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
    """Load the run state. `--resume` carries per-payment reservations forward but
    resets the global attempt budget.

    The budget is documented in config.py as a cap "across a batch run", and both the
    report and the demo read it that way. Carrying it forward instead made it a lifetime
    cap: after two resumed 50-record runs it sat at 100/100, and every subsequent run
    refused every payment as GLOBAL_BUDGET_EXHAUSTED — indistinguishable from the agent
    working, which is exactly the kind of silently-wrong number this project exists to
    avoid. Per-payment `attempts` and `actions` are NOT reset: those are the idempotency
    reservations, and forgetting them would let an action double-fire.
    """
    if resume and STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        state["global_actions"] = 0
        return state
    return gate.new_state()


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def process(record, state, now, use_real_api=False, use_llm_explain=None, use_llm_diagnose=True,
            persist=lambda _state: None, inject_category=None):
    """One payment through the whole loop. Returns the audit entry.

    `persist` is called with the run state after a reservation is made and before the
    action fires, so the reservation survives a crash. It defaults to a no-op, which
    keeps this function usable in tests without touching the filesystem.

    `inject_category` forces a wrong diagnosis, to demonstrate what the gate does when
    the classification path fails. The honest verdict is still computed first and kept
    in the audit trail beside the forced one, so nothing about the tampering is hidden.
    """
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

    # Record what the agent actually concluded before overwriting it, so the trail shows
    # both the real verdict and the fault forced over it.
    injected_fault = None
    if inject_category is not None:
        injected_fault = {
            "forced_category": inject_category,
            "actual_category": category,
            "actual_source": source,
        }
        category = inject_category
        source = "injected_fault"

    intervention = decide.decide(category)
    gate_result = gate.evaluate(record, category, intervention, state, now)

    # Commit AND persist before executing. Reserving the key in memory is not enough:
    # if the state only reached disk at the end of the batch, a crash mid-run would
    # lose every reservation and a re-run could fire the same action twice. Writing
    # first means the worst case is an action we recorded but never sent — which is
    # the right way round for a payments system.
    if gate_result.allowed:
        gate.commit(state, record["id"], gate_result, now)
        persist(state)

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
        injected_fault=injected_fault,
    )


def _parse_injections(specs):
    """Parse ID=CATEGORY pairs. Raises on anything malformed — a typo must never yield a
    clean-looking run that then gets presented as evidence."""
    injections = {}
    for spec in specs:
        payment_id, _, category = spec.partition("=")
        if not payment_id or not category:
            raise SystemExit(f"--inject-misdiagnosis expects ID=CATEGORY, got {spec!r}")
        if category not in diagnose.CATEGORIES:
            raise SystemExit(
                f"unknown category {category!r}; expected one of {sorted(diagnose.CATEGORIES)}"
            )
        injections[payment_id] = category
    return injections


def _banner_open(injected, records):
    by_id = {r["id"]: r for r in records}
    print("=" * 70)
    print("FAULT INJECTION — THIS RUN IS A DEMONSTRATION, NOT A MEASUREMENT")
    print("=" * 70)
    for payment_id, forced in injected.items():
        record = by_id[payment_id]
        print(f"  {payment_id}: diagnosis forced to {forced!r}")
        print(f"      record says error_reason={record.get('error_reason')!r}")
    print("\nNo metrics will be computed and run_state.json will not be written.")
    print("=" * 70)


def _banner_close(injected, entries):
    by_id = {e["payment_id"]: e for e in entries}
    print("\n" + "=" * 70)
    print("WHAT THE GATE DID ABOUT IT")
    print("=" * 70)
    for payment_id in injected:
        entry = by_id[payment_id]
        fault = entry["injected_fault"]
        verdict = entry["refusal_reason"] or f"ALLOWED -> {entry['action_taken']}"
        print(f"\n  {payment_id}")
        print(f"    really was:  {fault['actual_category']}  (via {fault['actual_source']})")
        print(f"    forced to:   {fault['forced_category']}")
        print(f"    gate:        {entry['gate_result'].upper()}  {verdict}")
        print(f"    executed:    {entry['execution_result']['status']}")
    print(f"\nTampered trail written to {config.INJECTED_AUDIT_LOG_PATH};")
    print(f"the honest {config.AUDIT_LOG_PATH} and {config.METRICS_PATH} are untouched.")
    print("=" * 70)


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
    parser.add_argument(
        "--now",
        type=int,
        metavar="EPOCH",
        help="treat this Unix timestamp as the current time instead of the wall clock. "
             "Makes --resume reproducible: cooling-off windows are measured against it, "
             "so the same pair of runs gives the same refusals whenever you run them.",
    )
    parser.add_argument(
        "--inject-misdiagnosis",
        action="append",
        default=[],
        metavar="ID=CATEGORY",
        help="DEMO ONLY: force a wrong diagnosis to show what the gate does about it. "
             "Repeatable. Writes a separate audit trail and computes no metrics.",
    )
    args = parser.parse_args()

    injected = _parse_injections(args.inject_misdiagnosis)
    if injected:
        if args.real_link:
            parser.error("--inject-misdiagnosis cannot be combined with --real-link: a "
                         "tampered diagnosis must never reach a real payment API")
        if args.resume:
            parser.error("--inject-misdiagnosis cannot be combined with --resume: injected "
                         "runs are in-memory and must not touch run_state.json")

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

    known = {record["id"] for record in records}
    for payment_id in injected:
        if payment_id not in known:
            parser.error(f"{payment_id} is not in this batch; nothing would be injected")

    audit_path = config.INJECTED_AUDIT_LOG_PATH if injected else None
    if injected:
        _banner_open(injected, records)

    state = load_state(args.resume)
    audit.reset(audit_path)
    # The gate measures every cooling-off window against this one value. Left to the wall
    # clock, a second `--resume` run more than two hours after the first lands in a NEW
    # bank_downtime window and legitimately fires 14 actions — correct behaviour, but it
    # turns the idempotency demo into its opposite without warning. --now pins it.
    now = args.now if args.now is not None else int(time.time())

    entries = []
    for record in records:
        entry = process(
            record,
            state,
            now,
            use_real_api=(record["id"] == args.real_link),
            # Injected traces produce prompts the cache has never seen, so the LLM is
            # switched off to keep a demonstration hermetic.
            use_llm_explain=False if (args.no_explain or args.no_llm or injected) else None,
            use_llm_diagnose=not args.no_llm,
            # An injected run never persists state — it must not pollute real reservations.
            persist=(lambda _state: None) if injected else save_state,
            inject_category=injected.get(record["id"]),
        )
        audit.append(entry, audit_path)
        entries.append(entry)

    if injected:
        _banner_close(injected, entries)
        return

    save_state(state)

    data = metrics.report(entries, ground_truth)
    metrics.write(data)
    metrics.render(data)
    print(
        f"Audit trail: {config.AUDIT_LOG_PATH}  |  state: {config.RUN_STATE_PATH}"
        f"  |  metrics: {config.METRICS_PATH}"
    )


if __name__ == "__main__":
    main()
