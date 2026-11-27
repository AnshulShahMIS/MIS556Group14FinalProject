"""Method 4 - agentic workflow & state orchestration (LangGraph).

    planner ─┬─ new_plan / revise ─> weather & events ─> attraction ─> dining ─> live info ─> route ─> budget ─> critic ─┬─> booking ─> concierge ─> END
             │                                               ^                                             │
             │                                               └──────────── repair (≤ 2 replans) <──────────┘
             ├─ tools (options, compare, nearby, book+approve, favorites, feedback, memory, events,
             │         weather, share, budget split, reallocation, transport, vote) ─> END
             ├─ question ─> qa (RAG) ─> END
             └─ chat (hello, thanks, help, off-topic, outside Arizona) ─> END

State (request, stops, budget, history) is checkpointed per conversation thread, so
follow-ups like "swap dinner for something cheaper" edit the existing plan.
"""
from __future__ import annotations

import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents.concierge import booking_node, concierge_node, qa_node
from .agents.critic import critic_node, repair_node, route_after_critic
from .agents.live_agent import live_node
from .agents.logistics import budget_node, route_node
from .agents.planner import planner_node
from .agents.specialists import attraction_node, dining_node, lodging_node
from .agents.tools import tools_node
from .agents.weather_agent import weather_node
import contextvars

from . import llm, shared
from .profiles import load_profile, load_trip_plan, load_trips, save_profile, save_trips, update_from_turn
from .security import clean_name, mask_key, redact, sanitize_markdown
from .state import PlannerState


def build_graph(checkpointer=None):
    g = StateGraph(PlannerState)
    g.add_node("planner", planner_node)
    g.add_node("weather_agent", weather_node)
    g.add_node("tools", tools_node)
    g.add_node("attraction_agent", attraction_node)
    g.add_node("dining_agent", dining_node)
    g.add_node("lodging_agent", lodging_node)
    g.add_node("live_agent", live_node)
    g.add_node("route_agent", route_node)
    g.add_node("budget_agent", budget_node)
    g.add_node("critic", critic_node)
    g.add_node("repair", repair_node)
    g.add_node("booking_agent", booking_node)
    g.add_node("concierge", concierge_node)
    g.add_node("qa", qa_node)

    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", lambda s: {"question": "qa", "chat": "end", "tools": "tools"}.get(s["intent"], "plan"),
                            {"qa": "qa", "plan": "weather_agent", "end": END, "tools": "tools"})
    g.add_edge("weather_agent", "attraction_agent")
    g.add_edge("tools", END)
    g.add_edge("attraction_agent", "dining_agent")
    g.add_edge("dining_agent", "lodging_agent")
    g.add_edge("lodging_agent", "live_agent")
    g.add_edge("live_agent", "route_agent")
    g.add_edge("route_agent", "budget_agent")
    g.add_edge("budget_agent", "critic")
    g.add_conditional_edges("critic", route_after_critic, {"repair": "repair", "booking": "booking_agent"})
    g.add_edge("repair", "attraction_agent")
    g.add_edge("booking_agent", "concierge")
    g.add_edge("concierge", END)
    g.add_edge("qa", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


class Planner:
    """Convenience wrapper: one object per app session, one thread per conversation."""

    def __init__(self, account: dict | None = None):
        self.graph = build_graph()
        self.thread_id = str(uuid.uuid4())
        self.account: dict | None = account  # {"id", "username"} when signed in (see accounts.py); None = guest
        self.profile = load_profile(self._aid)
        self.trips: list[dict] = load_trips(self._aid)
        self.trip_id: str | None = None  # which saved trip the current plan belongs to

    @property
    def _aid(self) -> str | None:
        return (self.account or {}).get("id")

    @property
    def profile_name(self) -> str | None:
        return (self.account or {}).get("username")

    @property
    def cfg(self) -> dict:
        return {"configurable": {"thread_id": self.thread_id}, "recursion_limit": 60}

    # ---------------- bring-your-own OpenAI key (memory only, this object only)
    _api_key: str | None = None

    def set_api_key(self, key: str | None) -> None:
        self._api_key = (key or "").strip() or None

    def clear_api_key(self) -> None:
        self._api_key = None

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    @property
    def api_key_hint(self) -> str:
        return mask_key(self._api_key)

    def __repr__(self) -> str:  # never print the key
        return f"<Planner thread={self.thread_id[:8]} key={'set' if self._api_key else 'none'}>"

    def __getstate__(self):  # never pickle the key (e.g. if a host serializes session state)
        d = dict(self.__dict__)
        d.pop("_api_key", None)
        return d

    def ask(self, message: str) -> dict:
        message = (message or "").strip()[:2000]
        if redact(message) != message:
            # Someone pasted a key/token into the chat: don't process, store, share or send it anywhere.
            st = self.state()
            return {**st, "intent": "chat", "llm_errors": [], "trace": [{"agent": "Planner", "via": "rules", "did": "blocked a pasted secret"}],
                    "answer": "That looks like an API key or token, so I ignored that message - it wasn't saved, shared or sent anywhere. "
                              "To use your own OpenAI key, click **OpenAI key** at the top right."}
        before = dict(self.graph.get_state(self.cfg).values or {})
        before_city = (before.get("request") or {}).get("city") if any(s.get("place") for s in before.get("slots") or []) else None
        key_token = llm.set_session_key(self._api_key)
        err_token = llm.start_error_capture()
        try:
            # copy_context() so LangGraph's worker threads see this session's key (and only this one's)
            ctx = contextvars.copy_context()
            out = ctx.run(self.graph.invoke, {"user_message": message, "trace": [], "profile": self.profile}, self.cfg)
            errors = ctx.run(llm.captured_errors)
        except BaseException:
            # A run that is interrupted part-way (e.g. the web app re-runs on a double submit) must not
            # leave a half-updated plan behind: roll the conversation back to the last good state.
            if before:
                self.graph.update_state(self.cfg, before, as_node="concierge")
            raise
        finally:
            llm.reset_session_key(key_token)
            llm._errors.reset(err_token)
        out["llm_errors"] = errors
        if out.get("answer"):
            out["answer"] = sanitize_markdown(out["answer"])
        self.profile = update_from_turn(out.get("profile") or self.profile, out)
        if self.profile.get("_forget"):  # "forget me": saved trips go too
            self.trips, self.trip_id = [], None
            save_trips(self._aid, [])
        save_profile(self._aid, self.profile)
        new_city = (out.get("request") or {}).get("city")
        if before_city and out.get("intent") == "new_plan" and new_city and new_city != before_city:
            if not self.trip_id:  # (a plan that is already in My trips was saved after its last change)
                self.save_trip(before)  # a brand-new trip somewhere else: keep the old one in My trips
            self.trip_id = None
        if any(s.get("place") for s in out.get("slots") or []):
            self.save_trip()  # keep My trips up to date with the current plan
        if self.shared_code and out.get("slots"):  # collaborative editing: publish the new version
            rec = shared.save(self.shared_code, out, editor=self.display_name)
            self.shared_version = rec["version"]
        return out

    # ---------------- shared itineraries / collaborative editing
    shared_code: str | None = None
    shared_version: int = 0
    display_name: str = "someone"

    def share(self, name: str | None = None) -> str:
        """Publish the current plan under a new join code."""
        if name:
            self.display_name = clean_name(name)
        self.shared_code = self.shared_code or shared.new_code()
        rec = shared.save(self.shared_code, self.state(), editor=self.display_name, votes={})
        self.shared_version = rec["version"]
        return self.shared_code

    def join(self, code: str, name: str | None = None) -> bool:
        rec = shared.load((code or "").strip().upper())
        if not rec:
            return False
        if name:
            self.display_name = clean_name(name)
        self.shared_code = rec["code"]
        self._load_shared(rec)
        return True

    def sync(self) -> bool:
        """Pull a newer version someone else saved. Returns True if the plan changed."""
        rec = shared.load(self.shared_code) if self.shared_code else None
        if not rec or rec["version"] <= self.shared_version:
            return False
        self._load_shared(rec)
        return True

    def leave(self) -> None:
        self.shared_code, self.shared_version = None, 0

    def _load_shared(self, rec: dict) -> None:
        self.graph.update_state(self.cfg, rec["state"], as_node="concierge")
        self.shared_version = rec["version"]

    def state(self) -> dict:
        return dict(self.graph.get_state(self.cfg).values)

    def apply_votes(self, choices: dict[str, str]) -> dict:
        """Group voting from the app: swap in the winners and re-plan logistics."""
        from .agents.tools import apply_votes
        new = apply_votes(self.state(), choices)
        keep = {k: new[k] for k in ("slots", "budget", "quality", "route", "issues", "booking", "answer", "trace")}
        self.graph.update_state(self.cfg, keep, as_node="concierge")
        if self.shared_code:  # publish the voted plan and clear the ballot
            self.shared_version = shared.save(self.shared_code, self.state(), editor=self.display_name, votes={})["version"]
        return self.state()

    def feedback(self, place: dict, sign: int) -> None:
        """Thumbs up/down from the app: learn the place's categories."""
        key = "liked_categories" if sign > 0 else "disliked_categories"
        bucket = self.profile.setdefault(key, {})
        for c in (place.get("categories") or "").split(", ")[:2]:
            bucket[c] = bucket.get(c, 0) + 1
        save_profile(self._aid, self.profile)

    # ---------------- accounts (accounts.py checks the password; this just switches whose data is used)
    def sign_in(self, account: dict) -> None:
        """Use this account's saved preferences and trips from now on. Trips planned before signing in, in
        this visit, are added to the account; preferences learned this visit are merged in."""
        self.account = {"id": account["id"], "username": account["username"]}
        saved = load_profile(self._aid)
        for k in ("cuisines", "interests", "liked_categories", "disliked_categories"):
            for item, n in (self.profile.get(k) or {}).items():
                saved.setdefault(k, {})[item] = saved.get(k, {}).get(item, 0) + n
        if self.profile.get("dietary"):
            saved["dietary"] = sorted(set(saved.get("dietary", [])) | set(self.profile["dietary"]))
        if self.profile.get("wheelchair"):
            saved["wheelchair"] = True
        favs = {f["id"]: f for f in saved.get("favorites", []) + self.profile.get("favorites", [])}
        if favs:
            saved["favorites"] = list(favs.values())
        saved["history"] = list(dict.fromkeys(saved.get("history", []) + self.profile.get("history", [])))[-300:]
        saved.setdefault("memory_on", True)
        self.profile = saved
        save_profile(self._aid, self.profile)
        known = {t["id"] for t in self.trips}
        self.trips = (self.trips + [t for t in load_trips(self._aid) if t["id"] not in known])[:self.MAX_TRIPS]
        save_trips(self._aid, self.trips)

    def sign_out(self) -> None:
        """Back to guest: nothing of the account stays in this session."""
        self.account = None
        self.profile = {"memory_on": False}
        self.trips, self.trip_id = [], None
        self.thread_id = str(uuid.uuid4())
        self.shared_code, self.shared_version = None, 0

    def forget(self) -> None:
        """Delete what was learned about the traveler, and their saved trips (a signed-in account stays)."""
        self.profile = {"memory_on": bool(self.account), "_forget": True}
        self.trips, self.trip_id = [], None
        save_trips(self._aid, [])
        save_profile(self._aid, self.profile)
        self.profile = {"memory_on": bool(self.account)}

    # ---------------- My trips: every plan is kept, "New trip" never throws one away
    TRIP_KEYS = ("request", "slots", "conditions", "budget", "quality", "route", "issues", "booking", "answer",
                 "events", "reservations", "notes", "options")
    MAX_TRIPS = 20
    KEEP_ON_SAVE = ("confirmed_at", "confirmed_sig", "checklist_done")

    def save_trip(self, state: dict | None = None) -> str | None:
        """Save (or update) a plan in My trips. Returns its trip id."""
        from datetime import datetime as _dt, timedelta as _td
        st = state if state is not None else self.state()
        if not any(s.get("place") for s in st.get("slots") or []):
            return None
        req, cond = st.get("request") or {}, st.get("conditions") or {}
        first = cond.get("date") or req.get("trip_date")
        days = req.get("days", 1)
        if first:
            d0 = _dt.fromisoformat(first)
            d1 = d0 + _td(days=days - 1)
            when = f"{d0:%a, %b} {d0.day}" + (f" - {d1:%a, %b} {d1.day}" if days > 1 else "")
        else:
            when = ""
        stops = [s for s in st["slots"] if s.get("place")]
        tid = self.trip_id if state is None and self.trip_id else uuid.uuid4().hex[:10]
        rec = {"id": tid, "city": req.get("city"), "when": when, "start": first, "days": days,
               "party": req.get("party_size", 2), "stops": len(stops),
               "cost_pp": (st.get("budget") or {}).get("total_pp"),
               "highlights": [s["place"]["name"] for s in stops[:3]],
               "saved_at": _dt.now().isoformat(timespec="minutes"),
               "state": {k: st.get(k) for k in self.TRIP_KEYS if st.get(k) is not None}}
        from . import checklist
        rec["checklist"] = checklist.build(st)  # what to book / check before the trip (rebuilt as the plan changes)
        old = next((t for t in self.trips if t["id"] == tid), None)
        if old:  # keep the confirmation mark, checklist ticks and reminder setting
            rec.update({k: old[k] for k in self.KEEP_ON_SAVE if k in old})
        self.trips = [t for t in self.trips if t["id"] != tid]
        self.trips.insert(0, rec)
        self.trips = self.trips[:self.MAX_TRIPS]
        if state is None:
            self.trip_id = tid
        save_trips(self._aid, self.trips)
        return tid

    @staticmethod
    def _plan_sig(st: dict) -> str:
        import hashlib
        stops = [(s.get("day"), s.get("time"), (s.get("place") or {}).get("id")) for s in st.get("slots") or [] if s.get("place")]
        return hashlib.sha256(repr((stops, (st.get("request") or {}).get("party_size"))).encode()).hexdigest()[:16]

    def trip_confirmation(self) -> dict | None:
        """For the current trip: {"at": when it was confirmed, "changed": whether the plan changed since}, or None."""
        rec = next((t for t in self.trips if t["id"] == self.trip_id), None) if self.trip_id else None
        if not rec or not rec.get("confirmed_at"):
            return None
        return {"at": rec["confirmed_at"], "changed": rec.get("confirmed_sig") != self._plan_sig(self.state())}

    # ---------------- pre-trip checklist (checklist.py) and trip confirmation
    def _current_rec(self) -> dict | None:
        if not self.trip_id:
            self.save_trip()
        return next((t for t in self.trips if t["id"] == self.trip_id), None)

    def checklist(self) -> list[dict]:
        """The current trip's checklist: items with "done" (ticked off, or booked in the app)."""
        from . import checklist
        rec = self._current_rec()
        return checklist.with_status(rec.get("checklist") or [], rec.get("checklist_done")) if rec else []

    def set_check(self, item_id: str, done: bool) -> None:
        rec = self._current_rec()
        if rec:
            ticks = set(rec.get("checklist_done") or [])
            ticks = ticks | {item_id} if done else ticks - {item_id}
            rec["checklist_done"] = sorted(ticks)
            save_trips(self._aid, self.trips)

    def confirm_trip(self) -> tuple[bool, str]:
        """Mark the current trip confirmed (saved to the account). The app then offers the itinerary page and the
        calendar file to download (confirm.py)."""
        from datetime import datetime as _dt
        st = self.state()
        if not any(s.get("place") for s in st.get("slots") or []):
            return False, "There's no trip to confirm yet."
        tid = self.save_trip()
        rec = next(t for t in self.trips if t["id"] == tid)
        rec["confirmed_at"] = _dt.now().isoformat(timespec="minutes")
        rec["confirmed_sig"] = self._plan_sig(st)
        save_trips(self._aid, self.trips)
        return True, "Trip confirmed."

    def open_trip(self, trip_id: str) -> bool:
        """Make a saved trip the current plan again (the current one stays saved)."""
        rec = next((t for t in self.trips if t["id"] == trip_id), None)
        if not rec:
            return False
        if rec.get("state") is None:  # a trip saved to the account: its full plan loads now
            rec["state"] = load_trip_plan(self._aid, trip_id)
            if not rec["state"]:
                return False
        self.save_trip()
        self.thread_id = str(uuid.uuid4())
        self.graph.update_state(self.cfg, dict(rec["state"]), as_node="concierge")
        self.trip_id = trip_id
        self.shared_code, self.shared_version = None, 0
        return True

    def delete_trip(self, trip_id: str) -> None:
        self.trips = [t for t in self.trips if t["id"] != trip_id]
        if self.trip_id == trip_id:
            self.trip_id = None
        save_trips(self._aid, self.trips)

    def reset(self):
        """New trip: the current plan is kept in My trips, then a fresh conversation starts."""
        self.save_trip()
        self.thread_id = str(uuid.uuid4())
        self.trip_id = None
        self.shared_code, self.shared_version = None, 0


def main():  # python -m azmulti.graph  -> terminal chat
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = Planner()
    print("Saguaro - Arizona multi-agent trip planner. Type 'quit' to exit.\n")
    while True:
        try:
            msg = input("you> ").strip()
        except EOFError:
            break
        if msg.lower() in ("quit", "exit"):
            break
        out = p.ask(msg)
        for t in out["trace"]:
            print(f"  [{t['agent']:<18} {t['via']:>5}] {t['did']}")
        print("\n" + out["answer"] + "\n")


if __name__ == "__main__":
    main()
