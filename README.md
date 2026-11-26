# startup-hunter 🎯

Funded startups dhundho → founders/CTO ke email nikalo → proof-of-work ke saath personalised mail + 2 follow-ups → replies pe ruk jao → aur roz X pe tere real kaam se yap karo.

```
RSS (Inc42, Entrackr, YourStory, TechCrunch, Google News)
   └─▶ radar      : funding filter + LLM extract (company, round, founders, relevance 0-10)
        └─▶ contacts : domain → Hunter.io / founder-name patterns / site careers@ → best 2 people
             └─▶ outreach : LLM email (picks 1-2 relevant projects) → Gmail SMTP → follow-ups day 4 & 9
                  └─▶ inbox    : IMAP watch → reply = stop + 🔥 Discord ping, bounce = next contact
yap: GitHub commits + HN + news + fresh raises → post in your voice → Typefully → X / LinkedIn
Everything logs to Discord #job-hunt (scout channel).
```

## Kaise chalta hai

GitHub Actions pe, tera laptop on ho ya off. Workflow: `.github/workflows/hunter.yml`

| Kab (IST) | Kya |
|---|---|
| har ghante | replies/bounces check → radar (har 6h) → contacts → outreach (sirf 10:00-19:00 Mon-Sat, 15/day) |
| 08:47 | LinkedIn draft Discord pe (tu khud post karega) |
| 09:23, 18:41 | X post Typefully queue mein |
| 21:00 ke baad | din ka summary Discord pe |
| reply aaya | 🔥 ping + 🧠 call-prep note Discord pe |

Har run ke baad `hunter.db` (kisko mail gaya, kisne reply kiya) repo mein commit hota hai, wahi bot ki memory hai.

## Setup (one time)

**1. Secrets** — repo → Settings → Secrets and variables → Actions → **New repository secret**:

| Secret | Kahan se |
|---|---|
| `LLM_API_KEY` | console.groq.com → API Keys |
| `GMAIL_ADDRESS` | vaibhavofficial745@gmail.com |
| `GMAIL_APP_PASSWORD` | myaccount.google.com → Security → 2-Step ON → App passwords |
| `HUNTER_API_KEY` | hunter.io → API |
| `TYPEFULLY_API_KEY` | Typefully → Settings → API (publish permission) |
| `TYPEFULLY_SOCIAL_SET_ID` | @Vaibhaavvvvvv ka id (Typefully mein add karne ke baad) |
| `DISCORD_WEBHOOK_URL` | #job-hunt → Edit Channel → Integrations → Webhooks |

**2. Variables** (same page, **Variables** tab) — optional:
- `DRY_RUN` = `true` (default). Drafts Discord + `outbox/` mein, kuch send nahi hota.
- `LLM_MODEL`, `LLM_BASE_URL` — sirf agar Groq ke alawa kuch use karna ho.

**3. Test** — Actions tab → startup-hunter → **Run workflow** → `radar`, phir `contacts`, phir `outreach`, phir `yap`, `linkedin`. Discord dekh.

**4. Go live** — drafts theek lage toh Variables mein `DRY_RUN` = `false`, phir Run workflow → `go-live`. Bas.

## Local run (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill it
python run.py status
```

## Tuning

- `radar.min_relevance` — 6 default. Zyada junk aaye toh 7.
- `outreach.daily_send_cap` — **15 se start kar**. Naye Gmail se 50+/day = spam folder. 2-3 hafte baad 25.
- Ek company mein sirf **ek** banda mail hota hai (CTO > founder > eng lead > careers@). Dusra sirf bounce pe.
- `yap.platforms: [x, linkedin]` — LinkedIn Typefully mein connect karke.
- `yap.mode: draft` — agar kabhi chahiye ki posts Typefully drafts mein ruk jaayein.

## Kyun aise bana hai (important)

- **X API direct nahi, Typefully se** — X ka API paid hai aur automated posting pe accounts flag hote hain. Typefully natural posting time ke saath tere account se post karta hai.
- **Auto-replies / auto-likes nahi daale** — X rules mein automated replies spam count hote hain, account suspend ka sabse fast tareeka. Original posts safe hain. Reply-guy game khud 15 min/day kar, wo tere real voice mein hi kaam karta hai.
- **Yap sirf real context se likhta hai** (tere commits, actual news, actual raises) aur fake metrics/claims banned hain. "Genuine lagna" = genuine hona; fake stats pakde jaate hain aur founders wahi log hain jinhe tu mail kar raha hai.
- **Emails mein soft opt-out vibe + max 2 follow-ups + reply pe stop** — deliverability bachi rahegi aur domain blacklist nahi hoga.
