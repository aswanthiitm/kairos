"""
STAGE 5 - NARRATE:  say it in the reader's language, without inventing anything.

The model receives a closed EVIDENCE PACKET of already-computed facts and may
only phrase them. Two mechanisms enforce that:

  1. The prompt contains no raw data and no tools - the model cannot compute.
  2. A NUMERIC GUARD parses every number out of the generated text and checks it
     against the packet. Any unverifiable figure fails the narrative, which is
     retried once and then falls back to a deterministic template.

This is the operational meaning of "the LLM is not the source of quantitative
truth": it is enforced by code at runtime, not promised in a diagram.
"""
import json, os, re, time
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .contract import Contract
from .security import Persona
from .telemetry import Telemetry, MethodType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICING = yaml.safe_load(open(os.path.join(ROOT, "config", "llm_pricing.yaml")))

NUM = re.compile(r"(?<![\w/])(\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")


def _fmt_inr(x: float) -> str:
    a = abs(x)
    if a >= 1e7:
        return "Rs %.2f Cr" % (x / 1e7)
    if a >= 1e5:
        return "Rs %.1f L" % (x / 1e5)
    return "Rs %.0f" % x


RESTRICTED = "[restricted by entitlement]"


def _redact_packet(packet: Dict[str, Any], persona: Persona, kpi_name: str
                   ) -> Dict[str, Any]:
    """Column-level security applied to the evidence packet itself.

    If a persona is denied a measure, the absolute values never enter the packet
    and therefore never reach the model. Relative movement is preserved where it
    is not itself sensitive, so a supply-chain lead still learns that something
    fell 16% without learning how many rupees that was.
    """
    denied = [c for c in persona.deny_columns]
    restricted: List[str] = []
    if kpi_name in denied:
        for f in ("actual", "expected", "delta", "delta_display"):
            packet[f] = RESTRICTED
        restricted.append(kpi_name)
        for c in packet.get("identity_components", []):
            c["display"] = RESTRICTED
        for r in packet.get("recommendations", []):
            r["expected_impact"] = RESTRICTED
        t = packet.get("separating_test")
        if isinstance(t, dict) and "cost_inr" in t:
            t = dict(t); t["cost_inr"] = RESTRICTED; packet["separating_test"] = t
    packet["restricted_fields"] = restricted
    if restricted:
        packet["restriction_note"] = (
            "Absolute %s values are withheld from %s by column-level entitlement; "
            "relative movement is shown instead."
            % (", ".join(restricted), persona.label))
    return packet


def evidence_packet(movement, split_res, verdict, recs, sep_test,
                    persona: Persona, withheld: List[Dict[str, Any]],
                    kpi_name: Optional[str] = None) -> Dict[str, Any]:
    """Everything the narrator is allowed to know. Numbers only appear here."""
    ident = split_res.get("identity") or {}
    leaders = []
    for g in verdict.get("leaders", [])[:3]:
        h, gr = g["hyp"], g["grade"]
        cf = gr["tests"].get("counterfactual") or {}
        leaders.append({
            "label": h["label"], "ladder": gr["ladder"],
            "ladder_meaning": {"L0": "co-movement only", "L1": "associated",
                               "L2": "likely cause", "L3": "quantified counterfactual"
                               }.get(gr["ladder"], gr["ladder"]),
            "explanatory_power_pct": (round(100 * h["explanatory_power"], 1)
                                      if h.get("explanatory_power") is not None else None),
            "mechanism": " -> ".join(gr.get("mechanism_path") or []),
            "did_pct": round(100 * cf["did_pp"], 1) if cf else None,
            "did_p_value": cf.get("p_value") if cf else None,
            "evidence_doc_count": len(gr.get("evidence_docs") or []),
            "independent_sources": (gr["tests"].get("corroboration") or {}).get(
                "independent_source_types"),
        })
    pkt = {
        "kpi": movement.label, "unit": movement.unit,
        "window": movement.window, "scope": movement.filters,
        "actual": movement.actual, "expected": movement.expected,
        "delta": movement.delta,
        "delta_display": (_fmt_inr(movement.delta) if movement.unit == "INR"
                          else ("%s units" % format(int(round(movement.delta)), ",d")
                                if movement.unit == "units"
                                else "%.2f pp" % (100 * movement.delta)
                                if movement.unit == "pct" else round(movement.delta, 2))),
        "pct_change": round(100 * movement.pct_change, 1),
        "z_score": round(movement.z, 2),
        "verdict": verdict["status"], "verdict_reason": verdict["reason"],
        "identity_components": [{"name": c["name"], "display": _fmt_inr(c["value"]),
                                 "pct_of_move": round(100 * c["pct_of_move"], 1)}
                                for c in ident.get("components", [])],
        "top_segments": [{"dimension": c["dimension"], "value": c["value"],
                          "explanatory_power_pct": round(100 * c["explanatory_power"], 1)}
                         for c in split_res.get("contributors", [])[:4]],
        "leaders": leaders,
        "recommendations": [{
            "action": r["action"], "lever": r["lever_label"], "owner": r["owner_role"],
            "expected_impact": _fmt_inr(r["expected_impact_inr"]),
            "confidence_pct": round(100 * r["confidence"]),
            "playbook": (r["source_playbook"] or {}).get("title"),
            "check_in_days": r["monitoring"]["check_in_days"]} for r in recs],
        "separating_test": sep_test,
        "data_quality_flags": movement.data_quality_flags,
        "withheld_evidence_count": len(withheld),
        "persona": {"label": persona.label, "focus": persona.narrative.get("focus"),
                    "depth": persona.narrative.get("depth"),
                    "max_words": persona.narrative.get("max_words")},
    }
    return _redact_packet(pkt, persona, kpi_name or "")


def _allowed_numbers(packet: Dict[str, Any]) -> List[float]:
    vals: List[float] = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            vals.append(float(o))
        elif isinstance(o, str):
            for m in NUM.finditer(o):
                try:
                    vals.append(float(m.group(1).replace(",", "")))
                except ValueError:
                    pass
    walk(packet)
    # Only admit the scale variant the UI actually renders. Emitting every possible
    # scaling inflates the allowed set and makes the guard easier to slip past.
    extra = []
    for v in vals:
        extra += [abs(v), round(v, 1), round(v, 2), round(v)]
        a = abs(v)
        if a >= 1e7:
            extra += [a / 1e7, round(a / 1e7, 2)]
        elif a >= 1e5:
            extra += [a / 1e5, round(a / 1e5, 1)]
    return vals + extra


def numeric_guard(text: str, packet: Dict[str, Any], tol: float = 0.01
                  ) -> Tuple[bool, List[str]]:
    """Every number in the narrative must trace to the packet. Years, small
    ordinals and percentages that appear verbatim are allowed through."""
    allowed = _allowed_numbers(packet)
    bad: List[str] = []
    for m in NUM.finditer(text):
        raw = m.group(1)
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if v in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 24, 28, 30, 100):
            continue
        if 1900 <= v <= 2100 and "." not in raw:
            continue
        ok = any(abs(v - a) <= max(tol * max(abs(a), 1e-9), 0.05) for a in allowed)
        if not ok:
            bad.append(raw)
    return (len(bad) == 0), bad


# ------------------------------------------------------------------ templates
def deterministic_narrative(packet: Dict[str, Any], persona: Persona) -> str:
    """Guaranteed-safe narrative assembled from the packet by string formatting.
    Used when no API key is configured and whenever the numeric guard rejects a
    generated draft. Every figure is copied, never restated."""
    p = packet
    scope = ", ".join("%s=%s" % (k, v) for k, v in (p["scope"] or {}).items()) or "all slices"
    if p.get("restricted_fields"):
        head = ("%s in %s moved %s%% against its expected band over %s to %s. "
                "Absolute values are withheld from this view by your entitlement."
                % (p["kpi"], scope, p["pct_change"],
                   p["window"]["start"], p["window"]["end"]))
    else:
        head = "%s in %s is %s vs expected (%s%%) over %s to %s." % (
            p["kpi"], scope, p["delta_display"], p["pct_change"],
            p["window"]["start"], p["window"]["end"])

    if p["verdict"] == "DATA_QUALITY":
        f = p["data_quality_flags"][0] if p["data_quality_flags"] else {}
        return ("%s This movement is an INSTRUMENTATION ARTEFACT, not a business event. %s "
                "No business explanation is offered until the feed is repaired and the "
                "window is re-run." % (head, f.get("detail", "")))

    if p["verdict"] in ("UNKNOWN", "COMPETING"):
        alts = "; ".join("%s (%s)" % (l["label"], l["ladder_meaning"]) for l in p["leaders"])
        t = p.get("separating_test") or {}
        return ("%s The engine cannot identify a single cause. Competing explanations: %s. "
                "%s Recommended next step: %s (about %s, %s days, owner %s)."
                % (head, alts, p["verdict_reason"], t.get("test", "gather more evidence"),
                   _fmt_inr(t.get("cost_inr", 0)), t.get("days_to_answer", "?"),
                   t.get("owner_role", "analyst")))

    lead = p["leaders"][0] if p["leaders"] else None
    body = ""
    if lead:
        body = (" The leading explanation is %s, graded %s (%s)." %
                (lead["label"], lead["ladder"], lead["ladder_meaning"]))
        if lead.get("did_pct") is not None:
            body += (" A difference-in-differences against an untreated cohort puts the "
                     "effect at %s%%." % lead["did_pct"])
        if lead.get("explanatory_power_pct") is not None:
            body += " It accounts for %s%% of the movement." % lead["explanatory_power_pct"]

    if persona.narrative.get("depth") == "executive":
        comp = "; ".join("%s %s (%s%% of the move)" % (c["name"], c["display"], c["pct_of_move"])
                         for c in p["identity_components"])
        body += " Decomposition: %s." % comp

    if persona.narrative.get("depth") == "operational" and p["top_segments"]:
        segs = ", ".join("%s %s (%s%%)" % (s["dimension"], s["value"], s["explanatory_power_pct"])
                         for s in p["top_segments"][:3])
        body += " Concentrated in: %s." % segs

    if persona.narrative.get("wants_method_detail"):
        body += (" Evidence: %s corroborating documents from %s independent source types."
                 % (lead.get("evidence_doc_count") if lead else 0,
                    lead.get("independent_sources") if lead else 0))

    act = ""
    if p["recommendations"]:
        r = p["recommendations"][0]
        act = (" Recommended: %s Lever: %s. Owner: %s. Expected recovery %s at %s%% "
               "confidence, reviewed in %s days."
               % (r["action"], r["lever"], r["owner"], r["expected_impact"],
                  r["confidence_pct"], r["check_in_days"]))
    wh = ""
    if p["withheld_evidence_count"]:
        wh = (" %s evidence items were withheld from this narrative by your access "
              "entitlements." % p["withheld_evidence_count"])
    return head + body + act + wh


# ------------------------------------------------------------------------ llm
SYSTEM = """You are the narration layer of a KPI analysis engine.

ABSOLUTE RULES
1. You are given a JSON evidence packet of ALREADY-COMPUTED facts. Every number
   you write MUST be copied from it. Never calculate, convert, round differently,
   infer or estimate any figure.
2. Never assert a cause that the packet does not grade L2 or L3. If the verdict
   is UNKNOWN or COMPETING you must say plainly that the cause is not established
   and lead with the separating test.
3. Do not add context, benchmarks or advice that is not in the packet.
4. Write for the named persona, in their register, within the word limit.
5. Plain prose. No headings, no bullets, no markdown, no preamble."""


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


def _simulate(packet: Dict[str, Any], persona: Persona, inject_hallucination: bool
              ) -> Tuple[str, int, int]:
    """Deterministic stand-in for the model, so the LLM path - including the guard,
    token accounting and cost telemetry - is demonstrable without an API key.
    Clearly labelled as simulated everywhere it surfaces."""
    base = deterministic_narrative(packet, persona)
    if inject_hallucination:
        base += (" Margin compression of 9.7% and a further Rs 4.2 Cr of exposure are "
                 "expected next quarter.")           # <- figures that are NOT in the packet
    in_tok = len(json.dumps(packet)) // 4 + 320       # prompt + system
    out_tok = max(60, len(base) // 4)
    return base, in_tok, out_tok


def narrate(packet: Dict[str, Any], persona: Persona, tel: Telemetry,
            purpose: str = "narrative", force_offline: bool = False,
            mode: str = "auto") -> Dict[str, Any]:
    """mode: auto | offline | simulate | simulate_bad"""
    model = PRICING["routing"].get(purpose, "claude-haiku-4-5-20251001")

    if mode in ("simulate", "simulate_bad"):
        last_bad: List[str] = []
        for attempt in (1, 2):
            t0 = time.time()
            text, in_tok, out_tok = _simulate(
                packet, persona, inject_hallucination=(mode == "simulate_bad"))
            time.sleep(0.05)                          # stand-in for network latency
            ms = (time.time() - t0) * 1000
            tel.llm(purpose, model, in_tok, out_tok, ms)
            ok, bad = numeric_guard(text, packet)
            tel.method("narrate", MethodType.LLM,
                       "persona narrative generation, SIMULATED (attempt %d)" % attempt,
                       "the model phrases pre-computed facts; it has no tools, no data "
                       "access and no arithmetic role")
            tel.method("narrate", MethodType.DETERMINISTIC, "numeric guard",
                       "every number in the generated text is parsed and matched against "
                       "the evidence packet; unverifiable figures fail the narrative",
                       detail="passed=%s unverified=%s" % (ok, bad))
            if ok:
                return {"text": text, "mode": "llm_simulated",
                        "guard": {"passed": True, "bad": []},
                        "attempts": attempt, "model": model + " (simulated)"}
            last_bad = bad
        text = deterministic_narrative(packet, persona)
        tel.method("narrate", MethodType.DETERMINISTIC, "fallback after guard failure",
                   "the generated draft failed the numeric guard twice, so the engine "
                   "published the deterministic narrative instead of an unverifiable one")
        return {"text": text, "mode": "deterministic_fallback",
                "guard": {"passed": False, "bad": last_bad},
                "attempts": 2, "model": model + " (simulated)"}

    client = None if (force_offline or mode == "offline") else _client()

    if client is None:
        text = deterministic_narrative(packet, persona)
        tel.method("narrate", MethodType.DETERMINISTIC, "template narrative (no API key)",
                   "the engine degrades to a deterministic narrator rather than to "
                   "silence; every figure is copied from the evidence packet")
        return {"text": text, "mode": "deterministic", "guard": {"passed": True, "bad": []},
                "attempts": 0, "model": None}

    user = ("PERSONA: %s\nFOCUS: %s\nMAX WORDS: %s\n\nEVIDENCE PACKET:\n%s"
            % (persona.label, ", ".join(persona.narrative.get("focus", [])),
               persona.narrative.get("max_words", 160),
               json.dumps(packet, indent=2, default=str)))

    last_bad: List[str] = []
    for attempt in (1, 2):
        t0 = time.time()
        msg = client.messages.create(
            model=model, max_tokens=700, system=SYSTEM,
            messages=[{"role": "user", "content": user if attempt == 1 else
                       user + ("\n\nYour previous draft contained figures that are not in "
                               "the packet: %s. Rewrite using ONLY packet numbers."
                               % ", ".join(last_bad))}])
        ms = (time.time() - t0) * 1000
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        tel.llm(purpose, model, msg.usage.input_tokens, msg.usage.output_tokens, ms)
        ok, bad = numeric_guard(text, packet)
        tel.method("narrate", MethodType.LLM, "persona narrative generation (attempt %d)" % attempt,
                   "the model phrases pre-computed facts; it has no tools, no data access "
                   "and no arithmetic role")
        tel.method("narrate", MethodType.DETERMINISTIC, "numeric guard",
                   "every number in the generated text is parsed and matched against the "
                   "evidence packet; unverifiable figures fail the narrative",
                   detail="passed=%s unverified=%s" % (ok, bad))
        if ok:
            return {"text": text, "mode": "llm", "guard": {"passed": True, "bad": []},
                    "attempts": attempt, "model": model}
        last_bad = bad

    text = deterministic_narrative(packet, persona)
    tel.method("narrate", MethodType.DETERMINISTIC, "fallback after guard failure",
               "the generated draft failed the numeric guard twice, so the engine "
               "published the deterministic narrative instead of an unverifiable one")
    return {"text": text, "mode": "deterministic_fallback",
            "guard": {"passed": False, "bad": last_bad}, "attempts": 2, "model": model}
