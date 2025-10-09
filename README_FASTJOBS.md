FastJobs Bot — Complete Guide
=============================

Automates employer-side "Bump this job" on FastJobs, logs to SQLite, and includes a Streamlit dashboard.

What’s in this repo
- fastjob_bot.py — main automation (prompts for interval each run)
- login_check.py — saves login session to storage/state.json
- db.py — SQLite schema/helpers (data/fastjob.db)
- dashboard/app.py — Streamlit UI (tables + coin chart + CSV export)
- start_fastjobs.ps1 — one-command launcher (handles setup + run)
- .env.example — template for credentials (copy to .env)

Ignored (via .gitignore): .env, data/, storage/, .venv/, __pycache__/, *.log, *.db, *.png

Environment setup (.env)
1) Create your .env from the template:
   Copy-Item .\.env.example .\.env
   notepad .\.env
2) Fill with your values:
   FASTJOBS_EMAIL=you@example.com
   FASTJOBS_PASSWORD=your_password
   FASTJOBS_LOGIN_URL=https://employer.fastjobs.sg/site/login/
   STORAGE_STATE=storage/state.json
3) Do not commit .env (already ignored)

First run (one command)
1) Set-ExecutionPolicy -Scope Process Bypass -Force   (only if needed)
2) .\start_fastjobs.ps1

The launcher will:
1) Ensure virtualenv and requirements
2) Ensure .env exists (copies from .env.example if needed)
3) Ensure Playwright browsers are installed
4) Ensure login session exists (runs login_check.py if missing)
5) Prompt for DRY/LIVE and LIMIT_JOBS
6) Run the bot (you will enter the time interval)
7) Offer to open the dashboard in a new window

Daily usage (after first run)
.\start_fastjobs.ps1
Stopping:
- Bot window: Ctrl + C
- Dashboard window: Ctrl + C

Dashboard (manual start if needed)
streamlit run .\dashboard\app.py
(default port 8501; use --server.port 8502 to change)
Shows jobs, recent bumps (filters), coin-usage chart, and CSV export

Useful commands
- Activate venv:
  .\.venv\Scripts\Activate.ps1
- DRY run:
  $env:DRY_RUN="true";  $env:LIMIT_JOBS="0";  python .\fastjob_bot.py
- LIVE run (spends coins):
  $env:DRY_RUN="false"; $env:LIMIT_JOBS="0";  python .\fastjob_bot.py
- Save/refresh login session (creates storage/state.json):
  python .\login_check.py
- Inspect artifacts and DB:
  explorer .\data
  python - << 'PY'
import sqlite3
conn = sqlite3.connect('data/fastjob.db')
print('Jobs:')
for r in conn.execute('select job_id,title,last_seen_at from jobs order by last_seen_at desc'): print(r)
print('\nRecent bumps:')
for r in conn.execute('select id,job_id,bumped_at,coins_used,outcome from bumps order by id desc limit 10'): print(r)
PY

Troubleshooting
- Playwright TLS/IPv6 hiccups:
  $env:NODE_OPTIONS="--dns-result-order=ipv4first"
  python -m playwright install chromium
- Database not found:
  Run the bot once (DRY is fine) to create data/fastjob.db
- Dashboard port in use:
  streamlit run .\dashboard\app.py --server.port 8502
- Execution policy blocks scripts:
  Set-ExecutionPolicy -Scope Process Bypass -Force

Sharing with a teammate
Share the repo or zip excluding: .venv/, data/, storage/, .env
Teammate quick start:
  git clone <repo>
  cd fastjob-bot
  Copy-Item .\.env.example .\.env   (then edit with their creds)
  Set-ExecutionPolicy -Scope Process Bypass -Force
  .\start_fastjobs.ps1

Repo hygiene
Ignored by .gitignore:
  .venv/
  __pycache__/
  .env
  storage/
  data/
  *.db
  *.log
  *.png
