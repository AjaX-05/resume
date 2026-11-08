"""
Jobright.ai — primary job source.

Listings and job pages are a Cloudflare-backed SPA. Plain HTTP gets 403, and
Firecrawl cookie-header scrapes miss `/jobs/info/{id}` URLs. We drive installed
Chrome via Playwright instead. Login is optional: the public listing already
returns remote US roles with salary and H1B hints.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from firecrawl import FirecrawlApp
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser import _UA, launch_chromium
from config import settings
from extract import ExtractedJob, to_job
from job_meta import parse_posted
from models import Job

logger = logging.getLogger(__name__)

_P = "locationName=United+States&isRemote=true&sortBy=date"

SEARCH_URLS = [
    f"https://jobright.ai/jobs/Software-Engineering?{_P}",
    f"https://jobright.ai/jobs/Machine-Learning?{_P}",
    f"https://jobright.ai/jobs/Artificial-Intelligence?{_P}",
    f"https://jobright.ai/jobs/Data-Science?{_P}",
    f"https://jobright.ai/jobs/Data-Engineering?{_P}",
    f"https://jobright.ai/jobs/Solutions-Architect?{_P}",
    f"https://jobright.ai/jobs/Software-Architecture?{_P}",
    f"https://jobright.ai/jobs/Engineering-Management?{_P}",
]

JOB_URL_RE = re.compile(
    r"https://(?:www\.)?jobright\.ai/jobs/info/[a-f0-9]+",
    re.I,
)

_ATS_HOSTS = (
    "greenhouse.io",
    "grnh.se",
    "careerpuck.com",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "workable.com",
    "gem.com",
    "rippling.com",
    "bamboohr.com",
    "icims.com",
    "jobvite.com",
    "lanl.jobs",
    "successfactors.com",
    "oraclecloud.com",
    "taleo.net",
    "applytojob.com",
    "paylocity.com",
)

_SKIP_ORIGINAL_HOSTS = (
    "jobright.ai",
    "wellfound.com",
    "crunchbase.com",
    "accounts.google.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
)

_pw = None
_browser = None
_context = None
_page = None
_listing_posted: dict[str, str] = {}


def init_auth(app: FirecrawlApp) -> bool:
    """Start Chrome. Login if credentials exist; otherwise scrape logged-out."""
    global _pw, _browser, _context, _page
    close_session()
    _listing_posted.clear()
    try:
        _pw = sync_playwright().start()
        _browser = launch_chromium(_pw, headless=True)
        storage = settings.auth_dir / "jobright_auth.json"
        settings.auth_dir.mkdir(parents=True, exist_ok=True)
        context_kwargs = {
            "user_agent": _UA,
            "viewport": {"width": 1400, "height": 900},
        }
        if storage.exists() and not settings.jobright_manual_cookie.strip():
            context_kwargs["storage_state"] = str(storage)
            logger.info("Jobright using saved session %s", storage.name)

        _context = _browser.new_context(**context_kwargs)
        _context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        _page = _context.new_page()

        if settings.jobright_manual_cookie.strip():
            _inject_cookie_header(settings.jobright_manual_cookie)
            logger.info("Jobright using JOBRIGHT_MANUAL_COOKIE")
        elif settings.jobright_email and settings.jobright_password:
            if _login_on_page():
                _context.storage_state(path=str(storage))
                logger.info("Jobright signed in as %s", settings.jobright_email.split("@")[0] + "@…")
            else:
                logger.warning("Jobright login failed — continuing logged out")
        else:
            logger.info("Jobright running logged-out (public listings)")

        return True
    except Exception as e:
        logger.warning("Jobright browser failed to start: %s", e)
        close_session()
        return False


def close_session() -> None:
    global _pw, _browser, _context, _page
    for closer in (_page, _context, _browser):
        if closer is None:
            continue
        try:
            closer.close()
        except Exception:
            pass
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:
            pass
    _pw = _browser = _context = _page = None


def paged_url(base: str, page: int) -> str:
    return base if page == 1 else f"{base}&page={page}"


def scan_listings(app: FirecrawlApp, url: str) -> list[dict]:
    """Phase 1: render the Jobright search page and collect /jobs/info/ IDs."""
    if _page is None:
        logger.warning("Jobright scan skipped — browser not started")
        return []
    try:
        _page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            _page.wait_for_selector('a[href*="/jobs/info/"]', timeout=20_000)
        except Exception:
            body_preview = (_page.inner_text("body") or "")[:400].lower()
            if "just a moment" in body_preview or "verify you are human" in body_preview:
                logger.warning("Jobright listing hit a Cloudflare challenge — waiting")
                _page.wait_for_timeout(8000)
        for _ in range(4):
            _page.mouse.wheel(0, 2400)
            _page.wait_for_timeout(800)
    except Exception as e:
        logger.warning("Jobright listing navigation failed: %s", e)
        return []

    cards = _page.eval_on_selector_all(
        "a",
        """els => {
            const out = [];
            const seen = new Set();
            for (const e of els) {
                const href = e.href || '';
                const cut = href.split('?')[0];
                if (!cut.includes('/jobs/info/') || seen.has(cut)) continue;
                seen.add(cut);
                const root = e.closest('article, li, [role="listitem"]') || e.parentElement || e;
                out.push({ href: cut, text: (root.innerText || '').slice(0, 500) });
            }
            return out;
        }""",
    )
    seen: set[str] = set()
    listings: list[dict] = []
    for card in cards or []:
        raw = card.get("href", "") if isinstance(card, dict) else str(card)
        match = JOB_URL_RE.search(raw or "")
        if not match:
            continue
        job_url = match.group(0)
        if job_url in seen:
            continue
        seen.add(job_url)
        text = card.get("text", "") if isinstance(card, dict) else ""
        posted_iso, posted_label = parse_posted(text)
        if posted_iso:
            _listing_posted[job_url] = posted_iso
        listings.append(
            {
                "url": job_url,
                "title": "",
                "company": "",
                "salary_snippet": "",
                "location": "",
                "posted_at": posted_iso or "",
                "posted_label": posted_label or "",
            }
        )
    logger.info("  Jobright listing: %d job URLs from %s", len(listings), url.split("?")[0])
    return listings


def scrape_job(app: FirecrawlApp, url: str) -> Job | None:
    """Phase 2: open the job page in Chrome and parse the visible posting."""
    if _page is None:
        return None
    try:
        _page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            _page.wait_for_selector("h1", timeout=15_000)
        except Exception:
            pass
        _page.wait_for_timeout(800)
    except Exception as e:
        logger.warning("Jobright job navigation failed [%s]: %s", url[:60], e)
        return None

    page_title = _page.title() or ""
    headings = _page.locator("h1").all_inner_texts()
    body = _page.inner_text("body") or ""
    links = _page.eval_on_selector_all(
        "a",
        """els => els.map(e => ({
            href: e.href || '',
            text: ((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')).trim().slice(0, 80)
        }))""",
    )
    hrefs = [str(item.get("href") or "") for item in (links or []) if item.get("href")]

    title, company = _title_company(page_title, headings, body)
    if not title:
        logger.warning("Jobright job had no title [%s]", url[:60])
        return None

    apply = _best_apply_url(hrefs, url, links if isinstance(links, list) else None)
    location = _location_text(body)
    salary = _salary_text(body)
    posted_iso, posted_label = parse_posted(body)
    if not posted_iso:
        posted_iso = _listing_posted.get(url.split("?")[0], "")
        posted_label = posted_label or ""
    extracted = ExtractedJob(
        title=title.strip(),
        company=company.strip() or "Unknown",
        description=_description_text(body, title),
        skills=_skills(body),
        salary_text=salary,
        location_text=location,
        sponsorship_text=_sponsorship_text(body),
        apply_url=apply,
        posting_date=posted_iso or posted_label or "",
    )
    try:
        return to_job(extracted, source="jobright", source_url=url.split("?")[0])
    except Exception as e:
        logger.warning("Jobright job parse failed [%s]: %s", url[:60], e)
        return None


def _inject_cookie_header(raw: str) -> None:
    assert _context is not None
    cookies = []
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".jobright.ai",
                "path": "/",
            }
        )
    if cookies:
        _context.add_cookies(cookies)


def _login_on_page() -> bool:
    assert _page is not None
    try:
        _page.goto("https://jobright.ai/login", wait_until="domcontentloaded", timeout=30_000)
        _page.wait_for_timeout(1500)
        filled = False
        for selector in (
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="Email" i]',
            "#email",
        ):
            try:
                _page.wait_for_selector(selector, timeout=4000, state="visible")
                _page.fill(selector, settings.jobright_email)
                filled = True
                break
            except Exception:
                continue
        if not filled:
            return False
        for selector in ('input[type="password"]', "#password", '[name="password"]'):
            try:
                _page.fill(selector, settings.jobright_password)
                break
            except Exception:
                continue
        for selector in (
            'button[type="submit"]',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
        ):
            try:
                _page.click(selector, timeout=4000)
                break
            except Exception:
                continue
        _page.wait_for_function(
            "() => !window.location.pathname.includes('login')",
            timeout=25_000,
        )
        return True
    except Exception as e:
        logger.warning("Jobright login did not complete: %s", e)
        return False


def _title_company(page_title: str, headings: list[str], body: str) -> tuple[str, str]:
    head = page_title.split("|")[0].strip()
    if " @ " in head:
        role, company = head.rsplit(" @ ", 1)
        return role.strip(), company.strip()
    title = headings[0].strip() if headings else ""
    company = ""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if title:
        for i, ln in enumerate(lines):
            if ln == title and i > 0:
                prev = lines[i - 1]
                if prev and prev not in {"Overview", "Company", "APPLY NOW", "Apply on Employer Site"}:
                    company = re.sub(r"\s*·.*$", "", prev).strip()
                break
    return title, company


def _location_text(body: str) -> str:
    bits: list[str] = []
    if re.search(r"\bRemote\b", body):
        bits.append("Remote")
    if re.search(r"\bUnited States\b", body):
        bits.append("United States")
    return ", ".join(bits) or "Remote, United States"


def _salary_text(body: str) -> str:
    m = re.search(
        r"\$[\d.]+\s*[Kk](?:/yr)?\s*[-–]\s*\$[\d.]+\s*[Kk](?:/yr)?",
        body,
    )
    if m:
        return m.group(0)
    m = re.search(r"\$[\d.]+\s*[Kk](?:/yr)?", body)
    return m.group(0) if m else ""


def _sponsorship_text(body: str) -> str:
    if re.search(r"H-?1B Sponsor Likely", body, re.I):
        return "H1B Sponsor Likely"
    if re.search(r"does not sponsor|no visa sponsorship|not sponsor", body, re.I):
        return "does not sponsor"
    return ""


def _skills(body: str) -> list[str]:
    marker = "Must-Have Skills for This Role"
    if marker not in body:
        return []
    chunk = body.split(marker, 1)[1]
    chunk = re.split(r"Optimize my Resume|Get Referral|Apply Faster", chunk, maxsplit=1)[0]
    skills = [ln.strip() for ln in chunk.splitlines() if ln.strip() and len(ln.strip()) < 60]
    return skills[:20]


def _description_text(body: str, title: str) -> str:
    text = body
    for noise in (
        "SIGN IN",
        "JOIN NOW",
        "Boost Your Interview Chances",
        "Keep Exploring Remote Jobs",
        "Apply Faster with Autofill Plugin",
        "3× Higher Response via Email Outreach",
        "Company data provided by crunchbase",
    ):
        text = text.replace(noise, "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if title and title in text:
        text = text.split(title, 1)[-1].strip()
        text = f"{title}\n{text}"
    return text[:20_000]


def _best_apply_url(
    hrefs: list[str], fallback: str, labeled: list[dict] | None = None
) -> str:
    """Prefer Original Job Post over Jobright Autofill / aggregator URLs."""
    for item in labeled or []:
        text = str(item.get("text") or "").lower()
        href = str(item.get("href") or "").strip()
        if "original job post" not in text and "original job" not in text:
            continue
        host = urlparse(href).netloc.lower()
        if href.startswith("http") and not any(skip in host for skip in _SKIP_ORIGINAL_HOSTS):
            return href
    for raw in hrefs:
        host = urlparse(raw).netloc.lower()
        if any(h in host for h in _ATS_HOSTS):
            return raw
    clean = fallback.split("?")[0]
    return clean
