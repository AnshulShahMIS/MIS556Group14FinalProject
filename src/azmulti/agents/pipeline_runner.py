"""Runs the specialist pipeline in-process (used by tools that re-plan: options, add a stop,
swap to an alternative, apply group votes, change transport...). Same order as the graph."""
from __future__ import annotations

from ..config import MAX_REPLANS
from .concierge import booking_node, concierge_node
from .critic import critic_node, repair_node
from .live_agent import live_node
from .logistics import budget_node, route_node
from .specialists import attraction_node, dining_node, lodging_node


def run_pipeline(state: dict, write: bool = True, refill: bool = True) -> dict:
    st = dict(state)
    st["replans"] = 0
    st["repaired"] = []
    steps = [attraction_node, dining_node, lodging_node, live_node, route_node, budget_node] if refill else [live_node, route_node, budget_node]
    for node in steps:
        st.update(node(st))
    for _ in range(MAX_REPLANS + 1):
        st.update(critic_node(st))
        if not st.get("needs_repair"):
            break
        st.update(repair_node(st))
        for node in (attraction_node, dining_node, lodging_node, live_node, route_node, budget_node):
            st.update(node(st))
    st.update(booking_node(st))
    if write:
        st.update(concierge_node(st))
    return st
