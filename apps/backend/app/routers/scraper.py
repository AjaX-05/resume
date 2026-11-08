"""Job scraper control endpoints."""

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scraper", tags=["Job Scraper"])

# Path to the job-scraper module (apps/job-scraper/)
_SCRAPER_DIR = Path(__file__).parents[3] / "job-scraper"
_PYTHON = _SCRAPER_DIR / ".venv" / "Scripts" / "python.exe"
_SCRIPT = _SCRAPER_DIR / "main.py"
_DISCOVERED_DIR = _SCRAPER_DIR / "data" / "discovered"

# In-memory state — resets on backend restart
_state: dict = {
    "running": False,
    "stopping": False,
    "source": None,
    "last_run_at": None,
    "last_jobs_found": 0,
    "last_error": None,
    "last_log_tail": None,  # last ~40 lines of subprocess output for debugging
    "last_auto_tailor_task_id": None,  # task_id if auto_tailor fired after this run
    "lookback_minutes": 60,
}
_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_stop_requested = False

# Timestamp of the scrape start — used to find NEW application cards afterwards
# (anything created on/after this timestamp is a product of this scrape run).
_scrape_started_at: dict[str, str] = {"value": ""}
_SCRAPE_TIMEOUT_SECONDS = (
    2400  # 40 min; cards are posted live so a timeout still leaves jobs
)


def _bump_jobs_from_line(line: str, jobs_found: int) -> int:
    if "Kanban card created" in line:
        jobs_found += 1
    m = re.search(r"(\d+)\s+new(?:/changed)?\s+jobs?", line, re.IGNORECASE)
    if m:
        jobs_found = max(jobs_found, int(m.group(1)))
    return jobs_found


def _scraper_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Cursor/sandbox caches a Playwright browser tree that Jobright cannot use.
    # channel=chrome needs the real installed Chrome, not that path.
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    return env


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def _kill_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def _run(source: str, lookback_minutes: int = 60) -> None:
    global _proc, _stop_requested
    with _lock:
        if _state["running"]:
            return
        _stop_requested = False
        _state.update(
            running=True,
            stopping=False,
            source=source,
            lookback_minutes=lookback_minutes,
            last_error=None,
            last_jobs_found=0,
            last_log_tail=None,
            last_auto_tailor_task_id=None,
        )
    # Record scrape start time so we can find NEW cards afterwards
    _scrape_started_at["value"] = datetime.now(timezone.utc).isoformat()

    try:
        cmd = [str(_PYTHON), "-u", str(_SCRIPT), "--once"]
        if source != "all":
            cmd += ["--source", source]
        cmd += ["--lookback-minutes", str(lookback_minutes)]

        proc = subprocess.Popen(
            cmd,
            cwd=str(_SCRAPER_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_scraper_env(),
            bufsize=1,
            **_popen_kwargs(),
        )
        with _lock:
            _proc = proc
            if _stop_requested:
                _kill_proc(proc)
        lines: list[str] = []
        jobs_found = 0

        def _pump() -> None:
            nonlocal jobs_found
            if proc.stdout is None:
                return
            for line in proc.stdout:
                lines.append(line)
                jobs_found = _bump_jobs_from_line(line, jobs_found)
                with _lock:
                    _state.update(
                        last_jobs_found=jobs_found,
                        last_log_tail="".join(lines[-40:]),
                    )

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout=_SCRAPE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_proc(proc)
        reader.join(timeout=5)
        returncode = proc.poll()
        combined = "".join(lines)
        log_tail = "\n".join(combined.splitlines()[-40:]) if combined else None
        with _lock:
            stopped = _stop_requested
        error = None
        if stopped:
            error = (
                f"Stopped by user; {jobs_found} job(s) already added"
                if jobs_found
                else "Stopped by user"
            )
        elif timed_out:
            error = (
                f"Stopped after {_SCRAPE_TIMEOUT_SECONDS // 60} minutes; "
                f"{jobs_found} job(s) already added"
                if jobs_found
                else f"Timed out after {_SCRAPE_TIMEOUT_SECONDS // 60} minutes"
            )
        elif returncode not in (0, None) and jobs_found == 0:
            error = combined[-400:] if combined else f"scraper exited {returncode}"

        with _lock:
            _proc = None
            _state.update(
                running=False,
                stopping=False,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_jobs_found=jobs_found,
                last_error=error,
                last_log_tail=log_tail,
            )

        if jobs_found > 0 and not timed_out and not stopped and returncode == 0:
            try:
                task_id = _run_auto_tailor_sync()
                if task_id:
                    with _lock:
                        _state["last_auto_tailor_task_id"] = task_id
                    logger.info("Auto-tailor fired after scrape: task=%s", task_id)
            except Exception as e:
                logger.warning("Auto-tailor after scrape failed: %s", e)

    except Exception as e:
        logger.error(f"Scraper subprocess error: {e}")
        with _lock:
            _proc = None
            _state.update(running=False, stopping=False, last_error=str(e))


def _run_auto_tailor_sync() -> str | None:
    """Run auto-tailor from the scraper's background thread.

    Returns the created task_id, or None if auto_tailor is off / nothing new
    to tailor. We're inside a sync thread (subprocess.run completed) so we
    spin a fresh asyncio loop to drive the async pipeline helpers.
    """
    from app.services.pipeline_service import (
        bulk_tailor,
        detect_ats_for_applications,
        load_config,
    )

    cfg = load_config()
    if not cfg.get("auto_tailor", False):
        return None
    master_id = cfg.get("master_resume_id", "")
    if not master_id:
        logger.warning("auto_tailor is on but master_resume_id is unset — skipping")
        return None

    async def _work() -> str | None:
        # Find cards created at/after this scrape started AND still "saved"
        # (freshly discovered, not yet tailored).
        all_apps = await db.list_applications()
        started = _scrape_started_at.get("value", "")
        new_saved_ids = [
            a["application_id"]
            for a in all_apps
            if a.get("status") == "saved"
            and (not started or a.get("created_at", "") >= started)
        ]
        if not new_saved_ids:
            return None
        # Filter down to supported ATS only
        ats_map = await detect_ats_for_applications(new_saved_ids)
        supported_ids = [aid for aid, info in ats_map.items() if info.get("supported")]
        if not supported_ids:
            logger.info(
                "Auto-tailor: 0 supported cards among %d new saved cards",
                len(new_saved_ids),
            )
            return None
        logger.info(
            "Auto-tailor: queuing %d supported cards (of %d new)",
            len(supported_ids),
            len(new_saved_ids),
        )
        task = await bulk_tailor(
            supported_ids,
            master_id,
            prompt_id=cfg.get("tailor_prompt_id", "full"),
            rate_limit_seconds=cfg.get("tailor_rate_limit_seconds", 10.0),
        )
        return task.task_id

    try:
        return asyncio.run(_work())
    except Exception as e:
        logger.exception("Auto-tailor worker crashed: %s", e)
        return None


_ALLOWED_LOOKBACK = {30, 60, 120, 180, 240, 360, 720, 1440}


class RunRequest(BaseModel):
    source: str = "all"  # "all" | "jobright" | "wellfound" | "ycjobs"
    lookback_minutes: int = 60


@router.post("/run")
def run_scraper(req: RunRequest) -> dict:
    """Trigger a one-time scrape run in the background."""
    if not _PYTHON.exists():
        raise HTTPException(
            status_code=503,
            detail="Job scraper not set up. Run: cd apps/job-scraper && uv sync",
        )
    lookback = req.lookback_minutes if req.lookback_minutes in _ALLOWED_LOOKBACK else 60
    with _lock:
        if _state["running"]:
            return {"status": "already_running", **_state}

    thread = threading.Thread(target=_run, args=(req.source, lookback), daemon=True)
    thread.start()
    return {"status": "started", "source": req.source, "lookback_minutes": lookback}


@router.post("/stop")
def stop_scraper() -> dict:
    """Kill the live scrape subprocess (including Chrome children) immediately."""
    global _stop_requested
    with _lock:
        if not _state["running"]:
            return {"status": "idle", **dict(_state)}
        _stop_requested = True
        _state["stopping"] = True
        proc = _proc
    if proc is not None:
        _kill_proc(proc)
    return {"status": "stopping"}


@router.get("/status")
def scraper_status() -> dict:
    """Get current scraper status."""
    with _lock:
        return dict(_state)


def _build_jd_from_record(job: dict) -> str:
    """Build the JD text from a scraped JSON record."""
    parts = [f"# {job.get('title', '')} at {job.get('company', '')}"]
    if job.get("salary_text"):
        parts.append(f"**Compensation:** {job['salary_text']}")
    if job.get("equity_text"):
        parts.append(f"**Equity:** {job['equity_text']}")
    if job.get("location_text"):
        parts.append(f"**Location:** {job['location_text']}")
    parts.append("")
    parts.append(job.get("description", ""))
    if job.get("skills"):
        parts.append("\n## Skills")
        parts.extend(f"- {s}" for s in job["skills"])
    if job.get("responsibilities"):
        parts.append("\n## Responsibilities")
        parts.extend(f"- {r}" for r in job["responsibilities"])
    parts.append(f"\n**Apply:** {job.get('apply_url', '')}")
    return "\n".join(parts)


def _build_notes(job: dict) -> str:
    lines = [
        f"Source: {job.get('source', '')} | {job.get('source_url', '')}",
        f"Salary: {job.get('salary_text') or 'not mentioned'}",
    ]
    if job.get("equity_text"):
        lines.append(f"Equity: {job['equity_text']}")
    lines.append(f"Sponsorship: {job.get('sponsorship_status', 'not_mentioned')}")
    if job.get("posting_date"):
        lines.append(f"Posted: {job['posting_date']}")
    if job.get("seniority"):
        lines.append(f"Seniority: {job['seniority']}")
    lines.append(f"Apply: {job.get('apply_url', '')}")
    if job.get("linkedin_contacts"):
        lines.append("LinkedIn: " + " | ".join(job["linkedin_contacts"]))
    lines.append(f"Discovered: {job.get('first_seen_at', '')}")
    return "\n".join(lines)


@router.post("/sync-existing")
async def sync_existing_discoveries() -> dict:
    """Push any scraped JSON files to the application tracker.

    Reads every batch-*.json under apps/job-scraper/data/discovered/
    and creates one application card per job. Skips jobs already in the tracker.
    """
    if not _DISCOVERED_DIR.exists():
        return {
            "synced": 0,
            "skipped": 0,
            "message": "No discovered jobs directory yet",
        }

    # Find the default master resume
    resumes = await db.list_resumes()
    master = next((r for r in resumes if r.get("is_default_master")), None)
    if not master:
        master = next((r for r in resumes if r.get("is_master")), None)
    if not master:
        raise HTTPException(
            status_code=400,
            detail="No master resume found. Upload one in /dashboard first.",
        )
    resume_id = master["resume_id"]

    # Get existing applications to skip duplicates (match on company + role,
    # ignoring "Role @ Company" title clutter so Omnicom doesn't land twice).
    from app.services.pipeline_service import card_fingerprint

    existing = await db.list_applications()
    seen = {
        card_fingerprint(a.get("company"), a.get("role"))
        for a in existing
        if card_fingerprint(a.get("company"), a.get("role"))
    }

    synced = 0
    skipped = 0
    errors = 0

    for batch_file in sorted(_DISCOVERED_DIR.rglob("batch-*.json")):
        try:
            data = json.loads(batch_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Could not read {batch_file}: {e}")
            continue

        for job in data:
            key = card_fingerprint(job.get("company"), job.get("title"))
            if key and key in seen:
                skipped += 1
                continue
            try:
                jd_text = _build_jd_from_record(job)
                notes = _build_notes(job)
                await db.create_manual_application(
                    content=jd_text,
                    resume_id=resume_id,
                    company=job.get("company", ""),
                    role=job.get("title", ""),
                    status="saved",
                    notes=notes,
                )
                synced += 1
                if key:
                    seen.add(key)
            except Exception as e:
                logger.warning(
                    f"Failed to sync {job.get('title')} @ {job.get('company')}: {e}"
                )
                errors += 1

    return {"synced": synced, "skipped": skipped, "errors": errors}
