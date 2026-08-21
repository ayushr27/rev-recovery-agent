"""Pure mapping from a recovery category to the Intervention to take. No I/O.

This module answers "what SHOULD we do", never "may we do it" — every bound, cap and
refusal lives in gate.py. Keeping the two apart is what lets the gate be the single
place a reviewer has to read to audit the agent's stopping behaviour.
"""

from dataclasses import dataclass

import config

# Action types. RETRY / SEND_LINK / ESCALATE are produced here; REQUIRES_AFA is only
# ever produced by the gate, when it refuses to let a retry fire silently.
RETRY = "retry"
SEND_LINK = "send_link"
ESCALATE = "escalate"
REQUIRES_AFA = "requires_afa"


@dataclass(frozen=True)
class Intervention:
    """What to do about a failed payment, before any stopping rule is applied."""

    action: str
    delay_hours: int
    touches_live_instrument: bool
    nudge: bool = False


# The four-category table. Delays read from config so the retry gaps stay tunable in
# one place rather than being restated here.
_INTERVENTIONS = {
    "bank_downtime": Intervention(
        action=RETRY,
        delay_hours=config.COOLING_OFF_HOURS["bank_downtime"],
        touches_live_instrument=True,
    ),
    "insufficient_funds": Intervention(
        action=RETRY,
        delay_hours=config.COOLING_OFF_HOURS["insufficient_funds"],
        touches_live_instrument=True,
        nudge=True,
    ),
    "dead_instrument": Intervention(
        action=SEND_LINK,
        delay_hours=0,
        touches_live_instrument=False,
    ),
    "exhausted": Intervention(
        action=ESCALATE,
        delay_hours=0,
        touches_live_instrument=False,
    ),
}


def decide(category):
    """Return the Intervention for a category.

    Raises on anything outside the four categories — including the "needs_llm"
    sentinel, which the caller must resolve before deciding. Failing closed here is
    deliberate: silently inventing an intervention for an unknown label is how an
    agent ends up retrying something it never classified.
    """
    if category not in _INTERVENTIONS:
        raise ValueError(
            f"cannot decide on unresolved category {category!r}; "
            f"expected one of {sorted(_INTERVENTIONS)}"
        )
    return _INTERVENTIONS[category]
