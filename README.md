# Saguaro: a multi-agent Arizona trip planner (MIS 556, Group 14)

[![tests](https://github.com/AnshulShahMIS/MIS556Group14FinalProject/actions/workflows/tests.yml/badge.svg)](https://github.com/AnshulShahMIS/MIS556Group14FinalProject/actions/workflows/tests.yml)

Saguaro turns a sentence like *"Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125
budget per person, want a fun outdoor activity, Mexican food for dinner, and somewhere to go out
afterward"* into a timed itinerary. The plan respects the budget, checks opening hours and keeps driving
short. Every pick comes with a reason taken from the data. You can then keep talking to it: *"swap dinner
for something cheaper"*, *"make it kid friendly"*, *"is the first stop good for beginners?"*

It is built on the team workbook `Arizona_Places_TripAdvisor_Hiking.xlsx`, which has 108,579 Google Maps
places, 636 TripAdvisor matches, and the TripAdvisor review and hiking summaries. It uses the four GenAI
methods from our midterm milestone.

**Live app: https://saguaro.streamlit.app/?embed=true** (free hosting: if it has been asleep, click "Yes, get this app back up" and give it about 2 minutes)  
**Code: https://github.com/AnshulShahMIS/MIS556Group14FinalProject**

## Where to find things

| What | Where |
|---|---|
| The web app (all pages) | [`app.py`](app.py) |
| The 13 agents and the LangGraph workflow | [`src/azmulti/graph.py`](src/azmulti/graph.py), [`src/azmulti/agents/`](src/azmulti/agents) |
| Method 1 - MiniLM embeddings | [`src/azmulti/embeddings.py`](src/azmulti/embeddings.py), [`src/azmulti/catalog.py`](src/azmulti/catalog.py) |
| Method 2 - GPT-4o prompts and personas | [`src/azmulti/prompts.py`](src/azmulti/prompts.py), [`src/azmulti/state.py`](src/azmulti/state.py) |
| Method 3 - RAG over real reviews | [`src/azmulti/reviews_index.py`](src/azmulti/reviews_index.py), [`src/azmulti/agents/concierge.py`](src/azmulti/agents/concierge.py) |
| Data pipeline (workbook → catalog) | [`src/azmulti/pipeline.py`](src/azmulti/pipeline.py), [`src/azmulti/build.py`](src/azmulti/build.py) |
| Team workbook and processed data | [`data/raw/`](data/raw), [`data/processed/`](data/processed) |
| Evaluation (Precision@5, Recall@5, personas, itinerary quality) | [`eval/evaluate.py`](eval/evaluate.py) → [`eval/results.md`](eval/results.md) |
| Every feature from the project ideas document | [`FEATURES.md`](FEATURES.md) |
| Security review | [`SECURITY.md`](SECURITY.md) |
| Tests | [`tests/`](tests) |
| How to deploy | [`DEPLOY.md`](DEPLOY.md) |

## The agents

```
 user ──> PLANNER ──┬─ new plan / edit / revise ──> WEATHER & EVENTS ──> ATTRACTION ──> DINING & NIGHTLIFE ──> LIVE INFO ──> ROUTE ──> CAR RENTAL ──> BUDGET ──> CRITIC ─┬─> BOOKING ──> CONCIERGE ──> answer
 (GPT-4o            │                                                       ^                                                              │
  structured        │                                                       └──────────────── repair (≤ 2 replans) <──────────────────────┘
  output +          ├─ tools ──> CONCIERGE TOOLS (options, compare, nearby, add/remove, book+approve, favorites, memory, vote, ...) ──> answer
  rule fallback)    ├─ question ──> Q&A (RAG) ──> answer
                    └─ chat (hello / thanks / help / outside Arizona / no plan yet) ──> answer
```

| Agent | Job | GenAI method |
|---|---|---|
| **Car Rental** (`agents/rental_agent.py`) | When the traveler needs a rental car ("we need a rental car", or *Rental car* under Getting around): picks the pickup (nearest airport, e.g. PHX, TUS, FLG, or a rental office from the place data), how many cars and what size (economy / midsize / SUV / minivan, from the group, kids, trailheads and distance), the days and an estimated cost (labelled as an estimate). The Budget agent adds it per person; the Checklist gets "Book a rental car" two weeks ahead with a Kayak search pre-filled with the airport and dates. Nothing is booked. | 4 |
| **Weather & Events** (`agents/weather_agent.py`) | Live Open-Meteo forecast (≤ 14 days) or climate normals, heat/rain/cold alerts, sunrise/sunset (NOAA equations), recurring local events; builds the day skeleton around them. | 4 (tool) |
| **Live Info** (`agents/live_agent.py`) | Checks every chosen stop with Google Places: current hours, closures (replaced automatically), price, website, parking. Live events (Ticketmaster), traffic (TomTom) and web lookups (Tavily) plug into the other agents. | 3 (live retrieval), 4 |
| **Concierge tools** (`agents/tools.py`) | Everything conversational after a plan: budget/balanced/premium options, compare, alternatives, nearby, add/remove stops, booking with approval + reservation list, favorites, feedback learning, memory, events, weather, share, cost split, budget reallocation, transport mode, group vote. | 2, 3, 4 |
| **Planner** (`agents/planner.py`) | Classifies the message (new plan, revision or question) and extracts a `TripRequest`: city, day, party, ages, budget, interests, cuisine, nightlife, accessibility. Lays out the day's stops and times. Heat rule: from May to September, outdoor stops move to 7 AM and afternoons stay indoors. | 2 (structured prompt + persona), 4 |
| **Attraction** (`agents/specialists.py`) | Fills the activity stops with filtered semantic search, relaxing constraints step by step if nothing matches. GPT-4o picks one of the top candidates and gives a one-sentence reason based on the data. | 1, 2, 3 |
| **Dining & Nightlife** | Same for meals and bars: cuisine, kids, vegetarian, group size, the meal tag, a cost cap from the budget, and open at the planned time. Bars are skipped for anyone under 21 or groups with kids. | 1, 2, 3 |
| **Route** (`agents/logistics.py`) | Orders the stops, estimates drive times and swaps activities when that saves driving. Checks the 2021 opening hours at the actual arrival time. | 4 (tool) |
| **Budget** | Cost per person: restaurant price level, or a category estimate when the price is missing (82% of places). Adds the per-person driving cost and flags overspend. | 4 (tool) |
| **Critic** (`agents/critic.py`) | Scores the itinerary out of 100 (budget fit, preference fit, efficiency, feasibility). Sends closed or over-budget stops back to the specialists, at most twice. | 4 (agentic loop) |
| **Booking** (`agents/concierge.py`) | Prepares Google Maps, directions and TripAdvisor links, plus reservation pages pre-filled with your date, time and party (OpenTable, Resy for restaurants; Booking.com, Expedia, Google Hotels for hotels) and flags where a reservation or ticket is advisable. Builds an `.ics` calendar file. It **never books**; that needs the traveler's approval. | 4 |
| **Concierge** | Writes the final answer, grounded in each place's description, amenities and TripAdvisor review summaries ("travelers love / complaints / tip"). | 2, 3 (RAG) |
| **Q&A** | Answers follow-up questions by retrieving the plan's places and similar places, then answering only from those records. | 3 (RAG) |

The full list of features from the project-ideas PDF, with status and example phrases, is in **[FEATURES.md](FEATURES.md)**.
Small talk ("hello", "thanks"), questions, places outside Arizona and follow-ups with no plan yet are handled without inventing a plan.

Agents share one LangGraph state (request, stops, budget, issues, history). It is checkpointed per
conversation, so a revision changes only the stops you asked about and keeps the rest.

### How the four milestone methods map to the code
| # | Method (milestone slide) | Where | What it does in the agent |
|---|---|---|---|
| 1 | **Dense vector embeddings** (Sentence-Transformers `all-MiniLM-L6-v2`, CPU) | `embeddings.py`, `catalog.py` | Turns all 21,762 places (name, categories, description, review summary, amenities) **and** 57,424 real review quotes into 384-d vectors; every traveler request is embedded the same way and compared by cosine similarity. TF-IDF is kept as a baseline. |
| 2 | **Prompt engineering & persona design** (GPT-4o, structured prompts) | `prompts.py`, `state.py`, `llm.py` | One persona per agent (Planner, specialists, Concierge, Q&A) with strict rules; GPT-4o fills Pydantic schemas (`Intake`, `TripRequest`, `Revision`, `Choice`). Each pick's reason must cite the retrieved data, including the retrieved review. Needs an OpenAI key; otherwise rule-based fallbacks run. |
| 3 | **Retrieval-augmented generation** (top-k similarity retrieval) | `reviews_index.py`, `catalog.py`, `agents/concierge.py` | Top-k search over places **and over real review text**: the best-matching review re-ranks candidates and is attached to every pick ("A reviewer said ..."). The Concierge and Q&A agents write only from retrieved records plus the top-k retrieved reviews, and are told not to invent prices, hours or menus. |
| 4 | **Agentic workflows & state orchestration** (LangGraph · tools and state) | `graph.py`, `agents/*`, `agents/tools.py` | LangGraph state machine: Planner → Weather & Events → Attraction → Dining → Lodging → Live Info → Route → Budget → Critic (repair loop) → Booking → Concierge, plus 25+ concierge tools. Agents choose filters (area, kids, access, price, hours) and re-rank across several steps; state and memory persist per thread. |

Ranking: `score = cosine + 0.5·best_review_match + 0.15·trust + 0.05·popularity − 0.10·distance/radius + 0.08·on_TripAdvisor`.
`trust` is the Bayesian-average rating with a prior of 38 reviews at 4.28★, both taken from our midterm EDA.

## Run it (Windows PowerShell)

```powershell
py -m pip install torch --index-url https://download.pytorch.org/whl/cpu
py -m pip install -r requirements.txt
copy .env.example .env      # optional: paste OPENAI_API_KEY to use GPT-4o
$env:PYTHONPATH="src"; py -m azmulti.build        # once: workbook -> catalog + place and review embeddings (~8 min)
py -m streamlit run app.py                         # web app
py -m azmulti.graph                                # or chat in the terminal
py -m pytest -q tests                              # 114 offline tests (live APIs mocked, incl. security)
py eval/evaluate.py                                # writes eval/results.md
```

The workbook is expected at `data/raw/Arizona_Places_TripAdvisor_Hiking.xlsx`. Set `AZ_XLSX` to point
somewhere else.

**Without an API key** every agent falls back to deterministic rules (regex intake, top-ranked pick,
template answer), so the app, tests and evaluation all run offline. With `OPENAI_API_KEY` set, the
Planner, the specialists' choices, the Concierge and Q&A use GPT-4o. The agent-trace tab shows `llm` or
`rules` for every step.

## Using the app

Four pages along the top, laid out like a trip planner (warm light theme by default, with a **Dark mode** switch):

| Page | What it's for |
|---|---|
| **Plan** (home) | Before a trip exists: a *Where in Arizona?* start screen with a quick form (one or more cities, dates on a calendar, adults, kids, budget, and *More options* for interests, dinner, getting around and extras), idea buttons (**Friends' day out, Family day, Date night, Game day**) that start a short tap-to-answer interview, *Popular places to start*, and a chat box for typing the trip or asking questions. Once there's a trip: **chat on the left** (changes like "make it cheaper", questions like "reviews of Phoenix Zoo", quick follow-up buttons) and **your trip on the right**: a trip card (cities, dates, travelers, budget bar, weather, hotels, plan score, Share), day tabs (with the city for multi-city trips), a map of that day, and a card per stop with **Reserve / Tickets** (the form opens right under the stop) and a **⋯** menu (details and reviews, swap, cheaper option, directions, remove, closed for good). Underneath: **Budget, Bookings, Events, How it was planned**, then calendar / save-as-page / Google Maps route. |
| **Map** | Every stop on a large map, numbered in visiting order and colored by type (activity, food, night out, stay), with the route between them and website, menu and directions buttons for each stop. |
| **My trips** | Every trip you've planned. **New trip** keeps the old one here; **Open** brings it back to edit, **Delete** removes it. Kept across visits and devices when you're signed in. |
| **Group** | Share the plan with a code, join a friend's plan, and vote on each stop together. |

The header shows which AI is planning (**AI Built In Planner**, or GPT-4o with a key) and the buttons **Dark mode /
Light mode**, **OpenAI key** (use your own key for this session), **Preferences** (what Saguaro remembers, favorites,
live-data status, about), **New trip** and the account menu.

### Accounts, trip confirmation and the checklist

Saguaro needs an account: signed out, every address shows a **sign-in page** (a short intro and the forms; the
Plan / Map / My trips / Group pages appear once you're signed in). **Create account** asks for a username and a password
(no email); **Keep me signed in** remembers the browser for 30 days. Once signed in, the name in the header opens
**Sign out**, **Change password** and **Delete my account**; preferences, favorites and trips belong to the account, on
any device. There's no password reset, so the sign-up form asks people to keep their password safe.

**Confirm trip** (on the trip card) marks the trip **Confirmed** in My trips and offers the itinerary page (day by day,
with times, places, links, costs, the budget and the Google Maps route) and a calendar file to download. If the trip
changes afterwards, the button becomes **Confirm changes**.

**Share** (on the trip card) creates a **view-only link**: anyone who opens it sees the trip - days, times, places
with links, costs, the map, a calendar file and the Google Maps route - without an account, and can't change anything.
The link always shows the latest version; **Stop sharing** (or deleting the trip or the account) turns it off. It
carries a long random code and shares only the plan and the owner's username - not the chat, preferences, checklist
ticks or reservation confirmation numbers (`src/azmulti/sharelink.py`, `tests/test_sharelink.py`). For friends who
should edit and vote, the same window links to **Group**.

**Checklist** (first tab under the trip) lists what to book or check before going, built from the plan and the place
data: hotels to book (3 weeks ahead), tables where reservations are recommended (a week ahead) or taken (2 days),
tickets for paid attractions (3 days), opening hours our data isn't sure about (the day before), the forecast, and
heat advice for summer hikes. Each item has a "do it by" date and a link to the booking site; ticks are saved with the
trip, and items tick themselves when a reservation is marked booked.

- Passwords are never stored: only a salted PBKDF2-SHA256 hash (200,000 rounds). Wrong passwords get the same message
  whether or not the username exists; 5 misses lock it for 5 minutes.
- Accounts, preferences and trips are JSON files in a **private GitHub repository** (secrets `AZ_STORE_REPO`,
  `AZ_STORE_TOKEN`, see DEPLOY.md), so they survive app updates; without them they're local files under
  `data/accounts/` (not committed).
- Code: `src/azmulti/accounts.py`, `confirm.py`, `checklist.py`, `store.py`, `profiles.py`; tests:
  `tests/test_accounts.py`, `test_checklist.py`, `test_store.py`.

The previous design is saved as the git tag `v1-classic-ui` (`git checkout v1-classic-ui -- app.py .streamlit/config.toml`
brings it back).

## Live data (optional keys)

Paste any of these into `.env` (copy `.env.example`). Each one switches on by itself; without it that
feature uses the offline workbook data. **Preferences → Live information** shows which are on.

| Key | What turns on | Where to get it | Cost |
|---|---|---|---|
| `GOOGLE_PLACES_API_KEY` | **Live Info agent**: current hours, open/closed (closed places are replaced automatically), price level, website, phone, parking, "takes reservations" for every chosen stop | console.cloud.google.com → enable **Places API (New)** → Credentials → API key (billing account required) | Monthly free usage, then pay per call; ~5 calls per plan, cached 24 h |
| `TICKETMASTER_API_KEY` | Real concerts, games and shows on your date; *add the Suns game to my plan* | developer.ticketmaster.com → My Apps → Consumer Key | Free (5,000 calls/day) |
| `TAVILY_API_KEY` | Web lookups with sources: menus, discounts, parking, admission, *summarize their website* | tavily.com → API key | Free tier (1,000 searches/month) |
| `TOMTOM_API_KEY` | Drive times with live/predicted traffic and delay per leg | developer.tomtom.com → My API keys | Free tier (2,500 calls/day) |

Only place names, addresses, coordinates, dates and search phrases are sent to these services, never anything
about the traveler. Answers are cached on disk (`data/cache/`) and say when the data was checked.

### Use your own OpenAI key
Click **OpenAI key** at the top right, paste a key from the OpenAI API dashboard and click
**Use key for this session**. The key is verified with OpenAI, kept only in that browser session's memory
(never saved, logged, shared or exported) and cleared on reload or with **Clear key**. See
**[SECURITY.md](SECURITY.md)** for the full security review.

### Plan together (shared plans)
On the **Group** tab, click **Create a share code** to get a code like `SAG-7K3QX9MP`. Others enter it under **Join a friend's plan**:
everyone sees the same plan, any chat edit is shared, the app syncs every 10 seconds, and the **Group vote**
tab pools one vote per person per stop. On your own computer this works across browser tabs; once the app
is deployed (e.g. Streamlit Community Cloud) it works across phones. Memory profiles are never shared.

## Expected results from the milestone, and where they are

| Expected result | How the agent delivers it | Evidence |
|---|---|---|
| **Personalized recommendations:** top 5 attractions from traveler preferences | Ask *"Top 5 attractions in Sedona for hiking and photography"* (or tap **Top 5 attractions for me**). Each pick shows its match to your interests, rating, cost, distance and a real review quote. | Part C below |
| **Preference matching** with AI embeddings | Your words and every place are embedded with MiniLM; similar places rank first, and the agent's filters handle kids, accessibility, price and area | Part A below |
| **Model evaluation:** Precision@5 and Recall@5 | `eval/evaluate.py` → `eval/results.md` | Part A below |
| **Different attractions for each traveler's interests** | Travelers in the same city get different top 5 lists | Part C: 0% overlap |
| **Business impact:** satisfaction, discovery, planning experience | Better-matched plans (quality 85 vs 54), stops open when you arrive (98% vs 69%), within budget (92% vs 50%), hidden gems mode, one-conversation planning with booking links | Part B below |

## Results (offline mode, `eval/results.md`)

**A. Retrieval** (33 intent-style queries such as *"somewhere to look at paintings and sculpture"*; relevant = the Google category matches the intent; Recall@5 pooled over all methods' top 20):

| Method | Precision@5 | Recall@5 | NDCG@5 |
|---|---|---|---|
| MiniLM embeddings + review RAG (ours) | **0.71** | **0.25** | 0.87 |
| MiniLM embeddings only | 0.68 | 0.25 | **0.90** |
| TF-IDF + ranking | 0.61 | 0.20 | 0.69 |
| Popularity baseline | 0.08 | 0.04 | 0.19 |

**C. Top 5 attractions for 8 traveler personas** (hiker, art lover, spiritual traveler, family with kids, history buff,
thrill seeker, stargazer, foodie): Precision@5 is 1.0 for six of them, 0.8 for the family and 0.2 for the stargazer
(Flagstaff has only one stargazing attraction with 20+ reviews in the data). Travelers in the same city share **0%** of their
top 5. Most picks are backed by a matching real review.

**B. Itineraries**

 (12 benchmark trips, including the project brief's example). The baseline fills the same stops with the most-reviewed nearby place:

| | Multi-agent (ours) | Popularity baseline |
|---|---|---|
| Quality score (critic, /100) | **84.8** | 54.4 |
| Stops open at the scheduled time | **98%** | 69% |
| Requested cuisine matched | **100%** | 33% |
| Plans within budget (±5%) | **92%** | 50% |
| Driving per plan (min) | **59** | 80 |
| Stops whose "why" cites a matching real review | **76%** | 0% |

## Limitations (worth saying in the presentation)
- **Closed places:** the data is a 2021 snapshot. The agent skips places closed then and places with no reviews since before 2020, but a place that closed after 2021 can only be caught live (Google Places key) or by a traveler tapping **Closed for good? Replace it**, which removes it for everyone using that copy of the app.
- Hours, prices and open/closed status come from a **2021 Google Maps snapshot**. Price is missing for 82% of places, so many costs are category estimates. The app labels them as estimates.
- Weather is live (Open-Meteo, free, no key). Hours, closures, events, traffic and web info are live **when their keys are set**; otherwise the 2021 snapshot is used and labelled. Booking means links plus your approval - OpenTable/Resy don't offer public booking APIs, so nothing is ever booked automatically.
- Memory is off by default and stored only on this computer (`data/profiles/`); "forget me" deletes it.
- RAG evidence is rich for the 31 places with TripAdvisor/hiking summaries. For everything else it is the Google description and amenity tags. Adding the separate 18M-review file (joined on `Google Map ID`) would make the grounding much stronger, and would allow the leave-one-out Recall@5 from the midterm.
- Evaluation relevance labels are Google categories, a proxy for real traveler satisfaction.
