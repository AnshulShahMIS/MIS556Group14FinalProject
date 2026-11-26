"""Send pre-trip checklist reminders (run once a day, e.g. by .github/workflows/reminders.yml at 8 am Arizona time).

For every account, for every upcoming trip with reminders turned on: email the checklist items that are due by today,
not ticked off and not emailed before - one email per trip. Then remember which items were sent, so nobody gets the
same reminder twice.

Uses the same storage and email settings as the app (AZ_STORE_REPO / AZ_STORE_TOKEN, AZ_SMTP_USER / AZ_SMTP_PASSWORD).
    py scripts/send_reminders.py            # send what's due today
    py scripts/send_reminders.py --dry-run  # only list what would be sent
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azmulti import checklist, mailer  # noqa: E402
from azmulti.store import get_store  # noqa: E402

log = logging.getLogger("reminders")


def run(today: date | None = None, dry_run: bool = False) -> list[dict]:
    """Returns what was (or would be) sent: [{"to_domain", "trip", "items"}]."""
    today = today or checklist.today_in_arizona()
    store = get_store()
    owners = {}  # account id -> (email, name)
    for key in store.list("users"):
        rec = store.get(key) or {}
        if rec.get("id") and rec.get("email"):
            owners[rec["id"]] = (rec["email"], rec.get("name") or "")
    sent = []
    for key in store.list("trips"):
        account_id = key.split("/")[-1][:-5]
        if account_id not in owners:
            continue
        trips = store.get(key) or []
        changed = False
        for trip in trips:
            start = trip.get("start")
            if not trip.get("reminders") or not start or date.fromisoformat(str(start)[:10]) < today:
                continue
            due = checklist.due_for_reminder(trip.get("checklist") or [], trip.get("checklist_done"),
                                             trip.get("reminded"), today)
            if not due:
                continue
            email, name = owners[account_id]
            sent.append({"to_domain": email.split("@")[-1], "trip": trip.get("city"), "items": [i["text"] for i in due]})
            if dry_run:
                continue
            subject, text, html = checklist.email(trip, due, name, today, reminder=True)
            ok, msg = mailer.send(email, subject, text, html)
            if ok:
                trip["reminded"] = sorted(set(trip.get("reminded") or []) | {i["id"] for i in due})
                changed = True
            else:
                log.warning("reminder for a %s trip not sent: %s", trip.get("city"), msg)
        if changed:
            store.put(key, trips)
    if hasattr(store, "flush"):
        store.flush(60)
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="only list what would be sent")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not mailer.configured() and not args.dry_run:
        log.info("Email isn't set up (AZ_SMTP_USER / AZ_SMTP_PASSWORD) - reminders will be saved to the outbox only.")
    out = run(dry_run=args.dry_run)
    for s in out:  # no full addresses in the log
        log.info("%s reminder to someone at %s about %s: %s", "Would send" if args.dry_run else "Sent", s["to_domain"],
                 s["trip"], "; ".join(s["items"]))
    log.info("%d reminder email(s) %s.", len(out), "to send" if args.dry_run else "sent")


if __name__ == "__main__":
    main()
