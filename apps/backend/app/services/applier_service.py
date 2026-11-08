"""Auto-applier service — Playwright-based job application engine.

Supports: Greenhouse (Phase 1), Lever/Ashby (Phase 2 stubs)

Flow per application:
  1. start_application() → launches background asyncio task
  2. Task navigates to apply URL, fills form, uploads resume PDF
  3. Task takes a full-page screenshot and sets status = "needs_review"
  4. Frontend polls status → shows screenshot in review modal
  5. User clicks Submit or Cancel
  6. approve/reject_application() signals the waiting task
  7. Task either clicks submit or closes browser
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

_STATE_ABBREV: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def _state_full_name(abbrev: str) -> str:
    return _STATE_ABBREV.get(abbrev.upper(), abbrev)


ApplyStatus = Literal[
    "pending",
    "filling",
    "needs_review",
    "submitting",
    "submitted",
    "failed",
    "cancelled",
]

_VAULT_PATH = Path(__file__).parents[2] / "data" / "vault.json"

# In-memory session registry — resets on backend restart
_sessions: dict[str, "ApplicationSession"] = {}


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

def load_vault() -> dict[str, Any]:
    if _VAULT_PATH.exists():
        return json.loads(_VAULT_PATH.read_text(encoding="utf-8"))
    return {}


def save_vault(vault: dict[str, Any]) -> None:
    _VAULT_PATH.write_text(json.dumps(vault, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class ApplicationSession:
    def __init__(self, application_id: str, apply_url: str) -> None:
        self.application_id = application_id
        self.apply_url = apply_url
        self.status: ApplyStatus = "pending"
        self.screenshot_b64: str | None = None
        self.filled_fields: dict[str, str] = {}
        self.error: str | None = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None
        self._approval_event = asyncio.Event()
        self._approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "apply_url": self.apply_url,
            "status": self.status,
            "has_screenshot": self.screenshot_b64 is not None,
            "filled_fields": self.filled_fields,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def get_session(application_id: str) -> ApplicationSession | None:
    return _sessions.get(application_id)


def list_sessions() -> list[dict[str, Any]]:
    return [s.to_dict() for s in _sessions.values()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_application(
    application_id: str,
    apply_url: str,
    resume_pdf_bytes: bytes,
) -> ApplicationSession:
    """Start an apply task in the background. Returns immediately."""
    if application_id in _sessions:
        existing = _sessions[application_id]
        if existing.status in ("pending", "filling", "needs_review", "submitting"):
            return existing
    session = ApplicationSession(application_id, apply_url)
    _sessions[application_id] = session
    vault = load_vault()
    asyncio.create_task(
        _run_apply_task(session, resume_pdf_bytes, vault),
        name=f"apply-{application_id[:8]}",
    )
    return session


async def approve_application(application_id: str) -> bool:
    session = _sessions.get(application_id)
    if not session or session.status != "needs_review":
        return False
    session._approved = True
    session._approval_event.set()
    return True


async def reject_application(application_id: str) -> bool:
    session = _sessions.get(application_id)
    if not session or session.status not in ("needs_review", "filling", "pending"):
        return False
    session._approved = False
    session._approval_event.set()
    session.status = "cancelled"
    return True


# ---------------------------------------------------------------------------
# Core task
# ---------------------------------------------------------------------------

async def _run_apply_task(
    session: ApplicationSession,
    resume_pdf_bytes: bytes,
    vault: dict[str, Any],
) -> None:
    from playwright.async_api import async_playwright

    headless = vault.get("settings", {}).get("headless_browser", True)
    timeout = vault.get("settings", {}).get("review_timeout_seconds", 600)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(resume_pdf_bytes)
        resume_path = tf.name

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            session.status = "filling"

            # Resolve aggregator URLs (Jobright) to the real underlying ATS URL
            apply_url = session.apply_url
            if "jobright.ai" in apply_url or "wellfound.com/jobs" in apply_url:
                resolved = await _resolve_aggregator_url(page, apply_url, vault)
                if resolved and resolved != apply_url:
                    logger.info(f"Resolved {apply_url[:40]} -> {resolved[:60]}")
                    apply_url = resolved
                    session.apply_url = resolved  # update session so UI shows real URL

            # Route to correct site adapter
            if "greenhouse.io" in apply_url or "grnh.se" in apply_url:
                filled = await _fill_greenhouse(page, apply_url, resume_path, vault)
            elif "ashbyhq.com" in apply_url or "app.ashbyhq" in apply_url:
                filled = await _fill_ashby(page, apply_url, resume_path, vault)
            elif "jobs.gem.com" in apply_url:
                filled = await _fill_gem(page, apply_url, resume_path, vault)
            elif "apply.workable.com" in apply_url or "workable.com" in apply_url:
                filled = await _fill_workable(page, apply_url, resume_path, vault)
            elif "dover.com" in apply_url or "app.dover" in apply_url:
                filled = await _fill_dover(page, apply_url, resume_path, vault)
            elif "lever.co" in apply_url or "hire.lever.co" in apply_url:
                filled = await _fill_lever(page, apply_url, resume_path, vault)
            elif "ats.rippling.com" in apply_url or "recruiting.rippling.com" in apply_url:
                filled = await _fill_rippling(page, apply_url, resume_path, vault)
            elif "smartrecruiters.com" in apply_url:
                filled = await _fill_smartrecruiters(page, apply_url, resume_path, vault)
            elif "bamboohr.com" in apply_url:
                filled = await _fill_bamboohr(page, apply_url, resume_path, vault)
            else:
                # Generic fallback — navigate and screenshot only
                logger.info(f"No specific adapter for {apply_url[:60]} — screenshot only")
                await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
                filled = {"site": "unknown", "note": "no adapter — review form manually"}

            session.filled_fields = filled

            # Full-page screenshot for review
            screenshot_bytes = await page.screenshot(full_page=True)
            session.screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            session.status = "needs_review"

            if not vault.get("settings", {}).get("approval_gate_enabled", True):
                # No gate — auto-approve
                session._approved = True
            else:
                try:
                    await asyncio.wait_for(
                        session._approval_event.wait(), timeout=float(timeout)
                    )
                except asyncio.TimeoutError:
                    session.status = "cancelled"
                    session.error = f"Review timed out after {timeout}s"
                    return

            if not session._approved:
                session.status = "cancelled"
                return

            session.status = "submitting"
            submitted = await _click_submit(page)
            if submitted:
                session.status = "submitted"
            else:
                session.status = "failed"
                session.error = "Could not find submit button"
            session.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        logger.error(f"Apply task failed for {session.application_id}: {e}", exc_info=True)
        session.status = "failed"
        session.error = str(e)[:400]
    finally:
        try:
            Path(resume_path).unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Greenhouse adapter
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Aggregator URL resolver
# ---------------------------------------------------------------------------

async def _resolve_aggregator_url(page: Any, agg_url: str, vault: dict[str, Any]) -> str:
    """Resolve a Jobright/Wellfound aggregator URL to the real ATS apply URL.

    Jobright shows a job detail page with an "Apply" button that redirects to
    the actual employer ATS (Greenhouse/Lever/Ashby/etc.). We navigate there
    (using the user's logged-in Jobright session), find the Apply button, and
    either:
      - If the button href is an external ATS URL → return that
      - If clicking the button opens a new tab → capture the destination URL
      - Else fall back to the aggregator URL (caller will do generic flow)
    """
    is_jobright = "jobright.ai" in agg_url
    is_wellfound = "wellfound.com" in agg_url

    # For Jobright, inject the manual cookie if available
    if is_jobright:
        cookie_header = vault.get("jobright", {}).get("session_cookie", "") or ""
        # Try to pull from env/vault stored in environment or applier settings
        import os
        if not cookie_header:
            cookie_header = os.environ.get("JOBRIGHT_MANUAL_COOKIE", "")
        if cookie_header:
            cookies = []
            for part in cookie_header.split(";"):
                if "=" in part:
                    name, _, val = part.strip().partition("=")
                    cookies.append({
                        "name": name.strip(),
                        "value": val.strip(),
                        "domain": ".jobright.ai",
                        "path": "/",
                    })
            if cookies:
                try:
                    await page.context.add_cookies(cookies)
                except Exception as e:
                    logger.debug(f"Could not inject Jobright cookies: {e}")

    try:
        await page.goto(agg_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
    except Exception as e:
        logger.warning(f"Aggregator navigation failed: {e}")
        return agg_url

    # Known external ATS domains we want to follow to
    ats_patterns = [
        "greenhouse.io", "grnh.se",
        "lever.co", "ashbyhq.com",
        "workable.com", "jobs.gem.com",
        "dover.com", "rippling.com",
        "smartrecruiters.com", "bamboohr.com",
        "myworkdayjobs.com", "icims.com",
    ]

    # Strategy 1: scan all anchor hrefs for an external ATS link
    try:
        hrefs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href).filter(h => h.startsWith('http'))"""
        )
        for href in hrefs:
            if any(p in href.lower() for p in ats_patterns):
                return href
    except Exception:
        pass

    # Strategy 2: click any Apply button and watch for popup/navigation
    try:
        apply_locator = page.locator(
            "button:has-text('Apply'), a:has-text('Apply'), "
            "button:has-text('APPLY'), a:has-text('APPLY')"
        ).first
        if await apply_locator.count() > 0:
            # Capture new-page popups and in-page redirects together
            async with page.context.expect_page(timeout=10_000) as popup_info:
                await apply_locator.click()
            popup = await popup_info.value
            await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
            resolved = popup.url
            await popup.close()
            if resolved and any(p in resolved.lower() for p in ats_patterns):
                return resolved
    except Exception:
        # expect_page times out if the click navigates in-place
        resolved = page.url
        if any(p in resolved.lower() for p in ats_patterns):
            return resolved

    # Fallback — aggregator didn't lead us anywhere useful
    return agg_url


async def _fill_greenhouse(
    page: Any,
    apply_url: str,
    resume_path: str,
    vault: dict[str, Any],
) -> dict[str, str]:
    """Fill a Greenhouse application form. Returns dict of filled field → value."""
    personal = vault.get("personal", {})
    work_auth = vault.get("work_auth", {})
    eeo = vault.get("eeo", {})
    std = vault.get("standard_answers", {})
    filled: dict[str, str] = {}

    # Navigate — if URL is a job detail page, click the Apply button first
    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)

    # If we're on a job detail page rather than the form, click Apply
    apply_btn = page.locator(
        "a[href*='/applications/new'], a:has-text('Apply'), "
        "button:has-text('Apply for this job')"
    ).first
    if await apply_btn.count() > 0:
        await apply_btn.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(1500)

    # --- Standard fields ---
    async def try_fill(selector: str, value: str, label: str) -> bool:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(value)
                filled[label] = value
                return True
        except Exception:
            pass
        return False

    first = personal.get("first_name", "")
    last = personal.get("last_name", "")

    await try_fill("input#first_name, input[name='job_application[first_name]']", first, "first_name")
    await try_fill("input#last_name, input[name='job_application[last_name]']", last, "last_name")
    await try_fill("input#email, input[name='job_application[email]']", personal.get("email", ""), "email")
    await try_fill("input#phone, input[name='job_application[phone]']", personal.get("phone", ""), "phone")

    # Location — try several selectors
    location_val = f"{personal.get('city', '')}, {personal.get('state', '')}"
    await try_fill(
        "input#job_application_location, "
        "input[placeholder*='location' i], "
        "input[placeholder*='city' i], "
        "input[autocomplete='off'][id*='location']",
        location_val, "location"
    )

    # Address
    address = f"{personal.get('address_line1', '')} {personal.get('city', '')} {personal.get('state', '')} {personal.get('zip', '')}".strip()
    if address:
        await try_fill(
            "input#job_application_location, "
            "input[placeholder*='address' i], "
            "input[placeholder*='street' i], "
            "input[autocomplete*='address' i]",
            address, "address"
        )

    # LinkedIn
    if personal.get("linkedin_url"):
        await try_fill(
            "input[placeholder*='linkedin' i], "
            "input[name*='linkedin' i], "
            "input[id*='linkedin' i]",
            personal["linkedin_url"], "linkedin"
        )

    # GitHub / portfolio — only fill if explicitly set (null means user has none)
    github = personal.get("github_url") or personal.get("portfolio_url")
    if github:
        await try_fill(
            "input[placeholder*='github' i], "
            "input[placeholder*='portfolio' i], "
            "input[placeholder*='website' i], "
            "input[name*='website' i]",
            github, "website"
        )

    # Resume upload
    try:
        file_input = page.locator("input[type='file']").first
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_path)
            filled["resume"] = resume_path
            await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"Resume upload failed: {e}")

    # "How did you hear" questions
    heard = std.get("how_did_you_hear", "Online job board")
    await _answer_question_by_label(page, ["how did you hear", "how did you find", "referred by"], heard, filled)

    # Work auth + EEO — fill ALL custom questions by label text
    await _fill_all_custom_questions(page, work_auth, eeo, personal, filled)
    await page.wait_for_timeout(800)

    # Scroll to bottom so all fields are in screenshot
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(800)

    return filled


async def _fill_all_custom_questions(
    page: Any,
    work_auth: dict[str, Any],
    eeo: dict[str, Any],
    personal: dict[str, Any],
    filled: dict[str, str],
) -> None:
    """Fill ALL custom/question fields on Greenhouse forms.

    Greenhouse uses react-select comboboxes (role=combobox, aria-haspopup=true)
    for Yes/No questions and multi-option dropdowns. The pattern:
      1. Click the combobox container to open the options list
      2. Wait for [role="option"] elements to appear
      3. Click the one whose text matches the desired answer

    For plain text inputs (address, linkedin, etc.) we use fill() directly.
    """
    import re as _re

    authorized = work_auth.get("authorized_us", True)
    needs_sponsor = work_auth.get("requires_sponsorship", False)
    address = (
        f"{personal.get('address_line1', '')} "
        f"{personal.get('city', '')} "
        f"{personal.get('state', '')} "
        f"{personal.get('zip', '')}"
    ).strip()

    # label substring patterns → answer string
    answer_map: list[tuple[list[str], str]] = [
        (["authorized to work", "legally authorized", "work in the united states",
          "work in united states", "eligible to work"],
         "Yes" if authorized else "No"),
        (["require.*sponsorship", "visa sponsorship", "require an employment visa",
          "sponsorship.*employment", "require sponsorship", "will you now"],
         "Yes" if needs_sponsor else "No"),
        (["physical address", "mailing address", "street address"], address),
        (["linkedin"], personal.get("linkedin_url", "")),
        (["gender identity", "describe your gender"], eeo.get("gender_identity", "Man")),
        (["racial", "ethnic background", "race/ethni", "racial/ethnic"],
         eeo.get("race_ethnicity", "Asian")),
        (["sexual orientation"], eeo.get("sexual_orientation", "Heterosexual / Straight")),
        (["identify as transgender", "transgender"], "No"),
        (["disability", "chronic condition"], eeo.get("disability_status", "I don't wish to answer")),
        (["veteran", "armed forces", "military service"],
         eeo.get("veteran_status", "No, I am not a veteran or active member")),
        (["hispanic", "latino"], "No"),
        (["how did you hear", "how did you find"], "Online job board"),
        # "Do you currently reside in any of the following states?" → Texas
        # States question — use full name since Greenhouse combobox shows "Texas" not "TX"
        (["reside in any", "which states", "currently reside"], _state_full_name(personal.get("state", "TX"))),
    ]

    labels = await page.locator("label").all()
    for label in labels:
        try:
            label_text = (await label.inner_text()).strip().lower()
            for_id = await label.get_attribute("for") or ""
            if not for_id:
                continue

            matched_answer = ""
            for patterns, answer in answer_map:
                if any(_re.search(p, label_text) for p in patterns):
                    matched_answer = answer
                    break
            if not matched_answer:
                continue

            el = page.locator(f'[id="{for_id}"]').first
            if await el.count() == 0:
                continue

            tag = (await el.evaluate("e => e.tagName")).upper()
            aria_popup = await el.get_attribute("aria-haspopup") or ""
            role = await el.get_attribute("role") or ""
            input_type = (await el.get_attribute("type") or "").lower()
            is_combobox = role == "combobox" or aria_popup in ("true", "listbox")

            if is_combobox:
                # React-Select combobox: after click, aria-controls points to
                # THIS combobox's specific listbox (not the shared global one).
                await el.scroll_into_view_if_needed()
                await page.wait_for_timeout(200)
                await el.click()
                await page.wait_for_timeout(700)

                listbox_id = await el.get_attribute("aria-controls") or ""
                if listbox_id:
                    opts_loc = page.locator(f'[id="{listbox_id}"] [role="option"]')
                else:
                    # Fallback: visible options (may overlap with other dropdowns)
                    opts_loc = page.locator('[role="option"]:visible')

                opt_count = await opts_loc.count()
                matched = False
                for i in range(opt_count):
                    opt = opts_loc.nth(i)
                    opt_text = (await opt.inner_text()).strip()
                    if opt_text and matched_answer.lower() in opt_text.lower():
                        await opt.click()
                        filled[f"q_{for_id[:20]}"] = matched_answer
                        await page.wait_for_timeout(300)
                        matched = True
                        break
                if not matched:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(200)

            elif tag == "SELECT":
                opts = await el.locator("option").all()
                for opt in opts:
                    opt_text = (await opt.inner_text()).strip()
                    if opt_text and matched_answer.lower() in opt_text.lower():
                        await el.select_option(label=opt_text)
                        filled[f"q_{for_id[:20]}"] = matched_answer
                        break

            elif tag == "INPUT" and input_type in ("text", "email", "tel", "search", ""):
                await el.fill(matched_answer)
                filled[f"q_{for_id[:20]}"] = matched_answer

            elif tag == "INPUT" and input_type == "radio":
                radios = await page.locator(
                    f'input[type="radio"][name="{for_id}"]'
                ).all()
                for radio in radios:
                    rid = await radio.get_attribute("id") or ""
                    rlabel = page.locator(f'label[for="{rid}"]').first
                    rlabel_text = (
                        (await rlabel.inner_text()).strip()
                        if await rlabel.count() > 0
                        else ""
                    )
                    if matched_answer.lower() in rlabel_text.lower():
                        await radio.click()
                        filled[f"q_{for_id[:20]}"] = matched_answer
                        break

            elif tag == "TEXTAREA":
                await el.fill(matched_answer)
                filled[f"q_{for_id[:20]}"] = matched_answer

        except Exception as e:
            logger.debug(f"Custom question fill skipped [{for_id}]: {e}")
            continue


# ---------------------------------------------------------------------------
# Shared helper: fill standard fields on any React-based ATS form
# ---------------------------------------------------------------------------

async def _fill_standard_fields(
    page: Any,
    vault: dict[str, Any],
    resume_path: str,
    filled: dict[str, str],
    *,
    full_name_selector: str = "",
    first_name_selector: str = "",
    last_name_selector: str = "",
    email_selector: str = "",
    phone_selector: str = "",
    linkedin_selector: str = "",
    location_selector: str = "",
    use_aria: bool = False,
) -> None:
    """Fill standard name/email/phone/resume fields.

    Strategy: try explicit selectors first (when provided), then fall back to
    aria-label / placeholder / name attribute matching. This double-layer
    approach is what makes adapters High-confidence — one strategy covers
    every ATS variant.
    """
    personal = vault.get("personal", {})
    full_name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()

    async def _try_fill(value: str, key: str, *selectors: str) -> bool:
        """Try each selector in order; return True on first match."""
        if not value:
            return False
        for sel in selectors:
            if not sel:
                continue
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.fill(value)
                    filled[key] = value
                    return True
            except Exception:
                pass
        return False

    def _aria_sels(patterns: list[str]) -> list[str]:
        """Generate aria/placeholder/name selectors for given label patterns."""
        sels = []
        for pat in patterns:
            sels += [
                f"input[aria-label*='{pat}' i]",
                f"input[placeholder*='{pat}' i]",
                f"textarea[placeholder*='{pat}' i]",
                f"input[name*='{pat}' i]",
                f"input[id*='{pat}' i]",
                f"input[data-testid*='{pat}' i]",
            ]
        return sels

    # Full name (single field)
    await _try_fill(
        full_name, "name",
        full_name_selector,
        *_aria_sels(["full name", "legal name", "your name", "fullname"]),
    )
    # First name
    await _try_fill(
        personal.get("first_name", ""), "first_name",
        first_name_selector,
        *_aria_sels(["first name", "firstname", "given name"]),
    )
    # Last name
    await _try_fill(
        personal.get("last_name", ""), "last_name",
        last_name_selector,
        *_aria_sels(["last name", "lastname", "surname", "family name"]),
    )
    # Email
    await _try_fill(
        personal.get("email", ""), "email",
        email_selector,
        "input[type='email']",
        *_aria_sels(["email", "e-mail"]),
    )
    # Phone
    await _try_fill(
        personal.get("phone", ""), "phone",
        phone_selector,
        "input[type='tel']",
        *_aria_sels(["phone", "mobile", "telephone"]),
    )
    # LinkedIn
    if personal.get("linkedin_url"):
        await _try_fill(
            personal["linkedin_url"], "linkedin",
            linkedin_selector,
            *_aria_sels(["linkedin", "linkedin url", "linkedin profile"]),
        )
    # City / location
    await _try_fill(
        personal.get("city", ""), "city",
        location_selector,
        *_aria_sels(["city", "location", "where are you", "current location"]),
    )

    # Resume upload — same across all ATS
    # Try visible file input first, then any file input
    for fi_sel in ["input[type='file']:visible", "input[type='file']"]:
        try:
            fi = page.locator(fi_sel).first
            if await fi.count() > 0:
                await fi.set_input_files(resume_path)
                filled["resume"] = resume_path
                await page.wait_for_timeout(1500)
                break
        except Exception as e:
            logger.debug(f"Resume upload attempt failed ({fi_sel}): {e}")


async def _wait_for_form(page: Any, timeout_ms: int = 12_000) -> None:
    """Wait until the form is interactive — checks multiple signals."""
    for selector in [
        "input[type='text']:not([disabled])",
        "input[type='email']:not([disabled])",
        "input[aria-label]",
        "input[placeholder]",
        "form",
    ]:
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms // len([1, 2, 3, 4, 5]))
            return
        except Exception:
            pass


async def _js_dispatch_fill(page: Any, selector: str, value: str) -> bool:
    """Fill a React input by dispatching the exact events React listens to.

    Playwright's fill() already does this correctly. This helper is a fallback
    for inputs that didn't respond to fill() — it sets the value via JS and
    fires both 'input' and 'change' events so React's synthetic event system
    picks up the change.
    """
    try:
        result = await page.evaluate(
            """([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeInputValueSetter.call(el, val);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }""",
            [selector, value],
        )
        return bool(result)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Gem adapter  (jobs.gem.com)  — 8% of startups
# ---------------------------------------------------------------------------

async def _fill_gem(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Gem ATS — jobs.gem.com/{company}/{id}
    Gem is a modern React SPA. Standard fields use aria-label or placeholder.
    Custom questions follow label[for] → combobox/input pattern.
    High-confidence: uses _wait_for_form + aria fill + JS-dispatch fallback.
    """
    filled: dict[str, str] = {}
    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await _wait_for_form(page)
    await page.wait_for_timeout(1500)

    await _fill_standard_fields(page, vault, resume_path, filled, use_aria=True)

    # JS-dispatch fallback for any field that Playwright fill() missed
    personal = vault.get("personal", {})
    if "email" not in filled:
        ok = await _js_dispatch_fill(page, "input[type='email']", personal.get("email", ""))
        if ok:
            filled["email_js"] = personal.get("email", "")

    await _fill_all_custom_questions(
        page, vault.get("work_auth", {}), vault.get("eeo", {}), personal, filled
    )
    return filled


# ---------------------------------------------------------------------------
# Workable adapter  (apply.workable.com)  — 7% of startups
# ---------------------------------------------------------------------------

async def _fill_workable(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Workable — apply.workable.com/{company}/j/{shortcode}
    Workable shows a job-description page first; clicking "Apply" loads the form.
    The form uses aria-label="First name" etc. After resume upload Workable
    sometimes auto-parses and pre-fills fields — we overwrite them anyway.
    High-confidence: Apply-button click + _wait_for_form + aria fill + JS fallback.
    """
    filled: dict[str, str] = {}
    personal = vault.get("personal", {})

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1500)

    # Workable uses a modal/step flow — click the primary Apply button
    for btn_sel in [
        "a.apply-button, button.apply-button",
        "a:has-text('Apply for this job')",
        "button:has-text('Apply now')",
        "a:has-text('Apply')",
        "button:has-text('Apply')",
    ]:
        try:
            btn = page.locator(btn_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1500)
                break
        except Exception:
            pass

    await _wait_for_form(page)
    await page.wait_for_timeout(1000)

    await _fill_standard_fields(page, vault, resume_path, filled, use_aria=True)

    # Workable-specific: fields sometimes named with ui-* data attributes
    await _fill_standard_fields(
        page, vault, resume_path, filled,
        first_name_selector="input[name='firstname'], input[name='first_name']",
        last_name_selector="input[name='lastname'], input[name='last_name']",
        email_selector="input[name='email']",
        phone_selector="input[name='phone']",
    )

    # JS fallback
    if "email" not in filled and personal.get("email"):
        await _js_dispatch_fill(page, "input[type='email'], input[name='email']", personal["email"])
        filled["email_js"] = personal["email"]

    await _fill_all_custom_questions(
        page, vault.get("work_auth", {}), vault.get("eeo", {}), personal, filled
    )
    return filled


# ---------------------------------------------------------------------------
# Dover adapter  (app.dover.com)  — 6% of startups
# ---------------------------------------------------------------------------

async def _fill_dover(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Dover — app.dover.com/apply/{company}/{uuid}
    Dover is a minimal multi-step React form. Step 1 is contact info;
    subsequent steps add resume + custom questions. We fill each step and
    click Next/Continue until we reach a submit state.
    High-confidence: step-through + aria fill + JS dispatch fallback.
    """
    filled: dict[str, str] = {}
    personal = vault.get("personal", {})

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await _wait_for_form(page)
    await page.wait_for_timeout(1500)

    # Dover sometimes shows a multi-step form
    for _step in range(4):  # up to 4 steps
        await _fill_standard_fields(page, vault, resume_path, filled, use_aria=True)

        # JS fallback for missed fields
        for aria, val in [
            ("email", personal.get("email", "")),
            ("phone", personal.get("phone", "")),
        ]:
            if aria not in filled and val:
                ok = await _js_dispatch_fill(page, f"input[aria-label*='{aria}' i], input[type='{aria}']", val)
                if ok:
                    filled[f"{aria}_js"] = val

        await _fill_all_custom_questions(
            page, vault.get("work_auth", {}), vault.get("eeo", {}), personal, filled
        )

        # Click Next/Continue if present (multi-step)
        next_btn = page.locator(
            "button:has-text('Next'), button:has-text('Continue'), button:has-text('Save and continue')"
        ).first
        if await next_btn.count() > 0 and await next_btn.is_visible():
            await next_btn.click()
            await page.wait_for_timeout(1500)
        else:
            break  # no more steps or on final page

    return filled


# ---------------------------------------------------------------------------
# Rippling adapter  (ats.rippling.com)  — 4% of startups
# ---------------------------------------------------------------------------

async def _fill_rippling(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Rippling ATS — ats.rippling.com/{company}/jobs/{uuid}
    Rippling renders a job card first; clicking Apply reveals the form inline
    or in a modal. Fields use aria-label + data-testid attributes.
    High-confidence: Apply-click + _wait_for_form + aria + data-testid + JS fallback.
    """
    filled: dict[str, str] = {}
    personal = vault.get("personal", {})

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)

    # Click Apply button to reveal form
    for btn_sel in [
        "button[data-testid='apply-button']",
        "button:has-text('Apply for this job')",
        "button:has-text('Apply now')",
        "button:has-text('Apply')",
    ]:
        try:
            btn = page.locator(btn_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            pass

    await _wait_for_form(page)

    # Rippling data-testid selectors (most specific — try first)
    await _fill_standard_fields(
        page, vault, resume_path, filled,
        first_name_selector="input[data-testid='firstName'], input[data-testid='first-name']",
        last_name_selector="input[data-testid='lastName'], input[data-testid='last-name']",
        email_selector="input[data-testid='email']",
        phone_selector="input[data-testid='phone'], input[data-testid='phoneNumber']",
        linkedin_selector="input[data-testid='linkedin'], input[data-testid='linkedinUrl']",
    )
    # Aria fallback for anything not filled
    await _fill_standard_fields(page, vault, resume_path, filled, use_aria=True)

    # JS dispatch for anything still missed
    for key, val in [("email", personal.get("email", "")), ("phone", personal.get("phone", ""))]:
        if key not in filled and val:
            ok = await _js_dispatch_fill(page, f"input[type='{key}'], input[name='{key}']", val)
            if ok:
                filled[f"{key}_js"] = val

    await _fill_all_custom_questions(
        page, vault.get("work_auth", {}), vault.get("eeo", {}), personal, filled
    )
    return filled


# ---------------------------------------------------------------------------
# SmartRecruiters adapter  — ~3–5% of postings
# ---------------------------------------------------------------------------

async def _fill_smartrecruiters(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """SmartRecruiters — jobs.smartrecruiters.com/{Company}/{id}
    SmartRecruiters has a job-listing page; clicking Apply opens a multi-step
    form. It uses data-smarttoken attributes (most reliable) + aria-label.
    High-confidence: Apply-click + data-smarttoken + aria + JS fallback.
    """
    filled: dict[str, str] = {}
    personal = vault.get("personal", {})

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)

    # Click Apply button to load the application form
    for btn_sel in [
        "a.btn-apply, button.btn-apply",
        "button[data-role='apply']",
        "a:has-text('Apply now')",
        "button:has-text('Apply now')",
        "a:has-text('Apply')",
        "button:has-text('Apply')",
    ]:
        try:
            btn = page.locator(btn_sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2000)
                break
        except Exception:
            pass

    await _wait_for_form(page)

    # data-smarttoken selectors — SmartRecruiters' own attribute (most reliable)
    await _fill_standard_fields(
        page, vault, resume_path, filled,
        first_name_selector="input[data-smarttoken='firstName'], input[name='firstName']",
        last_name_selector="input[data-smarttoken='lastName'], input[name='lastName']",
        email_selector="input[data-smarttoken='email'], input[name='email']",
        phone_selector="input[data-smarttoken='phoneNumber'], input[name='phone'], input[name='phoneNumber']",
        linkedin_selector="input[data-smarttoken='linkedinUrl'], input[name='linkedinUrl']",
        location_selector="input[data-smarttoken='city'], input[name='city']",
    )
    # Aria fallback
    await _fill_standard_fields(page, vault, resume_path, filled, use_aria=True)

    # JS dispatch for anything still missed
    for sel, val in [
        ("input[data-smarttoken='email']", personal.get("email", "")),
        ("input[data-smarttoken='firstName']", personal.get("first_name", "")),
    ]:
        if val and "email" not in filled:
            await _js_dispatch_fill(page, sel, val)

    await _fill_all_custom_questions(
        page, vault.get("work_auth", {}), vault.get("eeo", {}), personal, filled
    )
    return filled


# ---------------------------------------------------------------------------
# BambooHR adapter  (~3–4% of postings)
# ---------------------------------------------------------------------------

async def _fill_bamboohr(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """BambooHR career page apply form.
    URL: {company}.bamboohr.com/careers/{id}/apply
    BambooHR uses standard HTML inputs with id attributes.
    """
    filled: dict[str, str] = {}
    personal = vault.get("personal", {})

    if not apply_url.rstrip("/").endswith("/apply"):
        apply_url = apply_url.rstrip("/") + "/apply"

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)

    await _fill_standard_fields(
        page, vault, resume_path, filled,
        first_name_selector="input#firstName, input[name='firstName']",
        last_name_selector="input#lastName, input[name='lastName']",
        email_selector="input#email, input[name='email']",
        phone_selector="input#phone, input[name='phone']",
        linkedin_selector="input[id*='linkedin' i], input[name*='linkedin' i]",
        location_selector="input#city, input[name='city']",
    )
    await _fill_all_custom_questions(
        page, vault.get("work_auth", {}), vault.get("eeo", {}), personal, filled
    )
    return filled


async def _answer_work_auth_questions(
    page: Any, work_auth: dict[str, Any], filled: dict[str, str]
) -> None:
    """Detect and answer US work authorization + EEO questions via JS DOM scan.

    Uses page.evaluate() so we work entirely in the browser's JS engine —
    no Python-side Playwright locator chains that lose context across async calls.
    """
    authorized = work_auth.get("authorized_us", True)
    needs_sponsor = work_auth.get("requires_sponsorship", False)

    # Build a map of label substring → desired option substring
    # Order matters: more specific patterns first
    answer_map = {
        "authorized to work": "Yes" if authorized else "No",
        "legally authorized": "Yes" if authorized else "No",
        "work in the united states": "Yes" if authorized else "No",
        "work in the u.s": "Yes" if authorized else "No",
        "eligible to work": "Yes" if authorized else "No",
        "require sponsorship": "Yes" if needs_sponsor else "No",
        "visa sponsorship": "Yes" if needs_sponsor else "No",
        "employment visa": "Yes" if needs_sponsor else "No",
        "require an employment": "Yes" if needs_sponsor else "No",
        "sponsorship for": "Yes" if needs_sponsor else "No",
        "will you now": "Yes" if needs_sponsor else "No",
    }

    result = await page.evaluate("""
        (answerMap) => {
            const filled = {};
            // Scan every container that might hold a custom question
            const containers = document.querySelectorAll(
                'li.custom-question, div.field, div[class*="application-question"]'
            );
            containers.forEach(c => {
                const labelEl = c.querySelector('label, legend, .custom-question-title');
                if (!labelEl) return;
                const labelText = labelEl.innerText.trim().toLowerCase();

                for (const [pattern, answer] of Object.entries(answerMap)) {
                    if (!labelText.includes(pattern)) continue;

                    // Try <select>
                    const sel = c.querySelector('select');
                    if (sel) {
                        for (const opt of sel.options) {
                            const ot = opt.text.trim().toLowerCase();
                            if (ot === answer.toLowerCase() || ot.includes(answer.toLowerCase())) {
                                sel.value = opt.value;
                                sel.dispatchEvent(new Event('change', { bubbles: true }));
                                filled[pattern.substring(0, 20)] = answer;
                                break;
                            }
                        }
                        break;
                    }

                    // Try radio buttons
                    const radios = c.querySelectorAll('input[type="radio"]');
                    if (radios.length > 0) {
                        radios.forEach(radio => {
                            const radioLabel = document.querySelector(`label[for="${radio.id}"]`);
                            const radioText = (radioLabel ? radioLabel.innerText : radio.value).toLowerCase();
                            if (radioText === answer.toLowerCase() || radioText.includes(answer.toLowerCase())) {
                                radio.click();
                                filled[pattern.substring(0, 20)] = answer;
                            }
                        });
                        break;
                    }
                    break;
                }
            });
            return filled;
        }
    """, answer_map)

    if isinstance(result, dict):
        filled.update({f"work_auth_{k}": v for k, v in result.items()})


async def _answer_eeo_questions(
    page: Any, eeo: dict[str, Any], filled: dict[str, str]
) -> None:
    """Answer EEO demographic questions via JS DOM scan (same approach as work auth)."""
    gender = eeo.get("gender_identity", eeo.get("gender", "Male"))
    race = eeo.get("race_ethnicity", "Asian")
    veteran = eeo.get("veteran_status", "I am not a protected veteran")
    disability = eeo.get("disability_status", "I don't wish to answer")
    orientation = eeo.get("sexual_orientation", "")

    eeo_map = {
        "gender identity": gender,
        "how would you describe your gender": gender,
        "racial/ethnic": race,
        "racial or ethnic": race,
        "race": race,
        "ethnicity": race,
        "veteran": veteran,
        "military service": veteran,
        "protected veteran": veteran,
        "disability": disability,
        "disabled": disability,
        "sexual orientation": orientation,
    }
    # Filter empty values
    eeo_map = {k: v for k, v in eeo_map.items() if v}

    result = await page.evaluate("""
        (eeoMap) => {
            const filled = {};
            const selects = document.querySelectorAll('select');
            selects.forEach(sel => {
                // Walk up to find a label or heading near this select
                let container = sel.closest('div, li, fieldset');
                if (!container) return;
                const labelEl = container.querySelector('label, legend, h4, h3, p strong');
                if (!labelEl) return;
                const labelText = labelEl.innerText.trim().toLowerCase();

                for (const [pattern, answer] of Object.entries(eeoMap)) {
                    if (!labelText.includes(pattern)) continue;
                    for (const opt of sel.options) {
                        const ot = opt.text.trim().toLowerCase();
                        // Match: exact, starts with, or contains the answer
                        if (ot === answer.toLowerCase()
                            || ot.startsWith(answer.toLowerCase())
                            || ot.includes(answer.toLowerCase())) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            filled['eeo_' + pattern.substring(0, 15)] = answer;
                            break;
                        }
                    }
                    break;
                }
            });
            return filled;
        }
    """, eeo_map)

    if isinstance(result, dict):
        filled.update(result)


async def _answer_question_by_label(
    page: Any, label_patterns: list[str], answer: str, filled: dict[str, str]
) -> None:
    """Fill a text input or select whose label matches any of the patterns."""
    containers = await page.locator("div.field, li.custom-question").all()
    for c in containers:
        try:
            text = (await c.inner_text()).lower()
            if any(p in text for p in label_patterns):
                # Try text input first
                inp = c.locator("input[type='text'], textarea").first
                if await inp.count() > 0:
                    await inp.fill(answer)
                    filled["how_did_you_hear"] = answer
                    return
                # Try select
                sel = c.locator("select").first
                if await sel.count() > 0:
                    await sel.select_option(label=answer)
                    filled["how_did_you_hear"] = answer
                    return
        except Exception:
            continue


async def _pick_radio_or_select(container: Any, value: str) -> None:
    """Click a radio whose label matches value, or select from a <select>."""
    value_lower = value.lower()

    # Try radio/checkbox labels
    labels = await container.locator("label").all()
    for label in labels:
        try:
            label_text = (await label.inner_text()).lower().strip()
            if label_text == value_lower or value_lower in label_text:
                await label.click()
                return
        except Exception:
            continue

    # Try <select>
    try:
        sel = container.locator("select").first
        if await sel.count() > 0:
            options = await sel.locator("option").all()
            for opt in options:
                opt_text = (await opt.inner_text()).lower().strip()
                if opt_text == value_lower or value_lower in opt_text:
                    opt_val = await opt.get_attribute("value")
                    if opt_val:
                        await sel.select_option(value=opt_val)
                        return
    except Exception:
        pass


async def _click_submit(page: Any) -> bool:
    """Click the submit button on the form."""
    selectors = [
        "input[type='submit'][value*='Submit' i]",
        "input[type='submit']",
        "button[type='submit']:has-text('Submit')",
        "button:has-text('Submit application')",
        "button:has-text('Apply')",
        "button[type='submit']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Lever adapter (Phase 2 stub)
# ---------------------------------------------------------------------------

async def _fill_lever(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Lever.co application form filler.

    Lever standard fields use name= attributes. All custom questions are
    handled by _fill_all_custom_questions via label matching.
    URL: https://jobs.lever.co/{company}/{uuid}/apply
    """
    personal = vault.get("personal", {})
    work_auth = vault.get("work_auth", {})
    eeo = vault.get("eeo", {})
    filled: dict[str, str] = {}

    # Ensure /apply suffix
    if not apply_url.rstrip("/").endswith("/apply"):
        apply_url = apply_url.rstrip("/") + "/apply"

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2500)

    async def lever_fill(selector: str, value: str, key: str) -> None:
        if not value:
            return
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(value)
                filled[key] = value
        except Exception:
            pass

    full_name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
    await lever_fill("input[name='name']", full_name, "name")
    await lever_fill("input[name='email']", personal.get("email", ""), "email")
    await lever_fill("input[name='phone']", personal.get("phone", ""), "phone")
    await lever_fill("input[name='org']", "NMDP", "current_company")
    await lever_fill("input[name='urls[LinkedIn]']", personal.get("linkedin_url", ""), "linkedin")

    # Resume upload
    try:
        fi = page.locator("input[type='file']").first
        if await fi.count() > 0:
            await fi.set_input_files(resume_path)
            filled["resume"] = resume_path
            await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"Lever resume upload failed: {e}")

    # Work auth + EEO via label matching (same as Greenhouse)
    await _fill_all_custom_questions(page, work_auth, eeo, personal, filled)
    await page.wait_for_timeout(500)
    return filled


# ---------------------------------------------------------------------------
# Ashby adapter
# ---------------------------------------------------------------------------

async def _fill_ashby(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Ashby application form filler (jobs.ashbyhq.com).

    Ashby is a React SPA. System fields use aria-label / placeholder.
    Custom questions follow the same label-for pattern as Greenhouse.
    URL: https://jobs.ashbyhq.com/{company}/{uuid}/application
    """
    personal = vault.get("personal", {})
    work_auth = vault.get("work_auth", {})
    eeo = vault.get("eeo", {})
    filled: dict[str, str] = {}

    if "/application" not in apply_url:
        apply_url = apply_url.rstrip("/") + "/application"

    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    try:
        await page.wait_for_selector("input, form", timeout=12_000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)

    full_name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()

    async def ashby_fill(patterns: list[str], value: str, key: str) -> None:
        if not value:
            return
        for pat in patterns:
            for sel in [
                f"input[aria-label*='{pat}' i]",
                f"input[placeholder*='{pat}' i]",
                f"textarea[placeholder*='{pat}' i]",
                f"input[name*='{pat}' i]",
                f"input[id*='{pat}' i]",
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.fill(value)
                        filled[key] = value
                        return
                except Exception:
                    pass

    await ashby_fill(["full name", "legal name", "name"], full_name, "name")
    await ashby_fill(["first name"], personal.get("first_name", ""), "first_name")
    await ashby_fill(["last name"], personal.get("last_name", ""), "last_name")
    await ashby_fill(["email"], personal.get("email", ""), "email")
    await ashby_fill(["phone"], personal.get("phone", ""), "phone")
    await ashby_fill(["linkedin"], personal.get("linkedin_url", ""), "linkedin")
    await ashby_fill(["city", "location"], personal.get("city", ""), "city")

    # Resume upload
    try:
        fi = page.locator("input[type='file']").first
        if await fi.count() > 0:
            await fi.set_input_files(resume_path)
            filled["resume"] = resume_path
            await page.wait_for_timeout(1500)
    except Exception as e:
        logger.warning(f"Ashby resume upload failed: {e}")

    # Work auth + EEO
    await _fill_all_custom_questions(page, work_auth, eeo, personal, filled)
    await page.wait_for_timeout(500)
    return filled
