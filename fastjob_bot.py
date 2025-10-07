# fastjob_bot.py — DRY-safe bump discovery + optional scheduler loop
#
# WHAT THIS DOES
# - Logs in (reusing storage/state.json)
# - Goes to Manage Jobs
# - For each job card (div.job-ad-flexbox):
#     • Extracts jid from ?jid=... (fallbacks to stat ids / public URL)
#     • Extracts title from <h3><a><span class="job-ad-title">
#     • Finds bump button presence
#     • DRY (default): logs “Would bump …” and writes bumps row (outcome='dry-run')
#     • LIVE (DRY_RUN=false): clicks bump; if “insufficient coins” modal appears
#         outcome='insufficient-coins', coins_used=0 (screenshot saved)
# - LIMIT_JOBS respected
# - Optional loop scheduler: set EVERY_SECONDS to re-run continuously
#
# USAGE (PowerShell)
#   # DRY single run (no coins)
#   $env:DRY_RUN="true"
#   $env:LIMIT_JOBS="0"
#   python .\fastjob_bot.py
#
#   # DRY hourly loop (no coins)
#   $env:DRY_RUN="true"
#   $env:LIMIT_JOBS="0"
#   $env:EVERY_SECONDS="3600"
#   python .\fastjob_bot.py
#
#   # Single LIVE test (WILL SPEND COINS if you have coins)
#   $env:DRY_RUN="false"
#   $env:LIMIT_JOBS="1"
#   python .\fastjob_bot.py

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError, Locator

import db  # db.py with get_conn, upsert_job, insert_bump

# ---------------------- ENV ----------------------
load_dotenv()

STORAGE_STATE = os.getenv("STORAGE_STATE", "storage/state.json")
DB_PATH       = os.getenv("DB_PATH", "data/fastjob.db")

COYID    = os.getenv("FASTJOBS_COYID", "235927")
EMAIL    = os.getenv("FASTJOBS_EMAIL", "")
PASSWORD = os.getenv("FASTJOBS_PASSWORD", "")

BASE_URL  = "https://employer.fastjobs.sg"
LOGIN_URL = os.getenv("FASTJOBS_LOGIN_URL", f"{BASE_URL}/site/login/")
DASH_URL  = f"{BASE_URL}/p/my-activity/dashboard/?coyid={COYID}"

DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"
LIMIT_JOBS     = int(os.getenv("LIMIT_JOBS", "0"))  # 0 = all jobs
EVERY_SECONDS  = int(os.getenv("EVERY_SECONDS", "0"))  # 0 = run once; >0 = loop

JOBS_URLS = [
    f"{BASE_URL}/p/job/manage/?coyid={COYID}",
    f"{BASE_URL}/p/job/?coyid={COYID}",
    f"{BASE_URL}/p/jobs/manage/?coyid={COYID}",
]

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

def _clean_text(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()

def _normalize_title(t: str) -> str:
    t = (t or "").replace("\u00A0", " ").replace("\u2007", " ").replace("\u202F", " ")
    return _clean_text(t)

def safe_full_screenshot(page: Page, path: str) -> None:
    try:
        page.screenshot(path=path, full_page=True, timeout=6000)
    except Exception:
        try:
            page.screenshot(path=path, full_page=False, timeout=3000)
        except Exception:
            pass

# ---------------------- LOGIN ----------------------
def ensure_logged_in(page: Page) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    try:
        email_box = page.get_by_role("textbox", name=re.compile(r"Username|email", re.I))
        pwd_box   = page.get_by_role("textbox", name=re.compile(r"Password", re.I))
        email_box.wait_for(state="visible", timeout=2000)
        pwd_box.wait_for(state="visible", timeout=2000)

        if not EMAIL or not PASSWORD:
            raise RuntimeError("FASTJOBS_EMAIL / FASTJOBS_PASSWORD not set in .env")

        email_box.click()
        try: email_box.clear()
        except Exception: pass
        page.keyboard.insert_text(EMAIL)

        pwd_box.click()
        try: pwd_box.clear()
        except Exception: pass
        page.keyboard.insert_text(PASSWORD)

        page.get_by_role("button", name=re.compile(r"Login|Sign in", re.I)).click()
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        pass  # likely already logged in

# ---------------------- NAVIGATION ----------------------
def goto_jobs_list(page: Page) -> None:
    for name in ["Manage Jobs", "My Jobs", "Jobs", "Job Listings", "Manage Job"]:
        try:
            page.get_by_role("link", name=re.compile(rf"^{name}$", re.I)).first.click(timeout=1500)
            page.wait_for_load_state("networkidle")
            if page.locator("div.job-ad-flexbox").count() > 0:
                return
        except Exception:
            pass
    for url in JOBS_URLS:
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            if page.locator("div.job-ad-flexbox").count() > 0:
                return
        except Exception:
            continue
    raise RuntimeError("Could not reach a Jobs list page with job cards.")

# ---------------------- EXTRACTION ----------------------
JID_RE = re.compile(r"[?&]jid=(\d+)\b", re.I)
STAT_ID_RE = re.compile(r"jobAdStat_(?:views|applications|shares|messages|savedjob|invitation)_(\d+)$", re.I)

def find_job_cards(page: Page) -> Locator:
    return page.locator("div.job-ad-flexbox")

def extract_title(card: Locator) -> str:
    t = card.locator("h3 a span.job-ad-title")
    if t.count() > 0:
        return _normalize_title(t.first.inner_text())
    h = card.locator("h3")
    if h.count() > 0:
        return _normalize_title(h.first.inner_text())
    return "Unknown Title"

def _jid_from_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    m = JID_RE.search(href)
    return m.group(1) if m else None

def _jid_from_stat_id(stat_id: Optional[str]) -> Optional[str]:
    if not stat_id:
        return None
    m = STAT_ID_RE.search(stat_id.strip())
    return m.group(1) if m else None

def extract_jid(card: Locator) -> Optional[str]:
    # 1) anchors with ?jid=
    links = card.locator("a[href*='jid=']")
    n = min(links.count(), 20)
    for i in range(n):
        href = links.nth(i).get_attribute("href") or ""
        jid = _jid_from_href(href)
        if jid:
            return jid
    # 2) stat id: jobAdStat_*_<jid>
    stat = card.locator(".stat-number[id^='jobAdStat_']")
    if stat.count() > 0:
        sid = stat.first.get_attribute("id") or ""
        jid = _jid_from_stat_id(sid)
        if jid:
            return jid
    # 3) public URL /<jid>/
    pub = card.locator("a[href*='fastjobs.sg/'][href*='/job-ad/']")
    if pub.count() > 0:
        href = pub.first.get_attribute("href") or ""
        m = re.search(r"/(\d{6,})/", href)
        if m:
            return m.group(1)
    return None

def find_bump_button(card: Locator) -> Optional[Locator]:
    btn = card.locator("[data-action='bump']")
    if btn.count() > 0:
        return btn.first
    maybe = card.get_by_role("button", name=re.compile(r"\bbump\b", re.I))
    if maybe.count() > 0:
        return maybe.first
    return None

def discover_jobs(page: Page) -> List[Dict[str, Optional[str]]]:
    jobs: List[Dict[str, Optional[str]]] = []
    cards = find_job_cards(page)
    total = cards.count()
    for i in range(total):
        card = cards.nth(i)
        jobs.append({
            "jid": extract_jid(card),
            "title": extract_title(card),
            "bump_btn": find_bump_button(card)
        })
    return jobs

# ---------------------- LIVE HELPERS ----------------------
def detect_insufficient_coins_modal(page: Page) -> bool:
    try:
        modal = page.locator("#insufficientCoinsModal")
        if modal.count() > 0 and modal.is_visible(timeout=500):
            return True
    except Exception:
        pass
    try:
        h = page.get_by_text(re.compile(r"insufficient coins", re.I))
        return h.count() > 0
    except Exception:
        return False

def close_any_modal(page: Page) -> None:
    for sel in [
        ".insufficient-cancel-modal",
        "[data-dismiss='modal']",
        ".modal .fast-button",
        ".modal button",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=800)
                page.wait_for_timeout(300)
                return
        except Exception:
            pass
    try:
        page.locator(".modal-backdrop,.sheet-backdrop,.modal").first.click(timeout=500)
    except Exception:
        pass

# ---------------------- ONE CYCLE ----------------------
def run_cycle() -> None:
    Path(STORAGE_STATE).parent.mkdir(parents=True, exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    conn = db.get_conn(DB_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(storage_state=STORAGE_STATE, viewport={"width": 1366, "height": 900})
        page = context.new_page()

        ensure_logged_in(page)
        page.goto(DASH_URL, wait_until="domcontentloaded")
        goto_jobs_list(page)

        safe_full_screenshot(page, "data/jobs_list.png")

        jobs = discover_jobs(page)
        print(f"[INFO] Found {len(jobs)} job cards.")
        when = now_iso()

        to_process = jobs if LIMIT_JOBS == 0 else jobs[:LIMIT_JOBS]

        for j in to_process:
            jid   = j["jid"]
            title = j["title"]
            btn   = j["bump_btn"]
            has_bump = btn is not None

            if not jid:
                print(f"  [SKIP] Missing jid | title={title}")
                continue
            if jid == COYID:
                print(f"  [SKIP] Ignoring company id jid={jid} | title={title}")
                continue

            db.upsert_job(conn, jid, title, when)

            if DRY_RUN or not has_bump:
                print(f"  [DRY] Would bump jid={jid} | title={title} | bump={'yes' if has_bump else 'no'}")
                try:
                    db.insert_bump(conn, jid, when, None, "dry-run")
                except Exception:
                    pass
                continue

            # LIVE path
            outcome = "bump-attempted"
            coins_used = None
            try:
                btn.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1200)

                if detect_insufficient_coins_modal(page):
                    outcome = "insufficient-coins"
                    coins_used = 0
                    safe_full_screenshot(page, f"data/insufficient_{jid}.png")
                    close_any_modal(page)
                else:
                    outcome = "bumped"
                    safe_full_screenshot(page, f"data/after_bump_{jid}.png")

            except Exception as e:
                print(f"  [ERR] Live bump failed for jid={jid} | title={title}: {e}")
                outcome = "bump-failed"

            db.insert_bump(conn, jid, when, coins_used, outcome)
            print(f"  [LIVE] jid={jid} | title={title} | outcome={outcome} | coins_used={coins_used}")
            page.wait_for_timeout(800)

        context.storage_state(path=STORAGE_STATE)
        context.close()
        browser.close()

    print("[DONE] Cycle complete. Screenshot: data/jobs_list.png; DB: data/fastjob.db")

# ---------------------- SCHEDULER LOOP ----------------------
def main():
    if EVERY_SECONDS > 0:
        print(f"[LOOP] Starting continuous mode: EVERY_SECONDS={EVERY_SECONDS} (Ctrl+C to stop)")
        try:
            while True:
                start = datetime.now()
                print(f"\n=== Cycle @ {start.isoformat(timespec='seconds')}Z ===")
                run_cycle()
                elapsed = (datetime.now() - start).total_seconds()
                sleep_left = max(0, EVERY_SECONDS - int(elapsed))
                if sleep_left > 0:
                    print(f"[LOOP] Sleeping {sleep_left}s\n")
                    time.sleep(sleep_left)
        except KeyboardInterrupt:
            print("\n[LOOP] Stopped by user.")
    else:
        run_cycle()

if __name__ == "__main__":
    main()
