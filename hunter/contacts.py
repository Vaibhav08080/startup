"""Find domain + founder/CTO emails for each new company."""
import json
import re
from urllib.parse import urlparse

import dns.resolver
from bs4 import BeautifulSoup
import requests

from . import intel
from .core import CFG, db, env, get, llm, log, now_ist

GENERIC_HOSTS = ("linkedin", "twitter", "x.com", "facebook", "instagram", "crunchbase", "tracxn",
                 "youtube", "medium", "wikipedia", "google", "apple.com", "play.google")
# whole-word patterns: "director" must NOT match "cto"
ROLE_PRIORITY = [(r"\bcto\b", 100), (r"chief technology", 100), (r"\bco-?founder\b", 90), (r"\bfounder\b", 90),
                 (r"\bceo\b", 85), (r"chief executive", 85), (r"vp,? (of )?engineering", 80),
                 (r"head of (engineering|tech|data|platform)", 80), (r"director of (engineering|technology|data)", 75),
                 (r"engineering manager", 70), (r"\b(tech|engineering) lead\b", 65), (r"talent", 50),
                 (r"\bhr\b|human resources", 45), (r"recruit", 45), (r"\bpeople\b", 40)]
BAD_ROLES = re.compile(r"marketing|sales|editor|journalis|growth|finance|legal|accountant|support|"
                       r"customer|operations|\bpr\b|communications|design", re.I)
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


def homepage(domain):
    try:
        r = get(f"https://{domain}", timeout=12)
        if not r.ok:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        desc = " ".join(m.get("content", "") for m in soup.find_all("meta")
                        if m.get("name", "").lower() in ("description", "og:description")
                        or m.get("property", "").lower() == "og:description")
        for t in soup(["script", "style", "nav", "footer"]):
            t.decompose()
        body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:1500]
        return f"TITLE: {title}\nDESCRIPTION: {desc}\nTEXT: {body}"
    except Exception:
        return ""


def confirm_domain(c, domain):
    """Open the site and ask: is this really the startup from the news? India presence?"""
    page = homepage(domain)
    if not page:
        return None
    out = llm(f"""News says: "{c['name']}" — {c['what_they_do']} | sector: {c['sector']} | location: {c['location']} |
headline: {c['article_title']}

Website {domain}:
{page}

Is this website the SAME startup as in the news (not a namesake, not a media site, not an unrelated org)?
Does it show presence in India (Indian office/city, INR pricing, .in domain, India-focused customers or team)?
JSON: {{"same_company": bool, "confidence": 0-100, "india": "yes"|"no"|"unknown", "reason": str}}""",
              as_json=True, temperature=0)
    return out


def resolve_domain(c):
    """Collect candidates, return the first one the site itself confirms. ('' , None) if nothing passes."""
    cands = []
    if domain_of(c["website"]):
        cands.append(domain_of(c["website"]))
    key = env("HUNTER_API_KEY")
    if key:
        try:
            r = requests.get("https://api.hunter.io/v2/domain-search",
                             params={"company": c["name"], "limit": 1, "api_key": key}, timeout=20).json()
            d = domain_of((r.get("data") or {}).get("domain") or "")
            if d:
                cands.append(d)
        except Exception:
            pass
    try:
        guesses = llm(f"""Company: {c['name']} — {c['what_they_do']} ({c['location']}).
List the 5 most likely website domains for this startup (e.g. name.com, name.ai, name.in, getname.com,
tryname.com, name.io). JSON: {{"domains": [..]}}""", as_json=True, temperature=0).get("domains", [])
        cands += [domain_of(g) for g in guesses]
    except Exception:
        pass
    seen = []
    for d in cands:
        d = (d or "").lower()
        if not d or d in seen:
            continue
        seen.append(d)
        if not has_mx(d):
            continue
        v = confirm_domain(c, d)
        if v and v.get("same_company") and int(v.get("confidence") or 0) >= 70:
            log(f"domain ok: {c['name']} -> {d} (india: {v.get('india')})")
            return d, v
        log(f"domain rejected: {c['name']} -> {d} ({(v or {}).get('reason', 'site unreachable')})")
        if len(seen) >= 5:
            break
    return "", None


def score_role(role):
    role = (role or "").lower()
    hit = [sc for pat, sc in ROLE_PRIORITY if re.search(pat, role)]
    if hit:
        return max(hit)
    return 5 if BAD_ROLES.search(role) else 10


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


def hunter_verify(email):
    """valid / accept_all / unknown / invalid. 'invalid' never gets mailed."""
    key = env("HUNTER_API_KEY")
    if not key:
        return "unknown"
    try:
        d = requests.get("https://api.hunter.io/v2/email-verifier",
                         params={"email": email, "api_key": key}, timeout=40).json().get("data") or {}
        return d.get("status") or d.get("result") or "unknown"
    except Exception:
        return "unknown"


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
    good = [p for p in people if score_role(p["role"]) > 5]
    people = good or people
    keep, limit = [], CFG["outreach"]["max_contacts_per_company"]
    for p in people:
        if len(keep) >= limit:
            break
        if p["confidence"] < CFG["outreach"].get("verify_below_confidence", 80):
            status = hunter_verify(p["email"])
            if status == "invalid":
                log(f"verify: drop {p['email']} (invalid)")
                continue
            if status == "valid":
                p["confidence"] = max(p["confidence"], 90)
            p["source"] += f"+{status}"
        keep.append(p)
    return keep


def india_location(loc):
    return bool(re.search(r"india|bengaluru|bangalore|delhi|gurugram|gurgaon|noida|mumbai|pune|hyderabad|"
                          r"chennai|kolkata|ahmedabad|jaipur", loc or "", re.I))


def run(limit=10):
    con = db()
    rows = con.execute("SELECT * FROM companies WHERE status='new' ORDER BY relevance DESC LIMIT ?",
                       (limit,)).fetchall()
    n = 0
    for c in rows:
        domain, verdict = resolve_domain(c)
        if domain and CFG["radar"].get("india_only") and (verdict or {}).get("india") == "no" \
                and not india_location(c["location"]):
            con.execute("UPDATE companies SET status='skipped', domain=? WHERE id=?", (domain, c["id"])); con.commit()
            log(f"contacts: {c['name']} skipped, no India presence"); continue
        excluded = CFG["outreach"].get("exclude_domains", [])
        if domain and any(domain == x or domain.endswith("." + x) for x in excluded):
            con.execute("UPDATE companies SET status='skipped', domain=? WHERE id=?", (domain, c["id"])); con.commit()
            log(f"contacts: {c['name']} excluded ({domain})"); continue
        if domain and con.execute("SELECT 1 FROM companies WHERE domain=? AND id<>?", (domain, c["id"])).fetchone():
            con.execute("UPDATE companies SET status='skipped', domain=? WHERE id=?", (domain, c["id"])); con.commit()
            log(f"contacts: {c['name']} duplicate of an existing company ({domain})"); continue
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
