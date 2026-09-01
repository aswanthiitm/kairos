"""
DECISION-RIGHTS ROUTER - which mode of human-AI collaboration does this call require?

Grounded in the human-AI decision-division taxonomy set out by Prasanth, Vadakkan,
Surendran & Thomas, "Role of Artificial Intelligence and Business Decision Making",
IJACSA 14(6), 2023 (Fig. 4), which distinguishes aggregated human-AI choice
generation, full delegation to AI, and hybrid AI-human sequential choice creation;
and by Trunk, Birkel & Hartmann on AI-supported decision-making under uncertainty,
which argues the appropriate mode is a function of how much uncertainty remains
rather than of the technology's capability.

The engine already knew WHO owns an action. What it could not do was say what the
machine's role in that decision legitimately is. "decision_right: RSM up to Rs 25L;
above that CFO approval" was prose - the engine could print it but never check it.

Four modes, from most to least machine authority:

  AI_DELEGATED           machine decides and acts.  DELIBERATELY NEVER ASSIGNED.
  AI_LED_HUMAN_APPROVES  machine proposes a specific action, a named human approves
  HUMAN_LED_AI_SUPPORTS  machine supplies evidence, the human forms the decision
  HUMAN_ONLY_AI_ABSTAINS machine declines to shape the decision at all

The mode is derived, not configured: evidence grade x value at risk vs the owner's
authority limit x reversibility of the lever. That makes EU AI Act Art. 14 human
oversight an inspectable property of each recommendation rather than a policy claim.
"""
from typing import Any, Dict, Optional

from .contract import Contract
from .telemetry import Telemetry, MethodType

MODES = {
    "AI_DELEGATED": {
        "label": "Full delegation to the machine",
        "machine_may": "decide and execute without a human in the loop",
        "human_role": "none",
    },
    "AI_LED_HUMAN_APPROVES": {
        "label": "AI-led, human approves",
        "machine_may": "propose one specific action with an expected effect; a named "
                       "human approves before anything happens",
        "human_role": "approver",
    },
    "HUMAN_LED_AI_SUPPORTS": {
        "label": "Human-led, AI supports",
        "machine_may": "supply evidence, decomposition and options; it must not "
                       "single out one course of action as the answer",
        "human_role": "decision maker",
    },
    "HUMAN_ONLY_AI_ABSTAINS": {
        "label": "Human only, AI abstains",
        "machine_may": "state what it does not know and what would resolve it",
        "human_role": "decision maker, unaided on the causal question",
    },
}

LADDER_RANK = {"L3": 3, "L2": 2, "L1": 1, "L0": 0, "REJECTED": 0}


def route(contract: Contract, tel: Telemetry, rec: Dict[str, Any],
          verdict_status: str, at_risk_inr: float = 0.0) -> Dict[str, Any]:
    """Assign one recommendation to a collaboration mode, with the reasoning kept."""
    lever = contract.levers().get(rec.get("lever"), {})
    grade = rec.get("driver_ladder") or ("L0" if rec.get("abstention") else "L2")
    rank = LADDER_RANK.get(grade, 0)
    limit = lever.get("authority_limit_inr", 0) or 0
    reversibility = lever.get("reversibility", "hard_to_reverse")
    impact = abs(rec.get("expected_impact_inr") or 0) or abs(at_risk_inr)
    reasons = []

    # 1. Abstention states are never machine-shaped decisions.
    if verdict_status in ("UNKNOWN", "COMPETING", "INSUFFICIENT_HISTORY", "DATA_QUALITY") \
            or rec.get("abstention"):
        mode = "HUMAN_ONLY_AI_ABSTAINS"
        reasons.append("the engine did not establish a cause (%s), so it has no basis "
                       "to shape this decision" % verdict_status.lower().replace("_", " "))
    else:
        # 2. Evidence floor: below L3 the machine may inform but not lead.
        if rank >= 3:
            mode = "AI_LED_HUMAN_APPROVES"
            reasons.append("driver reached %s, a validated counterfactual" % grade)
        elif rank == 2:
            mode = "HUMAN_LED_AI_SUPPORTS"
            reasons.append("driver reached %s - likely, but not quantified against a "
                           "counterfactual" % grade)
        else:
            mode = "HUMAN_ONLY_AI_ABSTAINS"
            reasons.append("driver is only %s; leading a decision on it would be a "
                           "guess dressed as a finding" % grade)

        # 3. Authority: value at risk above the owner's limit escalates the mode.
        if mode == "AI_LED_HUMAN_APPROVES" and limit and impact > limit:
            mode = "HUMAN_LED_AI_SUPPORTS"
            reasons.append("expected impact Rs %.1fL exceeds the %s authority limit of "
                           "Rs %.1fL, so this escalates rather than being approved in place"
                           % (impact / 1e5, rec.get("owner_role"), limit / 1e5))

        # 4. Reversibility: an irreversible, externally visible act is never AI-led.
        if mode == "AI_LED_HUMAN_APPROVES" and reversibility == "hard_to_reverse":
            mode = "HUMAN_LED_AI_SUPPORTS"
            reasons.append("the lever is hard to reverse, so the machine may not be the "
                           "one to single it out")

    spec = dict(MODES[mode])
    out = {
        "mode": mode, "label": spec["label"], "machine_may": spec["machine_may"],
        "human_role": spec["human_role"], "reasons": reasons,
        "evidence_grade": grade,
        "authority_limit_inr": limit, "expected_impact_inr": impact,
        "within_authority": (not limit) or impact <= limit,
        "reversibility": reversibility,
        "external_facing": lever.get("external_facing", False),
        "decision_right": rec.get("decision_right"),
        "never_assigned": None,
    }
    tel.method("solve", MethodType.RULES, "human-AI decision-rights routing",
               "the mode of collaboration is derived from evidence grade, value at risk "
               "against the owner's authority limit, and reversibility - so Article 14 "
               "human oversight is an inspectable property of each recommendation rather "
               "than a policy statement (taxonomy after Prasanth et al., IJACSA 2023)",
               detail="lever=%s grade=%s impact=%.0f limit=%.0f -> %s"
                      % (rec.get("lever"), grade, impact, limit, mode))
    return out


def summary(contract: Contract) -> Dict[str, Any]:
    """What the engine will never do, stated up front."""
    return {
        "modes": MODES,
        "reserved_unused": {
            "mode": "AI_DELEGATED",
            "why": "no recommendation in this engine is ever routed to full delegation. "
                   "Every lever in the contract is either externally visible to a "
                   "customer, or moves money, or both. Auto-execution is where trust and "
                   "liability break simultaneously, so the mode exists in the taxonomy "
                   "and is deliberately left empty.",
        },
        "levers": {k: {"authority_limit_inr": v.get("authority_limit_inr"),
                       "reversibility": v.get("reversibility"),
                       "external_facing": v.get("external_facing"),
                       "decision_right": v.get("decision_right")}
                   for k, v in contract.levers().items()},
    }
