"""Shared state passed between agents (Method 4: state orchestration) and the structured
schemas the LLM must fill (Method 2: structured prompts)."""
from __future__ import annotations

import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

THEME_NAMES = ["outdoors", "gardens", "history_culture", "arts", "science_sky", "animals", "family_fun",
               "tours_adventure", "food_drink", "sports_nightlife", "shopping"]


class TripRequest(BaseModel):
    """Everything the Planner extracts from the traveler's message."""
    city: str = Field("Phoenix", description="Arizona city to plan around")
    weekday: Optional[int] = Field(None, description="0=Monday..6=Sunday of the (first) trip day; null if not said")
    month: Optional[int] = Field(None, description="1-12 if a month/season/date is mentioned")
    trip_date: Optional[str] = Field(None, description="exact first day of the trip as YYYY-MM-DD, if a specific date is given")
    days: int = Field(1, ge=1, le=14, description="number of days (weekend = 2; a date range = its length, up to 14)")
    legs: list[dict] = Field(default_factory=list,
                             description="multi-city trips: [{city, days}] in visiting order, e.g. Phoenix 3 days then Sedona 2; "
                                         "empty for a one-city trip")
    end_date: Optional[str] = Field(None, description="last day of the trip as YYYY-MM-DD when a date range is given")
    start_time: int = Field(540, description="minutes after midnight the day starts (9AM = 540)")
    end_time: int = Field(1320, description="minutes after midnight the day ends (10PM = 1320)")
    party_size: int = Field(2, ge=1, le=30)
    min_age: Optional[int] = Field(None, description="age of the youngest person if known")
    has_kids: bool = False
    budget_per_person: Optional[float] = Field(None, description="USD per person for the whole plan")
    tier: Literal["budget", "balanced", "premium"] = "balanced"
    interests: list[str] = Field(default_factory=list, description=f"subset of {THEME_NAMES}")
    activity_text: str = Field("", description="free-text description of activities wanted, for semantic search")
    cuisines: list[str] = Field(default_factory=list, description="e.g. mexican, italian, sushi -> japanese")
    meals: list[Literal["breakfast", "lunch", "dinner"]] = Field(default_factory=list)
    nightlife: bool = False
    dietary: list[str] = Field(default_factory=list, description="vegetarian, vegan, ...")
    wheelchair: bool = False
    occasion: Optional[str] = Field(None, description="date night, birthday, family outing, ...")
    hidden_gems: bool = False
    avoid_crowds: bool = False
    radius_km: float = 30.0
    zip_code: Optional[str] = Field(None, description="5-digit Arizona ZIP code to plan around, if given")
    transport: Literal["car", "rideshare", "walking", "transit"] = "car"
    rental_car: bool = Field(False, description="traveler needs to rent a car for the trip (transport is then 'car')")
    free_only: bool = Field(False, description="traveler wants free activities")
    min_rating: Optional[float] = Field(None, description="e.g. 4.5 for 'highly rated' / '4.5 stars or better'")
    atmosphere: Optional[str] = Field(None, description="one of Romantic, Upscale, Cozy, Casual, Historic, Trendy")
    indoor_only: bool = Field(False, description="traveler wants indoor activities")
    outdoor_pref: bool = Field(False, description="traveler explicitly wants outdoor activities")
    discounts: list[str] = Field(default_factory=list, description="student, military, senior")
    want_options: bool = Field(False, description="traveler asked for several plan options (budget/balanced/premium)")
    halal: bool = False
    need_hotel: bool = Field(False, description="traveler wants a hotel / place to stay overnight")
    needs_specified: bool = Field(False, description="traveler listed exactly what to plan (only those meals/stops)")
    want_activities: bool = True
    want_dessert: bool = False


class Revision(BaseModel):
    """What to change in an existing itinerary."""
    targets: list[str] = Field(description="stop labels to change, e.g. ['dinner'] or ['stop 2'] or ['all']")
    cheaper: bool = False
    new_query: Optional[str] = Field(None, description="what the replacement should be like")
    cuisine: Optional[str] = None
    make_kid_friendly: bool = False


class Intake(BaseModel):
    intent: Literal["new_plan", "revise", "question", "chat"]
    reply: Optional[str] = Field(None, description="for chat: a short friendly reply that steers toward an Arizona trip")
    request: Optional[TripRequest] = None
    revision: Optional[Revision] = None
    question: Optional[str] = None
    changed: Optional[list[str]] = Field(None, description="for a follow-up that edits the trip (day, party, budget...): the fields changed")


class Choice(BaseModel):
    """A specialist agent's pick among retrieved candidates."""
    choice_id: str
    reason: str = Field(description="one sentence, grounded in the candidate's data/evidence")


class PlannerState(TypedDict, total=False):
    user_message: str
    intent: str
    request: dict
    revision: dict
    question: str
    slots: list[dict]          # the itinerary: ordered stops, each with its chosen place
    budget: dict
    quality: dict
    issues: list[dict]
    notes: list[str]
    route: dict
    booking: dict
    replans: int
    needs_repair: bool
    repaired: list[dict]       # stops the critic already sent back this turn (so it tries a different one next)
    needs_skeleton: bool
    conditions: dict           # weather / sunrise / sunset from the Weather agent
    events: list[dict]
    rental: Optional[dict]     # the Car Rental agent's decision (car size, pickup, days, cost, link), if a car is rented
    options: Optional[dict]    # budget / balanced / premium variants
    profile: dict              # AI memory (only persisted when the traveler opts in)
    pending: Optional[dict]    # a booking waiting for the traveler's approval
    reservations: list[dict]   # approved booking requests (reservation management)
    interview: Optional[dict]  # guided questions in progress (see interview.py)
    command: Optional[dict]    # a Concierge-tools command detected this turn
    answer: str
    history: list[dict]        # chat transcript (role, content)
    trace: list[dict]          # what each agent did this turn (reset by the Planner each turn)
