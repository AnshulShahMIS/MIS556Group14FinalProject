"""The itinerary PDF ("Save as PDF" and the download after Confirm trip)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AZ_OFFLINE", "1")

from azmulti import checklist  # noqa: E402
from azmulti.graph import Planner  # noqa: E402
from azmulti.pdfdoc import _t, itinerary_pdf  # noqa: E402


def test_pdf_has_the_trip():
    p = Planner()
    p.ask("3 days in Tucson starting 2027-03-12 for 4 people, museums and hiking, we need a rental car")
    st = p.state()
    data = itinerary_pdf(st, "tester", checklist.build(st))
    assert data[:5] == b"%PDF-" and len(data) > 3000
    import zlib, re  # noqa: E401
    text = b"".join(zlib.decompress(m) for m in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S)
                    if m[:1] == b"x").decode("latin-1")
    for word in ("Tucson", "Day 3", "Rental car", "Before you go", "Total per person"):
        assert word in text, word
    first = next(s for s in st["slots"] if s.get("place"))["place"]["name"]
    assert _t(first)[:10] in text


def test_text_the_fonts_cannot_draw_is_swapped():
    assert _t("Phoenix → Sedona ★4.5 – café ✨") == "Phoenix to Sedona *4.5 - café "
