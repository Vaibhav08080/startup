"""Find domain + founder/CTO emails for each new company."""
import json
import re
from urllib.parse import urlparse

import dns.resolver
import requests

from . import intel
from .core import CFG, db, env, get, llm, log, now_ist

GENERIC_HOSTS = ("linkedin", "twitter", "x.com", "facebook", "instagram", "crunchbase", "tracxn",
                 "youtube", "medium", "wikipedia", "google", "apple.com", "play.google")
ROLE_PRIORITY = [("cto", 100), ("chief technology", 100), ("co-founder", 90), ("cofounder", 90),
                 ("founder", 90), ("ceo", 85), ("vp engineering", 80), ("head of engineering", 80),
                 ("engineering manager", 70), ("tech lead", 65), ("talent", 50), ("hr", 45),
                 ("recruit", 45), ("people", 40)]
GOOD_GENERIC = ("careers", "jobs", "hiring", "talent", "hr", "founders", "team")


def domain_of(url):
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return "" if any(g in host for g in GENERIC_HOSTS) else host


def has_mx(domain):
    try:
        return bool(dns.resolver.resolve(domain, "MX", lifetime=6))
    except Exception:
        return False


def site_mentions(domain, name):
    try:
        r = get(f"https://{domain}", timeout=12)
        key = re.sub(r"[^a-z0-9]", "", name.lower())[:8]
        return r.ok and key in re.sub(r"[^a-z0-9]", "", r.text.lower())
    except Exception:
        return False


def resolve_domain(c):
    d = domain_of(c["website"])
    if d and has_mx(d):
        return d
    # Hunter can map company name -> domain
    key = env("HUNTER_API_KEY")
    if key:
        try:
            r = requests.get("https://api.hunter.io/v2/domain-search",
                             params={"company": c["name"], "limit": 1, "api_key": key}, timeout=20).json()
            d = (r.get("data") or {}).get("domain")
            if d:
                return d
        except Exception:
            pass
    guesses = llm(f"""Company: {c['name']} — {c['what_they_do']} ({c['location']}).
List the 5 most likely website domains for this startup (e.g. name.com, name.ai, name.in, getname.com,
tryname.com, name.io). JSON: {{"domains": [..]}}""", as_json=True, temperature=0).get("domains", [])
    for g in guesses:
        g = domain_of(g)
        if g and has_mx(g) and site_mentions(g, c["name"]):
            return g
    return ""


def score_role(role):
    role = (role or "").lower()
    return max([s for k, s in ROLE_PRIORITY if k in role] or [10])


def hunter_people(domain):
    key = env("HUNTER_API_KEY")
    if not key:
        return []
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "limit": 10, "api_key": key}, timeout=20).json()
    except Exception:
        return []
    out = []
    for e in (r.get("data") or {}).get("emails", []):
        name = " ".join(x for x in [e.get("first_name"), e.get("last_name")] if x)
        out.append({"name": name, "role": e.get("position") or e.get("department") or "",
                    "email": e["value"], "source": "hunter", "confidence": e.get("confidence", 50)})
    return out


def hunter_find(first, last, domain):
    key = env("HUNTER_API_KEY")
    if not key:
        return None
    try:
        r = requests.get("https://api.hunter.io/v2/email-finder",
                         params={"domain": domain, "first_name": first, "last_name": last, "api_key": key},
                         timeout=20).json()
        d = r.get("data") or {}
        return (d.get("email"), d.get("score", 0)) if d.get("email") else None
    except Exception:
        return None


def scrape_emails(domain):
    found = set()
    for path in ["", "/about", "/about-us", "/careers", "/contact", "/team", "/jobs"]:
        try:
            html = get(f"https://{domain}{path}", timeout=10).text
        except Exception:
            continue
        for m in re.findall(r"[A-Za-z0-9._%+-]+@" + re.escape(domain), html):
            found.add(m.lower())
    return list(found)


def people_for(c, domain):
    people = hunter_people(domain)
    known = {p["email"].lower() for p in people}
    for f in json.loads(c["founders"] or "[]"):
        parts = f.get("name", "").split()
        if not parts:
            continue
        first, last = parts[0].lower(), (parts[-1].lower() if len(parts) > 1 else "")
        hit = hunter_find(first, last, domain)
        if hit:
            email, conf, src = hit[0], hit[1], "hunter-finder"
        else:
            # Most early startups use first@domain. Low confidence — bounces get auto-detected.
            email, conf, src = f"{first}@{domain}", 35, "pattern"
        if email.lower() not in known:
            people.append({"name": f["name"], "role": f.get("role") or "Founder",
                           "email": email, "source": src, "confidence": conf})
            known.add(email.lower())
    for e in scrape_emails(domain):
        local = e.split("@")[0]
        if e not in known and any(g in local for g in GOOD_GENERIC):
            people.append({"name": "", "role": "careers inbox", "email": e, "source": "site", "confidence": 60})
    people.sort(key=lambda p: (score_role(p["role"]), p["confidence"]), reverse=True)
    return people[:CFG["outreach"]["max_contacts_per_company"]]


def run(limit=10):
    con = db()
    rows = con.execute("SELECT * FROM companies WHERE status='new' ORDER BY relevance DESC LIMIT ?",
                       (limit,)).fetchall()
    n = 0
    for c in rows:
        domain = resolve_domain(c)
        if not domain or not has_mx(domain):
            con.execute("UPDATE companies SET status='no_contacts' WHERE id=?", (c["id"],)); con.commit()
            log(f"contacts: no domain for {c['name']}"); continue
        ppl = people_for(c, domain)
        try:
            info = intel.analyse(c, domain)
            con.execute("UPDATE companies SET intel=? WHERE id=?", (info, c["id"]))
        except Exception as e:
            log(f"intel fail {c['name']}: {e}")
        for p in ppl:
            con.execute("""INSERT OR IGNORE INTO contacts(company_id,name,role,email,source,confidence,created_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (c["id"], p["name"], p["role"], p["email"], p["source"], p["confidence"],
                         now_ist().isoformat()))
        con.execute("UPDATE companies SET domain=?, status=? WHERE id=?",
                    (domain, "enriched" if ppl else "no_contacts", c["id"]))
        con.commit()
        n += len(ppl)
        log(f"contacts: {c['name']} ({domain}) -> {[p['email'] for p in ppl]}")
    return n
