# Feature checklist: *Ideas for Final Project: AI Agent Capabilities/Usage*

Status key:
- ✅ **Built**: works in the app and is covered by a test in `tests/`.
- 🟡 **Partial**: built as far as the data allows; the "How" column says what is approximate.
- 🔗 **Link-out**: the PDF wants a live outside integration (booking APIs, real-time availability, live traffic). We give the honest equivalent (official links plus estimates) and **never** pretend to book or quote live data.

- 🔑 **Live with a key**: switches on when its API key is in `.env` (see README → Live data); without the key it falls back to the offline behaviour.

Try the example phrases in the app's chat after making a plan.

## 1. Personalized AI Itinerary & Experience Planner
| Feature | Status | How / where | Try |
|---|---|---|---|
| Half-day, full-day, weekend, multi-day | ✅ | Planner skeleton (`agents/planner.py: build_slots`), 1-3 days | "half day morning hike in Flagstaff", "3 day trip to Tucson" |
| Budget-based planning (food, activities, transport, admission) | ✅ | Budget agent (`logistics.py`); per-stop caps + critic re-plan | "$60 per person" |
| Group size & attendees (ages, kids) | ✅ | Party size, kids, ages → no bars under 21, no adults-only with kids, kid-tagged restaurants | "with two kids", "we're all 24" |
| Preference-based (food, nightlife, outdoors, sports, shopping, entertainment) | ✅ | 11 interest themes + semantic search | "breweries and hiking" |
| Occasion-based (date night, birthday, family) | ✅ | Occasion changes vibe, timing (evening), tier, dessert stop | "anniversary dinner, fancy" |
| Time-based (hours, opening times, travel) | ✅ | Time windows ("free 2-8pm"), opening hours checked at real arrival time | "we're free from 2 to 8pm" |
| Itinerary optimization (minimize driving/waiting) | ✅ | Stops anchored to neighbours; Route agent swaps stops to cut driving | - |
| Dynamic adjustments through conversation | ✅ | Revise, swap, add, remove, edit day/party/budget | "swap the museum for a hike", "actually make it Sunday", "remove the dessert" |
| Multiple itinerary options (budget/balanced/premium) | ✅ | `tools.py: h_options` runs the pipeline 3×; pick one | "show budget, balanced and premium options" → "go with premium" |

## 2. Attraction Discovery & Filtering
| Feature | Status | How / where | Try |
|---|---|---|---|
| Type filters | ✅ | Themes from Google categories | "museums", "zoo" |
| Location: city, ZIP, radius | ✅ | 345 cities, ZIP centroids, "within N miles" | "near 85281 within 3 miles" |
| Price filters (free/budget/moderate/luxury) | ✅ | `free_only`, tiers, cost caps | "free things to do in Tucson" |
| Age restrictions (family / 21+) | ✅ | `adults_only`, `kids_only` flags | - |
| Accessibility | ✅ | Wheelchair entrance required (also parking/restroom tags shown) | "wheelchair accessible" |
| Indoor vs outdoor | ✅ | `indoor_only`, heat/rain → indoor | "indoor activities" |
| Popularity & reviews | ✅ | Bayesian trust score, min rating | "4.5 stars or better" |
| Hidden gems | ✅ | Ranking flips the popularity weight | "hidden gems in Tucson" |
| Seasonal activities (festivals, spring training, concerts) | ✅ 🔑 | **Live:** Ticketmaster events on your exact date(s) with venue, time, price and ticket link; *add the event to my plan* schedules it. Offline: recurring-events calendar | "what events are in March in Mesa?" |
| Operating hours | ✅ | Parsed from data; event venues exempt | - |

## 3. Restaurant Discovery & Reservation Integration
| Feature | Status | How / where | Try |
|---|---|---|---|
| OpenTable / Resy integration | 🔗 | OpenTable and Resy pages pre-filled with date, time and party size (Booking.com / Expedia / Google Hotels with check-in, check-out, guests and rooms for hotels) | "book dinner", "reserve the hotel" |
| Filters: cuisine, price, diet, rating, atmosphere, location | ✅ | 19 cuisines, vegetarian/vegan/halal, Atmosphere tags (Romantic/Upscale/Cozy…) | "romantic", "halal food" |
| Real-time availability | 🔗 | Needs OpenTable / Resy partner API access, which the project doesn't have; each pre-filled link opens the site's live availability for your exact date, time and party | - |
| AI recommendations matched to the itinerary | ✅ | Dining agent anchors meals near neighbouring stops | - |
| Menu integration | ✅ 🔑 | **Live:** web search (Tavily) finds the menu page and GPT-4o summarizes it with sources (*what's on the menu at dinner?*). Offline: Google menu-highlight tags | - |
| Direct booking | 🔗 | **Reserve** on each restaurant / hotel in the Itinerary tab (or chat) → editable time, party, rooms, nights → request goes on the reservations list → user finishes on the site → **Mark as booked** with a confirmation number | "book dinner" → "yes at 7:30pm for 3" → "I booked it, confirmation AB12" |
| External booking links | ✅ | Google Maps, OpenTable, Resy, TripAdvisor, official-ticket search | - |
| Group dining | ✅ | Party ≥ 6 requires "Groups" tag; ≥ 8 → call-ahead advice | "for 8 people" |
| Restaurant alternatives | ✅ | Every stop keeps 3 alternatives; cheapest search | "other options for dinner", "cheapest option for dinner" |

## 4. Attraction Booking & Experience Reservations
| Feature | Status | How / where | Try |
|---|---|---|---|
| Website / direct booking links | ✅ 🔑 | **Live:** official website + phone from Google Places on every stop; offline: Google Maps listing + official-ticket search | - |
| Experience booking (tickets, tours) | 🔗 | **Tickets** on every paid activity (Itinerary tab) or *get tickets for stop N* → approve → official-ticket search plus GetYourGuide and Viator (date pre-filled) → mark booked; free stops say there's nothing to book | "get tickets for stop 3" |
| Ticket price comparisons | 🟡 🔑 | Ticketmaster price ranges for events + web search for admission prices; no reseller comparison API | - |
| Real-time availability | 🔗 | Not available | - |
| Reservation management | ✅ | Bookings tab: each reservation with status (Requested / Booked), confirmation number, booking buttons, mark booked, remove; Booked badge on the stop | "show my reservations" |
| Group booking support | ✅ | Group-rate / capacity advice for parties ≥ 10 | - |
| Booking conflict detection | ✅ | Route agent flags reservations that no longer match the schedule | - |

## 5. RAG & Real-Time Information Retrieval
| Feature | Status | How / where | Try |
|---|---|---|---|
| RAG grounded answers | ✅ | Concierge and Q&A answer only from retrieved records + TripAdvisor review summaries | "is stop 1 good for beginners?" |
| Official website connections | ✅ 🔑 | Google Places website/phone per stop | - |
| Updated pricing & hours | ✅ 🔑 | **Live Info agent:** Google Places current hours, price level and open/closed for every chosen stop; closed places are replaced automatically; answers say when it was checked | - |
| Event discovery | ✅ 🔑 | Ticketmaster live events (*what events are on that day?*) | "what's happening in February?" |
| Menu & attraction Q&A | ✅ | Q&A agent (RAG) | "where should we eat tacos in Tucson?" |
| Source citations | ✅ | Sources line + per-stop Google Maps links + "(TripAdvisor summary)" labels | - |
| Information freshness | ✅ | "Google 2021 snapshot / TripAdvisor gathered Nov 2026 / forecast vs climate normal" | - |
| AI website summaries | ✅ 🔑 | Web search + GPT-4o summary with numbered sources (*summarize their website*) | - |

## 6. Maps, Transportation & Location Optimization
| Feature | Status | How / where | Try |
|---|---|---|---|
| Interactive map | ✅ | pydeck map with numbered stops and route line | Map tab |
| Route optimization | ✅ | Neighbour anchoring + swap optimizer | - |
| Travel times: driving, walking, transit | ✅ | Per-leg estimates; rush-hour factor for driving | "how far is lunch from the first stop?" |
| Parking information | ✅ 🔑 | Google Places parking options (free lot, paid lot, street, valet, garage) + web lookup (*is there parking at stop 2?*) | - |
| Transport preference (car/rideshare/walking/transit) | ✅ | Changes search radius, times and cost | "we'll take Uber", "walking only" |
| Distance-based recommendations | ✅ | Nearby search + add a stop | "what's near stop 2?", "add a coffee stop after stop 1" |
| Google Maps integration | ✅ | Per-stop directions + whole-route link | "share this plan" |
| Traffic-aware planning | ✅ 🔑 | TomTom live/predicted traffic per leg (delay shown); offline: rush-hour factor | - |

## 7. Weather & Seasonal Intelligence
| Feature | Status | How / where | Try |
|---|---|---|---|
| Weather-based recommendations | ✅ | Live Open-Meteo forecast ≤ 14 days, else climate normals; rain → indoor | "what's the weather?" |
| Arizona heat awareness | ✅ | ≥ 98°F → sunrise outdoor stop, indoor afternoon; ≥ 108°F extreme alert | "hiking in Phoenix in July" |
| Seasonal destination suggestions | ✅ | Cool-escape suggestions in heat; Flagstaff/North Rim season notes | - |
| Weather alerts | ✅ | Heat, cold/ice, rain/flash-flood alerts | - |
| Alternative indoor suggestions | ✅ | Indoor re-plan on heat/rain | "rainy day ideas in Phoenix" |
| Sunrise & sunset planning | ✅ | NOAA solar equations; sunset activity starts 60 min before sunset | "sunset hike" |

## 8. User Profiles & Personalization
| Feature | Status | How / where | Try |
|---|---|---|---|
| Preference profiles | ✅ | Opt-in local profile (`profiles.py`) | "remember I'm vegetarian" |
| Past experience tracking | ✅ | Previously shown places ranked slightly lower | - |
| Favorite places | ✅ | Save/list/remove | "save stop 2", "show my favorites" |
| Recommendation learning from feedback | ✅ | 👍/👎 and "we don't like X" adjust category weights and replace stops | "we don't like museums" |
| Group preference matching | ✅ | Multiple interests are spread across the day; group vote | - |
| Dietary & accessibility preferences | ✅ | Vegetarian/vegan/halal, wheelchair | - |
| Conversational assistant | ✅ | Everything above works in chat | - |

## 9. Group Planning & Collaboration
| Feature | Status | How / where | Try |
|---|---|---|---|
| Shared itineraries / shareable links | ✅ | Share code (e.g. SAG-7K3Q): others join the same plan; plus route link and downloadable page | "share this plan" |
| Group voting | ✅ | Group vote tab; with a share code, one vote per person per stop pooled across devices → apply winners | "let's vote" |
| Group budget splitting | ✅ | Per person, group total, even split | "how much will this cost? split the bill" |
| Collaborative editing | ✅ | Everyone who joined can edit by chat; changes sync to the group every 10 s, with an edit log. Multi-device across the internet once the app is deployed | - |
| Calendar integration | ✅ | .ics for Google/Outlook/Apple + per-stop Google Calendar links | Itinerary tab |
| Group scheduling around availability | ✅ | Time window parsing | "we're free 2-8pm" |

## 10. Budget Tracking & Cost Optimization
| Feature | Status | How / where | Try |
|---|---|---|---|
| Total cost, breakdown, per person + group | ✅ | Budget agent | Quality & budget tab |
| Budget warnings | ✅ | Over / slightly over budget + automatic repair | - |
| Cheaper alternatives | ✅ | "Cheaper option" column; "make it cheaper" | "make it cheaper" |
| Free activity discovery | ✅ | `free_only` | "free things to do" |
| Discount discovery | ✅ 🔑 | Web search for student/senior/military/group offers with sources (*does the zoo have student discounts?*); Google military-discount tag offline | "military discount" |
| Flexible budget reallocation | ✅ | Raise one stop's floor, lower another's cap | "spend more on dinner and less on activities" |

## 11. Advanced AI Features
| Feature | Status | How / where |
|---|---|---|
| AI trip concierge | ✅ | Concierge + Q&A + tools agents |
| Multi-agent architecture (restaurant, attraction, routing, budget, reservation) | ✅ | 12 agents in LangGraph (`graph.py`) |
| Automatic replanning (closures, unavailable bookings) | ✅ | Critic → repair loop; "dinner is fully booked" |
| Natural-language planning ("$150 Scottsdale date night") | ✅ | GPT-4o structured output, rule fallback |
| Explainable recommendations | ✅ | One-sentence, data-grounded "why" per stop + agent trace |
| AI comparison tool | ✅ | "compare stop 2 and stop 3", "compare Phoenix Zoo vs Desert Botanical Garden" |
| Local event intelligence | ✅ 🔑 | Ticketmaster live events + recurring calendar |
| Itinerary quality scoring | ✅ | Budget fit, preference fit, efficiency, feasibility (0-100) |
| Reservation confirmation (approval before booking) | ✅ | Pending request → explicit yes/no |
| AI memory with privacy controls | ✅ | Off by default, name + PIN protected, "forget me" deletes |
| Bring your own OpenAI key (added) | ✅ | 🔑 dialog: verified, session-memory only, redacted, never stored; see SECURITY.md |


## Added after the review
| Feature | Status | How / where |
|---|---|---|
| Guided interview from starter ideas | ✅ | `interview.py`: 7 short questions with tap-to-answer options; typed answers understood; "just plan it" / cancel |
| Hotel / place to stay | ✅ | 1,136 hotels, resorts, motels, B&Bs; Lodging agent picks one near the evening's stops, same hotel every night; per-person per-night cost; Google Hotels / Booking.com / Expedia links |
| Reviews for every place | ✅ | Real Google review quotes (2018-2021 dataset, 21k places), star breakdown, praised / complained-about aspects, TripAdvisor summaries; live latest Google reviews when a Places key is set; chat: *reviews for stop 2*. The dataset's AI-generated sample reviews are never shown. |
| Website and menu on the Map | ✅ 🔑 | Every stop on the Map tab has **Website** (the real official site from Google Places when that key is set, otherwise a search that finds it) and restaurants / bars have **Menu**; the same links are in each stop's details on the Itinerary tab | Map tab |
| Any trip dates (calendar) | ✅ | Pick a start and end date on a calendar in **Quick plan**, the Chat page's **Trip dates** button, or the guided questions' *When?* step - any date up to a year ahead, trips of 1-14 days; or type *March 3-6* / *2027-03-03 to 2027-03-06*. Weather is the live forecast within 14 days, typical weather for that month beyond | "move the trip to 2027-05-10 to 2027-05-12" |
| Never plan closed places | ✅ 🔑 | Excludes places Google listed as permanently or temporarily closed (2021) and places with no reviews since before 2020 (978 places); with a Google Places key every stop is re-checked live. **Closed for good? Replace it** (Map / Itinerary) or *stop 2 is permanently closed* replaces the stop and never recommends that place again; asking about a closed place shows a warning | "stop 2 is permanently closed" |
| My trips (trip history) | ✅ | **New trip** never deletes a plan: every trip is kept in the **My trips** tab with dates, stops, cost and highlights; **Open** makes an old trip current again (and editable), **Delete** removes it. Starting a plan for a different city also saves the old one. Kept for the browser session, or across visits with *Remember me* (name + PIN); *forget me* deletes them | My trips tab |
| Multi-city road trips | ✅ | Visit several Arizona cities in one trip (up to 14 days): *3 days in Phoenix, then 2 in Sedona and a day at the Grand Canyon*, *10-day trip: Phoenix, Sedona, Page* (days shared out), several cities in Quick plan or the guided questions, or *add 2 days in Flagstaff* to an existing plan. Each day is planned around its own city, each city gets its own hotel, each day gets **that city's own weather** (live forecast within 14 days, typical for the month beyond), heat/rain rules and events, and travel days start with the city-to-city drive ("Travel day: about 2 h from Phoenix to Sedona") | "add 2 days in Sedona" |
