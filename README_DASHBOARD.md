FastJobs Bot — Streamlit Dashboard

Minimal dashboard to view bot activity and plot coin usage.

Quick start (Windows PowerShell)

1) From repo root:
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   pip install streamlit pandas

2) Run the app:
   streamlit run dashboard/app.py

Make sure the bot has run at least once so data/fastjob.db exists.

Shows
- Jobs table (jobs)
- Recent bumps (bumps) with filters (job / outcome / date)
- Coin usage over time (line chart on coins_used)
- CSV export for filtered bumps

Notes
- Read-only; does not modify the DB.
- To share with a friend: zip the repo without .venv, data, storage. They can run the bot once to create a local DB.
