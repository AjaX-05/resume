# MIGRATION — Setting up on a new laptop

Written for: you (Ajay), after `git clone`ing this repo onto a different machine. Everything personal (vault, DB, scraped jobs, API keys, corporate certs) is **not** in the repo — you have to recreate or copy those files out-of-band. This file is the checklist.

---

## 0. Prerequisites

Install these first:

| Tool | Why | Version check |
|------|-----|---------------|
| **Git** | clone the repo | `git --version` |
| **Python 3.13+** | backend + job-scraper | `python --version` |
| **uv** | Python package manager used by backend | `uv --version` ([install](https://github.com/astral-sh/uv)) |
| **Node.js 20+** (26 known to work) | frontend | `node --version` |
| **Playwright browsers** | auto-applier + PDF renderer | installed by `uv sync` + `playwright install chromium` |

> **Corporate network note:** on the NMDP laptop, the backend injects `truststore` to accept the corporate TLS-inspection cert. On a home machine this is a no-op. If you ever hit SSL errors from LiteLLM on a corp network, place `corporate-ca.pem` under `apps/backend/data/` — it is gitignored on purpose.

---

## 1. Clone + first install

```bash
git clone https://github.com/AjaX-05/resume.git resume-matcher
cd resume-matcher

# Backend
cd apps/backend
uv sync --extra dev
uv run playwright install chromium        # applier + PDF
cd ../..

# Frontend
cd apps/frontend
npm install
cd ../..

# Job scraper (optional)
cd apps/job-scraper
uv sync
cd ../..
```

---

## 2. Secrets you must recreate (NOT in repo)

Everything below is gitignored. Create them by hand on the new laptop.

### 2.1 Backend LLM config — `apps/backend/data/config.json`

Minimum shape for the "agent" (you-as-LLM) mode you've been using:

```json
{
  "provider": "agent",
  "model": "claude-opus-4-7",
  "api_key": ""
}
```

Or for a direct provider (Anthropic as example):

```json
{
  "provider": "anthropic",
  "model": "claude-opus-4-7",
  "api_key": "sk-ant-..."
}
```

Fill via the Settings page in the UI once the app is running — it writes this file.

### 2.2 Applier vault — `apps/backend/data/vault.json`

Personal + EEO details the Playwright applier fills into forms. Shape (copy from the old laptop, or recreate):

```json
{
  "personal": {
    "firstName": "",
    "lastName": "",
    "email": "",
    "phone": "",
    "address": { "line1": "", "city": "", "state": "", "zip": "", "country": "US" },
    "linkedin": "",
    "github": "",
    "website": "",
    "resumeHeadline": ""
  },
  "work_authorization": {
    "needs_sponsorship": true,
    "visa_status": "STEM OPT",
    "authorized_to_work": true
  },
  "eeo": {
    "gender": "",
    "ethnicity": "",
    "veteran_status": "",
    "disability": "",
    "sexual_orientation": ""
  },
  "approval_gate": true
}
```

> **Keep `approval_gate: true`.** It forces a human review before any ATS submit — this is deliberate.

### 2.3 Pipeline config — `apps/backend/data/pipeline.json`

Written automatically by the Pipeline toggles in the UI. On first run leave it absent; after you pick a master resume in the app, open the tracker and the pipeline row at the top will persist it. If you want to seed it:

```json
{
  "auto_tailor": false,
  "auto_apply": false,
  "approval_gate": true,
  "master_resume_id": "<uuid of master resume in SQLite>",
  "tailor_prompt_id": "full"
}
```

### 2.4 Job scraper secrets — `apps/job-scraper/.env`

```ini
FIRECRAWL_API_KEY=fc-...
# optional: self-hosted Firecrawl base URL if you're running it on RTX laptop
# FIRECRAWL_API_URL=http://localhost:3002
```

### 2.5 Job scraper session store — `apps/job-scraper/data/`

Gitignored. Contains:
- `auth/` — session cookies for Jobright/Wellfound (recreated on first login flow)
- `discovered/` — scraped job JSONs
- `jobs.db` — dedup store

Nothing to seed; it rebuilds itself.

---

## 3. Database

SQLite file at `apps/backend/data/resume_matcher.db` is gitignored. Two options:

**Option A — start fresh** (recommended if you don't need the old application cards):
Just run the backend; `db_engine.py` creates it with WAL mode and FK pragmas on first startup. The TinyDB→SQLite migration in `app/scripts/` is a no-op on a fresh DB.

**Option B — copy from the old laptop**:
Copy these three files (database + WAL + shared memory) together:
- `apps/backend/data/resume_matcher.db`
- `apps/backend/data/resume_matcher.db-wal`
- `apps/backend/data/resume_matcher.db-shm`

Then also copy `apps/backend/data/.secret_key` so the Fernet-encrypted API keys stored in `api_keys` can still be decrypted. **Without `.secret_key` the stored provider keys are unreadable** — you'd need to re-enter them in Settings.

---

## 4. Boot order

Two terminals.

```bash
# Terminal 1 — Backend  (port 8000, or 8002 if 8000 is blocked by firewall)
cd apps/backend
uv run uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2 — Frontend  (port 3000)
cd apps/frontend
npm run dev
```

> If you changed the backend port, point the frontend at it: `apps/frontend/.env.local` → `BACKEND_ORIGIN=http://127.0.0.1:8002`

Open <http://localhost:3000>.

---

## 5. Smoke tests (verify the new laptop works end-to-end)

1. **LLM reachable** — Settings → "Test Connection" returns ok.
2. **Master resume** — upload an existing PDF or build one in `/builder`. Note its id.
3. **Tracker loads** — `/tracker` shows your Kanban columns. If you did Option B, you'll see your old cards; if Option A, it's empty.
4. **Scraper button** — ScraperPanel in `/tracker` → run `All` (or `Jobright`). Firecrawl credits must have juice. Wait for the completion toast.
5. **ATS badges** — each new scraped JD card should show a green badge (GH/ASHBY/etc.) or orange (WD/iCIMS). Gray "no url" means the scrape didn't capture an apply link.
6. **Bulk tailor** — select two JD-column cards with green badges → "Tailor N" button appears → modal shows progress → cards move to the Tailored column with resumes attached.
7. **Bulk apply (keep approval gate ON)** — select one Tailored card with a green badge → "Apply N" → the applier launches Playwright, fills the form, and **stops at the review step**. Confirm visually in the headful browser window.
8. **Pre-push hook** (optional but recommended):
   ```bash
   git config core.hooksPath .githooks
   ```
   This runs the backend test suite + locale-parity check before every push. Red tests block the push.

---

## 6. Where the live state lives (so you don't lose it next time)

| What | Path | In repo? |
|------|------|----------|
| Code | `apps/backend/`, `apps/frontend/`, `apps/job-scraper/` | yes |
| LLM provider + keys | `apps/backend/data/config.json`, `apps/backend/data/.secret_key` | **no** |
| Resumes, jobs, applications | `apps/backend/data/resume_matcher.db*` | **no** |
| Applier identity | `apps/backend/data/vault.json` | **no** |
| Pipeline toggles | `apps/backend/data/pipeline.json` | **no** |
| Firecrawl key | `apps/job-scraper/.env` | **no** |
| Scraper session cookies + discovered JSONs | `apps/job-scraper/data/` | **no** |
| Local scraped JDs | `jobs/` (except `README.md`) | **no** |

Back these up separately (encrypted USB, 1Password attachment, whatever) — not in Git.

---

## 7. Known gotchas

- **Node 8 (VBrick bundled) is too old** for Next.js 16. Install Node 20+ (26 confirmed working) and put it earlier on PATH than any bundled Node.
- **Firecrawl free tier is 3 req/min and 1000 req total.** The scraper has `request_delay_seconds=22` to respect it. If credits exhaust, either upgrade or self-host Firecrawl via Docker Compose on an RTX machine.
- **Port 8000 blocked by corp firewall** → use 8002 (see §4). Home networks: 8000 is fine.
- **`approval_gate` must stay ON.** The applier will submit forms without it. The pipeline UI toggle respects whatever you flip there, so leave it green.
- **Workday / iCIMS** are deliberately **not** supported — they require an account per employer. Those cards show an orange badge; skip them manually.

---

## 8. Rolling back to known-good

If the new laptop boots but something's off:

```bash
git log --oneline -10         # find the known-good SHA
git checkout <sha>            # just to compare
git checkout main             # back to tip when done
```

Nothing destructive — the data files are outside Git so a code rollback can't eat them.
