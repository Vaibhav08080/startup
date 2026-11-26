# startup-hunter 🎯

Jin startups ko abhi funding mili hai, unke founder/CTO ko personalised cold email + 2 follow-ups. Reply aaye toh bot ruk jaata hai aur tujhe call-prep deta hai. Sab GitHub Actions pe chalta hai, laptop band ho tab bhi.

```
RSS (Inc42, Entrackr, YourStory, TechCrunch, Google News)
  └─▶ radar     funding news filter → LLM extract (company, round, founders, relevance 0-10)
      └─▶ contacts  domain → Hunter.io / founder-name pattern / careers@ → verify → best 2 people
          │         + company intel: their own site (product, eng problems, open roles)
          └─▶ outreach  email = their problem → your matching proof + real number → offer → ask
              │         quality gate → Gmail SMTP → follow-ups on day 4 and 9 (same thread)
              └─▶ inbox   reply = stop + 🔥 Discord + 🧠 call-prep · bounce = next contact
```

## Kab kya hota hai

| Kab | Kya |
|---|---|
| har ghante | replies/bounces check → radar (har 6h) → contacts → outreach |
| bhejne ka time | 10:00-19:00 IST, Mon-Sat |
| roz kitne | warm-up: pehle 7 din 5/day, din 8-14 10/day, phir 15/day |
| reply aaya | sequence stop + 🔥 ping + 🧠 call-prep note (unki problems, tere best proofs, questions, suggested reply) |
| 21:00 ke baad | din ka summary Discord pe |

## Safety (spam folder aur galat mails se bachne ke liye)

- **Ek company mein sirf ek banda.** CTO → founder → eng lead → careers@. Dusra sirf bounce pe.
- **Guessed emails verify hote hain** (Hunter). Invalid wale kabhi nahi bhejte.
- **Same company do baar nahi**, chahe naam alag likha ho (domain se match). `exclude_domains` mein jo companies hain unhe kabhi mail nahi.
- **Quality gate:** 60-160 words, greeting mein sahi naam, koi `[placeholder]` nahi, max 2 links, AI-wale phrases nahi, aur **koi bhi number jo tere resume ya unki news mein nahi hai = mail reject**. 4 tries mein clean mail na bane toh company skip + Discord pe batata hai.
- **Max 2 follow-ups**, phir band. Reply aaya toh turant band.

## Setup (one time)

**1. Secrets:** repo → Settings → Secrets and variables → Actions → **New repository secret**

| Secret | Kahan se |
|---|---|
| `LLM_API_KEY` | console.groq.com → API Keys (free) |
| `GMAIL_ADDRESS` | jis Gmail se bhejna hai |
| `GMAIL_APP_PASSWORD` | myaccount.google.com → Security → 2-Step Verification ON → App passwords |
| `HUNTER_API_KEY` | hunter.io → API (free: 25 searches + 50 verifications/month) |
| `DISCORD_WEBHOOK_URL` | #job-hunt → Edit Channel → Integrations → Webhooks → New |

**2. Variable** (same page, **Variables** tab): `DRY_RUN` = `true`. Is mode mein mails sirf Discord pe aur `outbox/` mein aate hain, bheje nahi jaate.

**3. Test:** Actions tab → startup-hunter → **Run workflow** → `radar`, phir `contacts`, phir `outreach`. Discord pe drafts dekh.

**4. Go live:** drafts theek lage toh `DRY_RUN` = `false` kar, phir Run workflow → `go-live`. Bas, ab har ghante apne aap chalega.

## Config (`config.yaml`)

- `me`: signature, resume, experience
- `proof_of_work`: tere proof atoms. Har ek ek sachchi achievement hai number ke saath, aur `url` mein case study. **Bot sirf yahi numbers use kar sakta hai.**
- `radar.min_relevance`: 6 default. Zyada junk aaye toh 7 kar.
- `outreach.daily_send_cap`, `warmup`, `exclude_domains`, `followups_after_days`

## Local run (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill it
python run.py radar && python run.py contacts && python run.py outreach
python run.py status
```
