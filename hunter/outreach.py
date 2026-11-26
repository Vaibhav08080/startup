"""Write + send personalised cold emails, follow-ups, reply/bounce detection."""
import email
import imaplib
import random
import re
import smtplib
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from pathlib import Path

from .core import CFG, DRY_RUN, IST, ROOT, ai_tells, db, discord, env, get, llm, log, now_ist

ME = CFG["me"]
OC = CFG["outreach"]

SYSTEM = """You write short cold emails from a student engineer to a startup founder/CTO.
Sound like a sharp engineer typing fast: plain, specific, slightly informal. Zero corporate tone.

Structure (body only, no signature):
1. "Hi <name>," then ONE line about THEM: use the given hook / a real eng challenge. Not congrats, not flattery.
2. THE BRIDGE (most important): pick their single most relevant eng challenge and connect it to ONE proof atom
   of mine that already solved a similar problem, with its real number. Pattern:
   "<their challenge> is close to what I did at/in <proof>: <what + number>."
   Optionally one more short proof line if it covers a different challenge.
3. One concrete offer: small paid task / trial week / take a ticket off their backlog.
4. One low-friction ask (e.g. "worth a 15 min call?").

Hard rules:
- 80-130 words. No em dashes. No buzzwords. No "I hope", "I came across", "passionate", "excited".
- Use ONLY numbers that appear in MY PROOF ATOMS or THEIR FACTS. Never invent metrics, users, or claims.
- If a proof atom has a case study link, include that ONE link (the most relevant). Max 1 link in the whole email.\n- Don't list all my experience. Don't paste the resume. Resume is attached; you may say "resume attached".
- Subject: 3-7 words, lowercase, specific to them (e.g. "kafka cdc help for <company>").
Return JSON {"subject": str, "body": str, "bridge_used": str}"""


_LIVE = {}


def live(url):
    """Only link case studies that are actually published."""
    if not url:
        return False
    if url not in _LIVE:
        try:
            _LIVE[url] = get(url, timeout=10).status_code == 200
        except Exception:
            _LIVE[url] = False
    return _LIVE[url]


def proof_text():
    return "\n".join(f"- [{p['name']}] {p['pitch']}" + (f" (case study: {p['url']})" if live(p["url"]) else "")
                     + f" [tags: {', '.join(p['tags'])}]" for p in CFG["proof_of_work"])


def first_name(c):
    n = (c["name"] or "").split()
    return n[0].capitalize() if n else ""


def _nums(text):
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def invented_numbers(body, allowed_text):
    """Every number in the email must exist in my proof atoms or their facts (small ones like '15 min' are ok)."""
    allowed = _nums(allowed_text)
    return [n for n in _nums(body) if n not in allowed and not (n.isdigit() and int(n) <= 30)]


def write_initial(contact, co):
    intel = co["intel"] or "{}"
    facts = (f"{co['what_they_do']} | sector: {co['sector']} | {co['location']} | news: {co['article_title']} | "
             f"round: {co['round']} {co['amount']} | investors: {co['investors']}")
    prompt = f"""RECIPIENT: {contact['name'] or 'the team'} ({contact['role']}) at {co['name']}. Greet as "Hi {first_name(contact) or 'there'},".
THEIR FACTS: {facts}
THEIR SITE ANALYSIS: {intel}
ME: {ME['one_liner']}. Want: {ME['roles_wanted']}.
MY PROOF ATOMS (pick by overlap with their eng_challenges, not by fame):
{proof_text()}"""
    allowed = proof_text() + facts + intel
    out = {}
    for _ in range(4):
        out = llm(prompt, system=SYSTEM, as_json=True, temperature=0.7)
        body, subj = out.get("body", ""), out.get("subject", "")
        bad = ai_tells(body + subj) + invented_numbers(body, allowed)
        if body and not bad:
            return subj.strip(), body.strip()
        log(f"rewrite {co['name']}: {bad}")
    return out.get("subject", f"backend help for {co['name']}").strip(), out.get("body", "").replace("—", "-").strip()


def write_followup(contact, co, step):
    angle = ("short bump, add ONE new useful thing (a small idea for their product or a relevant thing I built)"
             if step == 2 else "final, 2-3 lines, polite, say I won't follow up again, leave the door open")
    out = llm(f"""Write follow-up #{step - 1} to {contact['name'] or 'the team'} at {co['name']} ({co['what_they_do']}).
Earlier I emailed about working with them as {ME['roles_wanted']}. Angle: {angle}.
Max 60 words. No "just following up" / "circling back" / "bumping this". Return JSON {{"body": str}}.""",
              system=SYSTEM, as_json=True, temperature=0.8)
    return out.get("body", "").replace("—", "-").strip()


def build(contact, subject, body, reply_to_id=None):
    msg = EmailMessage()
    msg["From"] = f"{ME['name']} <{env('GMAIL_ADDRESS')}>"
    msg["To"] = contact["email"]
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=env("GMAIL_ADDRESS").split("@")[-1] or "gmail.com")
    if reply_to_id:
        msg["In-Reply-To"] = reply_to_id
        msg["References"] = reply_to_id
    msg.set_content(f"{body}\n\n{ME['email_signature'].strip()}\n")
    pdf = ME.get("resume_pdf_path")
    if pdf and Path(pdf).exists() and not reply_to_id:
        msg.add_attachment(Path(pdf).read_bytes(), maintype="application", subtype="pdf",
                           filename="Vaibhav_Resume.pdf")
    return msg


def smtp_send(msg):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD"))
        s.send_message(msg)


def in_window():
    n = now_ist()
    lo, hi = OC["send_window_ist"]
    return n.weekday() < 6 and lo <= n.hour < hi


def sent_today(con):
    day = now_ist().date().isoformat()
    return con.execute("SELECT COUNT(*) FROM sends WHERE sent_at LIKE ? AND dry=0", (day + "%",)).fetchone()[0]


def record(con, contact, step, subject, body, msg_id):
    ts = now_ist().isoformat()
    con.execute("INSERT INTO sends(contact_id,step,subject,body,sent_at,dry) VALUES(?,?,?,?,?,?)",
                (contact["id"], step, subject, body, ts, int(DRY_RUN)))
    con.execute("""UPDATE contacts SET step=?, state=?, subject=COALESCE(subject, ?),
                   message_id=COALESCE(message_id, ?), last_sent_at=? WHERE id=?""",
                (step, "dry" if DRY_RUN else "sent", subject, msg_id, ts, contact["id"]))
    con.execute("UPDATE companies SET status='contacted' WHERE id=?", (contact["company_id"],))
    con.commit()
    if DRY_RUN:
        out = ROOT / "outbox"; out.mkdir(exist_ok=True)
        (out / f"{contact['id']}_step{step}.txt").write_text(
            f"TO: {contact['email']}\nSUBJECT: {subject}\n\n{body}\n\n{ME['email_signature']}")


def dispatch(con, contact, co, step):
    if step == 1:
        subject, body = write_initial(contact, co)
        reply_to = None
    else:
        subject = "Re: " + (contact["subject"] or "")
        body = write_followup(contact, co, step)
        reply_to = contact["message_id"]
    msg = build(contact, subject, body, reply_to)
    if not DRY_RUN:
        smtp_send(msg)
    record(con, contact, step, subject, body, msg["Message-ID"])
    tag = "🧪 DRY" if DRY_RUN else "📨 SENT"
    discord(f"{tag} step {step} → **{co['name']}** · {contact['name'] or ''} ({contact['role']}) <{contact['email']}>\n"
            f"**{subject}**\n```{body[:1200]}```")
    log(f"{tag} step{step} {contact['email']}")


def run():
    if not in_window() and not DRY_RUN:
        log("outreach: outside send window"); return 0
    con = db()
    budget = min(OC["per_run_cap"], OC["daily_send_cap"] - sent_today(con))
    if budget <= 0:
        log("outreach: daily cap hit"); return 0
    done = 0
    # 1) follow-ups first (they are cheaper and convert better)
    f1, f2 = OC["followups_after_days"]
    live_state = "dry" if DRY_RUN else "sent"
    for c in con.execute("""SELECT * FROM contacts WHERE state=? AND step IN (1,2)
                            ORDER BY last_sent_at""", (live_state,)).fetchall():
        if done >= budget:
            break
        wait = f1 if c["step"] == 1 else (f2 - f1)
        last = datetime.fromisoformat(c["last_sent_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=IST)
        if last > now_ist() - timedelta(days=wait):
            continue
        co = con.execute("SELECT * FROM companies WHERE id=?", (c["company_id"],)).fetchone()
        dispatch(con, c, co, c["step"] + 1); done += 1
        pause()
    # 2) new initial emails, best companies first, one contact per company per run
    rows = con.execute("""SELECT ct.* FROM contacts ct JOIN companies co ON co.id=ct.company_id
                          WHERE ct.state='queued' AND NOT EXISTS (
                              SELECT 1 FROM contacts o WHERE o.company_id=ct.company_id
                              AND o.state IN ('sent','dry','replied','done'))
                          ORDER BY co.relevance DESC, ct.confidence DESC""").fetchall()
    seen_co = set()
    for c in rows:
        if done >= budget:
            break
        if c["company_id"] in seen_co:
            continue
        seen_co.add(c["company_id"])
        co = con.execute("SELECT * FROM companies WHERE id=?", (c["company_id"],)).fetchone()
        try:
            dispatch(con, c, co, 1); done += 1
        except Exception as e:
            log(f"send fail {c['email']}: {e}"); discord(f"⚠️ send failed {c['email']}: {e}")
        pause()
    # sequence finished after step 3
    con.execute("UPDATE contacts SET state='done' WHERE step>=3 AND state IN ('sent','dry')")
    con.commit()
    return done


def pause():
    if not DRY_RUN:
        time.sleep(random.randint(*OC["min_gap_seconds"]))


# ---------------- inbox watcher ----------------
def check_inbox(days=14):
    if not env("GMAIL_APP_PASSWORD"):
        return
    con = db()
    active = {r["email"].lower(): r for r in
              con.execute("SELECT * FROM contacts WHERE state IN ('sent','done')").fetchall()}
    if not active:
        return
    since = (now_ist() - timedelta(days=days)).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD"))
    M.select("INBOX", readonly=True)
    _, ids = M.search(None, f'(SINCE "{since}")')
    for i in ids[0].split()[-400:]:
        _, hdr = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        h = email.message_from_bytes(b"".join(p[1] for p in hdr if isinstance(p, tuple)))
        sender = parseaddr(h.get("From", ""))[1].lower()
        is_bounce = "mailer-daemon" in sender or "postmaster" in sender
        is_reply = sender in active and active[sender]["state"] != "replied"
        if not (is_bounce or is_reply):
            continue
        _, data = M.fetch(i, "(BODY.PEEK[])")
        m = email.message_from_bytes(b"".join(p[1] for p in data if isinstance(p, tuple)))
        if is_reply:
            con.execute("UPDATE contacts SET state='replied' WHERE email=?", (sender,))
            co = con.execute("SELECT name FROM companies WHERE id=?", (active[sender]["company_id"],)).fetchone()
            discord(f"🔥 **REPLY** from {sender} ({co['name']}) — subject: {m.get('Subject')}\n"
                    f"Go reply yourself, sequence stopped for them.")
            try:
                call_prep(active[sender], plain_text(m))
            except Exception as e:
                log(f"call prep failed: {e}")
            active[sender] = dict(active[sender], state="replied")
        else:
            text = m.as_string().lower()
            for addr, row in active.items():
                if addr in text and row["state"] != "bounced":
                    # bounced contact drops out -> next queued contact of that company gets picked next run
                    con.execute("UPDATE contacts SET state='bounced' WHERE email=?", (addr,))
                    log(f"bounce: {addr}")
    con.commit()
    M.logout()


def plain_text(msg, limit=2500):
    """Their reply only, without the quoted thread."""
    part = msg
    if msg.is_multipart():
        part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
    if part is None:
        return ""
    text = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="ignore")
    keep = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:$", line.strip()):
            break
        keep.append(line)
    return "\n".join(keep).strip()[:limit]


PREP_PROMPT = """A startup founder/engineer just replied to my cold email. Prepare me for the conversation.
Write a crisp call-prep note in markdown (max 300 words) with these sections:
**What they said** (1 line) · **What they build** · **Their likely eng problems** (3 bullets, specific)
· **My best 2 proofs for them** (from my proof atoms, with the real numbers) · **5 smart questions to ask**
· **Suggested reply** (3-4 lines, plain, moves toward a call/task; no fluff, no em dashes).
Use ONLY facts given. Never invent numbers.

THEIR REPLY: {reply}
COMPANY: {name} — {what} | {sector} | news: {news}
SITE ANALYSIS: {intel}
MY EMAIL SUBJECT: {subject}
ME: {me}
MY PROOF ATOMS:
{proofs}"""


def call_prep(contact, reply_text):
    con = db()
    co = con.execute("SELECT * FROM companies WHERE id=?", (contact["company_id"],)).fetchone()
    note = llm(PREP_PROMPT.format(reply=reply_text or "(could not read body)", name=co["name"],
                                  what=co["what_they_do"], sector=co["sector"], news=co["article_title"],
                                  intel=co["intel"] or "{}", subject=contact["subject"], me=ME["one_liner"],
                                  proofs=proof_text()), temperature=0.4)
    discord(f"🧠 **Call prep — {co['name']}** ({contact['name'] or contact['email']})\n{note}")


def go_live_reset():
    """Call once when flipping DRY_RUN=false: dry-run contacts go back to queue as fresh."""
    con = db()
    n = con.execute("UPDATE contacts SET state='queued', step=0, subject=NULL, message_id=NULL, "
                    "last_sent_at=NULL WHERE state='dry'").rowcount
    con.execute("DELETE FROM sends WHERE dry=1")
    con.commit()
    log(f"reset {n} dry-run contacts to queued")

