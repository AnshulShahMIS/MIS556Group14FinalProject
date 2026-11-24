"""Hand-picked Google categories that count as tourist attractions, grouped into themes.

A place is kept when its *primary* (first) Google category is on this list, or when it is
tagged "Tourist attraction" anywhere and has enough reviews to be a real draw.
Keyword matching was tried first and let through mobile home parks, trailer stores,
parking lots and business parks, so the list is explicit.
"""
from __future__ import annotations

THEMES: dict[str, list[str]] = {
    "outdoors": [
        "Hiking area", "Park", "City park", "State park", "National park", "National forest",
        "National reserve", "Nature preserve", "Scenic spot", "Mountain peak", "Lake", "River",
        "Spring", "Hot spring", "Trail head", "Biking trail", "Campground", "Picnic ground",
        "Waterfall", "Beach", "Swimming lake", "Desert", "Volcano", "Cave", "Crater",
        "Wildlife refuge", "Observation deck", "Country park", "Ecological park", "Marina",
        "Dam", "Ski resort",
    ],
    "gardens": ["Garden", "Botanical garden"],
    "history_culture": [
        "Museum", "History museum", "Local history museum", "Art museum", "Modern art museum",
        "Science museum", "Technology museum", "Children's museum", "Heritage museum",
        "Natural history museum", "Archaeological museum", "Rail museum", "Army museum",
        "Open air museum", "Sculpture museum", "Ethnographic museum", "National museum",
        "Historical landmark", "Historical place", "Historical place museum", "Historical society",
        "Monument", "Archaeological site", "Ruin", "Mission", "Ghost town", "Cultural center",
        "Cultural landmark", "Visitor center", "Tourist information center", "Plaza",
        "Train station", "Railroad company",
    ],
    "arts": [
        "Art gallery", "Art center", "Sculpture", "Performing arts theater", "Concert hall",
        "Amphitheater", "Live music venue", "Comedy club",
    ],
    "science_sky": ["Observatory", "Planetarium"],
    "animals": [
        "Zoo", "Aquarium", "Wildlife park", "Wildlife and safari park", "Animal park",
        "Wildlife rescue service",
    ],
    "family_fun": [
        "Amusement park", "Amusement park ride", "Water park", "Theme park", "Amusement center",
        "Children's amusement center", "Miniature golf course", "Go-kart track", "Bowling alley",
        "Video arcade", "Escape room center", "Indoor playground", "Pumpkin patch",
        "Rock climbing gym",
    ],
    "tours_adventure": [
        "Tour operator", "Tour agency", "Sightseeing tour agency", "Bus tour agency",
        "Boat tour agency", "Canoe & kayak tour agency", "Raft trip outfitter", "Rafting",
        "SCUBA tour agency", "Sport tour agency", "Balloon ride tour agency",
        "Helicopter tour agency", "Horseback riding service", "Boat rental service",
        "Bicycle rental service", "ATV rental service", "Ranch", "Dude ranch",
    ],
    "food_drink": ["Winery", "Vineyard", "Brewery", "Distillery", "Farmers' market"],
    "sports_nightlife": ["Casino", "Stadium", "Arena", "Rodeo", "Golf course", "Public golf course"],
}

CATEGORY_TO_THEME: dict[str, str] = {c: t for t, cats in THEMES.items() for c in cats}

TOURIST_ATTRACTION = "Tourist attraction"
# A "Tourist attraction" tag on a non-attraction primary category (a restaurant, a shop)
# only counts if the place is genuinely popular.
TOURIST_TAG_MIN_REVIEWS = 50


def themes_for(categories: list[str] | None) -> list[str]:
    seen: list[str] = []
    for c in categories or []:
        t = CATEGORY_TO_THEME.get(c)
        if t and t not in seen:
            seen.append(t)
    if not seen and categories and TOURIST_ATTRACTION in categories:
        seen.append("sightseeing")
    return seen


def is_attraction(categories: list[str] | None, num_reviews: int | None) -> bool:
    if not categories:
        return False
    if categories[0] in CATEGORY_TO_THEME or categories[0] == TOURIST_ATTRACTION:
        return True
    return TOURIST_ATTRACTION in categories and (num_reviews or 0) >= TOURIST_TAG_MIN_REVIEWS


# --------------------------------------------------------------------------------------
# Added for the multi-agent planner: dining, nightlife, cuisines, indoor/outdoor, costs.
# --------------------------------------------------------------------------------------
NIGHTLIFE = {
    "Bar", "Cocktail bar", "Night club", "Pub", "Sports bar", "Wine bar", "Lounge", "Brewpub",
    "Beer hall", "Irish pub", "Karaoke bar", "Dance club", "Gay bar", "Hookah bar", "Tiki bar",
    "Dive bar", "Beer garden", "Bar & grill", "Live music bar", "Jazz club", "Piano bar",
    "Country western bar", "Rooftop bar", "Comedy club", "Live music venue", "Casino",
}

FOOD_EXCLUDE = {
    "Grocery store", "Convenience store", "Gas station", "Supermarket", "Liquor store",
    "Catering food and drink supplier", "Caterer", "Food bank", "Wholesale food store",
    "Meal delivery", "Food producer", "Butcher shop", "Vending machine",
}

CUISINES: dict[str, list[str]] = {
    "mexican": ["Mexican", "Taco", "Tex-Mex", "Burrito", "Latin American"],
    "italian": ["Italian", "Pizza"],
    "american": ["American", "Hamburger", "Diner", "Barbecue", "Steak", "Southern", "Chicken wings", "Hot dog"],
    "chinese": ["Chinese", "Dim sum", "Cantonese", "Szechuan"],
    "japanese": ["Japanese", "Sushi", "Ramen"],
    "thai": ["Thai"],
    "vietnamese": ["Vietnamese", "Pho"],
    "indian": ["Indian"],
    "korean": ["Korean"],
    "mediterranean": ["Mediterranean", "Greek", "Middle Eastern", "Lebanese", "Falafel"],
    "seafood": ["Seafood", "Fish"],
    "steakhouse": ["Steak"],
    "bbq": ["Barbecue"],
    "vegan": ["Vegan", "Vegetarian"],
    "breakfast": ["Breakfast", "Brunch", "Pancake", "Bagel"],
    "coffee": ["Coffee", "Cafe", "Espresso"],
    "dessert": ["Dessert", "Ice cream", "Bakery", "Donut", "Frozen yogurt", "Cupcake"],
    "southwestern": ["Southwestern", "New American", "Native American"],
    "fast food": ["Fast food"],
}

OUTDOOR_THEMES = {"outdoors", "gardens"}
STRENUOUS_CATS = {"Hiking area", "Mountain peak", "Trail head", "Biking trail", "Rock climbing gym"}

# Rough per-person admission estimates (USD) by Google category, used only when the data has
# no price. Free public lands are 0; the budget agent labels every number as an estimate.
ADMISSION_BY_CATEGORY: dict[str, float] = {
    "Hiking area": 0, "Park": 0, "City park": 0, "Scenic spot": 0, "Trail head": 0,
    "Mountain peak": 0, "Plaza": 0, "Monument": 0, "Historical landmark": 0, "Lake": 0,
    "Nature preserve": 0, "Visitor center": 0, "Art gallery": 0, "Mission": 0, "Picnic ground": 0,
    "Biking trail": 0, "Church": 0, "Catholic church": 0, "Chapel": 0, "Farmers' market": 5,
    "State park": 10, "National park": 18, "National forest": 5, "Ghost town": 5,
    "Garden": 15, "Botanical garden": 25, "Museum": 18, "History museum": 15, "Art museum": 20,
    "Science museum": 22, "Children's museum": 15, "Observatory": 20, "Planetarium": 15,
    "Zoo": 30, "Aquarium": 30, "Wildlife park": 25, "Baseball field": 25, "Amusement park": 45, "Water park": 45,
    "Theme park": 45, "Miniature golf course": 12, "Go-kart track": 30, "Bowling alley": 20,
    "Video arcade": 20, "Escape room center": 35, "Rock climbing gym": 22, "Tour operator": 85,
    "Tour agency": 85, "Sightseeing tour agency": 85, "Balloon ride tour agency": 250,
    "Helicopter tour agency": 250, "Horseback riding service": 75, "Raft trip outfitter": 90,
    "ATV rental service": 120, "Boat rental service": 60, "Winery": 20, "Brewery": 0,
    "Casino": 0, "Stadium": 30, "Arena": 40, "Golf course": 60, "Public golf course": 45,
    "Performing arts theater": 45, "Concert hall": 50, "Live music venue": 20, "Comedy club": 25,
    "Amphitheater": 40, "Rodeo": 25, "Train station": 0, "Tourist attraction": 10,
}

PRICE_LEVEL_COST = {1: 15.0, 2: 30.0, 3: 60.0, 4: 100.0}

# Places to stay. Nightly room-rate estimates (USD) by category - labelled as estimates everywhere.
LODGING = {"Hotel": 150.0, "Resort hotel": 260.0, "Motel": 85.0, "Bed & breakfast": 190.0, "Extended stay hotel": 120.0,
           "Lodge": 180.0, "Inn": 170.0, "Hostel": 45.0, "Guest house": 140.0, "Cottage": 160.0}


def is_lodging(categories: list[str]) -> bool:
    return bool(categories) and categories[0] in LODGING


def cuisines_for(categories: list[str]) -> list[str]:
    text = " ".join(categories).lower()
    return [c for c, keys in CUISINES.items() if any(k.lower() in text for k in keys)]


def is_food(categories: list[str]) -> bool:
    if not categories or categories[0] in FOOD_EXCLUDE:
        return False
    first = categories[0].lower()
    return any(w in first for w in ("restaurant", "cafe", "coffee", "diner", "bakery", "grill",
                                     "steak", "pizza", "taco", "sushi", "dessert", "ice cream",
                                     "bistro", "eatery", "brunch", "breakfast", "sandwich",
                                     "food court", "deli", "bagel", "donut", "brewpub", "buffet"))


def is_nightlife(categories: list[str]) -> bool:
    return bool(categories) and categories[0] in NIGHTLIFE

# Shopping was in the project brief's interest list but not in the first prototype.
THEMES["shopping"] = ["Shopping mall", "Outlet mall", "Market", "Flea market", "Shopping center"]
for _c in THEMES["shopping"]:
    CATEGORY_TO_THEME[_c] = "shopping"
