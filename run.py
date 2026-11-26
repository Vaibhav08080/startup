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


def migrate():
    """One-time fixes to data collected by older versions."""
    con = db()
    if kv_get("migr_v2") != "done":
        # v1 had a role bug ("director" matched "cto") and unverified domains: redo contact finding
        n = con.execute("DELETE FROM contacts WHERE state='queued'").rowcount
        con.execute("UPDATE companies SET status='new', domain=NULL, intel=NULL "
                    "WHERE status IN ('enriched','no_contacts') AND id NOT IN "
                    "(SELECT company_id FROM contacts)")
        con.execute("UPDATE companies SET status='skipped' WHERE status='new' AND ("
                    "lower(name) IN ('undisclosed','unknown','sifted','stealth') OR "
                    "(location IS NOT NULL AND location<>'' AND lower(location) NOT LIKE '%india%' AND "
                    "lower(location) NOT LIKE '%bengaluru%' AND lower(location) NOT LIKE '%bangalore%' AND "
                    "lower(location) NOT LIKE '%delhi%' AND lower(location) NOT LIKE '%mumbai%' AND "
                    "lower(location) NOT LIKE '%gurugram%' AND lower(location) NOT LIKE '%noida%' AND "
                    "lower(location) NOT LIKE '%pune%' AND lower(location) NOT LIKE '%hyderabad%' AND "
                    "lower(location) NOT LIKE '%chennai%' AND lower(location) NOT LIKE '%remote%'))")
        con.commit()
        kv_set("migr_v2", "done")
        log(f"migration v2: dropped {n} unverified contacts, companies re-queued for verified lookup")


def migrate_v3():
    if kv_get("migr_v3") != "done":
        con = db()
        n = con.execute("DELETE FROM contacts WHERE state='queued' AND (email LIKE 'dpo@%' OR email LIKE 'privacy@%' "
                        "OR email LIKE 'legal@%' OR email LIKE 'support@%')").rowcount
        con.commit(); kv_set("migr_v3", "done"); log(f"migration v3: dropped {n} junk inboxes")


def radar_if_due(hours=6):
    last = kv_get("radar_last")
    if last and datetime.fromisoformat(last) > now_ist() - timedelta(hours=hours):
        radar.backlog()
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
    migrate()
    migrate_v3()
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
