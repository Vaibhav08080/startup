"""Read the startup's own site so the email talks about THEIR real problems."""
import json
import re

from bs4 import BeautifulSoup

from .core import get, llm

PAGES = ["", "/about", "/about-us", "/careers", "/jobs", "/product", "/docs", "/engineering", "/blog"]


def site_text(domain, per_page=1500, total=7000):
    chunks = []
    for path in PAGES:
        try:
            r = get(f"https://{domain}{path}", timeout=10)
            if not r.ok or len(r.text) < 500:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["script", "style", "nav", "footer", "svg"]):
                t.decompose()
            txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            if txt and txt[:200] not in "".join(chunks):
                chunks.append(f"[{path or '/'}] {txt[:per_page]}")
        except Exception:
            continue
        if sum(map(len, chunks)) > total:
            break
    return "\n".join(chunks)[:total]


PROMPT = """You are a senior backend engineer sizing up a startup from its own website + funding news.
Return JSON:
{{
 "product": str,                 // what they actually sell, concrete, 1 sentence
 "customers": str,               // who uses it
 "likely_stack": [str],          // only if hinted on site/job posts, else []
 "open_roles": [str],            // engineering roles visible on careers page, else []
 "eng_challenges": [str],        // 3 SPECIFIC backend/data/infra problems this product must solve at scale.
                                 // e.g. "ingesting bank statements from 50 formats reliably", not "scalability"
 "hook": str                     // one specific, true, POSITIVE or neutral observation a founder would be glad someone noticed
                                 // (an interesting product choice, a hard problem they took on, a customer win). NEVER criticise
                                 // their website, copy, marketing, sales process or product.
}}
Only use facts present below. If the site text is empty, infer challenges from the product description.

COMPANY: {name} — {what} ({sector})
NEWS: {news}
SITE:
{site}"""


def analyse(company, domain):
    site = site_text(domain)
    out = llm(PROMPT.format(name=company["name"], what=company["what_they_do"], sector=company["sector"],
                            news=company["article_title"], site=site or "(empty)"),
              as_json=True, temperature=0.2)
    return json.dumps(out)
