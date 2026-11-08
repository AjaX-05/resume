"""Pipeline coordinator — bulk tailor + bulk apply orchestration.

The pipeline has three phases that can be stitched together via toggles in
``apps/backend/data/pipeline.json``:

    Scrape (handled by job-scraper subprocess)
        │
        ▼
    Tailor (queue-based, this module queues jobs and tracks their completion)
        │
        ▼
    Apply (sequential Playwright via applier_service, this module drives it)

The pipeline tracks a *task* which groups N applications moving through the
tailor or apply phases together. Status is kept in-memory; a backend restart
clears it (the queue_items table still carries the actual tailoring work, so
restarting just loses the client-side progress view).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.database import db

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parents[2] / "data" / "pipeline.json"

# ────────────────────────────────────────────────────────────────────────────
# Supported ATS detection — the frontend uses this via GET /pipeline/ats-of
# but it's authoritative here so we never queue a tailor for an unsupported URL
# ────────────────────────────────────────────────────────────────────────────

SUPPORTED_ATS: dict[str, list[str]] = {
    "greenhouse":      ["greenhouse.io", "grnh.se"],
    "ashby":           ["ashbyhq.com"],
    "lever":           ["lever.co"],
    "workable":        ["workable.com"],
    "gem":             ["jobs.gem.com"],
    "dover":           ["dover.com"],
    "rippling":        ["ats.rippling.com", "recruiting.rippling.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "bamboohr":        ["bamboohr.com"],
}

UNSUPPORTED_ATS: dict[str, list[str]] = {
    "workday":   ["myworkdayjobs.com", "workday.com"],
    "icims":     ["icims.com"],
    "jobvite":   ["jobvite.com"],
    "linkedin":  ["linkedin.com"],
    "indeed":    ["indeed.com"],
}

# Aggregator URLs — the real ATS is behind an Apply button and gets resolved
# at apply time by applier_service._resolve_aggregator_url. We treat these as
# SUPPORTED optimistically (worst case, resolver falls back and we see a
# screenshot of the aggregator page for manual action).
AGGREGATOR_ATS: dict[str, list[str]] = {
    "jobright":  ["jobright.ai"],
    "wellfound": ["wellfound.com/jobs"],
}


def detect_ats(apply_url: str) -> tuple[str, bool]:
    """Classify an apply URL. Returns (ats_name, is_supported).

    Supported includes aggregators (Jobright/Wellfound) because the applier
    resolves them to the real ATS at submit time using the saved session.
    """
    if not apply_url:
        return ("none", False)
    url_lower = apply_url.lower()
    for ats, patterns in SUPPORTED_ATS.items():
        if any(p in url_lower for p in patterns):
            return (ats, True)
    for ats, patterns in AGGREGATOR_ATS.items():
        if any(p in url_lower for p in patterns):
            # Aggregator → supported (we resolve to real ATS at apply time)
            return (ats, True)
    for ats, patterns in UNSUPPORTED_ATS.items():
        if any(p in url_lower for p in patterns):
            return (ats, False)
    return ("unknown", False)


def extract_apply_url(notes: str, job_content: str = "") -> str:
    """Extract the apply URL stored by the scraper from notes/content."""
    import re
    if not notes:
        notes = ""
    if not job_content:
        job_content = ""
    # Notes format: "Apply: https://..."
    m = re.search(r"^\s*Apply:\s*(https?://\S+)", notes, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Fallback: look in job_content for "**Apply:** URL" or any apply URL
    m = re.search(r"\*\*Apply:\*\*\s*(https?://\S+)", job_content)
    if m:
        return m.group(1).strip()
    return ""


# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    if _CONFIG_PATH.exists():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "auto_tailor": False,
        "auto_apply": False,
        "approval_gate": True,
        "master_resume_id": "",
        "tailor_prompt_id": "full",
    }


def save_config(config: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────────────────────────────────
# In-memory task tracking
# ────────────────────────────────────────────────────────────────────────────

ItemStatus = Literal[
    "pending", "queued", "tailoring", "tailored",
    "applying", "needs_review", "applied", "failed", "skipped",
]


@dataclass
class PipelineItem:
    application_id: str
    company: str
    role: str
    ats: str
    status: ItemStatus = "pending"
    queue_item_id: str | None = None
    tailored_resume_id: str | None = None
    error: str | None = None


@dataclass
class PipelineTask:
    task_id: str
    kind: Literal["tailor", "apply"]
    items: list[PipelineItem] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it.status] = counts.get(it.status, 0) + 1
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "total": len(self.items),
            "counts": counts,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "items": [asdict(it) for it in self.items],
        }


# Tasks live in-memory; backend restart clears them.
_tasks: dict[str, PipelineTask] = {}
# Reverse lookup: queue_item_id → (task_id, application_id)
_queue_item_to_pipeline: dict[str, tuple[str, str]] = {}


def get_task(task_id: str) -> PipelineTask | None:
    return _tasks.get(task_id)


def list_tasks() -> list[dict[str, Any]]:
    return [t.summary() for t in _tasks.values()]


# ────────────────────────────────────────────────────────────────────────────
# BULK TAILOR
# ────────────────────────────────────────────────────────────────────────────

async def bulk_tailor(
    application_ids: list[str],
    master_resume_id: str,
    prompt_id: str | None = None,
    rate_limit_seconds: float = 5.0,
) -> PipelineTask:
    """Queue a tailor job for each supported application.

    Returns a PipelineTask tracking the batch. Items with unsupported ATS or
    no apply URL are marked `skipped` upfront to save LLM credits.
    """
    task = PipelineTask(task_id=f"tailor-{uuid4().hex[:8]}", kind="tailor")
    config = load_config()
    prompt = prompt_id or config.get("tailor_prompt_id", "full")

    for app_id in application_ids:
        app = await db.get_application(app_id)
        if not app:
            continue
        apply_url = extract_apply_url(app.get("notes", ""), app.get("job_content", ""))
        ats, supported = detect_ats(apply_url)

        item = PipelineItem(
            application_id=app_id,
            company=app.get("company", "") or "",
            role=app.get("role", "") or "",
            ats=ats,
        )

        if not supported:
            item.status = "skipped"
            item.error = f"ATS not supported ({ats})" if ats != "none" else "No apply URL"
            task.items.append(item)
            continue

        # Fetch the job description text to queue
        job = await db.get_job(app["job_id"])
        if not job:
            item.status = "failed"
            item.error = "Job not found"
            task.items.append(item)
            continue

        try:
            queue_item_id = str(uuid4())
            await db.create_queue_item(
                item_id=queue_item_id,
                resume_id=master_resume_id,
                job_description=job["content"],
                role=item.role,
                company=item.company,
                job_req_id="",
                rate_limit_seconds=rate_limit_seconds,
                prompt_id=prompt,
            )
            item.queue_item_id = queue_item_id
            item.status = "queued"
            _queue_item_to_pipeline[queue_item_id] = (task.task_id, app_id)
        except Exception as e:
            item.status = "failed"
            item.error = f"Queue failed: {str(e)[:100]}"

        task.items.append(item)

    _tasks[task.task_id] = task

    # Kick the queue worker
    try:
        from app.services.queue_worker import queue_manager
        queue_manager.notify()
    except Exception:
        pass

    return task


async def on_queue_item_completed(
    item_id: str,
    tailored_resume_id: str | None,
    error: str | None,
) -> None:
    """Called by queue_worker when a queue item finishes.

    Updates the matching PipelineItem + the Application card:
      - On success: card's resume_id → tailored_resume_id, status → 'applied' (Tailored col)
      - On failure: PipelineItem stays "failed", card unchanged
    """
    entry = _queue_item_to_pipeline.get(item_id)
    if not entry:
        return  # not a pipeline-managed queue item
    task_id, app_id = entry
    task = _tasks.get(task_id)
    if not task:
        return
    item = next((i for i in task.items if i.queue_item_id == item_id), None)
    if not item:
        return

    if error or not tailored_resume_id:
        item.status = "failed"
        item.error = error or "Unknown tailor failure"
        _check_task_complete(task)
        return

    # Link the application card to the newly tailored resume
    item.tailored_resume_id = tailored_resume_id
    item.status = "tailored"
    try:
        await db.update_application(
            app_id,
            {"status": "applied", "resume_id": tailored_resume_id},
        )
    except Exception as e:
        logger.warning(f"Failed to link app {app_id} to resume {tailored_resume_id}: {e}")

    _check_task_complete(task)

    # If auto_apply is on, kick off apply for this card
    config = load_config()
    if config.get("auto_apply", False):
        try:
            from app.services.applier_service import start_application
            from app.pdf import render_resume_pdf
            from app.schemas.models import PageFitSettings
            from app.services.page_fit import resume_print_url

            fit = PageFitSettings()
            url = resume_print_url(tailored_resume_id, fit)
            pdf_bytes = await render_resume_pdf(url)

            # Need the apply URL for this card
            app = await db.get_application(app_id)
            if app:
                apply_url = extract_apply_url(app.get("notes", ""), app.get("job_content", ""))
                if apply_url:
                    start_application(app_id, apply_url, pdf_bytes)
                    item.status = "applying"
        except Exception as e:
            logger.warning(f"Auto-apply trigger failed for {app_id}: {e}")


def _check_task_complete(task: PipelineTask) -> None:
    """Mark task complete if all items are in terminal states."""
    terminal = {"tailored", "applied", "failed", "skipped"}
    if all(i.status in terminal for i in task.items):
        task.completed_at = datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────────────────────────────────────
# BULK APPLY
# ────────────────────────────────────────────────────────────────────────────

async def bulk_apply(application_ids: list[str]) -> PipelineTask:
    """Start Playwright apply sessions for each supported application.

    Items are started sequentially (not in parallel) to avoid Chromium
    resource exhaustion and to make human review (if gated) manageable.
    """
    from app.pdf import render_resume_pdf
    from app.schemas.models import PageFitSettings
    from app.services.applier_service import start_application
    from app.services.page_fit import resume_print_url

    task = PipelineTask(task_id=f"apply-{uuid4().hex[:8]}", kind="apply")

    for app_id in application_ids:
        app = await db.get_application(app_id)
        if not app:
            continue

        apply_url = extract_apply_url(app.get("notes", ""), app.get("job_content", ""))
        ats, supported = detect_ats(apply_url)

        item = PipelineItem(
            application_id=app_id,
            company=app.get("company", "") or "",
            role=app.get("role", "") or "",
            ats=ats,
        )

        if not supported:
            item.status = "skipped"
            item.error = f"ATS not supported ({ats})"
            task.items.append(item)
            continue

        resume_id = app.get("resume_id", "")
        if not resume_id:
            item.status = "failed"
            item.error = "No resume on card — tailor first"
            task.items.append(item)
            continue

        try:
            fit = PageFitSettings()
            url = resume_print_url(resume_id, fit)
            pdf_bytes = await render_resume_pdf(url)
            session = start_application(app_id, apply_url, pdf_bytes)
            item.status = "applying"
            task.items.append(item)
        except Exception as e:
            item.status = "failed"
            item.error = f"Apply start failed: {str(e)[:100]}"
            task.items.append(item)
            continue

        # Wait briefly between launches so Playwright instances don't fight
        await asyncio.sleep(2)

    _tasks[task.task_id] = task
    return task


async def refresh_apply_task_statuses(task_id: str) -> PipelineTask | None:
    """Pull current status from applier_service for each item in an apply task."""
    from app.services.applier_service import get_session as get_apply_session

    task = _tasks.get(task_id)
    if not task or task.kind != "apply":
        return task

    _applier_status_to_item: dict[str, ItemStatus] = {
        "pending": "applying",
        "filling": "applying",
        "needs_review": "needs_review",
        "submitting": "applying",
        "submitted": "applied",
        "failed": "failed",
        "cancelled": "skipped",
    }

    for item in task.items:
        if item.status in {"applied", "failed", "skipped"}:
            continue
        session = get_apply_session(item.application_id)
        if session:
            mapped = _applier_status_to_item.get(session.status, item.status)
            item.status = mapped  # type: ignore[assignment]
            if session.error:
                item.error = session.error

    _check_task_complete(task)
    return task


# ────────────────────────────────────────────────────────────────────────────
# ATS Detection for frontend badges
# ────────────────────────────────────────────────────────────────────────────

async def detect_ats_for_applications(application_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Return {app_id: {ats, supported, apply_url}} for a batch of applications."""
    out: dict[str, dict[str, Any]] = {}
    for app_id in application_ids:
        app = await db.get_application(app_id)
        if not app:
            continue
        apply_url = extract_apply_url(app.get("notes", ""), app.get("job_content", ""))
        ats, supported = detect_ats(apply_url)
        out[app_id] = {
            "ats": ats,
            "supported": supported,
            "apply_url": apply_url,
        }
    return out
