"""Batch recovery report. The only module allowed to read eval-only ground-truth
fields — reports recovery rate against the recoverable denominator, diagnosis
accuracy, and the no-agent baseline.

Three deliberate choices about honesty:

1. The denominator is *recoverable*, not *total*. Dividing by total would inflate the
   rate by counting dead cards the agent correctly refused to chase. Dead failures are
   reported as "correctly never retried", never hidden. Note the precise claim: all 18
   are still *actioned* (12 get a recovery link, 6 escalate to a human) — what they
   never get is a retry. Saying "not pursued" would contradict the audit trail.
2. Diagnosis accuracy is reported alongside the recovery rate, always. A recovery rate
   on its own is untrustworthy: a misclassification silently moves a payment between
   the recoverable and dead buckets and distorts the very denominator above.
3. AFA-gated payments are broken out. They are recoverable and were deliberately not
   retried, so leaving them inside the plain rate would read as agent failure when it
   is regulatory compliance.
"""

import json
from collections import Counter
from pathlib import Path

import config
from core import decide, execute, gate

ACTION_APPROPRIATE_FOR_DEAD = frozenset({decide.SEND_LINK, decide.ESCALATE, decide.REQUIRES_AFA})


def _rupees(paise):
    return f"Rs {paise / 100:,.2f}"


def report(entries, ground_truth):
    """Compute the batch report. `entries` are audit rows; `ground_truth` is eval-only."""
    # A fault-injected run is a demonstration, never a measurement. Refusing here rather
    # than relying on the caller means no future change to run_batch can quietly produce
    # a scored report from tampered input.
    if any(entry.get("injected_fault") for entry in entries):
        raise ValueError(
            "refusing to score a fault-injected run: these entries carry a forced "
            "diagnosis and any metric derived from them would be meaningless"
        )

    def truth(entry):
        return ground_truth[entry["payment_id"]]

    recoverable = [e for e in entries if truth(e)["recoverable"]]
    dead = [e for e in entries if not truth(e)["recoverable"]]
    recovered = [e for e in entries if e["execution_result"]["status"] == execute.CAPTURED]

    afa_gated = [e for e in entries if e["refusal_reason"] == gate.AFA_REQUIRED_ABOVE_THRESHOLD]
    escalated = [e for e in entries if e["action_taken"] == decide.ESCALATE]
    refused = [e for e in entries if e["gate_result"] == gate.REFUSE]

    # A live-instrument action fired at a payment that was never recoverable. Catches a
    # misdiagnosed dead card actually being charged, which is the failure mode that
    # matters most here. Must be 0.
    false_interventions = [
        e for e in dead
        if e["action_taken"] is not None and e["action_taken"] not in ACTION_APPROPRIATE_FOR_DEAD
    ]

    # Should be structurally impossible — execute.simulate() refuses it — but check from
    # the output side too rather than trusting the input side.
    impossible_recoveries = [e for e in recovered if not truth(e)["recoverable"]]

    correct = [e for e in entries if e["category"] == truth(e)["category"]]
    by_source = {}
    for entry in entries:
        bucket = by_source.setdefault(entry["source"], {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += entry["category"] == truth(entry)["category"]

    recoverable_paise = sum(e["amount"] for e in recoverable)
    recovered_paise = sum(e["amount"] for e in recovered)
    afa_paise = sum(e["amount"] for e in afa_gated)

    return {
        "total": len(entries),
        "recoverable": len(recoverable),
        "dead": len(dead),
        "recovered": len(recovered),
        "recovery_rate": len(recovered) / len(recoverable) if recoverable else 0.0,
        "escalated": len(escalated),
        "refused": len(refused),
        # Without this breakdown a budget-limited run reads as a low recovery rate
        # rather than a bound doing its job. The reason matters as much as the count.
        "refused_by_reason": dict(Counter(e["refusal_reason"] for e in refused)),
        "budget_exhausted": any(
            e["refusal_reason"] == gate.GLOBAL_BUDGET_EXHAUSTED for e in refused
        ),
        "afa_gated": len(afa_gated),
        "false_intervention": len(false_interventions),
        "impossible_recoveries": len(impossible_recoveries),
        "diagnosis_accuracy": len(correct) / len(entries) if entries else 0.0,
        "diagnosis_by_source": by_source,
        "misclassified": len(entries) - len(correct),
        "recoverable_paise": recoverable_paise,
        "recovered_paise": recovered_paise,
        "afa_paise": afa_paise,
    }


def write(data, path=None):
    """Persist the report so the UI can render it without re-deriving anything."""
    target = Path(path or Path(__file__).resolve().parent.parent / config.METRICS_PATH)
    target.write_text(json.dumps(data, indent=2) + "\n")
    return target


def render(data):
    """Print the report. Recovery rate and diagnosis accuracy always appear together."""
    lines = [
        "",
        "=" * 70,
        "BATCH RECOVERY REPORT",
        "=" * 70,
        "",
        f"Payments processed      {data['total']}",
        f"  recoverable           {data['recoverable']}",
        f"  truly dead            {data['dead']}  (correctly never retried)",
        "",
        "-- Recovery ----------------------------------------------------------",
        f"Recovered               {data['recovered']} of {data['recoverable']} recoverable"
        f"   ({data['recovery_rate']:.1%})",
        f"Escalated to a human    {data['escalated']}",
        f"Refused by the gate     {data['refused']}",
    ]

    for reason, count in sorted(data["refused_by_reason"].items()):
        lines.append(f"  {reason:<30} {count}")

    lines += [
        f"AFA-gated               {data['afa_gated']}  (recurring, above Rs 15,000;",
        "                            auth link sent instead of a silent retry)",
    ]

    if data["budget_exhausted"]:
        lines += [
            "",
            "NOTE: the global attempt budget was reached, so payments after that point",
            "were never actioned. The recovery rate above is bounded by that cap, not by",
            "the agent failing to recover — raise config.GLOBAL_ATTEMPT_BUDGET to lift it.",
        ]

    lines += [
        "",
        "-- Diagnosis (shown alongside recovery, never on its own) ------------",
        f"Overall accuracy        {data['diagnosis_accuracy']:.1%}  "
        f"({data['misclassified']} misclassified)",
    ]

    for source, bucket in sorted(data["diagnosis_by_source"].items()):
        rate = bucket["correct"] / bucket["total"] if bucket["total"] else 0.0
        lines.append(
            f"  via {source:<20} {bucket['correct']}/{bucket['total']} correct ({rate:.0%})"
        )

    lines += [
        "",
        "-- Safety ------------------------------------------------------------",
        f"False interventions     {data['false_intervention']}  (must be 0)",
        f"Impossible recoveries   {data['impossible_recoveries']}  (must be 0)",
        "",
        "-- Money -------------------------------------------------------------",
        f"Recovered {_rupees(data['recovered_paise'])} of "
        f"{_rupees(data['recoverable_paise'])} recoverable across "
        f"{data['total']} failed payments.",
        f"A further {_rupees(data['afa_paise'])} awaits customer authentication "
        f"({data['afa_gated']} payments) — not written off.",
        "",
        "Baseline without the agent: 0 recovered, all "
        f"{_rupees(data['recoverable_paise'])} stays failed.",
        "=" * 70,
        "",
    ]
    print("\n".join(lines))
