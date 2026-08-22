"""Two questions the batch report cannot answer, computed from a completed run.

1. **Does the LLM earn its place?** 46 of 50 records are resolved by the rules table, so
   the 98% headline accuracy is overwhelmingly a rules result. That invites a fair
   criticism — where does the model actually add value? This answers it in rupees: how
   much of the recovered figure exists only because the ambiguous tail was classified
   rather than escalated.

2. **How much does the headline rest on a guess?** `SIMULATED_RETRY_SUCCESS_RATE` (0.75 /
   0.45) is plausible, not measured. The recovery figure inherits that assumption whole.
   Rather than caveat it in prose, this sweeps the assumption and prints the band.

Read-only: reads audit_log.jsonl and re-derives outcomes through the same `execute._roll`
the run used. Writes nothing, calls no API, and changes no published metric.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import decide, execute

ROOT = Path(__file__).resolve().parent.parent


def _rupees(paise):
    return f"Rs {paise / 100:,.2f}"


def load_entries(path=None):
    target = Path(path or ROOT / config.AUDIT_LOG_PATH)
    if not target.exists():
        raise SystemExit(f"{target} not found — run `python run_batch.py` first")
    entries = [json.loads(line) for line in target.read_text().splitlines() if line.strip()]
    if any(e.get("injected_fault") for e in entries):
        raise SystemExit(
            "refusing to analyse a fault-injected trail: those entries carry a forced "
            "diagnosis and any figure derived from them would be meaningless"
        )
    return entries


def llm_contribution(entries):
    """What the ambiguous tail was worth, in rupees.

    Without the LLM the rules return `needs_llm` and `run_batch --no-llm` escalates the
    record instead — so every rupee captured on an LLM-diagnosed row is a rupee the rules
    alone would not have recovered.
    """
    llm_rows = [e for e in entries if e["source"].startswith("llm")]
    captured = [e for e in llm_rows if e["execution_result"]["status"] == execute.CAPTURED]
    total_captured = sum(
        e["amount"] for e in entries if e["execution_result"]["status"] == execute.CAPTURED
    )
    return {
        "rows": llm_rows,
        "won_paise": sum(e["amount"] for e in captured),
        "total_paise": total_captured,
    }


def sensitivity(entries, scales=(0.6, 0.8, 1.0, 1.2, 1.4)):
    """Recovered money as a function of the assumed retry success rates.

    `execute._roll` is a pure hash of the payment id, so each retried record has a fixed
    threshold and the whole curve is computable without re-running the batch.
    """
    retried = [
        (e["payment_id"], e["category"], e["amount"], execute._roll(e["payment_id"], "retry"))
        for e in entries if e["action_taken"] == decide.RETRY
    ]
    base = config.SIMULATED_RETRY_SUCCESS_RATE

    def at(scale):
        rates = {k: min(1.0, max(0.0, v * scale)) for k, v in base.items()}
        won = [amount for _, cat, amount, roll in retried if roll < rates.get(cat, 0.0)]
        return len(won), sum(won)

    return retried, {scale: at(scale) for scale in scales}, at(0.0), at(1000.0)


def main():
    entries = load_entries()

    print("=" * 72)
    print("WHERE THE VALUE COMES FROM, AND WHAT IT RESTS ON")
    print("=" * 72)

    llm = llm_contribution(entries)
    print("\n-- 1. Does the LLM earn its place? ----------------------------------")
    print(f"  {len(llm['rows'])} of {len(entries)} records reached the model; "
          f"the rules resolved the rest.\n")
    for e in llm["rows"]:
        won = e["execution_result"]["status"] == execute.CAPTURED
        print(f"    {e['payment_id']}  {e['category']:<19}{e['action_taken']:<11}"
              f"{e['execution_result']['status']:<11}{_rupees(e['amount']):>13}"
              f"{'  <-- recovered' if won else ''}")
    share = llm["won_paise"] / llm["total_paise"] if llm["total_paise"] else 0.0
    print("\n  Recovered because the ambiguous tail was classified rather than escalated:")
    print(f"    {_rupees(llm['won_paise'])} of {_rupees(llm['total_paise'])}  "
          f"({share:.1%} of the headline)")
    print("  Without the LLM these records escalate to a human — verify with --no-llm.")
    print("  Note the honest shape: the model's value is real but small, and the 98%")
    print("  headline accuracy is a rules result, not an LLM result.")

    retried, curve, floor, ceiling = sensitivity(entries)
    rates = ", ".join(f"{k} {v}" for k, v in sorted(config.SIMULATED_RETRY_SUCCESS_RATE.items()))
    print("\n-- 2. How much does the headline rest on a guess? -------------------")
    print(f"  {len(retried)} records were retried. Their outcomes are simulated against")
    print(f"  assumed success rates ({rates}) — plausible industry")
    print("  figures, NOT measured from real data.\n")
    print(f"    {'assumed rates':<28}{'recovered':>11}{'money':>18}")
    labels = {0.6: "40% worse than assumed", 0.8: "20% worse",
              1.0: "as configured (headline)", 1.2: "20% better", 1.4: "40% better"}
    for scale in sorted(curve):
        count, paise = curve[scale]
        print(f"    {labels.get(scale, f'x{scale}'):<28}{count:>11}{_rupees(paise):>18}")
    print(f"\n    {'structural floor (0%)':<28}{floor[0]:>11}{_rupees(floor[1]):>18}")
    print(f"    {'structural ceiling (100%)':<28}{ceiling[0]:>11}{_rupees(ceiling[1]):>18}")
    print("\n  The ceiling is bounded by the gate, not by the success rate: only these")
    print("  records were ever allowed a retry. Read the headline as one point on this")
    print("  curve, not as a measurement.")
    print("=" * 72)


if __name__ == "__main__":
    main()
