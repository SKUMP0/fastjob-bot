import re
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError
from dotenv import load_dotenv
import os

load_dotenv()

EMAIL = os.getenv("FASTJOBS_EMAIL", "")
PASSWORD = os.getenv("FASTJOBS_PASSWORD", "")
LOGIN_URL = os.getenv("FASTJOBS_LOGIN_URL", "https://employer.fastjobs.sg/site/login/")
STORAGE_STATE = os.getenv("STORAGE_STATE", "storage/state.json")

assert EMAIL and PASSWORD, "Set FASTJOBS_EMAIL and FASTJOBS_PASSWORD in .env"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SCREEN_BEFORE = DATA_DIR / "login_before.png"
SCREEN_AFTER  = DATA_DIR / "login_after.png"

def main():
    with sync_playwright() as p:
        # slow_mo adds a small delay to each action so the site can react
        browser = p.chromium.launch(headless=False, slow_mo=150, args=["--start-maximized"])
        context = browser.new_context(
            viewport={"width": 1366, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        )
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.screenshot(path=str(SCREEN_BEFORE), full_page=True)

        # Handle cookie/consent banners if present (best-effort)
        for name in ["Accept", "I agree", "Agree", "OK", "Got it"]:
            try:
                page.get_by_role("button", name=re.compile(name, re.I)).click(timeout=1500)
                break
            except Exception:
                pass

        # Wait for the two fields to be visible
        email_loc = page.get_by_role("textbox", name="Username or email")
        pwd_loc   = page.get_by_role("textbox", name="Password")
        email_loc.wait_for(state="visible", timeout=10000)
        pwd_loc.wait_for(state="visible", timeout=10000)

        # Type like a human (some sites block .fill)
        email_loc.click()
        # clear any existing text
        try:
            email_loc.clear()
        except Exception:
            pass
        page.keyboard.insert_text(EMAIL)

        pwd_loc.click()
        try:
            pwd_loc.clear()
        except Exception:
            pass
        page.keyboard.insert_text(PASSWORD)

        # Click Login
        page.get_by_role("button", name="Login").click()

        # Wait for navigation away from /site/login
        try:
            page.wait_for_url(re.compile(r"https://employer\.fastjobs\.sg/(?!site/login).+"), timeout=15000)
        except TimeoutError:
            # Fallback: wait for network idle and then inspect URL
            page.wait_for_load_state("networkidle")

        page.screenshot(path=str(SCREEN_AFTER), full_page=True)
        print(f"[INFO] Current URL after submit: {page.url}")

        # Heuristic: success if not on the login page anymore
        success = "/site/login" not in page.url
        if success:
            print("[+] Logged in (likely). Saving session.")
            Path(STORAGE_STATE).parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=STORAGE_STATE)
        else:
            # Try to detect common error messages
            try:
                err = page.get_by_text(re.compile(r"(invalid|incorrect|try again|failed)", re.I)).first
                err.wait_for(timeout=1200)
                print("[!] Login error message likely present on page.")
            except Exception:
                pass
            print("[!] Still appears to be on the login page. See screenshots in the data/ folder for clues.")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
