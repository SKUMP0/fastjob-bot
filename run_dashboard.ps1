# run_dashboard.ps1 — launch the Streamlit dashboard on Windows
param(
  [string] = ".\.venv",
  [string] = ".\dashboard\app.py"
)

if (!(Test-Path )) {
  python -m venv 
}

# Activate venv
 = Join-Path  "Scripts\Activate.ps1"
. 

# Minimal deps for the dashboard
pip install streamlit pandas

# Launch
streamlit run 
