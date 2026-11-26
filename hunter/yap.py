"""Yap agent: real daily context -> post in your voice -> Typefully (X / LinkedIn)."""
import random
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .core import CFG, DRY_RUN, ai_tells, db, discord, env, get, llm, log, now_ist

Y = CFG["yap"]


# ---------------- context gathering ----------------
def github_activity(hours=36):
    user = Y["github_user"]
    h = {"Accept": "application/vnd.github+json"}
    if env("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {env('GITHUB_TOKEN')}"
    try:
        events = requests.get(f"https://api.github.com/users/{user}/events?per_page=50", headers=h, timeout=20).json()
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for e in events if isinstance(events, list) else []:
        if datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")) < cutoff:
            continue
        repo = e["repo"]["name"].split("/")[-1]
        if e["type"] == "PushEvent":
            for c in e["payload"].get("commits", [])[:5]:
                out.append(f"[{repo}] commit: {c['message'].splitlines()[0]}")
        elif e["type"] in ("CreateEvent", "ReleaseEvent", "PullRequestEvent"):
            out.append(f"[{repo}] {e['type']}")
    return out[:15]


def hn_top(n=12):
    try:
        ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=15).json()[:n]
        items = [requests.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=10).json() for i in ids]
        return [f"HN: {it['title']} ({it.get('score', 0)} pts)" for it in items if it]
    except Exception:
        return []


def tech_news(n=10):
    url = ("https://news.google.com/rss/search?q=AI+OR+startup+OR+developer+when:1d"
           "&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        return [f"NEWS: {e.title}" for e in feedparser.parse(get(url).content).entries[:n]]
    except Exception:
        return []


def recent_raises(n=5):
    rows = db().execute("""SELECT name, round, amount, what_they_do FROM companies
                           WHERE relevance>=6 ORDER BY id DESC LIMIT ?""", (n,)).fetchall()
    return [f"RAISE: {r['name']} raised {r['amount']} ({r['round']}) — {r['what_they_do']}" for r in rows]


def recent_posts(n=12):
    return [r["text"] for r in db().execute("SELECT text FROM posts ORDER BY id DESC LIMIT ?", (n,)).fetchall()]


# ---------------- writing ----------------
KIND_GUIDE = {
    "build_in_public": "a build-in-public update based ONLY on the real commits below. what you built/fixed, "
                       "one honest detail (a bug, a tradeoff). if no commits, return skip.",
    "news_take": "a genuine opinion on ONE of today's news/HN items, from the angle of a backend/data engineer. "
                 "take a side. no summarising the headline.",
    "funding_take": "a take on ONE startup that just raised: what's technically hard/interesting about what they "
                    "build. no tagging, no 'congrats', no begging for a job.",
    "learning": "one specific thing learned (DSA pattern, system design idea, DB internals, something from "
                "internships). concrete, like a note to self.",
    "project_showcase": "show one of my projects with its link: one problem it solves + one real technical detail. "
                        "not salesy.",
}

SYSTEM = """You ghostwrite posts for a specific person, matching their voice exactly from samples.
Hard rules:
- sound like a real 21-yo engineer typing on his phone. short lines. lowercase is fine. natural.
- NO hashtags, max 1 emoji (usually zero), no em dashes, no "here's the thing", no "let that sink in",
  no thread-bait like "a thread 🧵", no rhetorical question openers, no listicles.
- NEVER invent facts about the person (metrics, users, jobs, offers) — only use what's in the context.
- under 260 characters for X unless the idea truly needs 2 short tweets.
- vary structure vs. recent posts; don't repeat their openings.
Return JSON {"skip": bool, "x": str, "linkedin": str}. linkedin = same idea, 3-6 short lines, slightly fuller."""


def pick_kind(has_commits):
    mix = dict(Y["post_mix"])
    if not has_commits:
        mix.pop("build_in_public", None)
    today = [r["kind"] for r in db().execute(
        "SELECT kind FROM posts WHERE created_at LIKE ?", (now_ist().date().isoformat() + "%",))]
    for k in today:
        mix.pop(k, None)  # different kind each post of the day
    mix = mix or dict(Y["post_mix"])
    return random.choices(list(mix), weights=list(mix.values()))[0]


def write_post(kind=None):
    commits = github_activity()
    kind = kind or pick_kind(bool(commits))
    ctx = {
        "build_in_public": commits,
        "news_take": hn_top() + tech_news(),
        "funding_take": recent_raises(),
        "learning": [],
        "project_showcase": [f"{p['name']} {p['url']}: {p['pitch']}" for p in CFG["proof_of_work"]],
    }[kind]
    profile = (f"{CFG['me']['name']}: {CFG['me']['one_liner']}. "
               f"Experience: {'; '.join(CFG['me']['experience'])}. Projects: "
               + "; ".join(f"{p['name']} ({p['pitch']})" for p in CFG["proof_of_work"]))
    prompt = f"""TODAY: {now_ist():%A %d %b %Y}
PERSON: {profile}
LANGUAGE/VIBE: {Y['language']}
VOICE SAMPLES (match this):
{chr(10).join('- ' + s for s in Y['voice_samples'])}
RECENT POSTS (don't repeat):
{chr(10).join('- ' + s for s in recent_posts()) or '(none)'}

TASK: write {KIND_GUIDE[kind]}
CONTEXT:
{chr(10).join(ctx) or '(none — use the person profile only)'}"""
    for _ in range(3):
        out = llm(prompt, system=SYSTEM, as_json=True, temperature=0.95)
        if out.get("skip"):
            return kind, None
        x = (out.get("x") or "").strip().strip('"')
        if x and not ai_tells(x) and "#" not in x:
            return kind, {"x": x, "linkedin": (out.get("linkedin") or x).strip()}
    return kind, None


def split_thread(text, limit=280):
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(cur) + len(para) + 2 <= limit:
            cur = f"{cur}\n\n{para}".strip()
        else:
            if cur:
                parts.append(cur)
            cur = para[:limit]
    return parts + ([cur] if cur else [])


def push_typefully(post):
    platforms = {}
    if "x" in Y["platforms"]:
        platforms["x"] = {"enabled": True, "posts": [{"text": t} for t in split_thread(post["x"])]}
    if "linkedin" in Y["platforms"]:
        platforms["linkedin"] = {"enabled": True, "posts": [{"text": post["linkedin"]}]}
    body = {"platforms": platforms, "draft_title": f"yap {now_ist():%d %b %H:%M}"}
    if Y["mode"] == "schedule":
        body["publish_at"] = "next-free-slot"
    r = requests.post(f"https://api.typefully.com/v2/social-sets/{env('TYPEFULLY_SOCIAL_SET_ID')}/drafts",
                      json=body, headers={"Authorization": f"Bearer {env('TYPEFULLY_API_KEY')}"}, timeout=30)
    r.raise_for_status()
    return str(r.json().get("id", ""))


def run(n=None, kind=None):
    n = n or 1
    made = 0
    for _ in range(n):
        k, post = write_post(kind)
        if not post:
            log(f"yap: skipped ({k})"); continue
        tid = ""
        if not DRY_RUN and env("TYPEFULLY_API_KEY"):
            try:
                tid = push_typefully(post)
            except Exception as e:
                discord(f"⚠️ Typefully push failed: {e}"); log(f"typefully fail: {e}"); continue
        con = db()
        con.execute("INSERT INTO posts(kind,text,typefully_id,created_at) VALUES(?,?,?,?)",
                    (k, post["x"], tid, now_ist().isoformat()))
        con.commit()
        where = "🧪 DRY" if DRY_RUN else ("🗓️ scheduled" if Y["mode"] == "schedule" else "📝 drafted")
        discord(f"🗣️ **yap** ({k}) {where}:\n>>> {post['x']}")
        log(f"yap [{k}]: {post['x']}")
        made += 1
    return made


# ---------------- LinkedIn: draft only, you post it yourself ----------------
LI_SYSTEM = """You draft LinkedIn posts for a final-year CS student engineer who posts them himself.
Voice: plain, specific, confident, zero cringe. Like an engineer explaining something real to peers.
Rules:
- 120-220 words. Hook in line 1 (a concrete claim, number or problem, not a question).
- Short paragraphs, 1-2 lines each. Max 1 emoji, zero hashtags in the body (up to 3 at the very end is ok).
- No "I'm thrilled", "humbled", "excited to share", "Here's what I learned 👇", "Agree?", no em dashes.
- Teach one concrete technical thing (a trade-off, a bug, a design decision) using ONLY facts in the context.
- NEVER invent metrics, offers, users or achievements.
- End with one line that invites a real reply (e.g. how others handle the same problem), not engagement bait.
Return JSON {"skip": bool, "post": str, "angle": str}"""


def linkedin_draft():
    commits = github_activity(hours=72)
    proofs = "\n".join(f"- {p['name']}: {p['pitch']}" + (f" ({p['url']})" if p.get("url") else "")
                       for p in CFG["proof_of_work"])
    recent = [r["text"] for r in db().execute(
        "SELECT text FROM posts WHERE kind='linkedin' ORDER BY id DESC LIMIT 8").fetchall()]
    prompt = f"""TODAY: {now_ist():%A %d %b %Y}
PERSON: {CFG['me']['one_liner']}
REAL WORK TO DRAW FROM (pick ONE idea, rotate across days):
{proofs}
RECENT COMMITS (last 3 days):
{chr(10).join(commits) or '(none)'}
TODAY'S TECH NEWS (optional hook, only if it genuinely connects to his work):
{chr(10).join((hn_top(8) + tech_news(6))[:12])}
ALREADY POSTED RECENTLY (pick a different idea):
{chr(10).join('- ' + r[:160] for r in recent) or '(none)'}"""
    for _ in range(3):
        out = llm(prompt, system=LI_SYSTEM, as_json=True, temperature=0.85)
        post = (out.get("post") or "").strip()
        if out.get("skip") or not post:
            continue
        if not ai_tells(post):
            con = db()
            con.execute("INSERT INTO posts(kind,text,typefully_id,created_at) VALUES('linkedin',?,'',?)",
                        (post, now_ist().isoformat()))
            con.commit()
            discord(f"💼 **LinkedIn draft for today** ({out.get('angle', '')}) — copy, tweak, post yourself:\n"
                    f"```\n{post}\n```")
            log("linkedin draft sent")
            return post
    log("linkedin: nothing good enough today")
    return None
