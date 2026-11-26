"""Funding radar: RSS -> keyword filter -> LLM extraction -> companies table."""
import json
import re
from datetime import datetime, timedelta, timezone

import feedparser
from bs4 import BeautifulSoup

from .core import CFG, LLMError, db, discord, get, llm, log, now_ist

FUNDING_RE = re.compile(
    r"\b(raises?|raised|bags?|secures?|funding|seed|pre-seed|series [a-d]|pre-series|"
    r"crore|cr\b|\$\s?\d|million|mn\b|led by|backed)\b", re.I)
NOISE_RE = re.compile(r"\b(ipo|layoffs?|shuts? down|acquires|acquired|weekly funding|funding report|"
                      r"this week|roundup|top \d+)\b", re.I)


def article_text(url, limit=4000):
    if "news.google.com" in url:
        return ""
    try:
        r = get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "aside"]):
            t.decompose()
        paras = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
        links = [a["href"] for a in soup.find_all("a", href=True)
                 if a["href"].startswith("http") and not any(
                     s in a["href"] for s in ("inc42", "entrackr", "yourstory", "techcrunch", "twitter.com",
                                              "x.com", "facebook", "linkedin.com/share", "whatsapp", "google"))]
        return paras[:limit] + "\nOUTBOUND_LINKS: " + " ".join(links[:15])
    except Exception:
        return ""


EXTRACT_PROMPT = """From this startup news item, extract the funding event. Return JSON:
{{
 "is_single_company_funding": bool,   // false for roundups, IPOs, acquisitions, govt news
 "company": str, "website": str|null,  // website ONLY if present in text/links, else null
 "round": str, "amount": str, "investors": [str], "sector": str, "location": str,
 "what_they_do": str,                  // one plain sentence
 "founders": [{{"name": str, "role": str}}],  // only names actually in the text
 "relevance": int  // 0-10: is this a TECH PRODUCT company likely to hire {roles}? 10 = AI/devtools/fintech/SaaS with eng team in India, 0 = non-tech
}}
Preferred geos: {geos}. Skip-sectors (score <=2): {skip}.

TITLE: {title}
SUMMARY: {summary}
ARTICLE: {body}"""


def run():
    con = db()
    rc = CFG["radar"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=rc["lookback_days"])
    fresh = []
    for feed in rc["feeds"]:
        try:
            parsed = feedparser.parse(get(feed).content)
        except Exception as e:
            log(f"feed fail {feed[:60]}: {e}"); continue
        for e in parsed.entries[:60]:
            title = e.get("title", "")
            url = e.get("link", "")
            if not url or not FUNDING_RE.search(title) or NOISE_RE.search(title):
                continue
            pub = e.get("published_parsed") or e.get("updated_parsed")
            if pub and datetime(*pub[:6], tzinfo=timezone.utc) < cutoff:
                continue
            if con.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
                continue
            con.execute("INSERT INTO articles(url,title,published,seen_at) VALUES(?,?,?,?)",
                        (url, title, e.get("published", ""), now_ist().isoformat()))
            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True)
            fresh.append((url, title, summary[:1500]))
    con.commit()
    log(f"radar: {len(fresh)} new funding-looking articles")
    seen = {u for u, _, _ in fresh}
    for row in con.execute("SELECT url, title FROM articles WHERE processed=0 ORDER BY seen_at DESC LIMIT 150"):
        if row["url"] not in seen:
            fresh.append((row["url"], row["title"], ""))

    added, fails = [], 0
    for url, title, summary in fresh:
        try:
            d = llm(EXTRACT_PROMPT.format(
                roles=CFG["me"]["roles_wanted"], geos=", ".join(rc["geos_preferred"]),
                skip=", ".join(rc["skip_sectors"]), title=title, summary=summary,
                body=article_text(url)), as_json=True, temperature=0)
        except LLMError as e:
            fails += 1
            log(f"extract fail: {e}")
            if fails >= 3:
                discord(f"⚠️ radar stopped: LLM keeps failing.\n```{str(e)[:900]}```")
                log("radar: stopping after 3 LLM failures, unprocessed articles retry next run")
                break
            continue
        except Exception as e:
            log(f"extract fail: {e}"); continue
        fails = 0
        con.execute("UPDATE articles SET processed=1 WHERE url=?", (url,))
        name = (d.get("company") or "").strip()
        if not d.get("is_single_company_funding") or not name:
            continue
        if con.execute("SELECT 1 FROM companies WHERE name=?", (name,)).fetchone():
            continue  # same raise reported by multiple outlets
        rel = int(d.get("relevance") or 0)
        status = "new" if rel >= rc["min_relevance"] else "skipped"
        con.execute("""INSERT INTO companies(name,website,round,amount,investors,sector,location,what_they_do,
                       founders,relevance,article_url,article_title,status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (name, d.get("website"), d.get("round"), d.get("amount"),
                     ", ".join(d.get("investors") or []), d.get("sector"), d.get("location"),
                     d.get("what_they_do"), json.dumps(d.get("founders") or []), rel, url, title, status,
                     now_ist().isoformat()))
        con.commit()
        if status == "new":
            added.append(f"• **{name}** — {d.get('round')} {d.get('amount')} | {d.get('sector')} | rel {rel}/10")
    if added:
        discord("📡 **Funding radar** — new targets:\n" + "\n".join(added))
    log(f"radar: {len(added)} relevant companies added")
    return added
