"""Turns a decision trace (record + category + intervention + gate result) into one
plain-language rationale sentence via the LLM.

This is the output side of the LLM's job: the rules decide, the model explains. It
never influences a decision — by the time this runs, the outcome is already fixed.

Two properties matter. The prompt is built from stable fields only (no timestamps), so
re-running the batch hits the LLM cache instead of the API and the demo stays hermetic.
And any failure — no key, rate limit, bad provider — falls back to a deterministic
template sentence rather than aborting the run or leaving a blank rationale.
"""

import config
from core import gate, llm

_SYSTEM = (
    "You explain automated payment-recovery decisions to a finance operations reader. "
    "Reply with exactly one sentence, under 30 words, plain language, no preamble, no "
    "bullet points. State what was done and why. If an action was refused, say plainly "
    "that it was refused and name the reason."
)

# Human-readable glosses so the model is told what a code means rather than guessing.
_REASONS = {
    gate.DEAD_INSTRUMENT_NEVER_RETRIED: "the payment instrument is dead, so it must never be retried",
    gate.RETRY_CAP_EXCEEDED: "the payment has reached the maximum number of retry attempts",
    gate.AFA_REQUIRED_ABOVE_THRESHOLD: (
        "this recurring debit is above the RBI additional-factor-authentication "
        "threshold of Rs 15,000, so it cannot be silently retried"
    ),
    gate.DUPLICATE_ACTION_IN_WINDOW: "this exact action already fired inside the cooling-off window",
    gate.COOLING_OFF_ACTIVE: "the payment was actioned too recently and is still cooling off",
    gate.GLOBAL_BUDGET_EXHAUSTED: "the batch-wide action budget is exhausted",
}


def _prompt(trace):
    """Stable prompt text. Deliberately excludes the timestamp so the cache hits."""
    reason = trace.get("refusal_reason")
    lines = [
        f"Failure category: {trace['category']} (determined by {trace['source']})",
        f"Payment type: {trace['payment_type']}, amount: Rs {trace['amount'] / 100:,.2f}",
        f"Planned intervention: {trace['intervention']}",
        f"Gate decision: {trace['gate_result']}",
        f"Action taken: {trace['action'] or 'none'}",
        f"Outcome: {trace['execution_status']}",
    ]
    if reason:
        lines.append(f"Reason code {reason} — meaning: {_REASONS.get(reason, reason)}")
    return "\n".join(lines)


def _template(trace):
    """Deterministic fallback. Always available, never blank."""
    reason = trace.get("refusal_reason")
    gloss = _REASONS.get(reason, reason) if reason else None

    if trace["gate_result"] == gate.REFUSE:
        return f"No action taken on {trace['payment_id']} because {gloss}."
    if trace["gate_result"] == gate.CONVERT:
        return (
            f"Sent an authentication link for {trace['payment_id']} instead of retrying, "
            f"because {gloss}."
        )
    return (
        f"Actioned {trace['payment_id']} with {trace['action']} for a "
        f"{trace['category']} failure; outcome was {trace['execution_status']}."
    )


def explain(trace, use_llm=None):
    """Return a one-sentence rationale for a decision trace."""
    if use_llm is None:
        use_llm = config.EXPLAIN_WITH_LLM
    if not use_llm:
        return _template(trace)

    try:
        sentence = llm.complete(_SYSTEM, _prompt(trace)).strip()
    except Exception as exc:  # noqa: BLE001 - a missing rationale must not kill the run
        print(f"[explain] LLM unavailable ({exc}); using template rationale")
        return _template(trace)

    return sentence or _template(trace)
