"""Stateful stopping rules: retry caps, cooling-off windows, dead-instrument and
AFA-threshold refusals, global attempt budget, and idempotency.

This is the differentiator, and it is deliberately I/O-free. `evaluate()` is a pure
function of (record, category, intervention, state, now) and `commit()` is the only
mutator — it changes an in-memory dict and nothing else. Loading and saving
`run_state.json` belongs to the orchestrator, not here.

That separation is the scalability claim: because the gate is a pure function over
(record + state) and every action carries an idempotency key, it is safe to retry,
parallelise and replay. Reading a file in here would quietly destroy that property,
so every bound below reads from config.py and every input arrives as an argument —
including `now`, which is passed in rather than read from the clock so the gate stays
deterministic and testable.

What this gate can and cannot catch, stated plainly because the distinction matters:

  * Rule 1 keys off the DIAGNOSED CATEGORY. It guards against decide() proposing a
    retry for a category that must never be retried. Since decide() routes
    dead_instrument to send_link, it is unreachable in a real run and exists as a
    backstop against a future bug in that table.
  * Rule 1b keys off the RECORD. It is independent of the classification path, so a
    misdiagnosis cannot slip a retry past it — but only where the evidence is an
    explicit reason code.
  * Neither can see evidence that exists only in error_description. A payment whose
    only signal of a dead card is prose will be retried if the classifier says to.
    That residual risk is measured, not eliminated: report/metrics.py counts
    false interventions after the fact, and scripts/eval_llm.py measures how often
    the classifier is wrong in the first place.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

import config
from core import decide

# Structured outcomes. Every refusal names a rule, so an auditor never sees a bare
# "blocked" without knowing which bound produced it.
DEAD_INSTRUMENT_NEVER_RETRIED = "DEAD_INSTRUMENT_NEVER_RETRIED"
DEAD_INSTRUMENT_EVIDENCE_IN_RECORD = "DEAD_INSTRUMENT_EVIDENCE_IN_RECORD"
RETRY_CAP_EXCEEDED = "RETRY_CAP_EXCEEDED"
AFA_REQUIRED_ABOVE_THRESHOLD = "AFA_REQUIRED_ABOVE_THRESHOLD"
DUPLICATE_ACTION_IN_WINDOW = "DUPLICATE_ACTION_IN_WINDOW"
COOLING_OFF_ACTIVE = "COOLING_OFF_ACTIVE"
GLOBAL_BUDGET_EXHAUSTED = "GLOBAL_BUDGET_EXHAUSTED"

# Decisions the gate can return.
ALLOW = "allow"
REFUSE = "refuse"
CONVERT = "convert"  # the requested action was forbidden but a compliant one exists


@dataclass(frozen=True)
class GateResult:
    decision: str
    action: Optional[str]
    reason: Optional[str]
    idempotency_key: Optional[str]
    pre_debit_notified: bool = False

    @property
    def allowed(self):
        """True when something will actually be executed (including a conversion)."""
        return self.decision in (ALLOW, CONVERT)


def new_state():
    """A fresh, empty run state. Plain dict so it serialises straight to JSON."""
    return {"global_actions": 0, "payments": {}}


def _payment_state(state, payment_id):
    return state["payments"].get(payment_id, {"attempts": 0, "actions": []})


def cooling_off_hours(category):
    """Per-category retry gap, falling back to the default for non-retry actions.

    The 2h/24h figures reflect standard payment-processor retry practice (retrying
    too soon can itself cause the retry to fail), not a hard RBI rule. The ₹15,000
    AFA threshold below IS a hard rule — the distinction matters and is kept explicit.
    """
    return config.COOLING_OFF_HOURS.get(category, config.DEFAULT_COOLING_OFF_HOURS)


def _has_dead_instrument_evidence(record):
    """True when the record itself carries explicit proof the instrument is dead.

    Reads the raw record rather than the diagnosed category, so it holds whatever the
    classifier concluded. It sees machine-readable reason codes only — evidence that
    exists solely in error_description is invisible to it. See config for why this list
    is duplicated rather than imported from diagnose.
    """
    return record.get("error_reason") in config.HARD_DEAD_INSTRUMENT_REASONS


def _window(now, hours):
    """Bucket index for `now`. Rolls over once per cooling window."""
    return int(now // (hours * 3600))


def idempotency_key(payment_id, action, window):
    """Stable key for (payment, action, window).

    A boolean "already tried" flag cannot distinguish a legitimate later attempt from
    a duplicate of the current one. Bucketing by window means the same payment+action
    produces the identical key for as long as it must not re-fire, and a genuinely
    new key only once the window rolls over.
    """
    raw = f"{payment_id}|{action}|{window}"
    return "idem_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def evaluate(record, category, intervention, state, now):
    """Decide whether `intervention` may fire for `record`. Pure: no I/O, no mutation."""
    payment_id = record["id"]
    payment_state = _payment_state(state, payment_id)
    action = intervention.action

    # 1. A dead instrument is never retried. Checked first because it is a safety
    #    invariant rather than a budget: no amount of remaining quota makes it valid.
    #    This trusts the diagnosed category, so it guards against a bug in decide()
    #    rather than against a wrong diagnosis — see 1b.
    if category == "dead_instrument" and action == decide.RETRY:
        return GateResult(REFUSE, None, DEAD_INSTRUMENT_NEVER_RETRIED, None)

    # 1b. The same invariant, re-derived from the record instead of from the category.
    #     Rule 1 is only as good as the classification; if diagnosis is wrong it never
    #     fires. This one holds regardless of what the record was labelled, so no change
    #     to the classification path can authorise a retry against explicit evidence.
    #     Its reach is narrow and deliberately so: reason codes only, never prose.
    if action == decide.RETRY and _has_dead_instrument_evidence(record):
        return GateResult(REFUSE, None, DEAD_INSTRUMENT_EVIDENCE_IN_RECORD, None)

    # 2. Retry cap counts the processor's own prior attempts plus everything this run
    #    has already fired, so a payment that arrives near the cap cannot slip past it.
    if action == decide.RETRY:
        attempts = record.get("retry_count", 0) + payment_state["attempts"]
        if attempts >= config.MAX_RETRY_ATTEMPTS:
            return GateResult(REFUSE, None, RETRY_CAP_EXCEEDED, None)

    # 3. RBI E-mandate Framework (2026): a *recurring* auto-debit above ₹15,000
    #    requires additional-factor authentication and cannot be silently reattempted.
    #    The payment_type check is essential — the rule does not apply to one-time
    #    payments, and applying it to them would be factually wrong.
    decision, reason = ALLOW, None
    if (
        action == decide.RETRY
        and record.get("payment_type") == "recurring"
        and record.get("amount", 0) > config.AFA_THRESHOLD_PAISE
    ):
        action = decide.REQUIRES_AFA
        decision = CONVERT
        reason = AFA_REQUIRED_ABOVE_THRESHOLD

    # The key is derived from the FINAL action, so a converted action is deduplicated
    # as the authentication link it became, not the retry it started as.
    hours = cooling_off_hours(category)
    key = idempotency_key(payment_id, action, _window(now, hours))

    # 4. Idempotency: this exact action already fired in this window. Checked before
    #    the general cooling-off rule because it is the more specific diagnosis.
    if any(a["idempotency_key"] == key for a in payment_state["actions"]):
        return GateResult(REFUSE, None, DUPLICATE_ACTION_IN_WINDOW, key)

    # 5. Cooling-off: some *other* action touched this payment too recently.
    last_action_at = max((a["at"] for a in payment_state["actions"]), default=None)
    if last_action_at is not None and now - last_action_at < hours * 3600:
        return GateResult(REFUSE, None, COOLING_OFF_ACTIVE, key)

    # 6. Global budget across the whole batch — the runaway-loop backstop.
    if state["global_actions"] >= config.GLOBAL_ATTEMPT_BUDGET:
        return GateResult(REFUSE, None, GLOBAL_BUDGET_EXHAUSTED, key)

    # Pre-debit notification applies to actual debits only. An authentication link is
    # not a debit, so a converted action does not carry the notification step.
    return GateResult(
        decision,
        action,
        reason,
        key,
        pre_debit_notified=(action == decide.RETRY),
    )


def commit(state, payment_id, result, now):
    """Record an allowed action against the run state. The only mutator; still no I/O.

    Call this before executing, not after, so the key is reserved rather than recorded
    after the fact. On its own that only protects within a process: the caller must also
    persist the state before firing, or a crash would lose the reservation and a re-run
    could fire the same action twice. run_batch.process() does exactly that.
    """
    if not result.allowed:
        return state

    payment_state = state["payments"].setdefault(payment_id, {"attempts": 0, "actions": []})
    if result.action == decide.RETRY:
        payment_state["attempts"] += 1
    payment_state["actions"].append(
        {"idempotency_key": result.idempotency_key, "action": result.action, "at": now}
    )
    state["global_actions"] += 1
    return state
