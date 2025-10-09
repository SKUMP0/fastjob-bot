FastJobs Bot — Complete Guide
=============================

What this does
- Automates employer-side “Bump this job”
- Logs results to SQLite (data/fastjob.db)
- Optional Streamlit dashboard to view jobs, bumps, and coin usage

Prerequisites
- Windows + PowerShell
- Python 3.11+
- Git (optional)
- Credentials in .env (do NOT commit)

First-time setup (once per machine)
1) Open PowerShell in the project folder:
   cd <path>\fastjob-bot
2) Create and activate venv:
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
3) Install deps:
   pip install -r requirements.txt
   # If Playwright browsers not installed yet:
   python -m playwright install
4) Save session (if needed):
   python .\login_check.py

Daily use (start-of-day)
1) Run the one-command launcher:
   .\start_fastjobs.ps1
   - Choose Dry (D) vs Live (L)
   - Set LIMIT_JOBS (0 = all, 1 = first job)
   - The bot will ask for the time interval (blank = single run)
   - After the bot finishes, choose Y to open dashboard (new window)

Manual runs (advanced)
# DRY run:
true="true"
0="0"
python .\fastjob_bot.py

# LIVE run (spends coins):
true="false"
0="0"
python .\fastjob_bot.py

Dashboard (if not using the launcher)
streamlit run .\dashboard\app.py

Stopping
- Bot: Ctrl+C in its terminal
- Dashboard: Ctrl+C in its window (or .\stop_dashboard.ps1)

Artifacts & Data
- Screenshots/HTML: .\data\
- Database: .\data\fastjob.db
  - jobs(job_id, title, last_seen_at)
  - bumps(id, job_id, bumped_at, coins_used, outcome)

Troubleshooting
- “DB not found”: run the bot once to create data\fastjob.db
- “Port in use”: start dashboard with another port:
  streamlit run .\dashboard\app.py --server.port 8502
- “Module not found”: re-run pip install -r requirements.txt
- “Session expired”: python .\login_check.py

Sharing with a teammate
- Zip the repo **without**: .venv, data, storage
- They will:
  - Create venv + install requirements
  - Run python .\login_check.py to save session
  - Use .\\start_fastjobs.ps1 to run
