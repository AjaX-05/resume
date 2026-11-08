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
from app.services.apply_urls import (
    canonical_apply_url,
    effective_apply_url,
    extract_listed_apply_url,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parents[2] / "data" / "pipeline.json"

# ────────────────────────────────────────────────────────────────────────────
# Supported ATS detection — the frontend uses this via GET /pipeline/ats-of
# but it's authoritative here so we never queue a tailor for an unsupported URL
# ────────────────────────────────────────────────────────────────────────────

SUPPORTED_ATS: dict[str, list[str]] = {
    "greenhouse": ["greenhouse.io", "grnh.se", "careerpuck.com"],
    "ashby": ["ashbyhq.com"],
    "lever": ["lever.co"],
    "workable": ["workable.com"],
    "gem": ["jobs.gem.com"],
    "dover": ["dover.com"],
    "rippling": ["ats.rippling.com", "recruiting.rippling.com"],
    "smartrecruiters": ["smartrecruiters.com"],
    "bamboohr": ["bamboohr.com"],
    "workday": ["myworkdayjobs.com"],
    "email": ["mailto:", "privateai.com/jobs"],
}

UNSUPPORTED_ATS: dict[str, list[str]] = {
    "icims": ["icims.com"],
    "jobvite": ["jobvite.com"],
    "linkedin": ["linkedin.com"],
    "indeed": ["indeed.com"],
}

# Aggregator URLs — the real ATS is behind an Apply button and gets resolved
# at apply time by applier_service._resolve_aggregator_url. We treat these as
# SUPPORTED optimistically (worst case, resolver falls back and we see a
# screenshot of the aggregator page for manual action).
AGGREGATOR_ATS: dict[str, list[str]] = {
    "jobright": ["jobright.ai"],
    "wellfound": ["wellfound.com/jobs"],
}


def detect_ats(apply_url: str) -> tuple[str, bool]:
    """Classify an apply URL. Returns (ats_name, is_supported).

    Supported includes aggregators (Jobright/Wellfound) because the applier
    resolves them to the real ATS at submit time using the saved session.
    """
    if not apply_url:
        return ("none", False)
    apply_url = canonical_apply_url(apply_url)
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


def card_fingerprint(company: str | None, role: str | None) -> str:
    """Stable identity for a tracker card: company + role, ignoring title clutter.

    "Senior AI Engineer @ Omnicom" with company Omnicom is the same job as
    "Senior AI Engineer" at Omnicom. Empty / unknown cards return "" so they
    are never collapsed together.
    """
    company_key = (company or "").strip().lower()
    role_key = " ".join((role or "").split()).lower()
    if company_key:
        for sep in (" @ ", " at "):
            suffix = f"{sep}{company_key}"
            if role_key.endswith(suffix):
                role_key = role_key[: -len(suffix)].strip()
                break
    elif role_key:
        for sep in (" @ ", " at "):
            if sep in role_key:
                left, right = role_key.rsplit(sep, 1)
                role_key, company_key = left.strip(), right.strip()
                break
    if not company_key and not role_key:
        return ""
    return f"{company_key}::{role_key}"


def extract_apply_url(notes: str, job_content: str = "") -> str:
    """Extract the listed Apply: URL (Jobright/ATS) from notes/content."""
    return extract_listed_apply_url(notes, job_content)


def _is_clone_card(app: dict[str, Any]) -> bool:
    """Auto-created Tailored clones have no apply URL / posted-time notes."""
    return not extract_apply_url(app.get("notes") or "")


async def attach_tailored_resume(
    *,
    tailored_resume_id: str,
    master_resume_id: str | None,
    job_id: str | None,
    application_id: str | None = None,
    company: str | None = None,
    role: str | None = None,
) -> str | None:
    """Move the original JD card into Tailored. Never leave a clone behind.

    Prefers the saved card that still has the apply URL and posted time so
    Tailored shows the same posting metadata. Empty auto-created clones
    (NO URL / Role @ Company) are deleted.
    """
    apps = await db.list_applications()
    fingerprint = card_fingerprint(company, role)

    def same_job(app: dict[str, Any]) -> bool:
        return bool(job_id) and app.get("job_id") == job_id

    def same_fp(app: dict[str, Any]) -> bool:
        return (
            bool(fingerprint)
            and card_fingerprint(app.get("company"), app.get("role")) == fingerprint
        )

    target: dict[str, Any] | None = None
    if application_id:
        target = next(
            (app for app in apps if app["application_id"] == application_id), None
        )
    if target is None:
        saved = [
            app
            for app in apps
            if app.get("status") == "saved" and (same_job(app) or same_fp(app))
        ]
        saved.sort(
            key=lambda app: (
                0 if extract_apply_url(app.get("notes") or "") else 1,
                app.get("created_at") or "",
            )
        )
        target = saved[0] if saved else None
    if target is None:
        existing = [app for app in apps if same_job(app)]
        target = existing[0] if existing else None

    if target is None:
        if not job_id:
            return None
        created = await db.create_application(
            job_id=job_id,
            resume_id=tailored_resume_id,
            master_resume_id=master_resume_id,
            status="applied",
            company=company,
            role=role,
        )
        return created.get("application_id")

    target_id = target["application_id"]
    target_url = extract_apply_url(target.get("notes") or "")
    company_key = (company or target.get("company") or "").strip().lower()
    for extra in apps:
        extra_id = extra["application_id"]
        if extra_id == target_id:
            continue
        extra_url = extract_apply_url(extra.get("notes") or "")
        extra_company = (extra.get("company") or "").strip().lower()
        drop_clone = (
            extra.get("status") == "applied"
            and _is_clone_card(extra)
            and (
                same_job(extra)
                or same_fp(extra)
                or (
                    bool(application_id)
                    and bool(company_key)
                    and extra_company == company_key
                )
            )
        )
        drop_dup_jd = (
            extra.get("status") == "saved"
            and (same_fp(extra) or same_job(extra))
            and (not extra_url or extra_url == target_url)
        )
        if not (drop_clone or drop_dup_jd):
            continue
        try:
            await db.delete_application(extra_id)
        except Exception:
            logger.warning("Could not delete tailored clone %s", extra_id)

    try:
        await db.update_application(
            target_id,
            {"status": "applied", "resume_id": tailored_resume_id},
        )
    except Exception as exc:
        logger.warning("Failed to move JD card %s into Tailored: %s", target_id, exc)
    return target_id


def _role_stem(company: str | None, role: str | None) -> str:
    """Company + role with title clutter stripped, for matching JD vs clone cards.

    "AI Engineer 5 (Gen AI SDK: …)" and "AI Engineer 5 (Gen AI SDK) @ Capital One"
    collapse to the same stem so a Tailored clone can reclaim its JD card.
    """
    fingerprint = card_fingerprint(company, role)
    if not fingerprint:
        return ""
    company_key, part = fingerprint.split("::", 1)
    cut = len(part)
    for sep in (" (", ",", ":", " — ", " - "):
        idx = part.find(sep)
        if 0 < idx < cut:
            cut = idx
    return f"{company_key}::{part[:cut].strip()}"


async def collapse_tailored_clones() -> int:
    """If a JD is still in Saved but a clone sits in Tailored, merge them.

    Returns how many JD cards were moved. Safe to call on tracker load.
    """
    apps = await db.list_applications()
    groups: dict[str, list[dict[str, Any]]] = {}
    for app in apps:
        stem = _role_stem(app.get("company"), app.get("role"))
        if stem:
            groups.setdefault(stem, []).append(app)

    moved = 0
    seen_ids: set[str] = set()
    for cards in groups.values():
        saved = [app for app in cards if app.get("status") == "saved"]
        applied = [app for app in cards if app.get("status") == "applied"]
        if not saved or not applied:
            continue
        donor = max(applied, key=lambda app: app.get("updated_at") or "")
        target = next(
            (app for app in saved if extract_apply_url(app.get("notes") or "")),
            saved[0],
        )
        if target["application_id"] in seen_ids:
            continue
        result = await attach_tailored_resume(
            tailored_resume_id=donor["resume_id"],
            master_resume_id=donor.get("master_resume_id")
            or target.get("master_resume_id"),
            job_id=target.get("job_id") or donor.get("job_id"),
            application_id=target["application_id"],
            company=target.get("company") or donor.get("company"),
            role=target.get("role") or donor.get("role"),
        )
        if result:
            seen_ids.add(target["application_id"])
            moved += 1

    # Fallback: one leftover JD and one NO-URL clone at the same company
    # (LLM titles like "The job title is …" won't share a role stem).
    remaining = await db.list_applications()
    by_company: dict[str, list[dict[str, Any]]] = {}
    for app in remaining:
        company_key = (app.get("company") or "").strip().lower()
        if company_key:
            by_company.setdefault(company_key, []).append(app)
    for cards in by_company.values():
        saved = [app for app in cards if app.get("status") == "saved"]
        clones = [
            app
            for app in cards
            if app.get("status") == "applied" and _is_clone_card(app)
        ]
        if len(saved) != 1 or len(clones) != 1:
            continue
        target, donor = saved[0], clones[0]
        if target["application_id"] in seen_ids:
            continue
        result = await attach_tailored_resume(
            tailored_resume_id=donor["resume_id"],
            master_resume_id=donor.get("master_resume_id")
            or target.get("master_resume_id"),
            job_id=target.get("job_id") or donor.get("job_id"),
            application_id=target["application_id"],
            company=target.get("company") or donor.get("company"),
            role=target.get("role") or donor.get("role"),
        )
        if result:
            seen_ids.add(target["application_id"])
            moved += 1

    remaining = await db.list_applications()
    by_company = {}
    for app in remaining:
        company_key = (app.get("company") or "").strip().lower()
        if company_key:
            by_company.setdefault(company_key, []).append(app)
    for cards in by_company.values():
        noted = [
            app
            for app in cards
            if app.get("status") == "applied" and not _is_clone_card(app)
        ]
        clones = [
            app
            for app in cards
            if app.get("status") == "applied" and _is_clone_card(app)
        ]
        if not noted or not clones:
            continue
        for clone in clones:
            try:
                await db.delete_application(clone["application_id"])
            except Exception:
                logger.warning(
                    "Could not delete leftover clone %s", clone["application_id"]
                )
    return moved


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
        "tailor_max_pages": 3,
        "tailor_rate_limit_seconds": 10.0,
    }


def save_config(config: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = load_config()
    merged.update(config)
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────────────────────────────────
# In-memory task tracking
# ────────────────────────────────────────────────────────────────────────────

ItemStatus = Literal[
    "pending",
    "queued",
    "tailoring",
    "tailored",
    "applying",
    "needs_review",
    "applied",
    "failed",
    "skipped",
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
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None
    follow_up_task_id: str | None = None

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
            "follow_up_task_id": self.follow_up_task_id,
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
    rate_limit_seconds: float = 10.0,
) -> PipelineTask:
    """Queue a tailor job for each supported application.

    The queue worker runs these **one at a time** (oldest first) with a
    cooldown between items so a local LLM is never asked to tailor 10 JDs
    in parallel. Items with unsupported ATS, no apply URL, or a duplicate
    company+role (already tailored or already in this batch) are marked
    `skipped` upfront.
    """
    task = PipelineTask(task_id=f"tailor-{uuid4().hex[:8]}", kind="tailor")
    config = load_config()
    prompt = prompt_id or config.get("tailor_prompt_id", "full")

    existing = await db.list_applications()
    already_tailored = {
        card_fingerprint(a.get("company"), a.get("role"))
        for a in existing
        if a.get("status") == "applied"
        and card_fingerprint(a.get("company"), a.get("role"))
    }
    seen_in_batch: set[str] = set()

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
        fingerprint = card_fingerprint(item.company, item.role)

        if app.get("status") == "applied":
            item.status = "skipped"
            item.error = "Already tailored"
            task.items.append(item)
            continue

        if fingerprint and fingerprint in already_tailored:
            item.status = "skipped"
            item.error = "Duplicate of an already-tailored card"
            task.items.append(item)
            continue

        if fingerprint and fingerprint in seen_in_batch:
            item.status = "skipped"
            item.error = "Duplicate in this batch"
            task.items.append(item)
            continue

        if not supported:
            item.status = "skipped"
            item.error = (
                f"ATS not supported ({ats})" if ats != "none" else "No apply URL"
            )
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
                job_id=app.get("job_id"),
                application_id=app_id,
            )
            item.queue_item_id = queue_item_id
            item.status = "queued"
            _queue_item_to_pipeline[queue_item_id] = (task.task_id, app_id)
            if fingerprint:
                seen_in_batch.add(fingerprint)
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


def on_queue_item_started(item_id: str) -> None:
    """Called by the queue worker as it claims the next pipeline item.

    Flips that row from `queued` → `tailoring` so the tracker progress
    modal can show which JD the local model is working on right now.
    """
    entry = _queue_item_to_pipeline.get(item_id)
    if not entry:
        return
    task_id, _app_id = entry
    task = _tasks.get(task_id)
    if not task:
        return
    item = next((i for i in task.items if i.queue_item_id == item_id), None)
    if item and item.status == "queued":
        item.status = "tailoring"


async def on_queue_item_completed(
    item_id: str,
    tailored_resume_id: str | None,
    error: str | None,
) -> None:
    """Called by queue_worker when a queue item finishes.

    Updates the matching PipelineItem + the Application card:
      - On success: original JD card moves to Tailored (notes/posted time kept)
      - On failure: PipelineItem is "failed", JD card stays in Saved so the
        rest of the batch can keep running
    """
    entry = _queue_item_to_pipeline.get(item_id)
    queued = await db.get_queue_item(item_id)
    task: PipelineTask | None = None
    item: PipelineItem | None = None
    app_id = (queued or {}).get("application_id")
    if entry:
        task_id, mapped_app_id = entry
        app_id = app_id or mapped_app_id
        task = _tasks.get(task_id)
        if task:
            item = next(
                (row for row in task.items if row.queue_item_id == item_id), None
            )

    if error or not tailored_resume_id:
        if item:
            item.status = "failed"
            item.error = error or "Unknown tailor failure"
        if task:
            await _finish_tailor_if_done(task)
        return

    if item:
        item.tailored_resume_id = tailored_resume_id
        item.status = "tailored"

    try:
        await attach_tailored_resume(
            tailored_resume_id=tailored_resume_id,
            master_resume_id=(queued or {}).get("resume_id"),
            job_id=(queued or {}).get("job_id"),
            application_id=app_id,
            company=(queued or {}).get("company") or (item.company if item else None),
            role=(queued or {}).get("role") or (item.role if item else None),
        )
    except Exception as exc:
        logger.warning(
            "Failed to link app %s to resume %s: %s",
            app_id,
            tailored_resume_id,
            exc,
        )
        if item:
            item.status = "failed"
            item.error = "Could not move JD card"
            # Resume exists; do not fail the rest of the batch.

    if task:
        await _finish_tailor_if_done(task)


async def _finish_tailor_if_done(task: PipelineTask) -> None:
    """Close a tailor batch once every item is terminal, then chain auto-apply."""
    if task.kind != "tailor" or task.completed_at:
        if task.kind != "tailor":
            _check_task_complete(task)
        return
    tailor_done = {"tailored", "failed", "skipped"}
    if not all(row.status in tailor_done for row in task.items):
        return
    config = load_config()
    if config.get("auto_apply", False):
        apply_ids = [
            row.application_id for row in task.items if row.status == "tailored"
        ]
        if apply_ids:
            try:
                apply_task = await bulk_apply(apply_ids)
                task.follow_up_task_id = apply_task.task_id
            except Exception as exc:
                logger.warning("Auto-apply after tailor failed to start: %s", exc)
    task.completed_at = datetime.now(timezone.utc).isoformat()


def _check_task_complete(task: PipelineTask) -> None:
    """Mark task complete if all items are in terminal states."""
    terminal = {"tailored", "applied", "failed", "skipped"}
    if all(i.status in terminal for i in task.items):
        task.completed_at = datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────────────────────────────────────
# BULK APPLY
# ────────────────────────────────────────────────────────────────────────────


async def eligible_tailored_ids() -> list[str]:
    """Tailored cards (status=applied) that have a resume and a supported ATS."""
    ids: list[str] = []
    for app in await db.list_applications():
        if app.get("status") != "applied":
            continue
        if not app.get("resume_id"):
            continue
        apply_url = extract_apply_url(
            app.get("notes") or "", app.get("job_content") or ""
        )
        _ats, supported = detect_ats(apply_url)
        if supported:
            ids.append(app["application_id"])
    return ids


async def bulk_apply(application_ids: list[str]) -> PipelineTask:
    """Queue Playwright apply sessions one at a time.

    The function returns immediately with a task_id to poll. A background
    worker starts the next Chromium session only after the previous one
    settles (submitted / failed / cancelled), so the local machine is never
    asked to run ten browsers plus the LLM at once. If approval_gate is on,
    "settled" includes waiting for the user to approve or reject the form.
    """
    task = PipelineTask(task_id=f"apply-{uuid4().hex[:8]}", kind="apply")

    for app_id in application_ids:
        app = await db.get_application(app_id)
        if not app:
            continue

        listed_url = extract_apply_url(app.get("notes", ""), app.get("job_content", ""))
        apply_url = effective_apply_url(
            app.get("notes", "") or "", app.get("job_content", "") or ""
        )
        ats, supported = detect_ats(apply_url or listed_url)

        item = PipelineItem(
            application_id=app_id,
            company=app.get("company", "") or "",
            role=app.get("role", "") or "",
            ats=ats,
            tailored_resume_id=app.get("resume_id") or None,
        )

        if not apply_url and not listed_url:
            item.status = "skipped"
            item.error = "No apply URL on card"
        elif not app.get("resume_id"):
            item.status = "failed"
            item.error = "No resume on card — tailor first"
        else:
            # Queue even Workday/iCIMS: the applier opens the employer page for
            # review instead of skipping. Autofill adapters still only run for
            # Greenhouse/Lever/Ashby/etc.
            item.status = "queued"
            if not supported:
                item.ats = ats

        task.items.append(item)

    _tasks[task.task_id] = task
    if any(item.status == "queued" for item in task.items):
        asyncio.create_task(_run_apply_batch(task), name=f"apply-batch-{task.task_id}")
    else:
        task.completed_at = datetime.now(timezone.utc).isoformat()
    return task


async def _run_apply_batch(task: PipelineTask) -> None:
    """Drive one apply session at a time for ``task``."""
    from app.pdf import render_resume_pdf
    from app.schemas.models import PageFitSettings
    from app.services.applier_service import (
        get_session as get_apply_session,
    )
    from app.services.applier_service import (
        prepare_vault,
        start_application,
    )
    from app.services.page_fit import resume_print_url

    for item in task.items:
        if item.status != "queued":
            continue
        app = await db.get_application(item.application_id)
        if not app:
            item.status = "failed"
            item.error = "Card disappeared"
            continue
        notes = app.get("notes", "") or ""
        apply_url = effective_apply_url(notes, app.get("job_content", "") or "")
        resume_id = app.get("resume_id") or ""
        try:
            vault = await prepare_vault(resume_id)
            fit = PageFitSettings()
            pdf_bytes = await render_resume_pdf(resume_print_url(resume_id, fit))
            start_application(
                item.application_id,
                apply_url,
                pdf_bytes,
                vault=vault,
                company=app.get("company") or "",
                role=app.get("role") or "",
                notes=notes,
            )
            item.status = "applying"
            await _wait_for_apply_session(item.application_id)
            session = get_apply_session(item.application_id)
            if session:
                mapped = {
                    "submitted": "applied",
                    "failed": "failed",
                    "cancelled": "skipped",
                    "needs_review": "needs_review",
                    "filling": "applying",
                    "pending": "applying",
                    "submitting": "applying",
                }.get(session.status, item.status)
                item.status = mapped  # type: ignore[assignment]
                if session.error:
                    item.error = session.error
        except Exception as exc:
            item.status = "failed"
            item.error = f"Apply start failed: {str(exc)[:100]}"
            logger.warning("Apply item %s failed: %s", item.application_id, exc)

    _check_task_complete(task)


async def _wait_for_apply_session(
    application_id: str, timeout_seconds: float = 900
) -> None:
    """Block until this apply session is terminal (or the review timeout fires)."""
    from app.services.applier_service import get_session as get_apply_session

    terminal = {"submitted", "failed", "cancelled"}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        session = get_apply_session(application_id)
        if not session or session.status in terminal:
            return
        await asyncio.sleep(1)


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


async def detect_ats_for_applications(
    application_ids: list[str],
) -> dict[str, dict[str, Any]]:
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
