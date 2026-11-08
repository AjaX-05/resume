import json
import logging
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from browser import launch_chromium
from config import settings

logger = logging.getLogger(__name__)


def get_jobright_cookies() -> dict[str, str]:
    """
    Return Jobright session cookies using whichever method is available:
    1. JOBRIGHT_MANUAL_COOKIE in .env  →  parse and return immediately (no browser)
    2. Cached auth file still valid    →  return cached cookies
    3. Playwright auto-login           →  open browser, log in, save, return
    """

    # ── Option 1: manually-pasted cookie string (office laptop / corporate machines) ──
    if settings.jobright_manual_cookie.strip():
        logger.info("Using JOBRIGHT_MANUAL_COOKIE from .env (Playwright not needed)")
        return _parse_cookie_string(settings.jobright_manual_cookie)

    # ── Option 2: cached Playwright session ──
    auth_file = settings.auth_dir / "jobright_auth.json"
    settings.auth_dir.mkdir(parents=True, exist_ok=True)
    if auth_file.exists():
        cookies = _load_cookies(auth_file)
        if cookies:
            logger.debug("Using cached Jobright Playwright session")
            return cookies

    # ── Option 3: Playwright auto-login ──
    return _playwright_login(auth_file)


def _parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse 'name=value; name2=value2' into a dict."""
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _playwright_login(auth_file: Path) -> dict[str, str]:
    logger.info("Opening browser to log into Jobright.ai (Playwright)...")
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=False, slow_mo=300)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page.goto("https://jobright.ai/login", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)

        # Fill email
        for selector in [
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="Email" i]',
            "#email",
        ]:
            try:
                page.wait_for_selector(selector, timeout=5_000, state="visible")
                page.fill(selector, settings.jobright_email)
                logger.info(f"Filled email ({selector})")
                break
            except PWTimeout:
                continue
        else:
            screenshot = settings.auth_dir / "login_debug.png"
            page.screenshot(path=str(screenshot))
            browser.close()
            raise RuntimeError(
                f"Could not find email input. Screenshot: {screenshot}\n"
                "TIP: Set JOBRIGHT_MANUAL_COOKIE in .env to bypass Playwright entirely."
            )

        # Fill password
        for selector in ['input[type="password"]', "#password", '[name="password"]']:
            try:
                page.wait_for_selector(selector, timeout=5_000, state="visible")
                page.fill(selector, settings.jobright_password)
                break
            except PWTimeout:
                continue

        # Submit
        for selector in ['button[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Log in")']:
            try:
                page.click(selector, timeout=5_000)
                break
            except Exception:
                continue

        try:
            page.wait_for_function(
                "() => !window.location.pathname.includes('login')",
                timeout=30_000,
            )
        except PWTimeout:
            screenshot = settings.auth_dir / "login_debug.png"
            page.screenshot(path=str(screenshot))
            browser.close()
            raise RuntimeError(
                f"Login did not redirect. Screenshot: {screenshot}\n"
                "TIP: Set JOBRIGHT_MANUAL_COOKIE in .env to bypass Playwright entirely."
            )

        context.storage_state(path=str(auth_file))
        browser.close()

    logger.info(f"Jobright Playwright session saved → {auth_file}")
    return _load_cookies(auth_file)


def _load_cookies(auth_file: Path) -> dict[str, str]:
    try:
        state = json.loads(auth_file.read_text(encoding="utf-8"))
        return {c["name"]: c["value"] for c in state.get("cookies", [])}
    except Exception as e:
        logger.warning(f"Could not read Jobright auth file: {e}")
        return {}


def cookies_to_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())
