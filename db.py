import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = "data/fastjob.db"

DDL = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  title TEXT,
  last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS bumps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  bumped_at TEXT NOT NULL,
  coins_used INTEGER,
  outcome TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
"""

def get_conn(path: str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(DDL)
    conn.commit()
    return conn

def upsert_job(conn: sqlite3.Connection, job_id: str, title: str, when_iso: str):
    conn.execute(
        "INSERT INTO jobs(job_id,title,last_seen_at) VALUES(?,?,?) "
        "ON CONFLICT(job_id) DO UPDATE SET title=excluded.title, last_seen_at=excluded.last_seen_at",
        (job_id, title, when_iso),
    )
    conn.commit()

def insert_bump(conn: sqlite3.Connection, job_id: str, when_iso: str, coins: Optional[int], outcome: str):
    conn.execute(
        "INSERT INTO bumps(job_id,bumped_at,coins_used,outcome) VALUES(?,?,?,?)",
        (job_id, when_iso, coins, outcome),
    )
    conn.commit()
