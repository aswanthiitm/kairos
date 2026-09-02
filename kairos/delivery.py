"""
Delivery — rendering a finding into the channel a persona actually reads.

Each persona declares a channel in the entitlement contract. Until now that was
a label the UI printed and nothing more. This renders the finding into the shape
that channel needs and writes it to an outbox.

It does NOT send. There is no SMTP, no webhook, no API key, and nothing leaves
the machine. Writing the artifact and recording the intent is the honest extent
of what a prototype should do, and the outbox is what an integration would pick
up. Anything more would be a claim the code cannot support.
"""
import json, os, re
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTBOX = os.path.join(ROOT, "runtime", "outbox")

CHANNELS = {
    "exec_brief":    {"label": "Monday exec brief + email", "format": "md",  "max_words": 150},
    "crm_task":      {"label": "CRM task + digest",         "format": "json", "max_words": 90},
    "ops_board":     {"label": "Ops standup board",         "format": "md",  "max_words": 110},
    "analyst_bench": {"label": "Analyst workbench",         "format": "json", "max_words": 400},
}

PERSONA_CHANNEL = {"cfo": "exec_brief", "rsm_north": "crm_task",
                   "supply_chain_lead": "ops_board", "data_analyst": "analyst_bench"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40]


def render(result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a pipeline result into a channel-shaped payload."""
    persona = result["persona"]["key"]
    ch = PERSONA_CHANNEL.get(persona, "analyst_bench")
    spec = CHANNELS[ch]
    v = result["verdict"]
    mv = result.get("movement") or {}
    recs = result.get("recommendations") or []
    top = recs[0] if recs else None

    subject = "%s · %s%s" % (
        v["status"].replace("_", " ").title(),
        mv.get("label", "KPI"),
        (" · " + ", ".join("%s=%s" % kv for kv in (mv.get("filters") or {}).items()))
        if mv.get("filters") else "")

    if spec["format"] == "md":
        lines = ["# " + subject, "", result["narrative"]["text"]]
        if top:
            lines += ["", "## Recommended", "",
                      "- **Action** " + top["action"],
                      "- **Owner** " + str(top["owner_role"]),
                      "- **Mode** " + top.get("delegation", {}).get("label", "-"),
                      "- **Review** in %s days" % top["monitoring"]["check_in_days"]]
        if result.get("separating_test"):
            t = result["separating_test"]
            lines += ["", "## Before acting", "", t["test"],
                      "", "_%s, %s days, owner %s_" % (t["cost_inr"], t["days_to_answer"],
                                                       t["owner_role"])]
        body: Any = "\n".join(lines)
    else:
        body = {
            "subject": subject,
            "verdict": v["status"],
            "narrative": result["narrative"]["text"],
            "action": (top or {}).get("action"),
            "owner": (top or {}).get("owner_role"),
            "delegation_mode": (top or {}).get("delegation", {}).get("mode"),
            "evidence_grade": (result.get("hypotheses") or [{}])[0]
                              .get("grade", {}).get("ladder"),
            "withheld_evidence": len(result.get("withheld_evidence") or []),
            "case": result.get("scenario"),
        }
    return {"channel": ch, "channel_label": spec["label"], "format": spec["format"],
            "persona": persona, "subject": subject, "body": body,
            "word_budget": spec["max_words"]}


def queue(result: Dict[str, Any]) -> Dict[str, Any]:
    """Write the rendered payload to the outbox. Sending is out of scope."""
    payload = render(result)
    os.makedirs(OUTBOX, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    name = "%s__%s__%s.%s" % (ts, _slug(payload["persona"]),
                              _slug(result.get("scenario", "case")), payload["format"])
    path = os.path.join(OUTBOX, name)
    with open(path, "w") as f:
        f.write(payload["body"] if payload["format"] == "md"
                else json.dumps(payload["body"], indent=2, default=str))
    payload.update({"queued_at": datetime.utcnow().isoformat(), "path": path,
                    "sent": False,
                    "note": "written to the outbox; no transport is configured, so "
                            "nothing was sent"})
    return payload


def outbox() -> List[Dict[str, Any]]:
    if not os.path.isdir(OUTBOX):
        return []
    out = []
    for n in sorted(os.listdir(OUTBOX), reverse=True)[:40]:
        p = os.path.join(OUTBOX, n)
        out.append({"file": n, "bytes": os.path.getsize(p),
                    "modified": datetime.fromtimestamp(os.path.getmtime(p)).isoformat()})
    return out
