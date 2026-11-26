"""Shared stuff: config, env, sqlite, LLM, Discord, http."""
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
IST = timezone(timedelta(hours=5, minutes=30))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Safari/537.36"}


def env(key, default=""):
    return os.getenv(key, default).strip()


def now_ist():
    return datetime.now(IST)


def log(msg):
    print(f"[{now_ist():%H:%M:%S}] {msg}", flush=True)


# ---------------- DB ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  url TEXT PRIMARY KEY, title TEXT, published TEXT, seen_at TEXT, processed INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE COLLATE NOCASE, domain TEXT, website TEXT,
  round TEXT, amount TEXT, investors TEXT, sector TEXT, location TEXT,
  what_they_do TEXT, founders TEXT, relevance INT, intel TEXT, article_url TEXT, article_title TEXT,
  status TEXT DEFAULT 'new',   -- new | enriched | no_contacts | contacted | skipped
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INT, name TEXT, role TEXT, email TEXT UNIQUE COLLATE NOCASE,
  source TEXT, confidence INT,
  state TEXT DEFAULT 'queued', -- queued | sent | replied | bounced | done | dry
  step INT DEFAULT 0,          -- 0 = initial not sent, 1 = initial sent, 2/3 = followups sent
  subject TEXT, message_id TEXT, last_sent_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id INT, step INT, subject TEXT, body TEXT,
  sent_at TEXT, dry INT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, text TEXT, typefully_id TEXT, created_at TEXT
);
"""


def db():
    con = sqlite3.connect(ROOT / "hunter.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(companies)")}
    if "intel" not in cols:
        con.execute("ALTER TABLE companies ADD COLUMN intel TEXT")
    return con


def kv_get(k, default=None):
    r = db().execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def kv_set(k, v):
    con = db()
    con.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    con.commit()


# ---------------- LLM ----------------
def llm(prompt, system="You are a precise assistant.", as_json=False, temperature=0.4, retries=3):
    url = env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    body = {
        "model": env("LLM_MODEL", "gpt-4o-mini"),
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if as_json:
        body["response_format"] = {"type": "json_object"}
    last = None
    for i in range(retries):
        try:
            r = requests.post(url, json=body, timeout=90,
                              headers={"Authorization": f"Bearer {env('LLM_API_KEY')}"})
            if r.status_code == 429:
                time.sleep(15 * (i + 1)); continue
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"].strip()
            return parse_json(out) if as_json else out
        except Exception as e:  # noqa
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"LLM failed: {last}")


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


# ---------------- Discord ----------------
def discord(msg):
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        return
    for chunk in [msg[i:i + 1900] for i in range(0, len(msg), 1900)]:
        try:
            requests.post(hook, json={"content": chunk, "username": "scout • startup-hunter"}, timeout=15)
        except Exception:
            pass


def get(url, **kw):
    kw.setdefault("timeout", 20)
    kw.setdefault("headers", UA)
    return requests.get(url, **kw)


# AI-tell phrases we never want in emails/posts
BANNED = ["i hope this email finds you well", "i came across", "i am writing to", "delve", "leverage",
          "synergy", "passionate about", "thrilled", "excited to", "game-changer", "game changer",
          "cutting-edge", "in today's fast-paced", "i'd love to", "unlock", "elevate", "tapestry",
          "journey", "—", "rockstar", "ninja", "seamless", "revolutionize", "embark"]


def ai_tells(text):
    low = text.lower()
    return [b for b in BANNED if b in low]
