# fastjob_bot.py — resilient multi-bump with render/visibility gates
import os, re, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict
from urllib.parse import urlparse, parse_qs  # NEW

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, Locator, TimeoutError

import db

load_dotenv()

BASE_URL  = "https://employer.fastjobs.sg"
COYID     = os.getenv("FASTJOBS_COYID", "").strip() or "235927"  # keep default but allow auto-detect
EMAIL     = os.getenv("FASTJOBS_EMAIL", "")
PASSWORD  = os.getenv("FASTJOBS_PASSWORD", "")
LOGIN_URL = os.getenv("FASTJOBS_LOGIN_URL", f"{BASE_URL}/site/login/")
DASH_URL  = f"{BASE_URL}/p/my-activity/dashboard/?coyid={COYID}"

STORAGE_STATE = os.getenv("STORAGE_STATE", "storage/state.json")
DB_PATH       = os.getenv("DB_PATH", "data/fastjob.db")

DRY_RUN       = os.getenv("DRY_RUN", "true").lower() == "true"
LIMIT_JOBS    = int(os.getenv("LIMIT_JOBS", "0"))
EVERY_SECONDS = int(os.getenv("EVERY_SECONDS", "0"))
SLOW_MO_MS    = int(os.getenv("SLOW_MO_MS", "0"))

# Legacy fallbacks (kept as last-resort)
LEGACY_JOBS_URLS = [
    f"{BASE_URL}/p/job/manage/?coyid={COYID}",
    f"{BASE_URL}/p/job/?coyid={COYID}",
    f"{BASE_URL}/p/jobs/manage/?coyid={COYID}",
]

JID_RE     = re.compile(r"[?&]jid=(\d+)\b", re.I)
STAT_ID_RE = re.compile(r"jobAdStat_(?:views|applications|shares|messages|savedjob|invitation)_(\d+)$", re.I)

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0"," ")).strip()

def safe_full_screenshot(page: Page, path: str) -> None:
    try:
        page.screenshot(path=path, full_page=True, timeout=6000)
    except Exception:
        try:
            page.screenshot(path=path, timeout=3000)
        except Exception:
            pass

def save_html(page: Page, path: str) -> None:
    try:
        html = page.content()
        Path(path).write_text(html, encoding="utf-8")
    except Exception:
        pass

# ---------- UI hygiene (only removes truly global overlays) ----------

def ensure_clean_ui(page: Page) -> None:
    # Close random promos/toasts/backdrops that intercept clicks.
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        for sel in [
            ".modal-backdrop,.sheet-backdrop",
            ".swal2-container,.swal2-shown,.swal2-popup",
            "[class*='toast'],#toast-container",
        ]:
            try:
                if page.locator(sel).count() > 0:
                    page.locator(sel).first.click(timeout=200, force=True)
                    page.evaluate("(s)=>document.querySelectorAll(s).forEach(n=>{try{n.remove()}catch(e){}})", sel)
            except Exception:
                pass
        page.wait_for_timeout(80)

# ---------- login & navigation ----------

def ensure_logged_in(page: Page) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    try:
        email = page.get_by_role("textbox", name=re.compile("Username|Email", re.I))
        pwd   = page.get_by_role("textbox", name=re.compile("Password", re.I))
        email.wait_for(state="visible", timeout=1500)
        if not EMAIL or not PASSWORD:
            raise RuntimeError("FASTJOBS_EMAIL / FASTJOBS_PASSWORD missing")
        email.click(); page.keyboard.insert_text(EMAIL)
        pwd.click();   page.keyboard.insert_text(PASSWORD)
        page.get_by_role("button", name=re.compile("Login|Sign in", re.I)).click()
        page.wait_for_load_state("networkidle")
    except TimeoutError:
        # probably already logged-in (session valid)
        pass

def detect_coyid(page: Page) -> Optional[str]:
    """
    Discover employer 'coyid' by scanning links on the current page.
    """
    try:
        hrefs = page.eval_on_selector_all("a[href*='coyid=']", "els => els.map(e => e.href)")
        for href in hrefs or []:
            try:
                q = parse_qs(urlparse(href).query)
                cid = (q.get("coyid") or q.get("COYID") or [None])[0]
                if cid and cid.isdigit():
                    return cid
            except Exception:
                continue
    except Exception:
        pass
    return None

def jobs_url_for(coyid: str) -> str:
    # Canonical (works in your manual test):
    return f"{BASE_URL.rstrip('/')}/p/my-activity/jobs/?coyid={coyid}"

def goto_jobs_list(page: Page):
    """
    Navigate to a Jobs listing page that contains job cards.
    Prefers the canonical /p/my-activity/jobs/?coyid=... route.
    Falls back to menu clicks and a few legacy URLs.
    """
    def has_cards() -> bool:
        try:
            return page.locator("div.job-ad-flexbox").count() > 0
        except Exception:
            return False

    # --- ensure we know COYID ---
    global COYID
    if not COYID or not str(COYID).isdigit():
        maybe = detect_coyid(page)
        if not maybe:
            # go to a neutral area where links exist, then detect
            page.goto(f"{BASE_URL.rstrip('/')}/p/my-activity/", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            maybe = detect_coyid(page)
        if maybe:
            COYID = maybe

    # --- try canonical first, if we have coyid ---
    if COYID and str(COYID).isdigit():
        try:
            ensure_clean_ui(page)
            page.goto(jobs_url_for(str(COYID)), wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            ensure_clean_ui(page)
            if has_cards():
                return
        except Exception:
            pass

    # --- menu clicks (various labels the site has used) ---
    names = ["Manage Jobs", "My Jobs", "English Jobs", "Jobs", "Job Listings", "Manage Job"]
    for name in names:
        try:
            ensure_clean_ui(page)
            lnk = page.get_by_role("link", name=re.compile(rf"^{name}$", re.I)).first
            if lnk.count() > 0:
                lnk.click(timeout=1500)
                page.wait_for_load_state("networkidle")
                ensure_clean_ui(page)
                if has_cards():
                    return
        except Exception:
            continue

    # --- direct URLs (canonical again if coyid known, then legacy list) ---
    url_candidates: List[str] = []
    if COYID and str(COYID).isdigit():
        url_candidates.append(jobs_url_for(str(COYID)))
    url_candidates.extend([u for u in LEGACY_JOBS_URLS if u not in url_candidates])

    for url in url_candidates:
        try:
            ensure_clean_ui(page)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            ensure_clean_ui(page)
            if has_cards():
                return
        except Exception:
            continue

    # one refresh of canonical then final check (if coyid known)
    if COYID and str(COYID).isdigit():
        try:
            page.goto(jobs_url_for(str(COYID)), wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            ensure_clean_ui(page)
            if has_cards():
                return
        except Exception:
            pass

    # diagnostics before failing
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_full_screenshot(page, f"data/jobs_nav_fail_{ts}.png")
    save_html(page, f"data/jobs_nav_fail_{ts}.html")
    raise RuntimeError("Could not reach a Jobs list page with job cards.")

# ---------- discovery helpers ----------

def find_job_cards(page: Page) -> Locator:
    return page.locator("div.job-ad-flexbox")

def extract_title(card: Locator) -> str:
    t = card.locator("h3 a span.job-ad-title")
    if t.count() > 0:
        return _clean(t.first.inner_text())
    h = card.locator("h3")
    return _clean(h.first.inner_text()) if h.count() > 0 else "Unknown Title"

def _jid_from_href(href: Optional[str]) -> Optional[str]:
    if not href: return None
    m = JID_RE.search(href); return m.group(1) if m else None

def _jid_from_stat_id(stat_id: Optional[str]) -> Optional[str]:
    if not stat_id: return None
    m = STAT_ID_RE.search(stat_id.strip()); return m.group(1) if m else None

def extract_jid(card: Locator) -> Optional[str]:
    links = card.locator("a[href*='jid=']")
    n = min(links.count(), 20)
    for i in range(n):
        jid = _jid_from_href(links.nth(i).get_attribute("href") or "")
        if jid: return jid
    stat = card.locator(".stat-number[id^='jobAdStat_']")
    if stat.count() > 0:
        jid = _jid_from_stat_id(stat.first.get_attribute("id") or "")
        if jid: return jid
    pub = card.locator("a[href*='/job-ad/']")
    if pub.count() > 0:
        href = pub.first.get_attribute("href") or ""
        m = re.search(r"/(\d{6,})/", href)
        if m: return m.group(1)
    return None

# ---------- render gates & buttons ----------

def wait_for_card_ready(card: Locator) -> None:
    try:
        card.wait_for(state="visible", timeout=4000)
    except Exception:
        pass
    try:
        card.locator("[data-action], .job-action-link, button").first.wait_for(state="visible", timeout=1500)
    except Exception:
        pass

def find_bump_button_dynamic(page: Page, card: Locator) -> Optional[Locator]:
    try:
        card.scroll_into_view_if_needed()
    except Exception:
        pass
    page.wait_for_timeout(120)
    try:
        card.hover(timeout=500)
    except Exception:
        pass
    page.wait_for_load_state("networkidle")
    wait_for_card_ready(card)

    btn = card.locator("[data-action='bump']").first
    if btn.count() > 0:
        try:
            btn.wait_for(state="visible", timeout=1200)
            return btn
        except Exception:
            pass

    alt = card.get_by_role("button", name=re.compile(r"\bBump this job\b", re.I)).filter(has_text=re.compile("Bump", re.I)).first
    if alt.count() > 0:
        try:
            alt.wait_for(state="visible", timeout=1200)
            return alt
        except Exception:
            pass

    try:
        card.get_by_role("button", name=re.compile("More Actions", re.I)).first.focus()
        page.wait_for_timeout(200)
    except Exception:
        pass
    btn = card.locator("[data-action='bump']").first
    if btn.count() > 0:
        try:
            btn.wait_for(state="visible", timeout=1200)
            return btn
        except Exception:
            pass

    return None

# ---------- modal ops ----------

def open_bump_modal_for(page: Page, card: Locator, jid: str) -> bool:
    btn = find_bump_button_dynamic(page, card)
    if not btn:
        return False

    ensure_clean_ui(page)

    try:
        btn.scroll_into_view_if_needed()
        btn.click(timeout=1500)
    except Exception:
        try:
            btn.click(timeout=1200, force=True)
        except Exception:
            pass

    try:
        page.locator("#bump-schedule-modal[data-modal-ready='true']").wait_for(state="visible", timeout=6000)
        page.wait_for_timeout(150)
        return True
    except TimeoutError:
        return False

def visible_insufficient_modal(page: Page) -> bool:
    dialog = page.locator("div[role='dialog'], .fast-modal.open")
    if dialog.count() == 0:
        return False
    vis = dialog.filter(has_text=re.compile(r"insufficient\s+coins", re.I))
    try:
        return vis.count() > 0 and vis.first.is_visible()
    except Exception:
        return False

def confirm_bump_in_modal(page: Page) -> bool:
    modal = page.locator("#bump-schedule-modal[data-modal-ready='true']").first
    if modal.count() == 0:
        return False

    btn = modal.locator("#order-summary-content button:has(.button-content:has-text('Bump this job'))").first
    if btn.count() == 0:
        btn = modal.get_by_role("button", name=re.compile(r"\bBump this job\b", re.I)).first
        if btn.count() == 0:
            return False

    try:
        btn.scroll_into_view_if_needed()
    except Exception:
        pass

    for i in range(3):
        try:
            if i == 0:
                btn.click(timeout=1200)
            elif i == 1:
                btn.click(timeout=1200, force=True)
            else:
                page.evaluate("(b)=>b.click()", btn)
            break
        except Exception:
            continue

    try:
        page.locator("#bump-schedule-modal").wait_for(state="hidden", timeout=5000)
        return True
    except TimeoutError:
        return False

# ---------- coins helpers ----------

def _int_from_text(s: Optional[str]) -> Optional[int]:
    if not s: return None
    m = re.search(r"(\d[\d,\.]*)", s)
    if not m: return None
    try:
        return int(m.group(1).replace(",", "").split(".")[0])
    except Exception:
        return None

def coins_from_header(page: Page) -> Optional[int]:
    try:
        txt = page.locator(".header-credits-available").first.inner_text(timeout=900)
        return _int_from_text(txt)
    except Exception:
        return None

def coins_used_from_modal_total(page: Page) -> Optional[int]:
    try:
        txt = page.locator("#bump-schedule-modal .order-summary__total").first.inner_text(timeout=600)
        return _int_from_text(txt)
    except Exception:
        return None

# ---------- cycle ----------

def run_cycle() -> None:
    Path("data").mkdir(exist_ok=True)
    conn = db.get_conn(DB_PATH)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=SLOW_MO_MS if SLOW_MO_MS > 0 else 0)
        context = browser.new_context(storage_state=STORAGE_STATE, viewport={"width": 1366, "height": 900})
        page = context.new_page()

        ensure_logged_in(page)
        goto_jobs_list(page)
        safe_full_screenshot(page, "data/jobs_after.png")

        cards = find_job_cards(page)
        jobs: List[Dict[str, object]] = []
        for i in range(cards.count()):
            c = cards.nth(i)
            jobs.append({"card": c, "jid": extract_jid(c), "title": extract_title(c)})

        print(f"[INFO] Found {len(jobs)} job cards.")
        when = now_iso()
        batch = jobs if LIMIT_JOBS == 0 else jobs[:LIMIT_JOBS]

        for j in batch:
            ensure_clean_ui(page)

            card: Locator = j["card"]  # re-used but button is re-found each time
            jid  = j["jid"]
            title = j["title"]

            if not jid:
                print(f"  [SKIP] Missing jid | title={title}")
                continue
            if jid == COYID:
                print(f"  [SKIP] Ignoring company id as jid={jid}")
                continue

            db.upsert_job(conn, jid, title, when)

            # DRY
            if DRY_RUN:
                btn = find_bump_button_dynamic(page, card)
                print(f"  [DRY] Would bump jid={jid} | title={title} | bump={'yes' if btn else 'no'}")
                db.insert_bump(conn, jid, when, None, "dry-run")
                continue

            # LIVE
            before = coins_from_header(page)
            opened = open_bump_modal_for(page, card, jid)
            if not opened:
                print(f"  [ERR] Modal not found for jid={jid}")
                db.insert_bump(conn, jid, when, None, "modal-not-found")
                continue

            if visible_insufficient_modal(page):
                db.insert_bump(conn, jid, when, 0, "insufficient-coins")
                for sel in [
                    "#insufficientCoinsModal [data-dismiss='modal']",
                    "#insufficientCoinsModal button",
                    "#bump-schedule-modal .fast-modal__close",
                    "#bump-schedule-modal [data-dismiss='modal']",
                ]:
                    try:
                        page.locator(sel).first.click(timeout=400)
                    except Exception:
                        pass
                ensure_clean_ui(page)
                print(f"  [LIVE] jid={jid} | title={title} | outcome=insufficient-coins | coins_used=0")
                continue

            ok = confirm_bump_in_modal(page)
            if not ok:
                ensure_clean_ui(page)
                if not open_bump_modal_for(page, card, jid) or not confirm_bump_in_modal(page):
                    db.insert_bump(conn, jid, when, None, "bump-failed")
                    print(f"  [LIVE] jid={jid} | title={title} | outcome=bump-failed | coins_used=None")
                    ensure_clean_ui(page)
                    continue

            after  = coins_from_header(page)
            used   = coins_used_from_modal_total(page)
            if used is None and before is not None and after is not None and after <= before:
                used = before - after
            outcome = "bumped" if used is None or used >= 0 else "bumped-unknown-coins"
            safe_full_screenshot(page, f"data/after_bump_{jid}.png")
            db.insert_bump(conn, jid, when, used, outcome)
            print(f"  [LIVE] jid={jid} | title={title} | outcome={outcome} | coins_used={used}")

            ensure_clean_ui(page)

        context.storage_state(path=STORAGE_STATE)
        context.close()
        browser.close()

    print("[DONE] Cycle complete. Screenshot: data/jobs_after.png; DB: data/fastjob.db")

# ---------- loop ----------

def interactive_interval_if_needed() -> int:
    if EVERY_SECONDS > 0:
        return EVERY_SECONDS
    try:
        ans = input("Run every N seconds? (blank for single run): ").strip()
        return 0 if ans == "" else max(0, int(ans))
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
                try:
                    run_cycle()
                except Exception as e:
                    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                    print(f"[WARN] Cycle failed: {e.__class__.__name__}: {e}")
                    # optional: add capture here if you kept a page reference
                    time.sleep(10)
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
