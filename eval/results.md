# Evaluation results

Run: `python eval/evaluate.py` (offline mode: rule-based planner/chooser, MiniLM embeddings).

How the four GenAI methods show up here: **Method 1** (MiniLM embeddings) and **Method 3** (top-k retrieval over 57k real review quotes) drive Parts A, C and D; **Method 4** (LangGraph agents) produces the Part B itineraries; **Method 2** (GPT-4o prompts) runs only with an OpenAI key, so these offline numbers use the rule-based fallbacks.

## A. Retrieval quality on intent-style queries (Precision@5, Recall@5)

33 queries worded without the category name (e.g. *"somewhere to look at paintings and sculpture"*), each searched within 25 km of a city. A result is relevant if its Google category matches the intent. Recall@5 is pooled: a query's relevant set is every relevant place that any method returned in its top 20.

| method                                    |   Precision@5 |   Recall@5 |   NDCG@5 |   queries |
|:------------------------------------------|--------------:|-----------:|---------:|----------:|
| MiniLM embeddings + review RAG (ours)     |         0.715 |      0.251 |    0.869 |        33 |
| MiniLM embeddings only                    |         0.685 |      0.253 |    0.905 |        33 |
| TF-IDF + ranking                          |         0.594 |      0.197 |    0.676 |        33 |
| Popularity baseline (most reviews nearby) |         0.085 |      0.043 |    0.188 |        33 |

## C. Top 5 attractions for different travelers (personalized recommendations)

8 traveler personas; several share a city, so the lists show whether the agent recommends different attractions for different interests. `grounded_in_review` = share of the 5 picks backed by a retrieved real review that matches the request (similarity >= 0.3). A pick is relevant if its Google category or its name matches the persona's interest (many Sedona vortex sites are only categorized as 'Tourist attraction'). Flagstaff has a single stargazing attraction with 20+ reviews in the data (Lowell Observatory), which caps that row.

| traveler           | city      |   Precision@5 |   grounded_in_review | top 5                                                                                                                                                                          |
|:-------------------|:----------|--------------:|---------------------:|:-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Hiker              | Sedona    |           1   |                  1   | Devil's Bridge Trailhead; Cathedral Rock Trailhead; Bear Mountain Trail - Oski Approach/Doe Mountain Parking; Doe Mountain Trail; Fay Canyon Trailhead                         |
| Art lover          | Sedona    |           1   |                  0.8 | Exposures International Gallery of Fine Art; Sedona Artist Market; Bearcloud Art Gallery & Native Cultural Center; Vue Gallery; Jerome Artists Co-Op                           |
| Spiritual traveler | Sedona    |           1   |                  0.4 | Vortex Yoga Hiking In Sedona; Amitabha Stupa and Peace Park; Boynton Pass Vortex; Vortex at Bell Rock; Chapel of the Holy Cross                                                |
| Family with kids   | Phoenix   |           0.8 |                  0.6 | Phoenix Zoo; Wildlife World Zoo, Aquarium & Safari Park; Steele Indian School Park Dog Park; OdySea Aquarium; SEA LIFE Centre Arizona                                          |
| History buff       | Phoenix   |           1   |                  0.2 | Heard Museum; Arizona Museum of Natural History; Rosson House Museum at Heritage Square; Pueblo Grande Museum; Arizona Jewish Historical Society                               |
| Thrill seeker      | Phoenix   |           1   |                  0.4 | Octane Raceway; Phoenix Kart Racing Association (PKRA); K1 Speed - Indoor Go Karts, Corporate Event Venue, Team Building Activities; Penske Racing Museum; Castles N' Coasters |
| Stargazer          | Flagstaff |           0.2 |                  0   | Lowell Observatory; All-Star Grand Canyon Tours; Museum of Northern Arizona; Dark Sky Brewing Co.; Oak Creek Vista                                                             |
| Foodie explorer    | Tucson    |           1   |                  0.8 | Heirloom Farmers Markets - Rillito Park; FoodInRoot Farmers' Markets; San Xavier del Bac Mission; Santa Cruz River Farmers' Market; Motosonora Brewing Co                      |

Mean top-5 overlap (Jaccard) between travelers in the same city: **0.00** (0 = completely different lists, 1 = identical).

## B. Itinerary quality on benchmark trip requests

12 requests (including the example from the project brief). The baseline uses the same stop skeleton but fills each stop with the most-reviewed place of that kind near the city centre.

|                    |   Multi-agent (ours) |   Popularity baseline |
|:-------------------|---------------------:|----------------------:|
| in_area            |                1     |                 1     |
| open_during_visit  |                0.983 |                 0.692 |
| age_rules_ok       |                1     |                 1     |
| cuisine_match      |                1     |                 0.333 |
| wheelchair_match   |                1     |                 1     |
| filled             |                1     |                 1     |
| quality            |               84.817 |                54.417 |
| within_budget      |                0.917 |                 0.5   |
| drive_min          |               58.917 |                79.583 |
| replans            |                0.333 |                 0     |
| grounded_in_review |                0.764 |                 0     |

Metric notes: `in_area` = share of stops within 30 km of the requested city; `open_during_visit` = share not closed at the scheduled time (2021 hours); `age_rules_ok` = no bars for under-21s / adults-only places with kids; `cuisine_match`, `wheelchair_match` = requested constraint satisfied; `quality` = critic score out of 100.

Caveats: relevance labels come from Google categories, which also appear in the embedded text, so Part A measures intent matching, not category look-up; the queries deliberately avoid category words. The midterm's leave-one-out Recall@5 needs per-user review histories (the separate 18M-review file), which this workbook does not contain, so Recall@5 here is pooled over the methods' results instead.

`grounded_in_review` in Part B = share of plan stops whose 'why' cites a retrieved real review matching the request.