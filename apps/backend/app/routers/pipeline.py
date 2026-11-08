"""Pipeline API endpoints — bulk tailor, bulk apply, status, config."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.pipeline_service import (
    bulk_apply,
    bulk_tailor,
    detect_ats_for_applications,
    eligible_tailored_ids,
    get_task,
    list_tasks,
    load_config,
    refresh_apply_task_statuses,
    save_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipeline", tags=["Pipeline"])


class BulkTailorRequest(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=50)
    master_resume_id: str
    prompt_id: str | None = None
    rate_limit_seconds: float = Field(default=10.0, ge=0, le=60)


class BulkApplyRequest(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=50)


class PipelineConfig(BaseModel):
    auto_tailor: bool = False
    auto_apply: bool = False
    approval_gate: bool = True
    master_resume_id: str = ""
    tailor_prompt_id: str = "full"
    apply_task_id: str | None = None


class AtsDetectRequest(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=200)


# ────────────────────────────────────────────────────────────────────────────
# ATS detection — frontend uses this to show badges on JD cards
# ────────────────────────────────────────────────────────────────────────────

@router.post("/ats-detect")
async def ats_detect(req: AtsDetectRequest) -> dict[str, dict[str, Any]]:
    """Classify the ATS behind each application's apply URL.

    Returns {application_id: {ats, supported, apply_url}}. The frontend uses
    this to show a colored badge on each JD card — green if we can auto-apply,
    red if the ATS requires an account (Workday/iCIMS) or is just an
    aggregator link (Jobright/Wellfound). Unsupported cards shouldn't be
    tailored because that burns LLM credits on resumes we can't submit.
    """
    try:
        return await detect_ats_for_applications(req.application_ids)
    except Exception as e:
        logger.exception("ATS detect failed")
        raise HTTPException(status_code=500, detail=str(e))


# ────────────────────────────────────────────────────────────────────────────
# BULK TAILOR
# ────────────────────────────────────────────────────────────────────────────

@router.post("/bulk-tailor")
async def bulk_tailor_endpoint(req: BulkTailorRequest) -> dict[str, Any]:
    """Queue N applications for tailoring.

    Items with unsupported ATS or missing apply URL are skipped upfront to
    save LLM credits. Returns the task_id to poll for progress.
    """
    try:
        task = await bulk_tailor(
            req.application_ids,
            req.master_resume_id,
            prompt_id=req.prompt_id,
            rate_limit_seconds=req.rate_limit_seconds,
        )
        return task.summary()
    except Exception as e:
        logger.exception("Bulk tailor failed")
        raise HTTPException(status_code=500, detail=str(e))


# ────────────────────────────────────────────────────────────────────────────
# BULK APPLY
# ────────────────────────────────────────────────────────────────────────────

@router.post("/bulk-apply")
async def bulk_apply_endpoint(req: BulkApplyRequest) -> dict[str, Any]:
    """Start Playwright apply sessions for N tailored applications.

    Items launch sequentially (2s stagger) to avoid Chromium contention.
    If approval_gate is on, each session waits for user review before submit.
    """
    try:
        task = await bulk_apply(req.application_ids)
        return task.summary()
    except Exception as e:
        logger.exception("Bulk apply failed")
        raise HTTPException(status_code=500, detail=str(e))


# ────────────────────────────────────────────────────────────────────────────
# STATUS
# ────────────────────────────────────────────────────────────────────────────

@router.get("/status/{task_id}")
async def task_status(task_id: str) -> dict[str, Any]:
    """Get live status of a tailor or apply task."""
    # Refresh apply task statuses from applier_service before returning
    task = await refresh_apply_task_statuses(task_id)
    if not task:
        task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.summary()


@router.get("/tasks")
async def tasks_list() -> list[dict[str, Any]]:
    """List all active pipeline tasks (in-memory, cleared on restart)."""
    return list_tasks()


# ────────────────────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────────────────────

@router.get("/config", response_model=PipelineConfig)
async def get_pipeline_config() -> PipelineConfig:
    """Get the pipeline automation config (auto_tailor, auto_apply, gate)."""
    cfg = load_config()
    return PipelineConfig(
        auto_tailor=bool(cfg.get("auto_tailor", False)),
        auto_apply=bool(cfg.get("auto_apply", False)),
        approval_gate=bool(cfg.get("approval_gate", True)),
        master_resume_id=str(cfg.get("master_resume_id") or ""),
        tailor_prompt_id=str(cfg.get("tailor_prompt_id") or "full"),
    )


@router.put("/config", response_model=PipelineConfig)
async def update_pipeline_config(cfg: PipelineConfig) -> PipelineConfig:
    """Update pipeline automation toggles.

    Turning Auto-Apply ON queues every currently-Tailored card (resume +
    supported ATS) one at a time. The returned ``apply_task_id`` is what the
    tracker polls for the review/progress modal.
    """
    current = load_config()
    was_auto_apply = bool(current.get("auto_apply", False))
    save_config(
        {
            "auto_tailor": cfg.auto_tailor,
            "auto_apply": cfg.auto_apply,
            "approval_gate": cfg.approval_gate,
            "master_resume_id": cfg.master_resume_id,
            "tailor_prompt_id": cfg.tailor_prompt_id,
        }
    )
    apply_task_id: str | None = None
    if cfg.auto_apply and not was_auto_apply:
        ids = await eligible_tailored_ids()
        if ids:
            task = await bulk_apply(ids)
            apply_task_id = task.task_id
    return PipelineConfig(
        auto_tailor=cfg.auto_tailor,
        auto_apply=cfg.auto_apply,
        approval_gate=cfg.approval_gate,
        master_resume_id=cfg.master_resume_id,
        tailor_prompt_id=cfg.tailor_prompt_id,
        apply_task_id=apply_task_id,
    )
