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
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.services.apply_answers import (
    answer_for,
    attach_resume,
    gmail_credentials,
    persist_filled_answers,
)
from app.services.apply_urls import (
    canonical_apply_url,
    extract_employer_apply_url,
    extract_mailto,
    is_aggregator_url,
    is_ats_url,
    is_original_employer_url,
    lookup_public_apply_url,
    pick_original_job_post_url,
    upsert_employer_apply_notes,
)

logger = logging.getLogger(__name__)

_STATE_ABBREV: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
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


def _pick(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value:
            return str(value)
    return ""


def normalize_vault(vault: dict[str, Any]) -> dict[str, Any]:
    """Adapters expect snake_case personal/work_auth + settings.approval_gate_enabled.

    The on-disk vault uses a mix of camelCase (firstName) and nested address,
    and the UI Approval Gate toggle lives in pipeline.json — not vault.json.
    """
    out = dict(vault)
    raw_personal = dict(vault.get("personal") or {})
    address = (
        raw_personal.get("address")
        if isinstance(raw_personal.get("address"), dict)
        else {}
    )
    name = _pick(raw_personal, "name")
    first = _pick(raw_personal, "first_name", "firstName")
    last = _pick(raw_personal, "last_name", "lastName")
    if not first and name:
        parts = name.split()
        first = parts[0]
        last = " ".join(parts[1:])
    out["personal"] = {
        **raw_personal,
        "first_name": first,
        "last_name": last,
        "email": _pick(raw_personal, "email"),
        "phone": _pick(raw_personal, "phone"),
        "linkedin": _pick(raw_personal, "linkedin", "linkedin_url"),
        "linkedin_url": _pick(raw_personal, "linkedin", "linkedin_url"),
        "github": _pick(raw_personal, "github", "github_url"),
        "github_url": _pick(raw_personal, "github", "github_url"),
        "website": _pick(raw_personal, "website"),
        "city": _pick(raw_personal, "city") or _pick(address, "city"),
        "state": _pick(raw_personal, "state") or _pick(address, "state"),
        "zip": _pick(raw_personal, "zip") or _pick(address, "zip"),
        "line1": _pick(raw_personal, "line1") or _pick(address, "line1"),
        "address_line1": _pick(raw_personal, "address_line1", "line1")
        or _pick(address, "line1"),
        "country": _pick(raw_personal, "country") or _pick(address, "country") or "US",
    }
    raw_auth = dict(vault.get("work_auth") or vault.get("work_authorization") or {})
    out["work_auth"] = {
        **raw_auth,
        "authorized_us": raw_auth.get(
            "authorized_us", raw_auth.get("authorized_to_work", True)
        ),
        "requires_sponsorship": raw_auth.get(
            "requires_sponsorship", raw_auth.get("needs_sponsorship", False)
        ),
        "visa_status": raw_auth.get("visa_status", ""),
    }
    settings = dict(vault.get("settings") or {})
    gate = settings.get("approval_gate_enabled", vault.get("approval_gate", True))
    try:
        from app.services.pipeline_service import load_config

        gate = bool(load_config().get("approval_gate", gate))
    except Exception:
        pass
    settings["approval_gate_enabled"] = bool(gate)
    settings.setdefault("headless_browser", True)
    settings.setdefault("review_timeout_seconds", 600)
    out["settings"] = settings
    return out


def _fill_personal_from_resume(
    personal: dict[str, Any], resume_data: dict[str, Any]
) -> None:
    info = resume_data.get("personalInfo") if isinstance(resume_data, dict) else None
    if not isinstance(info, dict):
        return
    if not personal.get("first_name"):
        name = str(info.get("name") or "").strip()
        if name:
            parts = name.split()
            personal["first_name"] = parts[0]
            personal["last_name"] = personal.get("last_name") or " ".join(parts[1:])
    for src, dst in (
        ("email", "email"),
        ("phone", "phone"),
        ("linkedin", "linkedin"),
        ("github", "github"),
        ("website", "website"),
        ("location", "city"),
    ):
        if not personal.get(dst) and info.get(src):
            personal[dst] = str(info[src])


async def prepare_vault(resume_id: str | None = None) -> dict[str, Any]:
    """Load vault, normalize keys, and fill blanks from the tailored resume."""
    vault = normalize_vault(load_vault())
    if resume_id:
        try:
            from app.database import db

            resume = await db.get_resume(resume_id)
            data = (resume or {}).get("processed_data") or {}
            if not isinstance(data, dict) or "personalInfo" not in data:
                raw = (resume or {}).get("content")
                if isinstance(raw, str):
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = {}
                    if isinstance(parsed, dict):
                        data = parsed
                elif isinstance(raw, dict):
                    data = raw
            if isinstance(data, dict):
                _fill_personal_from_resume(vault["personal"], data)
                vault = attach_resume(vault, data)
            else:
                vault = attach_resume(vault, None)
        except Exception as exc:
            logger.warning("Could not enrich vault from resume %s: %s", resume_id, exc)
            vault = attach_resume(vault, None)
    else:
        vault = attach_resume(vault, None)
    return vault


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class ApplicationSession:
    def __init__(
        self,
        application_id: str,
        apply_url: str,
        *,
        company: str = "",
        role: str = "",
        notes: str = "",
    ) -> None:
        self.application_id = application_id
        self.apply_url = apply_url
        self.company = company
        self.role = role
        self.notes = notes
        self.status: ApplyStatus = "pending"
        self.screenshot_b64: str | None = None
        self.filled_fields: dict[str, str] = {}
        self.error: str | None = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None
        self._approval_event = asyncio.Event()
        self._approval_thread_event = threading.Event()
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
    vault: dict[str, Any] | None = None,
    company: str = "",
    role: str = "",
    notes: str = "",
) -> ApplicationSession:
    """Start an apply task in the background. Returns immediately."""
    if application_id in _sessions:
        existing = _sessions[application_id]
        if existing.status in ("pending", "filling", "needs_review", "submitting"):
            return existing
    session = ApplicationSession(
        application_id,
        apply_url,
        company=company,
        role=role,
        notes=notes,
    )
    _sessions[application_id] = session
    ready = normalize_vault(vault if vault is not None else load_vault())
    # Playwright needs a Proactor loop on Windows. Uvicorn's reloader loop
    # cannot spawn Chromium (NotImplementedError), so we always drive the
    # browser from a dedicated thread — same pattern as PDF rendering.
    thread = threading.Thread(
        target=_run_apply_task_in_thread,
        args=(session, resume_pdf_bytes, ready),
        name=f"apply-{application_id[:8]}",
        daemon=True,
    )
    thread.start()
    return session


def _run_apply_task_in_thread(
    session: ApplicationSession,
    resume_pdf_bytes: bytes,
    vault: dict[str, Any],
) -> None:
    if sys.platform == "win32":
        from asyncio.windows_events import ProactorEventLoop

        loop = ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_apply_task(session, resume_pdf_bytes, vault))
    except Exception as exc:
        logger.error(
            "Apply thread died for %s: %s", session.application_id, exc, exc_info=True
        )
        session.status = "failed"
        session.error = (
            f"{type(exc).__name__}: {exc}" if str(exc).strip() else type(exc).__name__
        )[:400]
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)


async def approve_application(application_id: str) -> bool:
    session = _sessions.get(application_id)
    if not session or session.status != "needs_review":
        return False
    session._approved = True
    session._approval_event.set()
    session._approval_thread_event.set()
    return True


async def reject_application(application_id: str) -> bool:
    session = _sessions.get(application_id)
    if not session or session.status not in ("needs_review", "filling", "pending"):
        return False
    session._approved = False
    session._approval_thread_event.set()
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
            browser = None
            launch_err: Exception | None = None
            for channel in ("chrome", "msedge", None):
                try:
                    kwargs: dict[str, Any] = {
                        "headless": headless,
                        "args": ["--disable-blink-features=AutomationControlled"],
                    }
                    if channel:
                        kwargs["channel"] = channel
                    browser = await p.chromium.launch(**kwargs)
                    break
                except Exception as exc:
                    launch_err = exc
            if browser is None:
                raise launch_err or RuntimeError(
                    "Could not launch Chrome, Edge, or Chromium"
                )
            ctx_kwargs: dict[str, Any] = {
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "viewport": {"width": 1280, "height": 900},
            }
            state_path = _session_state_path(session.apply_url)
            if state_path and state_path.exists():
                ctx_kwargs["storage_state"] = str(state_path)
            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            session.status = "filling"

            apply_url = canonical_apply_url(session.apply_url)
            employer = extract_employer_apply_url(session.notes)
            if employer:
                apply_url = canonical_apply_url(employer)
            elif is_aggregator_url(apply_url):
                resolved = await _resolve_aggregator_url(
                    page,
                    apply_url,
                    vault,
                    company=session.company,
                    role=session.role,
                )
                if resolved and resolved != apply_url:
                    logger.info("Resolved %s -> %s", apply_url[:40], resolved[:80])
                    apply_url = canonical_apply_url(resolved)
                    await _persist_employer_apply(session.application_id, apply_url)

            session.apply_url = apply_url
            state_path = _session_state_path(apply_url) or state_path
            if state_path:
                try:
                    await context.storage_state(path=str(state_path))
                except Exception:
                    pass

            if is_aggregator_url(apply_url):
                session.status = "failed"
                session.error = (
                    "Jobright is still showing a sign-up wall after login. "
                    f"Could not uncover the employer apply URL for {session.company or 'this company'} / "
                    f"{session.role or 'this role'}."
                )
                return

            # Route to correct site adapter
            if (
                "greenhouse.io" in apply_url
                or "grnh.se" in apply_url
                or "careerpuck.com" in apply_url
            ):
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
            elif (
                "ats.rippling.com" in apply_url
                or "recruiting.rippling.com" in apply_url
            ):
                filled = await _fill_rippling(page, apply_url, resume_path, vault)
            elif "smartrecruiters.com" in apply_url:
                filled = await _fill_smartrecruiters(
                    page, apply_url, resume_path, vault
                )
            elif "bamboohr.com" in apply_url:
                filled = await _fill_bamboohr(page, apply_url, resume_path, vault)
            elif "myworkdayjobs.com" in apply_url:
                filled = await _fill_workday(page, apply_url, resume_path, vault)
            elif (
                apply_url.lower().startswith("mailto:") or "privateai.com" in apply_url
            ):
                filled = await _apply_email(
                    page, apply_url, resume_path, vault, session
                )
            else:
                filled = await _fill_generic(
                    page, apply_url, resume_path, vault, session
                )

            persist_filled_answers(filled)
            session.filled_fields = filled
            if state_path:
                try:
                    await context.storage_state(path=str(state_path))
                except Exception:
                    pass

            # Full-page screenshot for review
            screenshot_bytes = await page.screenshot(full_page=True)
            session.screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            session.status = "needs_review"

            login_blocked = bool(filled.get("auth_note")) and "resume" not in filled
            if login_blocked:
                session.status = "failed"
                session.error = (
                    filled.get("auth_note")
                    or "Employer site asked for a login before the form could be filled"
                )
                session.completed_at = datetime.now(timezone.utc).isoformat()
                return

            if not vault.get("settings", {}).get("approval_gate_enabled", True):
                # No gate — auto-approve
                session._approved = True
                session._approval_thread_event.set()
            else:
                approved = await asyncio.to_thread(
                    session._approval_thread_event.wait, float(timeout)
                )
                if not approved:
                    session.status = "cancelled"
                    session.error = f"Review timed out after {timeout}s"
                    return

            if not session._approved:
                session.status = "cancelled"
                return

            session.status = "submitting"
            if filled.get("submitted_via") in {"email", "already"}:
                submitted = True
            else:
                submitted = await _click_submit(page)
            if submitted:
                session.status = "submitted"
            else:
                session.status = "failed"
                session.error = "Could not find submit button"
            session.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        logger.error(
            "Apply task failed for %s: %s", session.application_id, e, exc_info=True
        )
        session.status = "failed"
        session.error = (
            f"{type(e).__name__}: {e}" if str(e).strip() else type(e).__name__
        )[:400]
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


async def _persist_employer_apply(application_id: str, url: str) -> None:
    try:
        from app.database import db

        app = await db.get_application(application_id)
        if not app:
            return
        notes = upsert_employer_apply_notes(app.get("notes") or "", url)
        await db.update_application(application_id, {"notes": notes})
    except Exception as exc:
        logger.debug("Could not persist employer apply URL: %s", exc)


def _first_ats_url(candidates: list[str]) -> str:
    for href in candidates:
        if href and is_ats_url(href):
            return canonical_apply_url(href)
    return ""


def _first_employer_url(candidates: list[str]) -> str:
    ats = _first_ats_url(candidates)
    if ats:
        return ats
    for href in candidates:
        if href and is_original_employer_url(href):
            return canonical_apply_url(href)
    return ""


async def _labeled_links(page: Any) -> list[tuple[str, str]]:
    try:
        rows = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href], button'))
                .map(a => ({
                    href: a.href || a.getAttribute('href') || '',
                    text: ((a.innerText || '') + ' ' + (a.getAttribute('aria-label') || ''))
                        .replace(/\\s+/g, ' ').trim()
                }))"""
        )
        return [
            (str(r.get("text") or ""), str(r.get("href") or "")) for r in (rows or [])
        ]
    except Exception:
        return []


async def _open_original_job_post(page: Any) -> str:
    """Follow Jobright's Original Job Post control — never APPLY WITH AUTOFILL."""
    labeled = await _labeled_links(page)
    found = pick_original_job_post_url(labeled)
    if found:
        return found

    loc = page.locator(
        "a:has-text('Original Job Post'), button:has-text('Original Job Post'), "
        "[aria-label*='Original Job Post' i]"
    ).first
    try:
        if await loc.count() == 0:
            return ""
        try:
            async with page.context.expect_page(timeout=8_000) as popup_info:
                await loc.click()
            popup = await popup_info.value
            await popup.wait_for_load_state("domcontentloaded", timeout=15_000)
            resolved = popup.url
            popup_hrefs = await popup.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href).filter(h => h.startsWith('http'))"""
            )
            await popup.close()
            return _first_employer_url([resolved, *(popup_hrefs or [])])
        except Exception:
            await loc.click()
            await page.wait_for_timeout(2000)
            if is_ats_url(page.url) or is_original_employer_url(page.url):
                return canonical_apply_url(page.url)
            hrefs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href).filter(h => h.startsWith('http'))"""
            )
            return _first_employer_url(list(hrefs or []))
    except Exception:
        return ""


async def _next_data_urls(page: Any) -> list[str]:
    try:
        return list(
            await page.evaluate(
                """() => {
                    const el = document.getElementById('__NEXT_DATA__');
                    if (!el) return [];
                    let data;
                    try { data = JSON.parse(el.textContent || '{}'); }
                    catch (e) { return []; }
                    const out = [];
                    const walk = (node) => {
                        if (!node || typeof node !== 'object') return;
                        if (Array.isArray(node)) { node.forEach(walk); return; }
                        for (const [key, val] of Object.entries(node)) {
                            if (typeof val === 'string' && /^https?:\\/\\//i.test(val)
                                && /original|jobUrl|applyUrl|sourceUrl|postUrl|jobLink/i.test(key)) {
                                out.push(val);
                            } else {
                                walk(val);
                            }
                        }
                    };
                    walk(data);
                    return out;
                }"""
            )
            or []
        )
    except Exception:
        return []


async def _resolve_aggregator_url(
    page: Any,
    agg_url: str,
    vault: dict[str, Any],
    company: str = "",
    role: str = "",
) -> str:
    """Resolve a Jobright/Wellfound URL to the real employer apply URL.

    Logged-out Jobright only shows a Sign Up modal, so we also look up the
    company + title on public Greenhouse/Lever/Ashby boards and DuckDuckGo.
    """
    is_jobright = "jobright.ai" in agg_url

    if is_jobright:
        cookie_header = vault.get("jobright", {}).get("session_cookie", "") or ""
        import os

        if not cookie_header:
            cookie_header = os.environ.get("JOBRIGHT_MANUAL_COOKIE", "")
        if cookie_header:
            cookies = []
            for part in cookie_header.split(";"):
                if "=" in part:
                    name, _, val = part.strip().partition("=")
                    cookies.append(
                        {
                            "name": name.strip(),
                            "value": val.strip(),
                            "domain": ".jobright.ai",
                            "path": "/",
                        }
                    )
            if cookies:
                try:
                    await page.context.add_cookies(cookies)
                except Exception as e:
                    logger.debug("Could not inject Jobright cookies: %s", e)

    try:
        await page.goto(agg_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2500)
    except Exception as e:
        logger.warning("Aggregator navigation failed: %s", e)
        if is_jobright:
            await _jobright_login(page, vault)
        looked = await asyncio.to_thread(lookup_public_apply_url, company, role)
        return looked or agg_url

    if is_jobright:
        await _jobright_login(page, vault)
        try:
            await page.goto(agg_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    original = await _open_original_job_post(page)
    if original:
        return original

    try:
        hrefs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href).filter(h => h.startsWith('http'))"""
        )
        found = _first_ats_url(list(hrefs or []))
        if found:
            return found
    except Exception:
        pass

    found = _first_employer_url(await _next_data_urls(page))
    if found:
        return found

    try:
        apply_locator = page.locator(
            "a:has-text('Apply on Employer'), a:has-text('Employer Site'), "
            "button:has-text('Apply on Employer'), button:has-text('Employer Site')"
        ).first
        if await apply_locator.count() > 0:
            try:
                async with page.context.expect_page(timeout=8_000) as popup_info:
                    await apply_locator.click()
                popup = await popup_info.value
                await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
                resolved = popup.url
                popup_hrefs = await popup.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.href).filter(h => h.startsWith('http'))"""
                )
                await popup.close()
                found = _first_employer_url([resolved, *(popup_hrefs or [])])
                if found:
                    return found
            except Exception:
                await page.wait_for_timeout(1500)
                if is_ats_url(page.url) or is_original_employer_url(page.url):
                    return canonical_apply_url(page.url)
    except Exception:
        pass

    looked_up = ""
    if company and role:
        looked_up = await asyncio.to_thread(lookup_public_apply_url, company, role)
    return looked_up or agg_url


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

    # CareerPuck / job-boards listing pages embed the real form in an iframe.
    try:
        embed = page.locator("iframe[src*='greenhouse.io/embed']").first
        if await embed.count() > 0:
            src = await embed.get_attribute("src")
            if src:
                await page.goto(src, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1500)
    except Exception:
        pass

    # If we're on a job detail page rather than the form, click Apply
    apply_btn = page.locator(
        "a[href*='/applications/new'], a[href*='#application'], "
        "a:has-text('Apply'), button:has-text('Apply'), "
        "button:has-text('Apply for this job'), "
        "[data-qa='btn-apply'], [aria-label*='Apply' i]"
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

    await try_fill(
        "input#first_name, input[name='job_application[first_name]']",
        first,
        "first_name",
    )
    await try_fill(
        "input#last_name, input[name='job_application[last_name]']", last, "last_name"
    )
    await try_fill(
        "input#email, input[name='job_application[email]']",
        personal.get("email", ""),
        "email",
    )
    await try_fill(
        "input#phone, input[name='job_application[phone]']",
        personal.get("phone", ""),
        "phone",
    )

    # Location — try several selectors
    location_val = f"{personal.get('city', '')}, {personal.get('state', '')}"
    await try_fill(
        "input#job_application_location, "
        "input[placeholder*='location' i], "
        "input[placeholder*='city' i], "
        "input[autocomplete='off'][id*='location']",
        location_val,
        "location",
    )

    # Address
    address = f"{personal.get('address_line1', '')} {personal.get('city', '')} {personal.get('state', '')} {personal.get('zip', '')}".strip()
    if address:
        await try_fill(
            "input#job_application_location, "
            "input[placeholder*='address' i], "
            "input[placeholder*='street' i], "
            "input[autocomplete*='address' i]",
            address,
            "address",
        )

    # LinkedIn — vault and resume use `linkedin`, older adapters used `linkedin_url`
    linkedin = personal.get("linkedin") or personal.get("linkedin_url") or ""
    if linkedin:
        await try_fill(
            "input[placeholder*='linkedin' i], "
            "input[name*='linkedin' i], "
            "input[id*='linkedin' i], "
            "input[aria-label*='linkedin' i]",
            linkedin,
            "linkedin",
        )

    # GitHub / portfolio — only fill if explicitly set (null means user has none)
    github = (
        personal.get("github")
        or personal.get("github_url")
        or personal.get("portfolio_url")
    )
    if github:
        await try_fill(
            "input[placeholder*='github' i], "
            "input[placeholder*='portfolio' i], "
            "input[placeholder*='website' i], "
            "input[name*='website' i]",
            github,
            "website",
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
    heard = (
        std.get("how_did_you_hear")
        or answer_for(vault, "how did you hear about this job")
        or "Online job board"
    )
    await _answer_question_by_label(
        page, ["how did you hear", "how did you find", "referred by"], heard, filled
    )

    await _fill_employment_from_resume(page, vault, filled)

    # Work auth + EEO — fill ALL custom questions by label text
    await _fill_all_custom_questions(page, work_auth, eeo, personal, filled)
    await _fill_from_answer_bank(page, vault, filled)
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
        (
            [
                "authorized to work",
                "legally authorized",
                "work in the united states",
                "work in united states",
                "eligible to work",
            ],
            "Yes" if authorized else "No",
        ),
        (
            [
                "require.*sponsorship",
                "visa sponsorship",
                "require an employment visa",
                "sponsorship.*employment",
                "require sponsorship",
                "will you now",
            ],
            "Yes" if needs_sponsor else "No",
        ),
        (["physical address", "mailing address", "street address"], address),
        (["linkedin"], personal.get("linkedin") or personal.get("linkedin_url") or ""),
        (
            ["gender identity", "describe your gender"],
            eeo.get("gender")
            or eeo.get("gender_identity")
            or "Decline to self-identify",
        ),
        (
            [
                "racial",
                "ethnic background",
                "race/ethni",
                "racial/ethnic",
                "african american",
            ],
            eeo.get("ethnicity")
            or eeo.get("race_ethnicity")
            or "Decline to self-identify",
        ),
        (
            ["sexual orientation"],
            eeo.get("sexual_orientation") or "Decline to self-identify",
        ),
        (["identify as transgender", "transgender"], "No"),
        (
            ["disability", "chronic condition"],
            eeo.get("disability")
            or eeo.get("disability_status")
            or "I don't wish to answer",
        ),
        (
            ["veteran", "armed forces", "military service"],
            eeo.get("veteran_status") or "Decline to self-identify",
        ),
        (["hispanic", "latino"], "No"),
        (["how did you hear", "how did you find"], "Online job board"),
        # "Do you currently reside in any of the following states?" → Texas
        # States question — use full name since Greenhouse combobox shows "Texas" not "TX"
        (
            ["reside in any", "which states", "currently reside"],
            _state_full_name(personal.get("state", "TX")),
        ),
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

            elif tag == "INPUT" and input_type in (
                "text",
                "email",
                "tel",
                "search",
                "",
            ):
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
    full_name = (
        f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
    )

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
        full_name,
        "name",
        full_name_selector,
        *_aria_sels(["full name", "legal name", "your name", "fullname"]),
    )
    # First name
    await _try_fill(
        personal.get("first_name", ""),
        "first_name",
        first_name_selector,
        *_aria_sels(["first name", "firstname", "given name"]),
    )
    # Last name
    await _try_fill(
        personal.get("last_name", ""),
        "last_name",
        last_name_selector,
        *_aria_sels(["last name", "lastname", "surname", "family name"]),
    )
    # Email
    await _try_fill(
        personal.get("email", ""),
        "email",
        email_selector,
        "input[type='email']",
        *_aria_sels(["email", "e-mail"]),
    )
    # Phone
    await _try_fill(
        personal.get("phone", ""),
        "phone",
        phone_selector,
        "input[type='tel']",
        *_aria_sels(["phone", "mobile", "telephone"]),
    )
    linkedin = personal.get("linkedin") or personal.get("linkedin_url") or ""
    if linkedin:
        await _try_fill(
            linkedin,
            "linkedin",
            linkedin_selector,
            *_aria_sels(["linkedin", "linkedin url", "linkedin profile"]),
        )
    # City / location
    await _try_fill(
        personal.get("city", ""),
        "city",
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
            await page.wait_for_selector(
                selector, timeout=timeout_ms // len([1, 2, 3, 4, 5])
            )
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
        ok = await _js_dispatch_fill(
            page, "input[type='email']", personal.get("email", "")
        )
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
        page,
        vault,
        resume_path,
        filled,
        first_name_selector="input[name='firstname'], input[name='first_name']",
        last_name_selector="input[name='lastname'], input[name='last_name']",
        email_selector="input[name='email']",
        phone_selector="input[name='phone']",
    )

    # JS fallback
    if "email" not in filled and personal.get("email"):
        await _js_dispatch_fill(
            page, "input[type='email'], input[name='email']", personal["email"]
        )
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
                ok = await _js_dispatch_fill(
                    page, f"input[aria-label*='{aria}' i], input[type='{aria}']", val
                )
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
        page,
        vault,
        resume_path,
        filled,
        first_name_selector="input[data-testid='firstName'], input[data-testid='first-name']",
        last_name_selector="input[data-testid='lastName'], input[data-testid='last-name']",
        email_selector="input[data-testid='email']",
        phone_selector="input[data-testid='phone'], input[data-testid='phoneNumber']",
        linkedin_selector="input[data-testid='linkedin'], input[data-testid='linkedinUrl']",
    )
    # Aria fallback for anything not filled
    await _fill_standard_fields(page, vault, resume_path, filled, use_aria=True)

    # JS dispatch for anything still missed
    for key, val in [
        ("email", personal.get("email", "")),
        ("phone", personal.get("phone", "")),
    ]:
        if key not in filled and val:
            ok = await _js_dispatch_fill(
                page, f"input[type='{key}'], input[name='{key}']", val
            )
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
        page,
        vault,
        resume_path,
        filled,
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
        page,
        vault,
        resume_path,
        filled,
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

    result = await page.evaluate(
        """
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
    """,
        answer_map,
    )

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

    result = await page.evaluate(
        """
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
    """,
        eeo_map,
    )

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
    """Click the submit button on the form — not a listing-page Apply link."""
    try:
        body = (await page.inner_text("body")).lower()
        if (
            any(
                p in body
                for p in (
                    "create account",
                    "forgot password",
                    "verify your email",
                    "sign in to",
                )
            )
            and await page.locator("input[type='file']").count() == 0
        ):
            return False
    except Exception:
        pass

    selectors = [
        "input[type='submit'][value*='Submit' i]",
        "button[type='submit']:has-text('Submit')",
        "button:has-text('Submit application')",
        "button:has-text('Submit Application')",
        "button:has-text('Send application')",
        "button:has-text('Submit')",
        "[data-automation-id='pageFooterNextButton']:has-text('Submit')",
        "input[type='submit']",
        "button[data-automation-id='bottom-navigation-next-button']",
        "button[type='submit']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                label = ((await btn.inner_text()) or "").strip().lower()
                if label and "submit" not in label and "send" not in label:
                    continue
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

    full_name = (
        f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
    )
    await lever_fill("input[name='name']", full_name, "name")
    await lever_fill("input[name='email']", personal.get("email", ""), "email")
    await lever_fill("input[name='phone']", personal.get("phone", ""), "phone")
    await lever_fill("input[name='org']", "NMDP", "current_company")
    await lever_fill(
        "input[name='urls[LinkedIn]']", personal.get("linkedin_url", ""), "linkedin"
    )

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

    full_name = (
        f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
    )

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


def _session_state_path(url: str) -> Path | None:
    """Persist Playwright cookies per Workday tenant (or Jobright)."""
    import urllib.parse

    low = (url or "").lower()
    folder = Path(__file__).parents[2] / "data" / "browser_sessions"
    if "myworkdayjobs.com" in low:
        host = urllib.parse.urlparse(url).netloc.lower()
        safe = re.sub(r"[^a-z0-9.-]+", "_", host) or "workday"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{safe}.json"
    if "jobright.ai" in low:
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "jobright.json"
    return None


async def _set_control_value(el: Any, value: str) -> bool:
    try:
        tag = (await el.evaluate("e => (e.tagName || '').toLowerCase()")).lower()
        role = (await el.get_attribute("role") or "").lower()
        input_type = (await el.get_attribute("type") or "").lower()
        if tag == "select":
            opts = await el.locator("option").all()
            target = value.lower()
            for opt in opts:
                opt_text = (await opt.inner_text()).strip()
                if opt_text and (
                    target in opt_text.lower() or opt_text.lower() in target
                ):
                    await el.select_option(label=opt_text)
                    return True
            return False
        if (
            input_type in {"checkbox", "radio"}
            or tag == "input"
            and input_type in {"checkbox", "radio"}
        ):
            if value.lower() in {"yes", "true", "on", "1"}:
                if not await el.is_checked():
                    await el.check()
                return True
            return False
        if role == "combobox" or (await el.get_attribute("aria-haspopup") or "") in {
            "true",
            "listbox",
        }:
            page = el.page
            await el.click()
            await page.wait_for_timeout(400)
            opts = page.locator('[role="option"]:visible')
            count = await opts.count()
            target = value.lower()
            for i in range(count):
                opt = opts.nth(i)
                opt_text = (await opt.inner_text()).strip()
                if opt_text and (
                    target in opt_text.lower() or opt_text.lower() in target
                ):
                    await opt.click()
                    return True
            await page.keyboard.press("Escape")
            return False
        await el.fill(value)
        return True
    except Exception:
        return False


_HONEYPOT_RE = re.compile(
    r"robots only|if you.?re human|honeypot|beecatcher|do not enter",
    re.I,
)


async def _fill_from_answer_bank(
    page: Any, vault: dict[str, Any], filled: dict[str, str]
) -> None:
    """Walk every label on the page and reuse vault / resume answers."""
    labels = await page.locator("label").all()
    for label in labels:
        try:
            text = (await label.inner_text()).strip()
            if not text or len(text) > 240:
                continue
            if _HONEYPOT_RE.search(text):
                continue
            answer = answer_for(vault, text)
            if not answer:
                continue
            key = text[:80]
            if filled.get(key) or filled.get("linkedin") and "linkedin" in text.lower():
                continue
            for_id = await label.get_attribute("for") or ""
            el = None
            if for_id:
                loc = page.locator(f'[id="{for_id}"]').first
                if await loc.count() > 0:
                    el = loc
            if el is None:
                inner = label.locator(
                    "input, textarea, select, [role='combobox']"
                ).first
                if await inner.count() > 0:
                    el = inner
            if el is None:
                # following control in the same field wrapper
                parent = label.locator(
                    "xpath=ancestor::*[self::div or self::li or self::fieldset][1]"
                )
                nearby = parent.locator(
                    "input, textarea, select, [role='combobox']"
                ).first
                if await nearby.count() > 0:
                    el = nearby
            if el is None:
                continue
            auto_id = (await el.get_attribute("data-automation-id") or "").lower()
            name = (await el.get_attribute("name") or "").lower()
            if auto_id in {"beecatcher"} or name in {"website", "beecatcher"}:
                continue
            if await _set_control_value(el, answer):
                filled[key] = answer
        except Exception:
            continue

    # Checkboxes whose accessible name is the policy text (often not a <label for=>)
    for pattern in (
        "i have read",
        "i agree",
        "i acknowledge",
        "i certify",
        "i identify",
        "electronic signature",
        "family policy",
    ):
        try:
            box = page.get_by_text(pattern, exact=False).first
            if await box.count() > 0:
                await box.click()
                filled[pattern] = "Yes"
        except Exception:
            continue


async def _fill_employment_from_resume(
    page: Any, vault: dict[str, Any], filled: dict[str, str]
) -> None:
    history = (
        vault.get("_work_history")
        if isinstance(vault.get("_work_history"), list)
        else []
    )
    if not history:
        return
    row = history[0]
    pairs = [
        (["company", "employer", "organization"], row.get("company") or "", "employer"),
        (["stage", "title", "position", "role"], row.get("title") or "", "title"),
        (["start date", "from"], row.get("start") or "", "start_date"),
        (["end date", "to"], row.get("end") or "", "end_date"),
    ]
    for patterns, value, key in pairs:
        if not value:
            continue
        await _answer_question_by_label(page, patterns, value, filled)
        if key not in filled:
            filled[key] = value
    if row.get("current") == "Yes":
        try:
            current = page.get_by_text("Current role", exact=False).first
            if await current.count() > 0:
                await current.click()
                filled["current_role"] = "Yes"
        except Exception:
            pass


async def _click_first_visible(
    page: Any, selectors: list[str], timeout_ms: int = 2500
) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if await loc.is_visible():
                await loc.click()
                return True
        except Exception:
            continue
    return False


async def _logged_into_jobright(page: Any) -> bool:
    try:
        url = (page.url or "").lower()
        if "jobright.ai" not in url:
            return False
        google = page.locator(
            "button:has-text('Continue with Google'), button:has-text('Sign in with Google'), "
            "button:has-text('Google')"
        ).first
        if await google.count() > 0 and await google.is_visible():
            return False
        login = page.locator(
            "button:has-text('Login'), a:has-text('Login'), button:has-text('Log in'), "
            "a:has-text('Log in'), button:has-text('Sign Up'), button:has-text('Sign up')"
        ).first
        if await login.count() > 0 and await login.is_visible():
            return False
        body = (await page.inner_text("body")).lower()
        if "continue with google" in body or "sign up to apply" in body:
            return False
        return True
    except Exception:
        return False


async def _fill_google_login(page: Any, email: str, password: str) -> None:
    """Fill a Google account chooser / password form. User-owned account only."""
    try:
        await page.wait_for_timeout(800)
        email_el = page.locator("input[type='email'], input[name='identifier']").first
        if await email_el.count() > 0:
            await email_el.wait_for(state="visible", timeout=8_000)
            await email_el.fill(email)
            nxt = page.locator("#identifierNext, button:has-text('Next')").first
            if await nxt.count() > 0:
                await nxt.click()
            await page.wait_for_timeout(2000)
        pw_el = page.locator("input[type='password'], input[name='Passwd']").first
        if await pw_el.count() > 0:
            await pw_el.wait_for(state="visible", timeout=10_000)
            await pw_el.fill(password)
            nxt = page.locator("#passwordNext, button:has-text('Next')").first
            if await nxt.count() > 0:
                await nxt.click()
            await page.wait_for_timeout(3000)
        await _click_first_visible(
            page,
            [
                "button:has-text('Continue')",
                "button:has-text('I agree')",
                "button:has-text('Allow')",
            ],
        )
        await page.wait_for_timeout(1500)
    except Exception as exc:
        logger.debug("Google login fill skipped: %s", exc)


async def _jobright_google_signin(page: Any, email: str, password: str) -> None:
    google_btn = page.locator(
        "button:has-text('Continue with Google'), button:has-text('Sign in with Google'), "
        "button:has-text('Google'), a:has-text('Continue with Google')"
    ).first
    if await google_btn.count() == 0:
        return
    try:
        async with page.context.expect_page(timeout=10_000) as popup_info:
            await google_btn.click()
        google_page = await popup_info.value
        await google_page.wait_for_load_state("domcontentloaded", timeout=20_000)
        await _fill_google_login(google_page, email, password)
        await page.wait_for_timeout(3000)
    except Exception:
        await google_btn.click()
        await page.wait_for_timeout(2000)
        target = page
        for p in page.context.pages:
            if "accounts.google.com" in (p.url or ""):
                target = p
                break
        await _fill_google_login(target, email, password)
        await page.wait_for_timeout(3000)


async def _jobright_login(page: Any, vault: dict[str, Any]) -> None:
    """Log into Jobright with the same Apply Profile email + password.

    Tries Jobright's own email/password fields first, then Google with those
    same credentials. Does not use jobright.ai/login (that URL is a 404).
    """
    jr = vault.get("jobright") if isinstance(vault.get("jobright"), dict) else {}
    personal = vault.get("personal") if isinstance(vault.get("personal"), dict) else {}
    creds = (
        vault.get("credentials") if isinstance(vault.get("credentials"), dict) else {}
    )
    email = str(jr.get("email") or personal.get("email") or "").strip()
    password = str(jr.get("password") or creds.get("password") or "").strip()
    if password == "********":
        password = ""
    if not email or not password:
        return
    if await _logged_into_jobright(page):
        return
    try:
        opened = await _click_first_visible(
            page,
            [
                "button:has-text('Login')",
                "a:has-text('Login')",
                "button:has-text('Log in')",
                "a:has-text('Log in')",
                "button:has-text('Sign in')",
                "button:has-text('Sign Up')",
                "button:has-text('Sign up')",
                "button:has-text('Try For Free')",
            ],
        )
        if not opened:
            await page.goto(
                "https://jobright.ai/", wait_until="domcontentloaded", timeout=25_000
            )
            await page.wait_for_timeout(1500)
            await _click_first_visible(
                page,
                [
                    "button:has-text('Login')",
                    "a:has-text('Login')",
                    "button:has-text('Try For Free')",
                    "button:has-text('Sign Up')",
                ],
            )
        await page.wait_for_timeout(1200)

        email_el = page.locator(
            "input[type='email'], input[name='email'], input[placeholder*='email' i]"
        ).first
        pw_el = page.locator("input[type='password']").first
        if await email_el.count() > 0 and await email_el.is_visible():
            await email_el.fill(email)
            if await pw_el.count() > 0 and await pw_el.is_visible():
                await pw_el.fill(password)
            else:
                await _click_first_visible(
                    page,
                    [
                        "button:has-text('Continue')",
                        "button:has-text('Next')",
                        "button[type='submit']",
                    ],
                )
                await page.wait_for_timeout(800)
                pw_el = page.locator("input[type='password']").first
                if await pw_el.count() > 0:
                    await pw_el.fill(password)
            await _click_first_visible(
                page,
                [
                    "button[type='submit']",
                    "button:has-text('Log in')",
                    "button:has-text('Login')",
                    "button:has-text('Sign in')",
                    "button:has-text('Continue')",
                ],
            )
            await page.wait_for_timeout(2500)

        if await _logged_into_jobright(page):
            return
        await _jobright_google_signin(page, email, password)
        await page.wait_for_timeout(2000)
    except Exception as exc:
        logger.debug("Jobright login skipped: %s", exc)


def _smtp_send_application(
    gmail: str,
    app_password: str,
    mailto_url: str,
    resume_path: str,
    company: str,
    role: str,
    full_name: str,
    summary: str,
) -> None:
    import smtplib
    import urllib.parse
    from email.message import EmailMessage

    parsed = urllib.parse.urlparse(mailto_url)
    to_addr = parsed.path
    qs = urllib.parse.parse_qs(parsed.query)
    subject = (qs.get("subject") or [f"Application — {role} — {company}"])[0]
    msg = EmailMessage()
    msg["From"] = gmail
    msg["To"] = to_addr
    msg["Subject"] = subject
    intro = summary or f"{full_name} applying for {role} at {company}."
    msg.set_content(
        f"Hello {company or ''} team,\n\n"
        f"Please find my resume attached for the {role} role.\n\n"
        f"{intro}\n\nThank you,\n{full_name}\n"
    )
    with open(resume_path, "rb") as fh:
        msg.add_attachment(
            fh.read(), maintype="application", subtype="pdf", filename="resume.pdf"
        )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(gmail, app_password)
        smtp.send_message(msg)


def _imap_otp(gmail: str, app_password: str, sender_hint: str = "workday") -> str:
    import email as email_lib
    import imaplib

    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
    try:
        mail.login(gmail, app_password)
        mail.select("INBOX")
        typ, data = mail.search(None, "(UNSEEN)")
        if typ != "OK":
            return ""
        ids = data[0].split()[-8:]
        for msg_id in reversed(ids):
            typ, raw = mail.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email_lib.message_from_bytes(raw[0][1])
            blob = f"{msg.get('From', '')} {msg.get('Subject', '')}"
            if (
                sender_hint.lower() not in blob.lower()
                and "myworkday" not in blob.lower()
            ):
                continue
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body += part.get_payload(decode=True).decode(
                            "utf-8", errors="replace"
                        )
            else:
                payload = msg.get_payload(decode=True)
                body = (
                    payload.decode("utf-8", errors="replace")
                    if isinstance(payload, bytes)
                    else str(payload)
                )
            found = re.search(r"\b(\d{6})\b", body)
            if found:
                return found.group(1)
        return ""
    finally:
        try:
            mail.logout()
        except Exception:
            pass


async def _apply_email(
    page: Any,
    apply_url: str,
    resume_path: str,
    vault: dict[str, Any],
    session: ApplicationSession,
) -> dict[str, str]:
    mailto = apply_url if apply_url.lower().startswith("mailto:") else ""
    if not mailto:
        try:
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1500)
            html = await page.content()
            hrefs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href') || '')"""
            )
            mailto = (
                extract_mailto(apply_url)
                or extract_mailto(" ".join(hrefs or []))
                or extract_mailto(html)
            )
        except Exception as exc:
            logger.debug("Email-apply page fetch failed: %s", exc)
            mailto = extract_mailto(apply_url)
    if not mailto:
        return {
            "site": "email",
            "note": "careers page has no application form or mailto link",
        }
    gmail, app_password = gmail_credentials(vault)
    if not gmail or not app_password:
        raise RuntimeError(
            "This employer applies by email. Save your Gmail address and a Gmail App Password "
            "in Apply Profile (Google Account → Security → App passwords). "
            "Do not paste the password in chat."
        )
    derived = vault.get("_derived") if isinstance(vault.get("_derived"), dict) else {}
    personal = vault.get("personal") if isinstance(vault.get("personal"), dict) else {}
    await asyncio.to_thread(
        _smtp_send_application,
        gmail,
        app_password,
        mailto,
        resume_path,
        session.company,
        session.role,
        derived.get("full_name")
        or f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip(),
        derived.get("summary") or "",
    )
    to_addr = mailto.split("?", 1)[0].replace("mailto:", "")
    return {"submitted_via": "email", "email_to": to_addr, "role": session.role}


async def _fill_generic(
    page: Any,
    apply_url: str,
    resume_path: str,
    vault: dict[str, Any],
    session: ApplicationSession,
) -> dict[str, str]:
    await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2000)
    try:
        hrefs = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.getAttribute('href') || '')"""
        )
        mailto = extract_mailto(" ".join(hrefs or []))
        if mailto:
            return await _apply_email(page, mailto, resume_path, vault, session)
    except Exception:
        pass
    filled: dict[str, str] = {}
    try:
        file_input = page.locator("input[type='file']").first
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_path)
            filled["resume"] = resume_path
    except Exception:
        pass
    await _fill_from_answer_bank(page, vault, filled)
    if not filled:
        filled = {"site": "unknown", "note": "no adapter — opened employer page"}
    return filled


async def _workday_click_apply(page: Any) -> None:
    """Job posting → Apply modal → Autofill with Resume (then Create Account / form)."""
    try:
        await page.wait_for_selector(
            "[data-automation-id='adventureButton'], "
            "[data-automation-id='jobPostingApplyButton']",
            timeout=20_000,
        )
    except Exception:
        pass
    for sel in (
        "[data-automation-id='adventureButton']",
        "[data-automation-id='jobPostingApplyButton']",
        "a:has-text('Apply')",
        "button:has-text('Apply')",
    ):
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                break
        except Exception:
            continue
    try:
        await page.wait_for_selector(
            "[data-automation-id='autofillWithResume'], "
            "[data-automation-id='applyManually']",
            timeout=10_000,
        )
    except Exception:
        pass
    for sel in (
        "[data-automation-id='autofillWithResume']",
        "button:has-text('Autofill with Resume')",
        "[data-automation-id='applyManually']",
        "button:has-text('Apply Manually')",
    ):
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                break
        except Exception:
            continue
    try:
        await page.wait_for_selector(
            "input[data-automation-id='email'], input[type='password'], input[type='file']",
            timeout=20_000,
        )
    except Exception:
        pass


async def _workday_still_on_login(page: Any) -> bool:
    try:
        body = (await page.inner_text("body")).lower()
    except Exception:
        return False
    loginish = any(
        p in body
        for p in (
            "create account/sign in",
            "forgot your password",
            "forgot password",
            "verify your email",
            "verify password",
        )
    )
    if not loginish and "create account" in body and "sign in" in body:
        loginish = True
    if not loginish:
        return False
    try:
        if await page.locator("input[type='file']").count() > 0:
            return False
    except Exception:
        pass
    return True


async def _workday_fill_email_password(page: Any, email: str, password: str) -> None:
    email_el = page.locator("input[data-automation-id='email']").first
    if await email_el.count() > 0:
        await email_el.fill(email)
    pw = page.locator("input[data-automation-id='password']").first
    if await pw.count() > 0:
        await pw.fill(password)
    verify = page.locator("input[data-automation-id='verifyPassword']").first
    if await verify.count() > 0:
        await verify.fill(password)


async def _workday_auth(
    page: Any, email: str, password: str, vault: dict[str, Any], filled: dict[str, str]
) -> None:
    if not email or not password:
        filled["auth_note"] = (
            "Workday accounts are per company. Save one email + password in Apply Profile "
            "to create/sign-in on each tenant."
        )
        return
    try:
        create_btn = page.locator(
            "[data-automation-id='createAccountSubmitButton']"
        ).first
        if await create_btn.count() > 0 and await create_btn.is_visible():
            await _workday_fill_email_password(page, email, password)
            box = page.locator("[data-automation-id='createAccountCheckbox']").first
            if await box.count() > 0:
                try:
                    await box.check()
                except Exception:
                    await box.click()
            await create_btn.click()
            await page.wait_for_timeout(3500)

        body = (await page.inner_text("body")).lower()
        already = "already" in body and (
            "registered" in body or "exists" in body or "sign in" in body
        )
        signin_link = page.locator("[data-automation-id='signInLink']").first
        if already or (
            await _workday_still_on_login(page)
            and await signin_link.count() > 0
            and await create_btn.count() == 0
        ):
            if await signin_link.count() > 0 and await signin_link.is_visible():
                await signin_link.click()
                await page.wait_for_timeout(1200)
            await _workday_fill_email_password(page, email, password)
            go = page.locator("[data-automation-id='signInSubmitButton']").first
            if await go.count() > 0:
                await go.click()
                await page.wait_for_timeout(3000)

        gmail, app_pw = gmail_credentials(vault)
        if gmail and app_pw and await _workday_still_on_login(page):
            otp = ""
            for _ in range(3):
                otp = await asyncio.to_thread(_imap_otp, gmail, app_pw, "workday")
                if otp:
                    break
                await page.wait_for_timeout(4000)
            if otp:
                otp_el = page.locator(
                    "input[autocomplete='one-time-code'], input[id*='code' i], "
                    "input[name*='code' i]"
                ).first
                if await otp_el.count() > 0:
                    await otp_el.fill(otp)
                    verify_btn = page.locator(
                        "button:has-text('Verify'), button[type='submit']"
                    ).first
                    if await verify_btn.count() > 0:
                        await verify_btn.click()
                        await page.wait_for_timeout(2500)
                        filled["workday_otp"] = "entered"
    except Exception as exc:
        logger.debug("Workday auth step skipped: %s", exc)

    if await _workday_still_on_login(page):
        filled["auth_note"] = (
            "Workday login wall — this company needs its own account. "
            "Playwright used Apply Profile email/password on Create Account / Sign In."
        )


async def _fill_workday(
    page: Any, apply_url: str, resume_path: str, vault: dict[str, Any]
) -> dict[str, str]:
    """Per-tenant Workday apply. One signup is NOT global — cookies are saved per host."""
    filled: dict[str, str] = {"site": "workday"}
    await page.goto(apply_url, wait_until="domcontentloaded", timeout=45_000)
    await page.wait_for_timeout(2500)
    await _workday_click_apply(page)

    personal = vault.get("personal") if isinstance(vault.get("personal"), dict) else {}
    creds = (
        vault.get("credentials") if isinstance(vault.get("credentials"), dict) else {}
    )
    email = str(personal.get("email") or "").strip()
    password = str(creds.get("password") or "").strip()
    if password == "********":
        password = ""

    if (
        await _workday_still_on_login(page)
        or await page.locator("input[type='password']").count() > 0
    ):
        await _workday_auth(page, email, password, vault, filled)
        try:
            await page.wait_for_selector("input[type='file']", timeout=12_000)
        except Exception:
            pass

    try:
        file_input = page.locator("input[type='file']").first
        if await file_input.count() > 0:
            await file_input.set_input_files(resume_path)
            filled["resume"] = resume_path
            await page.wait_for_timeout(2000)
    except Exception:
        pass

    if await _workday_still_on_login(page) and "resume" not in filled:
        filled.setdefault(
            "auth_note",
            "Workday login wall — could not reach the application form.",
        )
        return filled

    await _fill_from_answer_bank(page, vault, filled)
    for _ in range(8):
        nxt = page.locator(
            "button:has-text('Next'), button:has-text('Continue'), "
            "[data-automation-id='bottom-navigation-next-button'], "
            "[data-automation-id='pageFooterNextButton']"
        ).first
        try:
            if await nxt.count() == 0 or not await nxt.is_visible():
                break
            label = ((await nxt.inner_text()) or "").strip().lower()
            if "submit" in label:
                break
            await nxt.click()
            await page.wait_for_timeout(1500)
            await _fill_from_answer_bank(page, vault, filled)
        except Exception:
            break
    if await _workday_still_on_login(page) and "resume" not in filled:
        filled.setdefault(
            "auth_note",
            "Workday login wall — could not reach the application form.",
        )
    return filled
