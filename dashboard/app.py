import sqlite3
from pathlib import Path
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "fastjob.db"

st.set_page_config(page_title="FastJobs Bot Dashboard", layout="wide")
st.title("FastJobs Bot — Bumps & Coins")

# ---- DB checks ----
if not DB_PATH.exists():
    st.error(f"Database not found at {DB_PATH}. Run the bot at least once to create it.")
    st.stop()

@st.cache_resource
def get_conn(db_path: Path):
    # check_same_thread=False so Streamlit threads can read
    return sqlite3.connect(str(db_path), check_same_thread=False)

@st.cache_data(ttl=10)
def load_tables(_conn):
    jobs = pd.read_sql_query(
        """
        SELECT job_id, title, last_seen_at
        FROM jobs
        ORDER BY last_seen_at DESC
        """, _conn,
    )
    bumps = pd.read_sql_query(
        """
        SELECT job_id, bumped_at, COALESCE(coins_used, 0) AS coins_used, outcome
        FROM bumps
        ORDER BY datetime(bumped_at) DESC
        """, _conn,
    )
    # Parse timestamps for filtering/plotting
    if not bumps.empty:
        bumps["bumped_at"] = pd.to_datetime(bumps["bumped_at"], errors="coerce", utc=True).dt.tz_convert(None)
    if not jobs.empty:
        jobs["last_seen_at"] = pd.to_datetime(jobs["last_seen_at"], errors="coerce", utc=True).dt.tz_convert(None)
    return jobs, bumps

conn = get_conn(DB_PATH)
jobs, bumps = load_tables(conn)

# ---- Sidebar Filters ----
with st.sidebar:
    st.header("Filters")

    # Build options by TITLE (display) but keep job_id as the underlying value
    if jobs.empty:
        job_option = "All"
        job_selection = "All"
    else:
        # List by title for readability; include [job_id] to disambiguate duplicates
        job_records = jobs[["job_id", "title"]].dropna().copy()
        job_records["job_id"] = job_records["job_id"].astype(str)
        job_records = job_records.sort_values("title", kind="stable")

        options = ["All"] + job_records.to_dict("records")
        job_selection = st.selectbox(
            "Job",
            options=options,
            index=0,
            format_func=lambda o: "All" if o == "All" else f'{o.get("title","(no title)")} [{o.get("job_id","?")}]'
        )

    outcome_options = ["All", "bumped", "dry-run", "modal-not-found", "insufficient-coins", "bump-failed", "bumped-unknown-coins"]
    sel_outcome = st.selectbox("Outcome", outcome_options, index=0)
    st.caption("Tip: Choose 'bumped' to focus on real coin usage; keep 'All' to include dry-runs.")

    # Date range (defaults to last date with data)
    if not bumps.empty:
        min_date = bumps["bumped_at"].min().date()
        max_date = bumps["bumped_at"].max().date()
        date_range = st.date_input("Date range", value=(max_date, max_date), min_value=min_date, max_value=max_date)
    else:
        date_range = None

# Apply filters
df = bumps.copy()
if job_selection != "All":
    sel_job_id = job_selection["job_id"]
    df = df[df["job_id"].astype(str) == sel_job_id]

if sel_outcome != "All":
    df = df[df["outcome"] == sel_outcome]

if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
    start = pd.to_datetime(date_range[0])
    end = pd.to_datetime(date_range[1]) + pd.Timedelta(days=1)
    df = df[(df["bumped_at"] >= start) & (df["bumped_at"] < end)]

# ---- KPIs ----
left, mid, right = st.columns(3)
with left:
    st.metric("Total Jobs", len(jobs))
with mid:
    st.metric("Total Bumps (filtered)", len(df))
with right:
    # Only count real coins for this KPI
    coins_total = int(df.loc[df["outcome"] == "bumped", "coins_used"].fillna(0).sum())
    st.metric("Coins Used (filtered)", coins_total)

# ---- Jobs table ----
st.subheader("Jobs")
if jobs.empty:
    st.info("No jobs found. Run the bot to populate the database.")
else:
    st.dataframe(jobs[["job_id", "title", "last_seen_at"]], use_container_width=True)

# ---- Bumps table ----
st.subheader("Recent Bumps")
if df.empty:
    st.info("No bumps match your filter selection.")
else:
    st.dataframe(df, use_container_width=True)

# ---- Coins time-series (include dry-runs as 0 to show a baseline) ----
st.subheader("Coin Usage Over Time")
if df.empty:
    st.info("No data to chart yet. Try widening the date range.")
else:
    ts = df.copy()
    ts = ts.sort_values("bumped_at")
    # Include dry-runs as zero to create a baseline
    ts["coins_plot"] = ts.apply(
        lambda r: int(r["coins_used"]) if pd.notnull(r["coins_used"]) and r.get("outcome") == "bumped" else 0,
        axis=1
    )
    ts = ts.set_index("bumped_at")
    st.line_chart(ts[["coins_plot"]])

# ---- CSV export ----
st.subheader("Export")
if not df.empty:
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download filtered bumps as CSV",
        data=csv_bytes,
        file_name="fastjobs_bumps_filtered.csv",
        mime="text/csv",
    )

st.caption("Reads SQLite at data/fastjob.db • Filters by job title • Chart shows dry-runs as 0 for a baseline.")
