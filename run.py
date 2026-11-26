#!/usr/bin/env python3
"""startup-hunter CLI.

  python run.py radar        # pull funding news -> companies
  python run.py contacts     # find founder/CTO emails
  python run.py outreach     # send initial emails + follow-ups (respects caps/window)
  python run.py inbox        # detect replies/bounces, stop sequences
  python run.py hourly       # inbox -> radar (every 6h) -> contacts -> outreach  (put THIS in cron)
  python run.py status       # pipeline numbers
  python run.py go-live      # after DRY_RUN=false: requeue dry-run contacts
  python run.py llmtest      # check the LLM key/model works
"""
import sys

from hunter import contacts, outreach, radar
from datetime import datetime, timedelta

from hunter.core import DRY_RUN, db, discord, kv_get, kv_set, log, now_ist


def status():
    con = db()
    q = lambda s: con.execute(s).fetchall()
    co = dict(q("SELECT status, COUNT(*) FROM companies GROUP BY status"))
    ct = dict(q("SELECT state, COUNT(*) FROM contacts GROUP BY state"))
    msg = (f"📊 **startup-hunter** {'(DRY RUN)' if DRY_RUN else ''}\ncompanies: {co}\ncontacts: {ct}")
    print(msg)
    return msg


def radar_if_due(hours=6):
    last = kv_get("radar_last")
    if last and datetime.fromisoformat(last) > now_ist() - timedelta(hours=hours):
        return
    radar.run()
    kv_set("radar_last", now_ist().isoformat())


def hourly():
    for name, fn in [("inbox", outreach.check_inbox),
                     ("radar", radar_if_due),
                     ("contacts", contacts.run), ("outreach", outreach.run)]:
        try:
            fn()
        except Exception as e:
            log(f"{name} crashed: {e}"); discord(f"⚠️ {name} crashed: {e}")
    today = now_ist().date().isoformat()
    if now_ist().hour >= 21 and kv_get("summary_day") != today:
        discord(status())
        kv_set("summary_day", today)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    {
        "radar": radar.run,
        "contacts": contacts.run,
        "outreach": outreach.run,
        "inbox": outreach.check_inbox,
        "hourly": hourly,
        "status": status,
        "go-live": outreach.go_live_reset,
        "llmtest": lambda: print("LLM OK:", __import__("hunter.core", fromlist=["llm"]).llm("Reply with the word pong.")),
    }[cmd]()
