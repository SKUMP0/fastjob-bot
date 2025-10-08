# fastjob_bot.py — robust modal open/confirm/close + header coins delta + popup killer
#
# USAGE (PowerShell)
#   # DRY (no coins), single run
#   $env:DRY_RUN="true"
#   $env:LIMIT_JOBS="0"
#   python .\fastjob_bot.py
#
#   # LIVE, all jobs (WILL spend coins)
#   $env:DRY_RUN="false"
#   $env:LIMIT_JOBS="0"
#   python .\fastjob_bot.py
#
#   # Optional loop
#   $env:EVERY_SECONDS="3600"
#   python .\fastjob_bot.py

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from dotenv import load_dotenv
from playwright.sync_api import (
    sync_playwright, Page, TimeoutError, Locator, BrowserContext
)

import db  # db.py (get_conn, upsert_job, insert_bump)

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
LIMIT_JOBS     = int(os.getenv("LIMIT_JOBS", "0"))       # 0 = all jobs
EVERY_SECONDS  = int(os.getenv("EVERY_SECONDS", "0"))    # 0 = run once

JOBS_URLS = [
    f"{BASE_URL}/p/job/manage/?coyid={COYID}",
    f"{BASE_URL}/p/job/?coyid={COYID}",
    f"{BASE_URL}/p/jobs/manage/?coyid={COYID}",
]

# ---------------------- regex helpers ----------------------
JID_RE      = re.compile(r"[?&]jid=(\d+)\b", re.I)
STAT_ID_RE  = re.compile(r"jobAdStat_(?:views|applications|shares|messages|savedjob|invitation)_(\d+)$", re.I)
COIN_NUM_RE = re.compile(r"(\d[\d,]*)\s*coin", re.I)

# ---------------------- utils ----------------------
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

def _read_int_from_text(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    try:
        return int(text.replace(",", "").strip())
    except Exception:
        return None

# ---------------------- login & nav ----------------------
def ensure_logged_in(page: Page) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    try:
        email_box = page.get_by_role("textbox", name=re.compile(r"Username|email", re.I))
        pwd_box   = page.get_by_role("textbox", name=re.compile(r"Password", re.I))
        email_box.wait_for(state="visible", timeout=2500)
        pwd_box.wait_for(state="visible", timeout=2500)

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
        pass  # probably already logged in

def close_global_popups(page: Page) -> None:
    # close marketing/feedback/popovers
    for sel in [
        "button[aria-label='Close']",
        ".modal.show .btn-close, .modal.show [data-dismiss='modal'], .modal.show .close",
        ".swal2-container .swal2-cancel, .swal2-container .swal2-close, .swal2-container .swal2-confirm",
        ".toast .btn-close",
        ".intercom-namespace .intercom-close-button",
        ".fc-modal .fc-close",
        ".fast-modal.open .btn-close, .fast-modal.open [data-dismiss='modal']",
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=600)
                page.wait_for_timeout(200)
        except Exception:
            pass

def goto_jobs_list(page: Page) -> None:
    close_global_popups(page)

    for name in ["Manage Jobs", "My Jobs", "Jobs", "Job Listings", "Manage Job"]:
        try:
            page.get_by_role("link", name=re.compile(rf"^{name}$", re.I)).first.click(timeout=1500)
            page.wait_for_load_state("networkidle")
            close_global_popups(page)
            if page.locator("div.job-ad-flexbox").count() > 0:
                return
        except Exception:
            pass
    for url in JOBS_URLS:
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            close_global_popups(page)
            if page.locator("div.job-ad-flexbox").count() > 0:
                return
        except Exception:
            continue
    raise RuntimeError("Could not reach a Jobs list page with job cards.")

# ---------------------- extraction ----------------------
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
    more = card.locator("button:has-text('Bump')")
    if more.count() > 0:
        return more.first
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

# ---------------------- coins: header & modal readers ----------------------
def read_header_coins(page: Page) -> Optional[int]:
    try:
        txt = page.locator(".header-credits-available").first.inner_text(timeout=900)
        m = COIN_NUM_RE.search(txt or "")
        if m:
            return _read_int_from_text(m.group(1))
    except Exception:
        pass
    try:
        txt = page.locator(".summary-content .summary-title").first.inner_text(timeout=800)
        m = COIN_NUM_RE.search(txt or "")
        if m:
            return _read_int_from_text(m.group(1))
    except Exception:
        pass
    return None

# ---------------------- bump modal: open / confirm / close ----------------------
def _any_bump_modal(page: Page) -> Locator:
    # Support both fast-modal and bootstrap-like modal
    return page.locator(
        "#bump-schedule-modal.fast-modal.open, "
        ".fast-modal.open:has(#order-summary-content), "
        ".modal.show:has(.order-summary__total), "
        ".modal.show:has(.button-content:has-text('Bump this job'))"
    )

def wait_for_bump_modal(page: Page, timeout_ms: int = 8000) -> Optional[Locator]:
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        loc = _any_bump_modal(page)
        if loc.count() > 0 and loc.first.is_visible():
            return loc.first
        page.wait_for_timeout(120)
    return None

def wait_modal_closed(page: Page, timeout_ms: int = 6000) -> bool:
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        if _any_bump_modal(page).count() == 0 and page.locator(".modal-backdrop").count() == 0:
            return True
        page.wait_for_timeout(120)
    return False

def force_close_bump_modal(page: Page) -> None:
    # try close button
    for sel in [
        ".fast-modal.open .btn-close", ".fast-modal.open [data-dismiss='modal']",
        ".modal.show .btn-close",     ".modal.show [data-dismiss='modal']",
        ".modal.show .close"
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=600)
                page.wait_for_timeout(250)
                if wait_modal_closed(page, 2500):
                    return
        except Exception:
            pass
    # try ESC
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        if wait_modal_closed(page, 2500):
            return
    except Exception:
        pass
    # try clicking backdrop
    try:
        bd = page.locator(".modal-backdrop").first
        if bd.count() > 0 and bd.is_visible():
            bd.click(timeout=600)
            page.wait_for_timeout(200)
            if wait_modal_closed(page, 2500):
                return
    except Exception:
        pass
    # last resort: remove nodes with JS
    try:
        page.evaluate("""
            () => {
              for (const sel of ['.fast-modal.open','.modal.show','.modal-backdrop']) {
                document.querySelectorAll(sel).forEach(n => n.remove());
              }
              document.body.style.overflow = '';
            }
        """)
    except Exception:
        pass

def read_modal_cost_and_balance(modal: Locator) -> Tuple[Optional[int], Optional[int]]:
    coins_cost: Optional[int] = None
    coins_after: Optional[int] = None
    try:
        t = modal.locator(".order-summary__total").first.inner_text(timeout=800)
        m = COIN_NUM_RE.search(t or "")
        if m:
            coins_cost = _read_int_from_text(m.group(1))
    except Exception:
        pass
    try:
        t = modal.locator(".coin-balance").first.inner_text(timeout=800)
        m = COIN_NUM_RE.search(t or "")
        if m:
            coins_after = _read_int_from_text(m.group(1))
    except Exception:
        pass
    return coins_cost, coins_after

def click_confirm_in_modal(modal: Locator, page: Page) -> bool:
    # exact button per your HTML
    btn = modal.locator("button.button-container:has(.button-content:has-text('Bump this job'))").first
    try:
        if btn.count() > 0:
            btn.scroll_into_view_if_needed(timeout=1500)
            btn.click(timeout=1800)
            page.wait_for_timeout(350)
            return True
    except Exception:
        pass
    # fallback JS click on the .button-content
    try:
        modal.evaluate("""
            (root) => { const b = root.querySelector("button.button-container .button-content"); if (b) b.click(); }
        """)
        page.wait_for_timeout(300)
        return True
    except Exception:
        return False

def detect_visible_insufficient(page: Page) -> bool:
    try:
        loc = page.locator(".fast-modal.open:has-text('Insufficient'), .modal.show:has-text('Insufficient')")
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False

# ---------------------- post-bump coin detection ----------------------
def coins_from_toast_or_body(page: Page) -> Optional[int]:
    texts: List[str] = []
    for sel in [
        "[role='alert']",
        ".toast", ".alert", "#toast-container",
        ".swal2-popup", ".modal-dialog",
        "[class*='toast']", "[class*='alert']",
    ]:
        try:
            arr = page.locator(sel).all_inner_texts()
            if arr:
                texts.extend([_clean_text(a) for a in arr if a and a.strip()])
        except Exception:
            pass
    for t in texts:
        m = COIN_NUM_RE.search(t)
        if m:
            return _read_int_from_text(m.group(1))
    return None

def coins_from_activity_pages(page: Page, jid: Optional[str], title: Optional[str]) -> Optional[int]:
    for name in ["Credits", "Wallet", "Transactions", "Activity", "Logs", "Usage", "Billing"]:
        try:
            link = page.get_by_role("link", name=re.compile(name, re.I)).first
            if link.count() == 0:
                continue
            link.click(timeout=1500)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            safe_full_screenshot(page, f"data/activity_{name.lower()}.png")
            body = "\n".join(page.locator("body").all_inner_texts() or [])
            lines = re.findall(r".{0,80}bump.{0,80}", body, flags=re.I)
            candidates = lines if lines else [body]
            for text in candidates:
                m = COIN_NUM_RE.search(text)
                if m:
                    return _read_int_from_text(m.group(1))
        except Exception:
            continue
    return None

# ---------------------- one cycle ----------------------
def run_cycle() -> None:
    Path(STORAGE_STATE).parent.mkdir(parents=True, exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    conn = db.get_conn(DB_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=80)
        context = browser.new_context(storage_state=STORAGE_STATE, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.set_default_timeout(10000)

        ensure_logged_in(page)
        page.goto(DASH_URL, wait_until="domcontentloaded")
        close_global_popups(page)
        goto_jobs_list(page)

        safe_full_screenshot(page, "data/jobs_after.png")

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
            coins_used: Optional[int] = None

            # header coins BEFORE
            coins_before = read_header_coins(page)

            try:
                # open modal
                btn.scroll_into_view_if_needed(timeout=1500)
                btn.click(timeout=2000)
                page.wait_for_timeout(450)
                close_global_popups(page)

                modal = wait_for_bump_modal(page, timeout_ms=9000)
                if not modal:
                    safe_full_screenshot(page, f"data/no_modal_{jid}.png")
                    print(f"  [ERR] Modal not found for jid={jid}")
                    outcome = "modal-not-found"
                    db.insert_bump(conn, jid, when, None, outcome)
                    # ensure no leftover overlay before moving on
                    force_close_bump_modal(page)
                    continue

                # read cost/after (pre-confirm snapshot)
                _cost, _after = read_modal_cost_and_balance(modal)

                # confirm
                clicked = click_confirm_in_modal(modal, page)
                if not clicked:
                    safe_full_screenshot(page, f"data/confirm_click_fail_{jid}.png")
                    outcome = "bump-failed"
                    db.insert_bump(conn, jid, when, None, outcome)
                    force_close_bump_modal(page)
                    continue

                # give time for processing
                page.wait_for_timeout(1200)

                # strict insufficient detector
                if detect_visible_insufficient(page):
                    outcome = "insufficient-coins"
                    coins_used = 0
                else:
                    # compute via header delta
                    coins_after = read_header_coins(page)
                    if coins_before is not None and coins_after is not None and coins_after <= coins_before:
                        delta = coins_before - coins_after
                        if delta > 0:
                            coins_used = delta
                    # modal re-check if still open with updated balance
                    m2 = wait_for_bump_modal(page, timeout_ms=1200)
                    if coins_used is None and m2:
                        _, modal_after2 = read_modal_cost_and_balance(m2)
                        if coins_before is not None and modal_after2 is not None and modal_after2 <= coins_before:
                            delta = coins_before - modal_after2
                            if delta > 0:
                                coins_used = delta
                    # final fallbacks
                    if coins_used is None:
                        coins_used = coins_from_toast_or_body(page)
                    if coins_used is None:
                        coins_used = coins_from_activity_pages(page, jid=jid, title=title)

                    outcome = "bumped" if (coins_used is not None and coins_used >= 0) else "bumped-unknown-coins"

                safe_full_screenshot(page, f"data/after_bump_{jid}.png")

            except Exception as e:
                print(f"  [ERR] Live bump failed for jid={jid} | title={title}: {e}")
                outcome = "bump-failed"

            # ALWAYS close any modal/backdrop before next job
            force_close_bump_modal(page)
            wait_modal_closed(page, timeout_ms=3000)

            db.insert_bump(conn, jid, when, coins_used, outcome)
            print(f"  [LIVE] jid={jid} | title={title} | outcome={outcome} | coins_used={coins_used}")
            page.wait_for_timeout(500)

        context.storage_state(path=STORAGE_STATE)
        context.close()
        browser.close()

    print("[DONE] Cycle complete. Screenshot: data/jobs_after.png; DB: data/fastjob.db")

# ---------------------- scheduler loop ----------------------
def interactive_interval_if_needed() -> int:
    if EVERY_SECONDS > 0:
        return EVERY_SECONDS
    try:
        ans = input("Run every N seconds? (blank for single run): ").strip()
        if ans == "":
            return 0
        return max(0, int(ans))
    except Exception:
        return 0

def main():
    interval = interactive_interval_if_needed()
    if interval > 0:
        print(f"[LOOP] Starting continuous mode: EVERY_SECONDS={interval} (Ctrl+C to stop)")
        try:
            while True:
                start = datetime.now()
                print(f"\n=== Cycle @ {start.isoformat(timespec='seconds')}Z ===")
                run_cycle()
                elapsed = (datetime.now() - start).total_seconds()
                sleep_left = max(0, interval - int(elapsed))
                if sleep_left > 0:
                    print(f"[LOOP] Sleeping {sleep_left}s\n")
                    time.sleep(sleep_left)
        except KeyboardInterrupt:
            print("\n[LOOP] Stopped by user.")
    else:
        run_cycle()

if __name__ == "__main__":
    main()
