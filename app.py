"""Saguaro web app:  py -m streamlit run app.py

Pages along the top:
  Plan      - start screen ("Where in Arizona?") until there's a trip; then chat on the left and the trip on the right
              (trip card, day tabs, map, stops with Reserve and more actions, budget / bookings / events tabs)
  Map       - every stop on a large map, numbered in order, with the route
  My trips  - every trip you've planned
  Group     - share a trip with a code and vote together
The OpenAI key, Preferences and New trip buttons sit at the top right of every page.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import re
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

# Set up the page FIRST, before the slow imports below (agents, PyTorch), so the tab icon, the "Connecting..."
# text and the hidden Streamlit Cloud badges apply within the first second of a visit.
st.set_page_config(page_title="Saguaro", page_icon=str(Path(__file__).parent / "assets" / "icon.png"),
                   layout="wide")
# Replace Streamlit's "running man" + Stop button (top right) with plain text: "Connecting..." while the page first
# loads, "Working..." while the agents run later.
def _dark_logo_uri() -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode((Path(__file__).parent / "assets" / "wordmark-dark.png").read_bytes()).decode()


st.html("""<style>
[data-testid="stStatusWidget"] > * { display: none !important; }
[data-testid="stStatusWidget"]::before {
  content: "%s"; font-size: 0.9rem; color: rgba(49, 51, 63, 0.75); padding: 0 0.5rem; white-space: nowrap;
  animation: az-pulse 1.4s ease-in-out infinite; }
@keyframes az-pulse { 0%%, 100%% { opacity: 1; } 50%% { opacity: 0.45; } }
/* trip-planner look: white cards on the sand background, a white header bar */
[class*="st-key-card"], [data-testid="stForm"], [data-testid="stExpander"] details,
[data-testid="stPopoverBody"] { background: #FFFFFF; }
/* chat: the agents' replies sit on the page background; the visitor's own messages are lightly tinted */
[data-testid="stChatMessage"] { background: transparent; }
[class*="st-key-umsg"] [data-testid="stChatMessage"] { background: #F3EBE1; }
.az-dark [class*="st-key-umsg"] [data-testid="stChatMessage"] { background: #26201C; }
[class*="st-key-card"] { box-shadow: 0 1px 2px rgba(42, 36, 32, 0.04); }
[data-testid="stHeader"] { background: #FFFFFF; border-bottom: 1px solid #EADFD3; }
h1 { letter-spacing: -0.01em; }
/* Popular places: a coloured picture tile over the name, the whole card clickable (an invisible button covers it) */
[class*="st-key-popcard-"] { position: relative; gap: 0; background: #FFFFFF; border: 1px solid #EADFD3;
  border-radius: 16px; overflow: hidden; transition: box-shadow 0.15s, transform 0.15s; }
[class*="st-key-popcard-"]:hover { box-shadow: 0 6px 18px rgba(42, 36, 32, 0.10); transform: translateY(-2px); }
[class*="st-key-popcard-"]:has(button:focus-visible) { outline: 2px solid #B4532A; outline-offset: 2px; }
.az-pop-art { height: 120px; display: flex; align-items: center; justify-content: center; background: var(--l); }
.az-pop-art .az-dark-only { display: none; }
.az-pop-text { padding: 12px 16px 14px; display: flex; flex-direction: column; gap: 2px; }
.az-pop-text b { font-size: 1rem; font-weight: 600; }
.az-pop-text span { font-size: 0.9rem; color: #6E6259; }
[class*="st-key-popbtn-"] { position: absolute !important; inset: 0; z-index: 2; margin: 0 !important; }
[class*="st-key-popbtn-"] button { width: 100%%; height: 100%%; opacity: 0; cursor: pointer; }
.az-dark [class*="st-key-popcard-"] { background: #221D1A; border-color: #3A322D; }
.az-dark .az-pop-art { background: var(--d); }
.az-dark .az-pop-art .az-light { display: none; }
.az-dark .az-pop-art .az-dark-only { display: block; }
.az-dark .az-pop-text span { color: #B9ADA2; }
/* My trips: cards with a coloured tile on top; the current trip has an accent border */
[class*="st-key-tripcard-"] { gap: 0.6rem; background: #FFFFFF; border: 1px solid #EADFD3; border-radius: 16px;
  overflow: hidden; padding-bottom: 16px; }
[class*="st-key-tripcard-"] > div:not(:first-child) { padding-left: 16px; padding-right: 16px; }
[class*="st-key-tripcard-"][class*="-current"] { border: 2px solid #B4532A; }
.az-trip-art { height: 110px; position: relative; background: var(--l); }
.az-trip-badge { position: absolute; left: 12px; top: 12px; font-size: 0.78rem; font-weight: 500; padding: 4px 10px;
  border-radius: 999px; background: #FFFFFF; color: #6B2F16; }
.az-trip-text { padding: 12px 16px 4px; display: flex; flex-direction: column; gap: 4px; }
.az-trip-text b { font-size: 1.1rem; font-weight: 600; }
.az-trip-text span { font-size: 0.88rem; color: #6E6259; }
.az-trip-text .az-trip-hl { color: inherit; }
[class*="st-key-topen-"] button { border-color: #B4532A; color: #8A3E1F; font-weight: 600; }
.az-dark [class*="st-key-tripcard-"] { background: #221D1A; border-color: #3A322D; }
.az-dark [class*="st-key-tripcard-"][class*="-current"] { border-color: #E07A4B; }
.az-dark .az-trip-art { background: var(--d); }
.az-dark .az-trip-badge { background: #171412; color: #F0A27A; }
.az-dark .az-trip-text span { color: #B9ADA2; }
.az-dark .az-trip-text .az-trip-hl { color: inherit; }
.az-dark [class*="st-key-topen-"] button { border-color: #E07A4B; color: #F0A27A; }
/* the chat's quick suggestions look like small rounded chips */
.st-key-followups button { border-radius: 999px; min-height: 2rem; padding: 0.15rem 0.8rem; }
.st-key-followups button p { font-size: 0.85rem; }
/* planning screen: the chat column stays in view while the trip on the right scrolls, and its conversation fills
   the screen height (wide screens only; on phones the chat sits above the trip) */
@media (min-width: 900px) {
  [data-testid="stColumn"]:has(.st-key-chatpanel) { position: sticky; top: 4.5rem; align-self: flex-start; }
  /* (Streamlit gives a fixed-height box a fixed flex size on its wrapper: both are overridden) */
  [data-testid="stLayoutWrapper"]:has(> .st-key-chatmsgs), .st-key-chatmsgs {
    height: calc(100vh - 22rem) !important; min-height: 18rem; flex: 0 0 auto !important; }
}
/* the agents' replies sit in a narrow chat column: smaller headings there */
[data-testid="stChatMessage"] h1, [data-testid="stChatMessage"] h2, [data-testid="stChatMessage"] h3 { font-size: 1.2rem; }
[data-testid="stChatMessage"] h4, [data-testid="stChatMessage"] h5 { font-size: 1.02rem; }
/* the AI status and account buttons sit in the header bar, on the right (left of Streamlit's status text and menu) */
@media (min-width: 1180px) {
  .st-key-topbar { position: fixed; top: 0.2rem; right: 9.5rem; height: 3.75rem; width: auto !important; z-index: 1000001;
    align-items: center; }
  .st-key-topbar > div, .st-key-topbar [data-testid="stElementContainer"] { margin: 0 !important; }
  .st-key-topbar [data-testid="stCaptionContainer"] { margin: 0 0.4rem 0 0; white-space: nowrap; }
  .st-key-topbar [data-testid="stCaptionContainer"] p { margin: 0; }
}
.az-dark .st-key-theme_btn button p { font-size: 0; }
.az-dark .st-key-theme_btn button p::after { content: "Light mode"; font-size: 0.875rem; }
.az-dark .st-key-theme_btn [data-testid="stIconMaterial"] { font-size: 0; }
.az-dark .st-key-theme_btn [data-testid="stIconMaterial"]::after { content: "light_mode"; font-size: 1.25rem; }
/* dark mode: the same layout on warm charcoal */
.az-dark [class*="st-key-card"], .az-dark [data-testid="stForm"], .az-dark [data-testid="stExpander"] details,
.az-dark [data-testid="stPopoverBody"] { background: #221D1A; }
.az-dark [data-testid="stHeader"] { background: #1C1816; border-bottom-color: #3A322D; }
.az-dark [data-testid="stStatusWidget"]::before { color: rgba(243, 236, 228, 0.7); }
.az-dark img[data-testid="stHeaderLogo"] { content: url("%s"); }
/* Streamlit Cloud's "Fork" / GitHub / share buttons in the app's top bar: not shown to visitors */
[data-testid="stToolbarActions"] { display: none !important; }
/* the zero-height helper frame below takes no space */
[data-testid="stElementContainer"]:has(iframe[height="0"]), .element-container:has(iframe[height="0"]) { display: none; }
</style>""" % ("Working..." if st.session_state.get("_ready") else "Connecting...", _dark_logo_uri()))
# Streamlit Community Cloud wraps the app in its own page: it adds a "Hosted with Streamlit" badge and the creator's
# profile picture for visitors, and shows Streamlit's tab icon until the app has started. That page is on the same
# address as the app, so this script (run from inside the app) hides the two badges and puts the saguaro icon and
# the app's title on the browser tab straight away. The owner-only "Manage app" button is untouched. Locally it
# finds no Streamlit Cloud page and only sets the tab icon, which the app does anyway.
def _early_js() -> str:
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.open(Path(__file__).parent / "assets" / "icon.png").resize((64, 64), Image.LANCZOS).save(buf, "PNG", optimize=True)
    icon = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return ("try{var d=document;if(!d.getElementById('az-hide-toolbar')){var x=d.createElement('style');"
            "x.id='az-hide-toolbar';x.textContent='[data-testid=\"stToolbarActions\"]{display:none!important}';"
            "(d.head||d.documentElement).appendChild(x);}}catch(e){}"
            # the Streamlit Cloud page: the outermost page this frame may reach (the top window, or, when the
            # app is shown inside the GitHub Pages page, a different site, the Streamlit page just inside it)
            "try{var w=window,t=document;try{while(w.parent!==w){t=w.parent.document;w=w.parent;}}catch(e){}"
            "if(!t.getElementById('az-hide-cloud-badges')){"
            "var s=t.createElement('style');s.id='az-hide-cloud-badges';s.textContent="
            "'[class*=\"_viewerBadge_\"],[class*=\"_profileContainer_\"],[class*=\"_profilePreview_\"],"
            # embed mode's "Built with Streamlit / Fullscreen" strip (the Fullscreen link leads back to the badge)
            "[class*=\"_container_\"]:has(> [class*=\"_hostedName_\"]),[class*=\"_hostedName_\"],[class*=\"_linkOutText_\"]"
            "{display:none!important}"
            # the embed frame's grey rounded border and the empty space left where that strip was: the app fills
            # the window (the page behind it takes the app's background colour, see THEME_SRC)
            "[class*=\"_streamlitAppContainer_\"]{border:0!important;border-radius:0!important;padding:0!important}"
            "';t.head.appendChild(s);"
            # keep only the saguaro icon on the tab: ours (built in) or the app's own (served from /media/);
            # Streamlit's default icons are removed whenever they're added or switched in
            "var ic='" + icon + "';var keep=function(){var ours=null;"
            "t.querySelectorAll('link[rel~=\"icon\"],link[rel=\"apple-touch-icon\"],link[rel=\"mask-icon\"]')"
            ".forEach(function(l){var h=l.getAttribute('href')||'';if(h.indexOf('/media/')>=0)return;"
            "if(l.id==='az-icon'){ours=l;if(h!==ic)l.setAttribute('href',ic);}else{l.parentNode.removeChild(l);}});"
            "if(!ours){var n=t.createElement('link');n.id='az-icon';n.rel='icon';n.type='image/png';n.href=ic;"
            "t.head.appendChild(n);}"
            "if(t.title!=='Saguaro'){t.title='Saguaro';}};"
            "keep();new MutationObserver(keep).observe(t.head,{childList:true,subtree:true,attributes:true,"
            "attributeFilter:['href']});}}catch(e){}")


# Light / dark theme. This code runs in the Streamlit app's own page (not in a component frame, whose code stops when
# Streamlit replaces the frame):
#  - __azPick('Light' | 'Dark') switches the look in place, without reloading the page (a reload would start a new
#    session and lose the trip). Where Streamlit's menu exists (running locally) it clicks the menu's Light / Dark
#    setting. In the embedded app on Streamlit Cloud there is no menu, so it sends Streamlit the theme itself (the
#    SET_CUSTOM_THEME_CONFIG message Streamlit accepts from its host page) and remembers the choice for next time;
#  - a visitor who hasn't chosen yet gets the light theme, even if the computer is set to dark;
#  - while the dark theme shows, the page gets class az-dark, for the card colours and the switch's label above.
def _theme_src() -> str:
    import json
    import tomllib

    cfg = tomllib.loads((Path(__file__).parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))["theme"]
    def font_url(setting: str) -> str:  # "Inter:https://...&display=swap, sans-serif" -> the https://... part
        return setting.split(":", 1)[1].split(",")[0].strip()

    common = {"bodyFont": "Inter, sans-serif", "headingFont": "Fraunces, serif", "baseRadius": cfg.get("baseRadius", "0.75rem"),
              "showWidgetBorder": True,
              # the font files too: without them the fonts are dropped when Streamlit switches themes on a new page
              "fontSources": [{"configName": "font", "sourceUrl": font_url(cfg["font"])},
                              {"configName": "headingFont", "sourceUrl": font_url(cfg["headingFont"])}]}
    keys = ("primaryColor", "backgroundColor", "secondaryBackgroundColor", "textColor", "borderColor", "linkColor")
    themes = {"Light": {"base": 0, **{k: cfg["light"][k] for k in keys}, **common},
              "Dark": {"base": 1, **{k: cfg["dark"][k] for k in keys}, **common}}
    menu = "document.querySelector('[data-testid=\"stMainMenu\"] button, button[aria-label=\"Main menu\"]')"
    return (
        "if(window.__azPickV!==3){window.__azPickV=3;var TH=" + json.dumps(themes) + ";"
        "window.__azPick=async function(n){var D=document,s=function(m){return new Promise(function(r){setTimeout(r,m)})};"
        "var b=" + menu + ";"
        "if(!b){try{localStorage.setItem('azTheme',n);}catch(e){}"
        "window.postMessage({stCommVersion:1,type:'SET_CUSTOM_THEME_CONFIG',themeName:'Saguaro '+n,themeInfo:TH[n]},'*');return;}"
        "var it=null;for(var i=0;i<20&&!it;i++){if(i%5===0)b.click();await s(120);"
        "it=D.querySelector('[data-testid=\"stMainMenuItem-theme-'+n+'\"]');}"
        "if(it){it.click();await s(150);}if(D.querySelector('[data-testid^=\"stMainMenuItem\"]'))b.click();};}"
        "if(window.__azTheme!==4){window.__azTheme=4;var K='stActiveTheme-'+location.pathname+'-v2';"
        "var v=localStorage.getItem(K);var first=!v||v==='\"System\"';if(first)localStorage.setItem(K,JSON.stringify('Light'));"
        "var embedded=window!==window.parent,started=false,last=0;"
        "setInterval(function(){var a=document.querySelector('.stApp');if(!a)return;"
        "var m=getComputedStyle(a).backgroundColor.match(/[0-9]+/g);if(!m)return;var dark=(+m[0])+(+m[1])+(+m[2])<300;"
        "document.documentElement.classList.toggle('az-dark',dark);"
        # Streamlit Cloud's page around the embedded app: same background, so no white edges in either theme
        "var o=null;try{var w=window;while(w.parent!==w){w=w.parent;o=w.document;}}catch(e){}"
        "if(o&&o!==document&&o.head){var st=o.getElementById('az-outer-bg');if(!st){st=o.createElement('style');"
        "st.id='az-outer-bg';o.head.appendChild(st);}var css='html,body{background:'+getComputedStyle(a).backgroundColor+'!important}';"
        "if(st.textContent!==css)st.textContent=css;}"
        "var b=" + menu + ";"
        "if(b){if(!started){started=true;if(first&&dark)window.__azPick('Light');}}"  # menu: Streamlit remembers it
        # embedded (no menu): keep the visitor's choice. Streamlit goes back to its default theme when another page
        # opens, so the choice is put back straight away, every time
        "else if(embedded&&document.querySelector('[data-testid=\"stMain\"]')){"
        "var want=localStorage.getItem('azTheme')||'Light';"
        "if((dark?'Dark':'Light')!==want&&Date.now()-last>700){last=Date.now();window.__azPick(want);}}"
        "},100);}")


THEME_SRC = _theme_src()


def in_app_page(src: str) -> str:
    """Script for a component frame that runs `src` in the Streamlit app's page (the frame's parent)."""
    import json
    return "try{window.parent.Function(" + json.dumps(src) + ")();}catch(e){}"


EARLY_JS = _early_js()
components.html(f"<script>{EARLY_JS}{in_app_page(THEME_SRC)}</script>", height=0)

# Privacy: in embed mode (?embed=true) Streamlit Cloud never draws the creator's profile picture or the badge at all,
# so a visitor who opens the plain link is sent to the embed version straight away. Only in the frame's first page
# (loaded once per visit), never on reruns, so moving between the app's tabs can't trigger a reload.
EMBED_REDIRECT_JS = ("try{var L=window.top.location;if(/\\.streamlit\\.app$/.test(L.hostname)&&"
                     "!/[?&]embed=true/.test(L.search)){var u=new URL(L.href);u.searchParams.set('embed','true');"
                     "L.replace(u.toString());}}catch(e){}")


@st.cache_resource(show_spinner=False)
def _hide_badges_from_first_paint(head_script: str) -> str:
    """Streamlit Cloud draws its badge at the same moment it loads the app's frame. The script above only runs once
    the app's Python code starts (about 2 s later), so the badge flashes. Here the same script goes into the frame's
    own HTML page, which loads in a fraction of a second: a copy of Streamlit's index.html with the script added is
    written to a temporary folder, and Streamlit's page server is told to look there first. Streamlit's installed
    files are never changed. Runs once per server start, only on the Linux cloud server; does nothing locally."""
    if (not sys.platform.startswith("linux") and os.getenv("AZ_FORCE_INDEX") != "1") or os.getenv("AZ_KEEP_INDEX") == "1":
        return "skipped (not on the cloud server)"
    try:
        import gc
        import tempfile

        from starlette.staticfiles import StaticFiles

        html = (Path(st.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
        folder = Path(tempfile.gettempdir()) / "saguaro_static"
        folder.mkdir(exist_ok=True)
        (folder / "index.html").write_text(html.replace("<head>", f"<head><script>{head_script}</script>", 1),
                                           encoding="utf-8")
        patched = 0
        for obj in gc.get_objects():
            if isinstance(obj, StaticFiles) and type(obj).__name__ == "_StreamlitStaticFiles":
                if str(folder) not in map(str, obj.all_directories):
                    obj.all_directories = [str(folder), *obj.all_directories]  # our index.html wins; assets as before
                obj._index_path = str(folder / "index.html")  # also for the page-not-found fallback
                patched += 1
        return f"badge rule in first paint ({patched} handler(s))"
    except Exception as e:  # a Streamlit change: the in-app script above still hides the badges
        return f"not applied: {type(e).__name__}"


_hide_badges_from_first_paint(f"{EMBED_REDIRECT_JS}{EARLY_JS}try{{{THEME_SRC}}}catch(e){{}}")

import pandas as pd  # noqa: E402
import pydeck as pdk  # noqa: E402

from azmulti import live, llm, shared  # noqa: E402
from azmulti.agents.concierge import web_links  # noqa: E402
from azmulti.catalog import get_catalog  # noqa: E402
from azmulti.changes import diff_plans, record  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.hours import DAYS, fmt_time  # noqa: E402
from azmulti.units import snip  # noqa: E402
from azmulti.security import RateLimiter, clean_name, esc, redact, safe_url, sanitize_markdown, valid_key_format  # noqa: E402

md = sanitize_markdown
REQUIRE_USER_KEY = os.getenv("AZ_REQUIRE_USER_KEY", "0") == "1"

CITIES = ["Phoenix", "Scottsdale", "Tempe", "Mesa", "Tucson", "Sedona", "Flagstaff", "Chandler", "Gilbert", "Glendale",
          "Grand Canyon Village", "Prescott", "Page", "Paradise Valley", "Peoria", "Yuma", "Lake Havasu City"]
INTERESTS = {"Hiking & outdoors": "hiking and outdoors", "Museums & history": "museums and history", "Art": "art galleries",
             "Animals & zoos": "zoo and animals", "Fun & games": "fun activities", "Shopping": "shopping", "Sports": "sports",
             "Tours & adventure": "tours and adventure", "Breweries & wineries": "breweries and wineries", "Scenic views": "scenic views"}
FOODS = ["No preference", "Mexican", "Italian", "American", "BBQ", "Steakhouse", "Sushi", "Thai", "Indian", "Chinese",
         "Mediterranean", "Seafood", "Vegan", "Pizza"]
EXTRAS = {"Hotel stay": "and a hotel to stay overnight", "Night out (21+)": "then go out for drinks, we're all 21+", "Wheelchair accessible": "wheelchair accessible",
          "Vegetarian": "vegetarian food", "Hidden gems": "hidden gems", "Indoor only": "indoor activities",
          "Free activities": "free things to do", "Highly rated only": "highly rated"}
EXAMPLES = [
    ("Friends' day out", "Plan a Saturday in Scottsdale for four friends. We're all 24, have a $125 budget per person, want a fun outdoor activity, Mexican food for dinner, and somewhere to go out afterward."),
    ("Family day", "Family day in Phoenix in July with two kids, we like animals and museums, $60 per person"),
    ("Date night", "Romantic date night in Sedona on Friday, scenic sunset then Italian dinner, $150 per person"),
    ("Game day", "Spring training game day in Mesa in March for 5 friends aged 30, $90 each, then bars"),
]
FOLLOW_UPS = ["Make it cheaper", "Swap dinner", "Book dinner", "What's the weather?",  # first four shown as buttons
              "Show budget, balanced and premium options", "Add a coffee stop after stop 1", "What events are on that day?",
              "Make it kid friendly", "Top 5 attractions near here"]
QUICK = {"Top 5 attractions for me": "Top 5 attractions in Sedona for hiking and photography"}  # example; users type their own
KIND = {"attraction": ("Activity", [37, 99, 235]), "restaurant": ("Food", [217, 119, 6]), "nightlife": ("Night out", [124, 58, 237]),
        "lodging": ("Stay", [5, 150, 105])}
MODE = {"car": "drive", "rideshare": "ride", "walking": "walk", "transit": "by transit"}
I = {  # Material icons
    "chat": ":material/chat:", "form": ":material/edit_note:", "itinerary": ":material/event_note:", "map": ":material/map:",
    "group": ":material/group:", "key": ":material/key:", "prefs": ":material/tune:", "new": ":material/restart_alt:",
    "swap": ":material/swap_horiz:", "cheaper": ":material/savings:", "directions": ":material/directions:",
    "web": ":material/language:", "menu": ":material/restaurant_menu:", "trips": ":material/luggage:",
    "book": ":material/confirmation_number:", "save": ":material/bookmark:", "remove": ":material/delete:",
    "up": ":material/thumb_up:", "down": ":material/thumb_down:", "check": ":material/check_circle:", "warn": ":material/warning:",
    "info": ":material/info:", "sync": ":material/sync:", "share": ":material/share:", "vote": ":material/how_to_vote:",
    "calendar": ":material/calendar_month:", "download": ":material/download:", "route": ":material/route:",
    "add": ":material/add:", "star": ":material/star:", "spark": ":material/auto_awesome:", "lock": ":material/lock:",
    "user": ":material/person:", "bot": ":material/explore:",
}


@st.cache_resource(show_spinner="Getting things ready (the first load takes about 30 seconds)...")
def warm():
    cat = get_catalog()
    cat.embed("warm up")
    try:  # run one plan so the agents' code paths are loaded before the first visitor asks
        Planner().ask("Saturday in Phoenix for 2, museum and lunch")
    except Exception as e:
        logging.getLogger("app").warning("warm-up plan failed: %s", redact(e))
    return cat


_T0 = time.time()
warm()
ss = st.session_state
if "planner" not in ss:
    ss.planner = Planner()
    ss.chat, ss.votes = [], {}
    ss.changes, ss.changed_ids, ss.unseen, ss.updated_at = [], set(), 0, None
    ss.msg_limit, ss.join_limit = RateLimiter(20, 60), RateLimiter(5, 600)
    ss.pin_limit, ss.key_limit = RateLimiter(5, 600), RateLimiter(5, 600)


# ================================================================== helpers
_CARD = iter(range(10**9))


def card():
    """A white, rounded card (a bordered container; the key gives it the card style below)."""
    return st.container(border=True, key=f"card{next(_CARD)}")


def city_stays(legs):
    from azmulti.units import city_stays as _cs
    return _cs(legs)


def dist(km) -> str:
    """Distance in miles first (US audience) with km alongside, e.g. '24.9 mi (40.1 km)'."""
    km = float(km or 0)
    return f"{km * 0.621371:.1f} mi ({km:.1f} km)"


def plan_state() -> dict | None:
    st_ = ss.planner.state()
    return st_ if any(s.get("place") for s in st_.get("slots") or []) else None


def note_changes(before: dict | None, after: dict | None, who: str = "You") -> dict:
    d = diff_plans(before, after)
    if d["summary"]:
        ss.changes = record(ss.changes, d, who)
        ss.changed_ids = set() if d["new_trip"] else d["changed"]
        ss.unseen += 1
        ss.updated_at = time.time()
    return d


MAX_DAYS = 14
DATE_RX = re.compile(r"\b20\d\d-\d\d-\d\d\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|"
                     r"\b(today|tomorrow|tonight|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)


def dates_text(start: date, end: date) -> str:
    return f"on {start.isoformat()}" if end == start else f"from {start.isoformat()} to {end.isoformat()}"


def date_range_picker(key: str, label: str = "Trip dates", default: tuple[date, date] | None = None,
                      summary: bool = True) -> tuple[date, date] | None:
    """Calendar: pick a start and an end date (any date up to a year ahead, trips of 1-14 days)."""
    today = date.today()
    if default is None:
        sat = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
        default = (sat, sat)
    picked = st.date_input(label, value=default, min_value=today, max_value=today + timedelta(days=365), key=key,
                           format="MM/DD/YYYY", help="Click a start date, then an end date. Pick the same day twice for a day trip.")
    if not isinstance(picked, (tuple, list)) or len(picked) == 0:
        return None
    start, end = picked[0], picked[-1]
    if (end - start).days + 1 > MAX_DAYS:
        st.warning(f"Trips can be up to {MAX_DAYS} days - pick an end date by {start + timedelta(days=MAX_DAYS - 1):%b} "
                   f"{(start + timedelta(days=MAX_DAYS - 1)).day}, or plan the rest as a second trip.", icon=I["warn"])
        return None
    n = (end - start).days + 1
    if summary:  # (inside a form the summary would only refresh on submit, so forms skip it)
        st.caption(f"{start:%a, %b} {start.day}" + (f" to {end:%a, %b} {end.day} · {n} days" if n > 1 else " · day trip"))
    return start, end


def ask(prompt: str) -> dict | None:
    """Send a message to the agents and track what changed in the itinerary."""
    prompt = (prompt or "")[:2000]
    pd_ = ss.get("pending_dates")
    iv_on = bool((ss.planner.state().get("interview") or {}).get("active"))
    if pd_ and not plan_state() and not iv_on and not DATE_RX.search(prompt) and len(prompt.split()) > 2:
        prompt = f"{prompt.rstrip('. ')}, {dates_text(*pd_)}"  # dates picked on the calendar before the request was typed
        ss.pending_dates = None
    if not ss.msg_limit.allow():
        st.toast(f"Slow down a little - try again in {ss.msg_limit.retry_in()} s.", icon=I["warn"])
        return None
    shown = prompt if redact(prompt) == prompt else "[message hidden - it contained a key or token]"
    before = plan_state()
    ss.chat.append({"role": "user", "content": shown})
    try:
        out = ss.planner.ask(prompt)
    except Exception as e:  # never show raw tracebacks
        logging.getLogger("app").error("request failed: %s", redact(e))
        ss.chat.append({"role": "assistant", "content": "Sorry - something went wrong. Your plan is unchanged; please try again."})
        return None
    for err in out.get("llm_errors") or []:
        st.toast(err + " Using the built-in planner for now.", icon=I["warn"])
    d = note_changes(before, out if any(s.get("place") for s in out.get("slots") or []) else None)
    ss.chat.append({"role": "assistant", "content": out.get("answer", ""), "trace": out.get("trace", []),
                    "plan_changed": bool(d["summary"])})
    return out


def act(prompt: str, done: str):
    """One-click action on the trip: run it and stay on the page."""
    with st.spinner("Updating your itinerary..."):
        out = ask(prompt)
    if out is not None:
        st.toast(done, icon=I["check"])
    st.rerun()


def ago(ts: float | None) -> str:
    if not ts:
        return ""
    s = int(time.time() - ts)
    return "just now" if s < 45 else f"{s // 60} min ago" if s < 3600 else datetime.fromtimestamp(ts).strftime("%I:%M %p")


def stars(p: dict) -> str:
    return f"{I['star']} {(p.get('rating') or 0):.1f} ({int(p.get('n_reviews') or 0):,})"


def goto(where: str | None):
    """Switch pages outside any form/column context."""
    if where:
        st.switch_page({"itinerary": plan_page, "chat": plan_page, "plan": plan_page, "map": map_page,
                        "trips": trips_page, "group": group_page}[where])


@st.fragment(run_every=10)
def auto_sync():
    """Shared plans: pull the group's latest version every 10 seconds, on every page."""
    if not ss.planner.shared_code:
        return
    before = plan_state()
    if ss.planner.sync():
        rec = shared.load(ss.planner.shared_code) or {}
        who = clean_name(rec.get("editor"))
        note_changes(before, plan_state(), who)
        ss.chat.append({"role": "assistant", "content": f"**{who}** updated the shared plan. Changes are marked *Updated* on your trip."})
        st.toast(f"{who} updated the plan", icon=I["sync"])
        st.rerun(scope="app")


def compose_request(city, day: tuple, length, adults, kids, budget, interests, food, extras, transport) -> str:
    """Turn the quick-plan form into a sentence the Planner agent understands."""
    when = {"Full day": "", "Morning": "half day morning", "Afternoon": "afternoon", "Evening": "evening"}[length]
    start, end = day
    where = " then ".join(city) if isinstance(city, (list, tuple)) else city  # several cities: a road trip
    parts = [f"{when} trip in {where} {dates_text(start, end)}".strip()]
    parts.append(f"for {adults + kids} people" + (f" with {kids} kids" if kids else ""))
    if budget:
        parts.append(f"${budget} per person")
    if interests:
        parts.append("we like " + ", ".join(INTERESTS[i] for i in interests))
    if food and food != "No preference":
        parts.append(f"{food.lower()} food for dinner")
    parts += [EXTRAS[e] for e in extras if not (kids and e.startswith("Night out"))]
    parts.append({"Car": "", "Rideshare": "we'll take uber", "Walking": "walking only", "Transit": "by light rail and bus"}[transport])
    return ", ".join(p for p in parts if p)


def empty_state(text: str):
    st.info(text, icon=I["info"])
    st.page_link(plan_page, label="Plan a trip", icon=I["spark"])


# ================================================================== dialogs
@st.dialog("Use your own OpenAI API key")
def key_dialog():
    st.markdown("Add your key to let **GPT-4o** run the agents: understanding your requests, choosing places, writing "
                "the plan and answering questions. Requests use your OpenAI project's quota and any applicable charges.")
    st.markdown("[Get a key in the OpenAI API dashboard](https://platform.openai.com/api-keys)")
    key = st.text_input("OpenAI API key", type="password", placeholder="Paste your OpenAI API key (sk-...)",
                        key="key_field", autocomplete="off")
    st.caption("Kept only in this browser session's memory on the app server; reloading the page or closing the tab "
               "clears it. It is sent over HTTPS only to OpenAI. The app does not save it to disk, logs, chats, shared "
               "plans, memory profiles or downloads, and no part of it is ever shown again.")
    st.caption("Use an OpenAI API key for GPT models. A ChatGPT subscription does not include API usage; API billing is separate.")
    if ss.planner.has_api_key:
        st.success("Your key is active for this session. It is hidden and never shown again.", icon=I["check"])
    elif REQUIRE_USER_KEY or not os.getenv("OPENAI_API_KEY"):
        st.info("No key added. Planning still works with the built-in planner.", icon=I["info"])
    else:
        st.info("No key added. The agents use the app's own key.", icon=I["info"])
    st.caption("Only paste your key into apps you trust. Authentication and quota are checked when you add it.")
    c1, c2, c3 = st.columns([1.4, 1, 1])
    if c1.button("Use key for this session", type="primary", use_container_width=True):
        k = (key or "").strip()
        if not valid_key_format(k):
            st.error("That doesn't look like an OpenAI API key (it should start with sk-).")
        elif not ss.key_limit.allow():
            st.error(f"Too many attempts - try again in {ss.key_limit.retry_in()} s.")
        else:
            ok, msg = llm.verify_key(k)
            if ok is False:
                st.error(msg)
            else:
                ss.planner.set_api_key(k)
                ss.pop("key_field", None)
                st.toast(msg if ok else "Key saved for this session. " + msg, icon=I["key"])
                st.rerun()
    if c2.button("Clear key", use_container_width=True, disabled=not ss.planner.has_api_key):
        ss.planner.clear_api_key()
        ss.pop("key_field", None)
        st.toast("Key cleared from this session.", icon=I["key"])
        st.rerun()
    if c3.button("Cancel", use_container_width=True):
        ss.pop("key_field", None)
        st.rerun()


@st.dialog("Preferences & privacy", width="large")
def prefs_dialog():
    st.markdown("**Remember my preferences**")
    st.caption("Off by default. When on, your favorite cuisines and interests, diet, accessibility needs, favorites and places "
               "you've seen are saved under a hash of your name + PIN. Never shared.")
    prof = ss.planner.profile
    c1, c2 = st.columns(2)
    name = c1.text_input("Profile name", value=ss.planner.profile_name or "", max_chars=40)
    pin = c2.text_input("PIN (4+ characters)", type="password", max_chars=32, autocomplete="off")
    mem = st.toggle("Remember me", value=bool(prof.get("memory_on")), disabled=len(pin or "") < 4)
    if len(pin or "") >= 4 and (mem != bool(prof.get("memory_on")) or (name and name != (ss.planner.profile_name or ""))
                                or pin != ss.get("_pin_used")):
        if ss.pin_limit.allow():
            ss.planner.set_memory(mem, name or None, pin)
            ss["_pin_used"] = pin
            st.rerun()
        else:
            st.error(f"Too many attempts - try again in {ss.pin_limit.retry_in()} s.")
    c1, c2 = st.columns(2)
    if c1.button("What do you remember?", use_container_width=True):
        ask("what do you remember about me?")
        st.rerun()
    if c2.button("Forget me", icon=I["remove"], use_container_width=True):
        ss.planner.forget()
        st.toast("Deleted saved preferences and turned memory off.", icon=I["check"])
        st.rerun()
    favs = ss.planner.profile.get("favorites", [])
    if favs:
        st.markdown("**Favorites**")
        for f in favs:
            st.markdown(md(f"- [{f['name']}]({safe_url(f.get('url')) or ''}) · {f['city']}"))
    st.divider()
    st.markdown("**Live information**")
    on = [n for n, v in live.status().items() if v]
    st.caption("Weather forecast is live. " + (f"Also live: {', '.join(on)}." if on else
               "Hours, events, web lookups and traffic turn on when the app owner adds their keys (see README)."))
    st.divider()
    st.markdown("**About**")
    st.caption("Saguaro is a team of AI agents (MIS 556, Group 14): Planner, Weather & Events, Attraction, Dining & Nightlife, "
               "Live Info, Route, Budget, a Critic that scores and fixes the plan, Booking, and a Concierge that writes it up. "
               "Data: 20,626 Arizona places from Google Maps (2021) and TripAdvisor (2026). Your OpenAI key stays in this "
               "session only; trip details are sent only to the live services above.")


ASPECT = {"food_quality": "Food", "service": "Service", "value": "Value", "wait_time": "Wait times", "cleanliness": "Cleanliness",
          "atmosphere": "Atmosphere", "staff": "Staff", "price": "Prices", "views": "Views", "parking": "Parking"}


def render_reviews(p: dict):
    """What reviewers say: rating summary, star breakdown, praised / complained-about aspects, real quotes."""
    import json
    c = st.columns([1.3, 2.7])
    with c[0]:
        st.metric("Google rating", f"{(p.get('rating') or 0):.1f} / 5")
        st.caption(f"{int(p.get('n_reviews') or 0):,} reviews")
        if p.get("ta_rating"):
            st.caption(f"TripAdvisor {p['ta_rating']:.1f} / 5" + (f" · {p['ta_rank']}" if p.get("ta_rank") else ""))
        if p.get("rating_trend") in ("improving", "declining"):
            st.caption(f"Recent ratings are {p['rating_trend']}")
    with c[1]:
        try:
            dist = json.loads(p.get("rating_dist") or "{}")
        except Exception:
            dist = {}
        tot = sum(dist.values()) if dist else 0
        for k in ("5", "4", "3", "2", "1"):
            if tot:
                share = dist.get(k, 0) / tot
                st.progress(share, text=f"{k} star · {share:.0%}")
    try:
        good = [ASPECT.get(a, a.replace("_", " ").capitalize()) for a in json.loads(p.get("praised") or "[]")]
        bad = [ASPECT.get(a, a.replace("_", " ").capitalize()) for a in json.loads(p.get("complaints") or "[]")]
    except Exception:
        good, bad = [], []
    if good or bad:
        st.markdown(" ".join([f":green-badge[{g}]" for g in good] + [f":orange-badge[{b}]" for b in bad]))
        st.caption("Green = what reviewers praise · orange = common complaints")
    if p.get("ev_love"):
        st.markdown(md(f"**TripAdvisor travelers love:** {p['ev_love']}  \n**Watch out for:** {p.get('ev_complaints') or '-'}"))
    live_reviews = (p.get("live") or {}).get("reviews") or []
    if live_reviews:
        quotes, src = [(r.get("stars"), r.get("when"), r.get("text")) for r in live_reviews[:3]], "Latest Google reviews (live)"
    else:
        try:
            quotes = [(q.get("stars"), q.get("year"), q.get("text")) for q in json.loads(p.get("review_quotes") or "[]") if q.get("text")][:3]
        except Exception:
            quotes = []
        src = "Real Google reviews (2018-2021)"
    if quotes:
        st.caption(src)
        for stars_, when, text in quotes:
            with card():
                st.markdown(md(f"{'★' * int(stars_ or 0)}{'☆' * (5 - int(stars_ or 0))}  ·  {when or ''}"))
                st.markdown(md(text[:400] + ("..." if len(text) > 400 else "")))
    if safe_url(p.get("url")):
        st.link_button("Read all reviews on Google Maps", safe_url(p["url"]), icon=I["star"])


def top_bar():
    """The AI status and the account buttons, shown on the right of the header bar (next to Plan / Map / My trips /
    Group); on narrow screens they drop to their own row under the header (see the .st-key-topbar style)."""
    has_plan = bool(plan_state())
    with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small",
                      wrap=False, key="topbar"):
        bits = ["AI " + ("GPT-4o With Your Key" if ss.planner.has_api_key
                         else "GPT-4o (App Key)" if os.getenv("OPENAI_API_KEY") and not REQUIRE_USER_KEY and os.getenv("AZ_OFFLINE", "0") != "1"
                         else "Built In Planner")]
        if ss.planner.shared_code:
            bits.append(f"shared trip {ss.planner.shared_code}")
        st.caption(" · ".join(bits), width="content")
        # one switch: it reads "Dark mode" in light mode and "Light mode" in dark mode (swapped by the az-dark style,
        # so it is right even when the theme was changed from Streamlit's own menu)
        if st.button("Dark mode", icon=":material/dark_mode:", type="tertiary", key="theme_btn",
                     help="Switch between the light and dark look"):
            ss["_theme_switch"] = ss.get("_theme_switch", 0) + 1
        key_clicked = st.button("OpenAI key", icon=I["key"], type="tertiary")
        prefs_clicked = st.button("Preferences", icon=I["prefs"], type="tertiary")
        new_clicked = st.button("New trip", icon=I["new"], type="tertiary",
                                help="Start a new trip - the current one is kept under My trips")
    if ss.get("_theme_switch"):  # Streamlit's own theme setting (menu > Light / Dark), clicked for the visitor
        components.html("<script>/*%d*/%s</script>" % (ss["_theme_switch"], in_app_page(
            THEME_SRC + "window.__azPick(document.documentElement.classList.contains('az-dark')?'Light':'Dark');")), height=0)
    if key_clicked:
        key_dialog()
    elif prefs_clicked:
        prefs_dialog()
    if new_clicked:
        start_new_trip()


def start_new_trip():
    """Clear the planner for a new trip (the current one is kept under My trips) and go to the start screen."""
    had = bool(plan_state())
    ss.planner.reset()  # the current plan is saved to My trips first
    ss.chat, ss.votes, ss.changes, ss.changed_ids, ss.unseen, ss.updated_at = [], {}, [], set(), 0, None
    ss.pop("day_pick", None)
    if had:
        st.toast("Your last trip is saved under My trips.", icon=I["check"])
    ss["_goto"] = "plan"


# ================================================================== chat (left side of the Plan page)
def chat_messages(height: int | None = None):
    """The conversation, newest at the bottom."""
    box = st.container(height=height, border=False, key="chatmsgs") if height else st.container()
    with box:
        for i, m in enumerate(ss.chat):
            # each message in a keyed box: the visitor's own messages get a light tint (style above)
            user = m["role"] == "user"
            with st.container(key=f"{'umsg' if user else 'amsg'}{i}"), \
                    st.chat_message(m["role"], avatar=I["user"] if user else I["bot"]):
                st.markdown(md(m["content"]))
                if m.get("trace"):
                    with st.expander("How the agents worked on this"):
                        for t in m["trace"]:
                            st.markdown(md(f"**{t['agent']}** - {t['did']}"))


def pending_buttons(st_):
    pend = (st_ or {}).get("pending")
    if pend:
        c1, c2 = st.columns([1.4, 1])
        if c1.button(f"Yes, request {pend['name'][:28]}", icon=I["check"], type="primary", use_container_width=True):
            with st.spinner("Saving..."):
                ask("yes")
            st.rerun()
        if c2.button("No, cancel", use_container_width=True):
            ask("no")
            st.rerun()


def question_card() -> dict | None:
    """Guided questions: tap to answer (typing works too). Returns the open question, if any."""
    iv = ss.planner.state().get("interview") or {}
    q = iv.get("q") if iv.get("active") else None
    if not q:
        return None
    with card():
        st.caption(f"Question {q['step']} of {q['total']}")
        if q["key"] == "when":  # pick exact dates on a calendar, or tap a quick option below
            rng = date_range_picker(f"iv-cal-{len(ss.chat)}", "Pick your dates", default=ss.get("pending_dates"))
            if st.button("Use these dates", key=f"iv-cal-go-{len(ss.chat)}", icon=I["calendar"], type="primary", disabled=not rng):
                ss.pending_dates = None
                with st.spinner("Got it..."):
                    ask(dates_text(*rng))
                st.rerun()
            st.caption("Or tap one:")
        if q["multi"]:
            sel = st.pills("Pick any", q["options"], selection_mode="multi", default=q.get("default") or None,
                           key=f"iv-{len(ss.chat)}", label_visibility="collapsed")
            c1, c2, _ = st.columns([1.2, 1, 2])
            if c1.button("Continue", icon=I["check"], type="primary", use_container_width=True):
                with st.spinner("Got it..."):
                    ask(", ".join(sel) if sel else "skip")
                st.rerun()
            if c2.button("Skip", use_container_width=True):
                ask("skip")
                st.rerun()
        else:
            pick = st.pills("Pick one", q["options"], key=f"iv-{len(ss.chat)}", label_visibility="collapsed")
            if pick:
                with st.spinner("Planning your trip..." if q["key"] == "confirm" else "Got it..."):
                    ask(pick)
                st.rerun()
        if st.button("Stop the questions and just plan it", type="tertiary"):
            with st.spinner("Planning your trip..."):
                ask("just plan it")
            st.rerun()
    return q


def chat_box(q: dict | None, placeholder: str):
    prompt = st.chat_input("Type your answer..." if q else placeholder)
    if prompt:
        with st.spinner("Your agents are working..."):
            ask(prompt)
        st.rerun()


def chat_panel(st_):
    """Left side of the planning screen: the conversation, quick follow-ups and the message box."""
    st.markdown("#### Chat with Saguaro")
    chat_messages(height=430)
    pending_buttons(st_)
    q = question_card()
    if not q:  # the most used follow-ups as buttons, the rest one click away (keeps the chat column tidy)
        with st.container(horizontal=True, gap="small", vertical_alignment="center", key="followups"):  # wraps
            pick = None
            for k, s in enumerate(FOLLOW_UPS[:4]):
                if st.button(s, key=f"sugg-{len(ss.chat)}-{k}"):
                    pick = s
            with st.popover("More ideas"):
                more = st.pills("More ideas", FOLLOW_UPS[4:], key=f"more-{len(ss.chat)}", label_visibility="collapsed")
        pick = pick or more
        if pick:
            with st.spinner("Working on it..."):
                ask(pick)
            st.rerun()
    chat_box(q, "Ask for a change or a new trip...")


# ================================================================== start screen (no trip yet)
# Popular places: (label, city for the form, blurb, line icon, light tile / icon colour, dark tile / icon colour)
POPULAR = [
    ("Phoenix", "Phoenix", "Sunrise hikes, museums",
     '<path d="M3 21h18"/><path d="M5 21V8l5-3v16"/><path d="M10 21V11h9v10"/>', "#F4D9C8", "#8A3E1F", "#3B2B22", "#E9A47E"),
    ("Sedona", "Sedona", "Red rocks, vortexes",
     '<path d="M3 20l6-11 4 6 3-4 5 9z"/>', "#F1C9A8", "#7A3A12", "#46301F", "#F0B07E"),
    ("Grand Canyon", "Grand Canyon Village", "Rim views, trails",
     '<path d="M2 18h20"/><path d="M4 18c2-6 5-9 8-9s6 3 8 9"/><path d="M9 18c1-3 2-4 3-4s2 1 3 4"/>',
     "#D8E6D9", "#2F5D3A", "#233127", "#9CC9A3"),
    ("Tucson", "Tucson", "Saguaros, street tacos",
     '<path d="M12 21V4a2 2 0 0 1 4 0v8"/><path d="M12 13H9a2 2 0 0 1-2-2V8a2 2 0 0 1 4 0"/>'
     '<path d="M16 9h1a2 2 0 0 1 2 2v1"/><path d="M8 21h8"/>', "#E7DEC6", "#5B4A1E", "#36311F", "#D8C98E"),
]


def icon_uri(paths: str, color: str) -> str:
    """A line icon as an image (st.html removes inline <svg>, but keeps images)."""
    import base64
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def start_view():
    if ss.chat:  # a conversation is under way (guided questions or a question): show it front and centre
        _, mid, _ = st.columns([1, 3, 1])
        with mid:
            st.title("Let's plan your trip", anchor=False)
            chat_messages()
            pending_buttons(None)
            q = question_card()
            if not q:
                pick = st.pills("Ideas", [t for t, _ in EXAMPLES] + list(QUICK), key=f"sugg-{len(ss.chat)}",
                                label_visibility="collapsed")
                if pick:
                    with st.spinner("Working on it..."):
                        ask(QUICK.get(pick, pick))
                    st.rerun()
        chat_box(q, "Describe your trip, or ask anything...")
        return

    _, mid, _ = st.columns([0.5, 5, 0.5])
    with mid:
        st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)
        st.title("Where in Arizona?", anchor=False)
        st.markdown("Tell me the plan, or tap an idea and I'll ask a few quick questions.")
        with st.form("start_form", border=True):
            c = st.columns([2.3, 1.9, 0.8, 0.8, 1.1], vertical_alignment="bottom")
            city = c[0].multiselect("Where", CITIES, key="qp_city", max_selections=6, placeholder="Pick a city (or several)",
                                    help="Pick several cities for a road trip - the days are shared between them in the order you pick")
            with c[1]:
                rng = date_range_picker("qp_dates", "When", summary=False)
            adults = c[2].number_input("Adults", 1, 20, 2)
            kids = c[3].number_input("Kids", 0, 10, 0)
            budget = c[4].number_input("Budget each ($)", 0, 1000, 100, step=10, help="0 means no budget")
            with st.expander("More options: interests, food, getting around"):
                length = st.segmented_control("Each day", ["Full day", "Morning", "Afternoon", "Evening"], default="Full day")
                interests = st.pills("Interests", list(INTERESTS), selection_mode="multi")
                o = st.columns(2)
                food = o[0].selectbox("Dinner", FOODS)
                with o[1]:
                    transport = st.segmented_control("Getting around", ["Car", "Rideshare", "Walking", "Transit"], default="Car")
                extras = st.pills("Extras", list(EXTRAS), selection_mode="multi")
            if st.form_submit_button("Plan my trip", icon=":material/search:", type="primary", use_container_width=True):
                if not city:
                    st.error("Pick at least one city.", icon=I["warn"])
                elif not rng:
                    st.error(f"Pick a start and end date (up to {MAX_DAYS} days).", icon=I["warn"])
                elif len(city) > (rng[1] - rng[0]).days + 1:
                    st.error(f"You picked {len(city)} cities - choose at least {len(city)} days so each city gets a day.", icon=I["warn"])
                else:
                    ss["_plan_next"] = compose_request(city, rng, length or "Full day", int(adults), int(kids), int(budget),
                                                       interests or [], food, extras or [], transport or "Car")
                    st.rerun()
        pick = st.pills("Ideas", [t for t, _ in EXAMPLES] + list(QUICK), key="ideas", label_visibility="collapsed")
        if pick:
            with st.spinner("Working on it..."):
                ask(QUICK.get(pick, pick))  # a scenario title starts the guided questions
            st.rerun()

        st.subheader("Popular places to start", anchor=False)
        cols = st.columns(4)
        for col, (label, city_name, blurb, icon, bg, fg, bg_d, fg_d) in zip(cols, POPULAR):
            slug = label.replace(" ", "")
            with col, st.container(key=f"popcard-{slug}"):  # picture tile + name; the whole card is one button
                st.html(f'<div class="az-pop" style="--l:{bg};--d:{bg_d}"><div class="az-pop-art">'
                        f'<img class="az-light" src="{icon_uri(icon, fg)}" width="46" height="46" alt="">'
                        f'<img class="az-dark-only" src="{icon_uri(icon, fg_d)}" width="46" height="46" alt=""></div>'
                        f'<div class="az-pop-text"><b>{label}</b><span>{blurb}</span></div></div>')
                # fills in Where on the form above (set before the form is drawn, so it sticks)
                st.button(f"Plan a trip to {label}", key=f"popbtn-{slug}", on_click=ss.__setitem__, args=("qp_city", [city_name]))
        if ss.planner.trips:
            st.page_link(trips_page, label=f"Your saved trips ({len(ss.planner.trips)})", icon=I["trips"])
    chat_box(None, "Or just tell me: 3 days in Sedona for 2, hiking and Italian food...")


# ================================================================== the trip (right side of the Plan page)
def reservation_panel(pnd: dict):
    """The reservation request form, shown right under the stop it's for."""
    hotel = pnd["kind"] == "lodging"
    with card():
        what = {"lodging": "Reserve a room at", "attraction": "Get tickets for"}.get(pnd["kind"], "Reserve a table at")
        st.markdown(md(f"**{what} {pnd['name']}**"))
        st.caption(f"{pnd.get('date', '')} · check the details, then send the request. Nothing is booked or paid until you "
                   "finish on the booking site, which shows live availability.")
        f = st.columns(3)
        party = f[0].number_input("Guests" if hotel else "Party size", 1, 40, int(pnd["party"]), key="rsv_party")
        if hotel:
            rooms = f[1].number_input("Rooms", 1, 20, int(pnd.get("rooms", 1)), key="rsv_rooms")
            nights = f[2].number_input("Nights", 1, 30, int(pnd.get("nights", 1)), key="rsv_nights")
            msg = f"yes for {party} guests, {rooms} rooms, {nights} nights"
        else:
            times = [m for m in range(7 * 60, 23 * 60 + 1, 15)]
            t0 = min(times, key=lambda m: abs(m - int(pnd["time_min"])))
            tm = f[1].selectbox("Time", times, index=times.index(t0), format_func=fmt_time, key="rsv_time")
            msg = f"yes at {tm // 60}:{tm % 60:02d} for {party}"
        c1, c2, _ = st.columns([1.3, 1, 3])
        if c1.button("Send request", icon=I["check"], type="primary", use_container_width=True):
            act(msg, "Added to your reservations - finish it on the booking site")
        if c2.button("Cancel", use_container_width=True):
            act("no", "Cancelled - nothing was booked")


@st.dialog("Stop details", width="large")
def stop_dialog(n: int, s: dict):
    """Everything about one stop: reviews, hours, links, other options and feedback."""
    p = s["place"]
    name = f"[{p['name']}]({safe_url(p.get('url'))})" if safe_url(p.get("url")) else p["name"]
    st.markdown(md(f"### {name}"))
    st.caption(md(f"{s.get('time', '')} · {KIND.get(s['kind'], ('Stop',))[0]} · {p.get('primary_category', '')} · "
                  f"{p.get('city', '')} · {'$' + format(s['cost_pp'], '.0f') + ' per person' if s.get('cost_pp') else 'Free'}"))
    if s.get("reason"):
        st.markdown(md(s["reason"]))
    t_rev, t_info, t_alt = st.tabs([f"Reviews · {(p.get('rating') or 0):.1f}★", "Details and booking", "Other options"])
    with t_rev:
        render_reviews(p)
    with t_info:
        lv = p.get("live") or {}
        if lv.get("checked"):
            st.caption(md(f"Checked live (Google, {lv['checked']}): {'open' if lv.get('status') == 'OPERATIONAL' else lv.get('status')}"
                          + (f" · hours that day {s.get('hours_that_day')}" if s.get("hours_that_day") else "")
                          + (f" · parking: {', '.join(lv['parking'])}" if lv.get("parking") else "")
                          + (f" · {lv['phone']}" if lv.get("phone") else "")))
        elif s.get("hours_that_day"):
            st.caption(md(f"Hours that day (2021 data): {s['hours_that_day']}"))
        if p.get("ev_love"):
            st.markdown(md(f"**Travelers love:** {p['ev_love']}  \n**Watch out for:** {p.get('ev_complaints') or '-'}  \n"
                           f"**Tip:** {p.get('ev_tip') or '-'}  \n*TripAdvisor summary*"))
        if p.get("menu_highlights"):
            st.caption(md(f"Menu highlights: {p['menu_highlights'][:200]}"))
        bk = s.get("booking") or {}
        if bk.get("advice"):
            st.caption(md(bk["advice"]) + " Nothing is booked without your OK.")
        web = web_links(p, s["kind"])
        links = {**{k: v for k, v in web.items()}, **{k: v for k, v in (bk.get("links") or {}).items()}}
        btn = [(k, safe_url(v)) for k, v in links.items() if safe_url(v)]
        if btn:
            cols = st.columns(min(4, len(btn)))
            for i, (k, u) in enumerate(btn[:8]):
                cols[i % len(cols)].link_button(k, u, use_container_width=True)
    with t_alt:
        alts = s.get("alternatives") or []
        if not alts:
            st.caption("No other options for this stop. Use Swap to look for a different place.")
        for k, alt in enumerate(alts[:3], 1):
            cc = st.columns([5, 1.3], vertical_alignment="center")
            cc[0].markdown(md(f"**{alt['name']}** · {I['star']} {(alt.get('rating') or 0):.1f} · ~${alt.get('cost_pp') or 0:.0f} per person"))
            if cc[1].button("Use this", key=f"alt{n}-{k}", use_container_width=True):
                act(f"use option {k} for stop {n}", f"Stop {n} is now {alt['name']}")
    st.divider()
    f1, f2, f3, _ = st.columns([1.2, 1.1, 1, 1.2])
    if f1.button("More like this", key=f"up{n}", icon=I["up"], use_container_width=True):
        ss.planner.feedback(p, +1)
        st.toast("Noted - I'll favor places like this.", icon=I["check"])
    if f2.button("Not for us", key=f"dn{n}", icon=I["down"], use_container_width=True):
        act(f"we don't like stop {n}", "Replaced and noted for next time")
    if f3.button("Save", key=f"sv{n}", icon=I["save"], use_container_width=True):
        act(f"save stop {n}", "Saved to favorites")


def trip_map(stops_n: list, height: int = 280):
    """Numbered stops with the route between them, coloured by kind."""
    rows = []
    for n, s in stops_n:
        p = s["place"]
        label, color = KIND.get(s["kind"], ("Stop", [80, 80, 80]))
        rows.append({"n": str(n), "name": p["name"], "lat": p["lat"], "lon": p["lon"], "day": s["day"],
                     "time": (s.get("time") or "").split(" - ")[0], "kind": label, "color": color})
    if not rows:
        return
    pts = pd.DataFrame(rows)
    span = max(pts.lat.max() - pts.lat.min(), (pts.lon.max() - pts.lon.min()) * 0.8, 0.01)
    zoom = 13 if span < 0.03 else 12 if span < 0.06 else 11 if span < 0.12 else 10 if span < 0.25 else 9 if span < 0.5 else 8 if span < 1.2 else 7
    paths = [{"path": [[r.lon, r.lat] for r in pts[pts.day == d].itertuples()]} for d in pts.day.unique()]
    st.pydeck_chart(pdk.Deck(
        map_style="light",
        initial_view_state=pdk.ViewState(latitude=float(pts.lat.mean()), longitude=float(pts.lon.mean()), zoom=zoom),
        layers=[
            pdk.Layer("PathLayer", paths, get_path="path", get_color=[60, 60, 60, 140], width_min_pixels=3),
            pdk.Layer("ScatterplotLayer", pts, get_position="[lon, lat]", get_fill_color="color", get_radius=60,
                      radius_min_pixels=13, radius_max_pixels=18, stroked=True, get_line_color=[255, 255, 255],
                      line_width_min_pixels=2, pickable=True),
            pdk.Layer("TextLayer", pts, get_position="[lon, lat]", get_text="n", get_size=13,
                      get_color=[255, 255, 255], font_weight=700, get_alignment_baseline="'center'"),
        ],
        tooltip={"html": "<b>{n}. {name}</b><br/>{time} · {kind}", "style": {"fontSize": "13px"}}),
        height=height)


def stop_card(n: int, s: dict, st_: dict, res_by: dict, nstops: int):
    p = s["place"]
    with card():
        c = st.columns([1.05, 5, 1.35, 0.55], vertical_alignment="center")
        start, _, end = (s.get("time") or "").partition(" - ")
        c[0].markdown(f"**{start}**")
        if end:
            c[0].caption(f"to {end}")
        kind_label = KIND.get(s["kind"], ("Stop", None))[0]
        badges = []
        if s["id"] in ss.changed_ids:
            badges.append(":blue-badge[Updated]")
        if s.get("open_ok") is False:
            badges.append(":orange-badge[Check hours]")
        if p["id"] in res_by:
            badges.append(":green-badge[Booked]" if res_by[p["id"]]["status"] == "booked" else ":violet-badge[Reservation requested]")
        c[1].markdown(md(f"**{n}. {p['name']}** " + " ".join(badges)))
        cost = f"~${s['cost_pp']:.0f}" + (" a night" if s["kind"] == "lodging" else "") if s.get("cost_pp") else "free"
        c[1].caption(md(f"{s['label']} · {kind_label} · {(p.get('rating') or 0):.1f}★ ({int(p.get('n_reviews') or 0):,}) · {cost}"
                        + (f" · {p.get('city')}" if p.get("city") else "")))
        if s.get("reason"):
            c[1].caption(md(snip(s["reason"], 140)))
        bookable = s["kind"] != "attraction" or s.get("cost_pp")
        if bookable:
            if c[2].button("Tickets" if s["kind"] == "attraction" else "Reserve", key=f"bk{n}", type="primary",
                           use_container_width=True,
                           help={"lodging": "Reserve a room", "attraction": "Get tickets or book a tour"}.get(s["kind"], "Reserve a table")):
                act(f"book stop {n}", "Request ready - check the details under this stop")
        elif c[2].button("Details", key=f"dt{n}", use_container_width=True):
            stop_dialog(n, s)
        with c[3].popover("", icon=":material/more_horiz:", use_container_width=True, help="More actions"):
            if bookable and st.button("Details and reviews", key=f"dt{n}", icon=I["star"], use_container_width=True):
                stop_dialog(n, s)
            if st.button("Swap for another place", key=f"sw{n}", icon=I["swap"], use_container_width=True):
                act(f"replace stop {n}", f"Swapped stop {n}")
            if st.button("Find a cheaper option", key=f"ch{n}", icon=I["cheaper"], use_container_width=True, disabled=not s.get("cost_pp")):
                act(f"make stop {n} cheaper", f"Found a cheaper option for stop {n}")
            directions = (s.get("booking") or {}).get("links", {}).get("Directions")
            if safe_url(directions):
                st.link_button("Directions", safe_url(directions), icon=I["directions"], use_container_width=True)
            if st.button("Remove this stop", key=f"rm{n}", icon=I["remove"], use_container_width=True, disabled=nstops <= 1):
                act(f"remove stop {n}", f"Removed stop {n}")
            if st.button("Closed for good? Replace it", key=f"cl{n}", icon=I["warn"], use_container_width=True,
                         help="If this place has shut down, I'll replace it and never recommend it again"):
                act(f"stop {n} is permanently closed", f"Replaced stop {n} - it won't be recommended again")
        if (st_.get("pending") or {}).get("slot_id") == s["id"]:
            reservation_panel(st_["pending"])  # right under this place


def trip_panel(st_: dict):
    """Right side of the planning screen: the trip card, the day's stops, the map and the trip details."""
    req, cond, b, q = st_.get("request") or {}, st_.get("conditions") or {}, st_.get("budget") or {}, st_.get("quality") or {}
    stops = [s for s in st_["slots"] if s.get("place")]
    first = datetime.fromisoformat(cond["date"]) if cond.get("date") else None
    ndays = req.get("days", 1)
    day_txt = (f"{first:%a, %b} {first.day}" if first else DAYS[req["weekday"]] if req.get("weekday") is not None else "")
    if first and ndays > 1:
        last = first + timedelta(days=ndays - 1)
        day_txt += f" – {last:%a, %b} {last.day}"
    title = " → ".join(lg["city"] for lg in req.get("legs") or []) or req.get("city", "")
    by_day = (cond or {}).get("by_day") or {}

    with card():
        h = st.columns([5, 1], vertical_alignment="center")
        h[0].header(title, anchor=False)
        if h[1].button("Share", icon=I["share"], use_container_width=True, help="Plan together: share this trip with a code"):
            ss["_goto"] = "group"
        st.caption(" · ".join(x for x in [
            day_txt, f"{req.get('party_size', 2)} {'travelers' if req.get('party_size', 2) > 1 else 'traveler'}",
            f"{ndays} days" if ndays > 1 else "",
            f"updated {ago(ss.updated_at)}" if ss.updated_at else ""] if x))
        if b and b.get("budget_pp"):
            st.progress(min(1.0, b["total_pp"] / b["budget_pp"]),
                        text=f"${b['total_pp']:.0f} of ${b['budget_pp']:.0f} per person"
                             + (f" · ${b['total_pp'] - b['budget_pp']:.0f} over" if b["total_pp"] > b["budget_pp"] else ""))
        elif b:
            st.caption(f"About ${b['total_pp']:.0f} per person")
        chips = []
        if by_day:
            seen = {}
            for v in by_day.values():
                seen.setdefault(v["city"], v["high_f"])
            chips += [f":orange-badge[{c} · {h}°F]" for c, h in seen.items()]
        elif cond.get("high_f"):
            chips.append(f":orange-badge[{req.get('city', '')} · {cond['high_f']}°F · sunset {fmt_time(cond['sunset'])}]")
        hotels = sum(1 for s in stops if s["kind"] == "lodging")
        if hotels:
            chips.append(f":green-badge[{hotels} hotel night{'s' if hotels > 1 else ''}]")
        chips.append(f":gray-badge[Travel {(st_.get('route') or {}).get('minutes', 0)} min]")
        chips.append(f":violet-badge[Plan score {q.get('total', 0):.0f}/100]")
        st.markdown(" ".join(chips), help="Plan score: budget fit, match to what you asked, travel efficiency and opening hours.")
    for a in cond.get("alerts", []):
        st.warning(a, icon=I["warn"])

    if ss.changes:
        last = ss.changes[-1]
        with st.expander(f"Latest update by {last['who']} · {ago(last['t'])}", expanded=bool(ss.unseen)):
            for line in last["what"][:6]:
                st.markdown(md(f"- {line}"))
            for c in reversed(ss.changes[:-1]):
                st.caption(md(f"**{c['who']}** · {ago(c['t'])}: " + "; ".join(c["what"][:4])))
        ss.unseen = 0

    # ---- which day to show
    stays = city_stays(req.get("legs"))  # multi-city: {first day: (city, days)}
    days = sorted({s["day"] for s in stops})
    pick = days[0] if days else 1
    if len(days) > 1:
        city_at = {}
        cur = None
        for d in days:
            if d in stays:
                cur = stays[d][0]
                city_at[d] = f"Day {d} · {cur}"
            else:
                city_at[d] = f"Day {d}"
        opts = ["All days"] + [city_at[d] for d in days]
        if ss.get("day_pick") not in opts:
            ss["day_pick"] = opts[1]
        sel = st.segmented_control("Day", opts, key="day_pick", label_visibility="collapsed") or opts[1]
        pick = None if sel == "All days" else int(sel.split()[1].strip("·"))
    numbered = list(enumerate(stops, 1))
    shown = [(n, s) for n, s in numbered if pick is None or s["day"] == pick]

    trip_map(shown, height=260)
    route_bits = sum(s.get("drive_km") or 0 for _, s in shown)
    mins = sum(s.get("drive_min") or 0 for n, s in shown if n > 1)
    m = st.columns([3, 1.3], vertical_alignment="center")
    m[0].caption(f"{'Whole trip' if pick is None else f'Day {pick}'} route · {dist(route_bits)} · about {mins} min of travel  ·  "
                 + "   ".join(f":{c}-badge[{k}]" for k, c in (("Activity", "blue"), ("Food", "orange"), ("Night out", "violet"), ("Stay", "green"))))
    if m[1].button("Full map", icon=I["map"], use_container_width=True):
        ss["_goto"] = "map"

    res_by = {r["place_id"]: r for r in st_.get("reservations") or []}
    shown_ids = {s["id"] for _, s in shown}
    cur_day = None
    for n, s in shown:
        if s["day"] != cur_day and ndays > 1:
            cur_day = s["day"]
            stay = stays.get(cur_day)
            if stay and (pick is None or len(stays) > 1):  # multi-city: the city, its days and dates
                city, cdays = stay
                span = ""
                if first:
                    d0, d1 = first + timedelta(days=cur_day - 1), first + timedelta(days=cur_day + cdays - 2)
                    span = f" · {d0:%a, %b} {d0.day}" + (f" – {d1:%a, %b} {d1.day}" if cdays > 1 else "")
                st.subheader(f"{city} · {cdays} day{'s' if cdays > 1 else ''}", anchor=False)
                if span:
                    st.caption(span.strip(" ·"))
            st.markdown(f"**Day {cur_day}" + (f" · {first + timedelta(days=cur_day - 1):%A, %B} "
                                              f"{(first + timedelta(days=cur_day - 1)).day}**" if first else "**"))
            wx = by_day.get(cur_day) or by_day.get(str(cur_day))
            if wx:
                st.caption(md(f"{wx['city']}: high **{wx['high_f']}°F**"
                              + (f" · {wx['rain_pct']}% chance of rain" if wx.get("rain_pct") else "")
                              + f" · sunrise {fmt_time(wx['sunrise'])} · sunset {fmt_time(wx['sunset'])}"
                              + (" · live forecast" if "forecast" in (wx.get("source") or "").lower() else " · typical for the month")))
                for a in wx.get("alerts") or []:
                    st.warning(md(a), icon=I["warn"])
            if s.get("transfer"):
                tr = s["transfer"]
                st.info(f"Travel day: about {tr['min'] // 60} h {tr['min'] % 60:02d} min ({dist(tr['km'])}) from {tr['from']} "
                        f"to {tr['to']}. Leave around {fmt_time(max(420, s.get('start', 540) - tr['min']))}.", icon=I["route"])
        if s.get("drive_min") and n > 1 and not s.get("transfer"):
            traffic = f" · includes {s['traffic']['delay_min']} min traffic" if (s.get("traffic") or {}).get("delay_min") else ""
            st.caption(f":material/more_vert: {s['drive_min']} min {MODE.get(req.get('transport', 'car'), 'drive')} · {dist(s.get('drive_km'))}{traffic}")
        stop_card(n, s, st_, res_by, len(stops))

    if st.button("Add a stop", icon=I["add"], type="tertiary"):
        ss.chat.append({"role": "assistant", "content": "What would you like to add? For example *add a coffee stop after stop 1* or *add dessert after dinner*."})
        st.rerun()
    pnd = st_.get("pending")
    if pnd and pnd.get("slot_id") not in shown_ids:  # asked for in chat for a stop on another day: keep it visible
        reservation_panel(pnd)

    trip_details(st_, req, b, stops, day_txt)


def trip_details(st_: dict, req: dict, b: dict, stops: list, day_txt: str):
    """Budget, bookings, events and how the agents planned it, then calendar / save / route buttons."""
    t_budget, t_book, t_events, t_how = st.tabs(["Budget", "Bookings", "Events", "How it was planned"])
    with t_budget:
        if b:
            st.markdown(f"**${b['total_pp']:.0f} per person** · group of {b.get('party_size', 2)}: **${b['group_total']:.0f}** · "
                        f"tax and tip about ${b['extras_pp']:.0f} per person extra")
            if b.get("budget_pp"):
                st.progress(min(1.0, b["total_pp"] / b["budget_pp"]), text=f"{b['total_pp'] / b['budget_pp']:.0%} of your ${b['budget_pp']:.0f} budget")
            lines = pd.DataFrame(b["lines"]).rename(columns={"stop": "Stop", "place": "Place", "per_person": "$ per person",
                                                            "basis": "Based on", "cheaper_option": "Cheaper option", "category": "Type"})
            st.dataframe(lines, hide_index=True, use_container_width=True)
            if st.button("Make the whole plan cheaper", icon=I["cheaper"]):
                act("make it cheaper", "Trimmed the cost")
    with t_book:
        res = st_.get("reservations") or []
        if not res:
            st.caption("No reservations yet. Use Reserve on a restaurant or hotel or Tickets on a paid activity, or say "
                       "*book dinner* / *reserve the hotel* / *get tickets for stop 3* in chat.")
        for k, r in enumerate(res):
            hotel = r["kind"] == "lodging"
            with card():
                top = st.columns([4, 1.4])
                when = (f"check in {r.get('date', '')} · {r.get('nights', 1)} night(s) · {r.get('rooms', 1)} room(s)" if hotel
                        else f"{r.get('date', '')} at {fmt_time(r['time_min'])}")
                top[0].markdown(md(f"**{r['name']}**  \n{when} · {r['party']} {'guests' if hotel else 'people'}"))
                booked = r["status"] == "booked"
                top[1].markdown(":green-badge[Booked]" + (f"  \n`{r['confirmation']}`" if r.get("confirmation") else "")
                                if booked else ":violet-badge[Requested]")
                if not booked:
                    sites = {"lodging": ("Booking.com", "Expedia", "Google Hotels"),
                             "attraction": ("Official website", "Find official tickets", "GetYourGuide", "Viator")}.get(
                        r["kind"], ("OpenTable", "Resy", "Google (Reserve a table)"))
                    btn = [(x, safe_url((r.get("links") or {}).get(x))) for x in sites]
                    btn = [(x, u) for x, u in btn if u]
                    if btn:
                        st.caption("Opens the official ticket search and tour sites for this place - check times and prices there."
                                   if r["kind"] == "attraction" else "Opens with your date, " + ("guests and rooms" if hotel else
                                   "time and party size") + " filled in - pick from the live availability there.")
                        cols = st.columns(len(btn))
                        for col, (x, u) in zip(cols, btn):
                            col.link_button(x, u, use_container_width=True)
                    m = st.columns([2, 1.2, 1])
                    code = m[0].text_input("Confirmation number", key=f"conf{k}", max_chars=20, placeholder="Confirmation number (optional)",
                                           label_visibility="collapsed")
                    if m[1].button("Mark as booked", key=f"mb{k}", icon=I["check"], use_container_width=True):
                        clean = "".join(ch for ch in code if ch.isalnum() or ch == "-")[:20]
                        act(f"mark {r['name']} booked" + (f" confirmation {clean}" if clean else ""), f"{r['name']} marked as booked")
                    if m[2].button("Remove", key=f"rc{k}", use_container_width=True):
                        act(f"cancel reservation {r['name']}", "Reservation removed")
        for i in st_.get("issues") or []:
            if i["type"] == "booking_conflict":
                st.error(md(i["msg"]), icon=I["warn"])
    with t_events:
        evs = st_.get("events") or []
        if not evs:
            st.caption("No events found for your dates. Ask *what events are on that day?* in chat to check again.")
        for e in evs[:8]:
            st.markdown(md(f"- **{e['name']}** - {e['note']}" + (f" ([tickets]({safe_url(e.get('url'))}))" if safe_url(e.get("url")) else "")))
    with t_how:
        st.caption("What each AI agent did for the latest change.")
        st.dataframe(pd.DataFrame(st_.get("trace") or []).rename(columns={"agent": "Agent", "via": "How", "did": "What it did"}),
                     hide_index=True, use_container_width=True)

    bk = st_.get("booking") or {}
    c = st.columns(3)
    if bk.get("ics"):
        c[0].download_button("Add to my calendar", bk["ics"], file_name="arizona_itinerary.ics", mime="text/calendar",
                             icon=I["calendar"], use_container_width=True)
    page = ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            "<title>Arizona itinerary</title><style>body{font-family:system-ui;max-width:760px;margin:2em auto;line-height:1.5;color:#1f2328}"
            "li{margin:.6em 0}</style></head><body><h2>Our Arizona plan - " + esc(req.get("city")) + " · " + esc(day_txt) + "</h2><ol>"
            + "".join(f"<li><b>{esc(s.get('time'))} - <a href='{esc(safe_url(p_.get('url')) or '#')}' rel='noopener noreferrer'>{esc(p_['name'])}</a></b> ({esc(p_['city'])}) · "
                      f"{(p_['rating'] or 0):.1f}/5 · ~${s.get('cost_pp') or 0:.0f} per person<br><small>{esc(s.get('reason'))}</small></li>"
                      for s in stops for p_ in [s["place"]])
            + f"</ol><p>Estimated ${b.get('total_pp', 0):.0f} per person. "
            + (f"<a href='{esc(safe_url(bk.get('route_url')))}' rel='noopener noreferrer'>Open the route in Google Maps</a>" if safe_url(bk.get("route_url")) else "")
            + "</p><p><small>Planned with Saguaro (MIS 556). Verify hours and prices before you go.</small></p></body></html>")
    c[1].download_button("Save as a page", page, file_name="arizona_itinerary.html", mime="text/html", icon=I["download"], use_container_width=True)
    if safe_url(bk.get("route_url")):
        c[2].link_button("Route in Google Maps", safe_url(bk["route_url"]), icon=I["route"], use_container_width=True)
    st.caption("Hours and prices come from Google Maps (2021) and TripAdvisor unless marked as checked live. Please verify before you go.")


def page_plan():
    if ss.get("_plan_next"):  # a plan requested from the start form: build it, then show it
        with st.spinner("Your agents are planning your trip..."):
            ask(ss.pop("_plan_next"))
        st.rerun()
    st_ = plan_state()
    if not st_:
        start_view()
        return
    left, right = st.columns([1, 1.9], gap="large")
    with left, st.container(key="chatpanel"):
        chat_panel(st_)
    with right:
        trip_panel(st_)


def page_map():
    st_ = plan_state()
    if not st_:
        st.header("Map")
        empty_state("No trip yet. Once you plan one, every stop shows up here in order.")
        return
    req = st_.get("request") or {}
    stops = [s for s in st_["slots"] if s.get("place")]
    head = st.columns([5, 1.3])
    head[0].header(f"Map · {' → '.join(lg['city'] for lg in req.get('legs') or []) or req.get('city', '')}")
    how = {"car": "driving", "rideshare": "by rideshare", "walking": "walking", "transit": "by transit"}.get(req.get("transport", "car"), "driving")
    head[0].caption(f"{len(stops)} stops · {dist((st_.get('route') or {}).get('km'))} · "
                    f"{(st_.get('route') or {}).get('minutes', 0)} min {how} in total")
    days = sorted({s["day"] for s in stops})
    pick_day = None
    if len(days) > 1:
        sel = head[1].segmented_control("Day", ["All"] + [f"Day {d}" for d in days], default="All", label_visibility="collapsed")
        pick_day = None if sel in (None, "All") else int(sel.split()[1])
    shown = [(n, s) for n, s in enumerate(stops, 1) if pick_day is None or s["day"] == pick_day]

    rows = []
    for n, s in shown:
        p = s["place"]
        label, color = KIND.get(s["kind"], ("Stop", [80, 80, 80]))
        rows.append({"n": str(n), "name": p["name"], "lat": p["lat"], "lon": p["lon"], "day": s["day"],
                     "time": (s.get("time") or "").split(" - ")[0], "kind": label, "color": color,
                     "updated": s["id"] in ss.changed_ids})
    pts = pd.DataFrame(rows)
    left, right = st.columns([3, 1.4], gap="medium")
    with left:
        span = max(pts.lat.max() - pts.lat.min(), (pts.lon.max() - pts.lon.min()) * 0.8, 0.01)
        zoom = 13 if span < 0.03 else 12 if span < 0.06 else 11 if span < 0.12 else 10 if span < 0.25 else 9 if span < 0.5 else 8
        paths = [{"path": [[r.lon, r.lat] for r in pts[pts.day == d].itertuples()]} for d in pts.day.unique()]
        st.pydeck_chart(pdk.Deck(
            map_style="light",
            initial_view_state=pdk.ViewState(latitude=float(pts.lat.mean()), longitude=float(pts.lon.mean()), zoom=zoom),
            layers=[
                pdk.Layer("PathLayer", paths, get_path="path", get_color=[60, 60, 60, 140], width_min_pixels=3),
                pdk.Layer("ScatterplotLayer", pts, get_position="[lon, lat]", get_fill_color="color", get_radius=60,
                          radius_min_pixels=13, radius_max_pixels=18, stroked=True, get_line_color=[255, 255, 255],
                          line_width_min_pixels=2, pickable=True),
                pdk.Layer("TextLayer", pts, get_position="[lon, lat]", get_text="n", get_size=13,
                          get_color=[255, 255, 255], font_weight=700, get_alignment_baseline="'center'"),
            ],
            tooltip={"html": "<b>{n}. {name}</b><br/>{time} · {kind}", "style": {"fontSize": "13px"}}),
            height=560)
        st.caption("   ".join(f":{c}-badge[{k}]" for k, c in (("Activity", "blue"), ("Food", "orange"), ("Night out", "violet"), ("Stay", "green"))))
    with right:
        for n, s in shown:
            p = s["place"]
            with card():
                upd = " :blue-badge[Updated]" if s["id"] in ss.changed_ids else ""
                st.markdown(md(f"**{n}. {p['name']}**{upd}"))
                st.caption(md(f"{(s.get('time') or '').split(' - ')[0]} · {KIND.get(s['kind'], ('Stop',))[0]} · {p.get('city', '')}"))
                web = web_links(p, s["kind"])  # official site (live) or a search for it; menu for restaurants and bars
                site = web.get("Website") or web.get("Find website")
                b1, b2 = st.columns(2)
                if safe_url(site):
                    b1.link_button("Website", safe_url(site), icon=I["web"], use_container_width=True,
                                   help="Official website" if web.get("Website") else "Finds the official website")
                if safe_url(web.get("Menu")):
                    b2.link_button("Menu", safe_url(web["Menu"]), icon=I["menu"], use_container_width=True,
                                   help="See this restaurant's menu")
                d = (s.get("booking") or {}).get("links", {}).get("Directions")
                if safe_url(d):  # beside Website for activities and hotels, on its own row under Website + Menu for food
                    (st if web.get("Menu") else b2).link_button("Directions", safe_url(d), icon=I["directions"],
                                                                use_container_width=True)
                if st.button("Closed for good? Replace it", key=f"mcl{n}", type="tertiary", icon=I["warn"],
                             help="If this place has shut down, I'll replace it and never recommend it again"):
                    act(f"stop {n} is permanently closed", f"Replaced stop {n} - it won't be recommended again")
        bk = st_.get("booking") or {}
        if safe_url(bk.get("route_url")):
            st.link_button("Whole route in Google Maps", safe_url(bk["route_url"]), icon=I["route"], type="primary", use_container_width=True)


def page_group():
    st.header("Plan together")
    st.caption("Share your plan with a code. Everyone who joins sees the same itinerary, can change it by chat, and can vote "
               "on each stop. Changes show up for everyone within about 10 seconds.")
    who = st.text_input("Your name (shown to your group)", value="" if ss.planner.display_name == "someone" else ss.planner.display_name,
                        max_chars=30, placeholder="e.g. Jordan")
    if who:
        ss.planner.display_name = clean_name(who)
    st_ = plan_state()
    if ss.planner.shared_code:
        with card():
            st.markdown(f"Your share code: **`{ss.planner.shared_code}`**")
            st.caption("Send this code to your group. Sharing includes the itinerary and the chat - never your key, PIN or saved preferences.")
            c1, c2, _ = st.columns([1, 1, 2])
            if c1.button("Check for updates", icon=I["sync"], use_container_width=True):
                before = plan_state()
                if ss.planner.sync():
                    note_changes(before, plan_state(), clean_name((shared.load(ss.planner.shared_code) or {}).get("editor")))
                    st.toast("Got the latest version", icon=I["check"])
                else:
                    st.toast("You're up to date", icon=I["check"])
                st.rerun()
            if c2.button("Stop sharing here", use_container_width=True):
                ss.planner.leave()
                st.rerun()
            rec = shared.load(ss.planner.shared_code) or {}
            if rec.get("log"):
                st.markdown("**Recent activity**")
                for e in reversed(rec["log"][-5:]):
                    st.caption(md(f"{clean_name(e['who'])} · {ago(e['t'])}: {e['what'][:60]}"))
    else:
        c1, c2 = st.columns(2)
        with c1, card():
            st.markdown("**Share my plan**")
            st.caption("Creates a code your friends can join.")
            if st.button("Create a share code", icon=I["share"], type="primary", use_container_width=True, disabled=not st_):
                ss.planner.share(who or None)
                st.rerun()
            if not st_:
                st.caption("Make a plan first.")
        with c2, card():
            st.markdown("**Join a friend's plan**")
            code = st.text_input("Share code", placeholder="SAG-XXXXXXXX", max_chars=12, label_visibility="collapsed")
            if st.button("Join", use_container_width=True, disabled=not code):
                if not ss.join_limit.allow():
                    st.error(f"Too many attempts - try again in {ss.join_limit.retry_in()} s.")
                elif ss.planner.join(code.strip(), who or None):
                    note_changes(None, plan_state(), "Your group")
                    st.toast(f"Joined {code.strip().upper()}", icon=I["check"])
                    ss["_goto"] = "itinerary"
                else:
                    st.error("No shared plan with that code - check it and try again.")

    if st_:
        st.divider()
        st.subheader("Vote on the stops")
        code = ss.planner.shared_code
        ballot = (shared.load(code) or {}).get("votes", {}) if code else None
        st.caption("Pick your favorite for each stop" + (" - one vote per person; everyone sees the same totals." if code
                                                         else ". Share the plan so friends can vote from their own phones."))
        for n, s in enumerate([s for s in st_["slots"] if s.get("place")], 1):
            opts = [s["place"]] + list(s.get("alternatives") or [])[:3]
            counts = ({o["id"]: len(ballot.get(s["id"], {}).get(o["id"], [])) for o in opts} if ballot is not None
                      else ss.votes.setdefault(s["id"], {o["id"]: 0 for o in opts}))
            st.markdown(md(f"**{n}. {s['label']}**"))
            cols = st.columns(len(opts))
            for col, o in zip(cols, opts):
                if col.button(f"{o['name'][:24]} · {counts.get(o['id'], 0)}", key=f"v{s['id']}{o['id']}", icon=I["vote"], use_container_width=True):
                    if ballot is not None:
                        shared.vote(code, s["id"], o["id"], ss.planner.display_name or "someone")
                    else:
                        counts[o["id"]] = counts.get(o["id"], 0) + 1
                    st.rerun()
        if st.button("Use the winners", icon=I["check"], type="primary"):
            tallies = ({sid: {pid: len(v) for pid, v in stop.items()} for sid, stop in ((shared.load(code) or {}).get("votes", {})).items()}
                       if code else ss.votes)
            winners = {sid: max(t, key=t.get) for sid, t in tallies.items() if t and max(t.values()) > 0}
            before = plan_state()
            ss.planner.apply_votes(winners)
            note_changes(before, plan_state(), "Group vote")
            ss.votes = {}
            st.toast("Applied the group's votes", icon=I["check"])
            ss["_goto"] = "itinerary"


def safe(fn):
    """Run a page; log any unexpected error (redacted) and show a friendly message instead of a blank page."""
    def run_page():
        t0 = time.time()
        try:
            fn()
            if time.time() - t0 > 2:
                logging.getLogger("app").warning("slow page %s: %.1fs", fn.__name__, time.time() - t0)
        except Exception:  # Streamlit's own rerun/stop signals are BaseException and pass through
            import traceback
            logging.getLogger("app").error("page %s failed: %s", fn.__name__, redact(traceback.format_exc()))
            st.error("Sorry - this page hit a problem. Your plan is safe; try again or open the Plan page.", icon=I["warn"])
    run_page.__name__ = fn.__name__
    return run_page


TRIP_TILES = [("#F1C9A8", "#46301F"), ("#D8E6D9", "#233127"), ("#E7DEC6", "#36311F"), ("#F4D9C8", "#3B2B22")]


def page_trips():
    trips = ss.planner.trips
    remembered = ss.planner.profile.get("memory_on")
    h = st.columns([5, 1.3], vertical_alignment="bottom")
    h[0].title("My trips", anchor=False)
    h[0].caption("Starting a new trip never deletes an old one. "
                 + ("Saved to your remembered profile, so they're here next time too." if remembered else
                    "Turn on **Remember me** in Preferences to keep them for next time."))
    if h[1].button("Plan a new trip", type="primary", use_container_width=True):
        start_new_trip()
    if not trips:
        empty_state("No trips yet. Plan one on the Plan page and it shows up here.")
        return
    for row in range(0, len(trips), 3):
        cols = st.columns(3)
        for k, (col, t) in enumerate(zip(cols, trips[row:row + 3])):
            current = t["id"] == ss.planner.trip_id
            light, dark = TRIP_TILES[(row + k) % len(TRIP_TILES)]
            party = t.get("party", 2)
            line1 = " · ".join(x for x in [t.get("when") or "", f"{t['days']} days" if t.get("days", 1) > 1 else "",
                                           f"{party} {'people' if party > 1 else 'person'}"] if x)
            line2 = " · ".join(x for x in [f"{t.get('stops', 0)} stops",
                                           f"about ${t['cost_pp']:.0f} per person" if t.get("cost_pp") else ""] if x)
            with col, st.container(key=f"tripcard-{t['id']}" + ("-current" if current else "")):
                st.html(f'<div class="az-trip-art" style="--l:{light};--d:{dark}">'
                        + ('<span class="az-trip-badge">Current trip</span>' if current else "") + "</div>"
                        f'<div class="az-trip-text"><b>{esc(t.get("city") or "Trip")}</b><span>{esc(line1)}</span>'
                        f'<span>{esc(line2)}</span>'
                        + (f'<span class="az-trip-hl">{esc(snip(", ".join(t.get("highlights") or []), 70))}</span>'
                           if t.get("highlights") else "") + "</div>")
                b = st.columns([2.4, 1], vertical_alignment="center")
                if current:
                    if b[0].button("View", key=f"tv-{t['id']}", type="primary", use_container_width=True):
                        ss["_goto"] = "plan"
                    if b[1].button("Share", key=f"ts-{t['id']}", use_container_width=True):
                        ss["_goto"] = "group"
                else:
                    if b[0].button("Open", key=f"topen-{t['id']}", use_container_width=True,
                                   help="Make this the current trip (your current one stays saved here)"):
                        if ss.planner.open_trip(t["id"]):
                            ss.chat = [{"role": "assistant", "content": f"Opened your **{t.get('city')}** trip ({t.get('when')}). "
                                                                        "Ask me to change anything."}]
                            ss.votes, ss.changes, ss.changed_ids, ss.unseen, ss.updated_at = {}, [], set(), 0, time.time()
                            ss.pop("day_pick", None)
                            ss["_goto"] = "plan"
                    with b[1].popover("Delete", use_container_width=True):
                        st.caption("Delete this trip for good?")
                        if st.button("Yes, delete it", key=f"td-{t['id']}", type="primary"):
                            ss.planner.delete_trip(t["id"])
                            st.rerun()


# ================================================================== navigation (titles stay fixed)
plan_page = st.Page(safe(page_plan), title="Plan", icon=":material/explore:", default=True)
map_page = st.Page(safe(page_map), title="Map", icon=I["map"], url_path="map")
trips_page = st.Page(safe(page_trips), title="My trips", icon=I["trips"], url_path="trips")
group_page = st.Page(safe(page_group), title="Group", icon=I["group"], url_path="group")
st.logo(str(Path(__file__).parent / "assets" / "wordmark.png"), size="large")
nav = st.navigation([plan_page, map_page, trips_page, group_page], position="top")

top_bar()
auto_sync()
nav.run()
ss["_ready"] = True  # first full page shown: later runs say "Working..." instead of "Connecting..."
if time.time() - _T0 > 2:
    logging.getLogger("app").warning("slow run (%s): %.1fs", nav.url_path or "chat", time.time() - _T0)
goto(ss.pop("_goto", None))  # page switches happen here, outside any form or column
