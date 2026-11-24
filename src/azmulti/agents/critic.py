"""CRITIC agent: itinerary quality score (0-100) + automatic replanning.

Four 25-point components, matching the project brief's "Itinerary Quality Scoring":
budget fit, preference fit, efficiency (driving vs time) and feasibility (open, on time).
Fixable problems (closed place, over budget, overrun) are sent back to the specialist
agents, at most MAX_REPLANS times - this is the agentic loop in the LangGraph.
"""
from __future__ import annotations

from ..config import MAX_REPLANS
from ..state import PlannerState


def score(state: PlannerState) -> dict:
    req, slots = state["request"], state["slots"]
    b = state.get("budget", {})
    issues = state.get("issues", [])
    filled = [s for s in slots if s.get("place")]
    budget_pp = req.get("budget_per_person")
    if budget_pp:
        over = max(0.0, b.get("total_pp", 0) - budget_pp)
        budget_fit = 25 * max(0.0, 1 - 2 * over / budget_pp)
    else:
        budget_fit = 25.0
    sims = [s["place"].get("similarity") or 0 for s in filled]
    pref = sum(min(1.0, max(0.0, (x - 0.15) / 0.4)) for x in sims) / max(1, len(sims))
    cuisine_miss = [s for s in filled if s.get("cuisine") and s["cuisine"] not in (s["place"].get("cuisines") or "")]
    pref_fit = 25 * pref * (0.8 if cuisine_miss else 1.0) * (len(filled) / max(1, len(slots)))
    span = max(60, max((s.get("end", 0) for s in filled), default=0) - min((s.get("start", 0) for s in filled), default=0))
    route = state.get("route", {})
    drive = route.get("minutes", 0) - route.get("transfer_min", 0)  # city-to-city drives on multi-city trips are planned, not waste
    efficiency = 25 * max(0.0, 1 - drive / (0.35 * span * max(1, req.get("days", 1))))
    hard = sum(1 for i in issues if i["type"] in ("closed", "overrun"))
    soft = sum(1 for i in issues if i["type"] in ("late", "long_drive", "booking_conflict"))
    unknown = sum(1 for s in filled if s.get("open_ok") is None and s["kind"] != "lodging")  # hotels don't close
    days = max(1, req.get("days", 1))  # long trips: penalties are averaged per day, not summed over every stop
    feasibility = max(0.0, 25 - (8 * hard + 4 * soft + 1.5 * unknown) / days)
    parts = {"budget_fit": budget_fit, "preference_fit": pref_fit, "efficiency": efficiency, "feasibility": feasibility}
    return {k: round(v, 1) for k, v in parts.items()} | {"total": round(sum(parts.values()), 1),
                                                         "cuisine_misses": [s["label"] for s in cuisine_miss]}


def critic_node(state: PlannerState) -> dict:
    q = score(state)
    issues = state.get("issues", [])
    fixes = [i for i in issues if i["type"] in ("closed", "over_budget", "overrun")]
    replans = state.get("replans", 0)
    trace = [{"agent": "Critic", "via": "rules", "did": f"quality {q['total']}/100; {len(fixes)} fixable issue(s)"}]
    return {"quality": q, "trace": state.get("trace", []) + trace, "needs_repair": bool(fixes) and replans < MAX_REPLANS}


def route_after_critic(state: PlannerState) -> str:
    return "repair" if state.get("needs_repair") else "booking"


def repair_node(state: PlannerState) -> dict:
    """Clear the stops the critic flagged, with tighter constraints, so specialists refill them."""
    slots = [dict(s) for s in state["slots"]]
    by_id = {s["id"]: s for s in slots}
    did = []
    for i in state.get("issues", []):
        if i["type"] not in ("closed", "over_budget", "overrun"):
            continue  # warnings (late, long drive) are reported, not re-searched
        s = by_id.get(i.get("slot"))
        if not s or not s.get("place"):
            continue
        if i["type"] == "overrun":
            slots.remove(s)
            did.append(f"dropped {s['label']} (runs late)")
            continue
        s["exclude"] = list(set(s.get("exclude", [])) | {s["place"]["id"]})
        if i["type"] == "over_budget":
            # cheaper, but stay within reason: aim to cover the overspend, never below a sensible floor
            s["max_cost"] = max(0.0 if s["kind"] == "attraction" else 10.0, min(s.get("cost_pp", 0) - 1, s.get("cost_pp", 0) - i["over"]))
            s["fallback_place"] = s["place"]  # keep the original if nothing cheaper exists
        did.append(f"re-search {s['label']} ({i['type']})")
        s.update(place=None, reason="", locked=False)
    trace = {"agent": "Critic", "via": "rules", "did": "replan: " + "; ".join(did)}
    repaired = list(state.get("repaired", [])) + [{"slot": i.get("slot"), "type": i["type"]} for i in state.get("issues", [])
                                                  if i["type"] in ("closed", "over_budget", "overrun")]
    return {"slots": slots, "replans": state.get("replans", 0) + 1, "repaired": repaired, "trace": state.get("trace", []) + [trace]}
