"""Auto-applier API endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import db
from app.services.applier_service import (
    ApplyStatus,
    approve_application,
    get_session,
    list_sessions,
    load_vault,
    prepare_vault,
    reject_application,
    save_vault,
    start_application,
)
from app.services.apply_answers import merge_vault, redact_vault

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/applier", tags=["Auto-Applier"])


class StartApplyRequest(BaseModel):
    application_id: str
    resume_id: str


class ApplyStatusResponse(BaseModel):
    application_id: str
    apply_url: str
    status: ApplyStatus
    has_screenshot: bool
    filled_fields: dict[str, str]
    error: str | None
    started_at: str
    completed_at: str | None


@router.post("/start", response_model=ApplyStatusResponse)
async def start_apply(req: StartApplyRequest) -> ApplyStatusResponse:
    """Start auto-applying to the job linked to an application card.

    Fetches the apply URL from the application notes, generates the tailored
    resume PDF, and launches a Playwright session to fill the form.
    """
    # Get application to find the apply URL
    app = await db.get_application(req.application_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    from app.services.apply_urls import effective_apply_url

    notes: str = app.get("notes") or ""
    apply_url = effective_apply_url(notes, app.get("job_content") or "")

    if not apply_url:
        raise HTTPException(
            status_code=400,
            detail="No apply URL found in this application card. "
            "Check that the scraper stored an Apply: line in the notes.",
        )

    # Generate resume PDF using the same render pipeline as the download endpoint
    from app.pdf import PDFRenderError, render_resume_pdf
    from app.schemas.models import PageFitSettings
    from app.services.page_fit import resume_print_url

    fit = PageFitSettings()  # default margins / template settings
    print_url = resume_print_url(req.resume_id, fit)

    try:
        pdf_bytes = await render_resume_pdf(print_url)
    except PDFRenderError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not render resume PDF: {e}. "
            "Make sure the frontend is running on port 3000.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected PDF error: {e}",
        )

    vault = await prepare_vault(req.resume_id)
    session = start_application(
        req.application_id,
        apply_url,
        pdf_bytes,
        vault=vault,
        company=app.get("company") or "",
        role=app.get("role") or "",
        notes=notes,
    )

    # Update application status to ready_to_apply
    try:
        await db.update_application(req.application_id, {"status": "ready_to_apply"})
    except Exception:
        pass

    return ApplyStatusResponse(**session.to_dict())


@router.get("/status/{application_id}", response_model=ApplyStatusResponse)
async def apply_status(application_id: str) -> ApplyStatusResponse:
    """Poll the status of an in-progress application.

    When the applier reports a terminal state (submitted/failed/cancelled)
    we mirror it onto the Kanban card so the user sees progress:
      - submitted  → card moves to 'no_response' (Applied column)
      - failed     → stays in 'applied' (Tailored) with error context
      - cancelled  → stays in 'applied' (Tailored)
    """
    session = get_session(application_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active apply session")

    # Mirror terminal status onto the application card (idempotent — the first
    # time we see 'submitted' we move the card; subsequent polls are no-ops
    # because the card is no longer in 'applied' column).
    if session.status == "submitted":
        try:
            current = await db.get_application(application_id)
            if current and current.get("status") != "no_response":
                await db.update_application(application_id, {"status": "no_response"})
        except Exception as e:
            logger.warning(f"Failed to move card to Applied: {e}")

    return ApplyStatusResponse(**session.to_dict())


@router.get("/screenshot/{application_id}")
async def apply_screenshot(application_id: str) -> dict[str, str | None]:
    """Get the base64 screenshot of the filled form (for review modal)."""
    session = get_session(application_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active apply session")
    return {
        "application_id": application_id,
        "status": session.status,
        "screenshot_b64": session.screenshot_b64,
    }


@router.post("/approve/{application_id}", response_model=ApplyStatusResponse)
async def approve_apply(application_id: str) -> ApplyStatusResponse:
    """User approves the filled form — triggers final submit click.

    The actual submit happens in the Playwright task running in the background;
    polling /status/{id} will eventually show 'submitted'. The status endpoint
    then moves the Kanban card to the Applied column.
    """
    ok = await approve_application(application_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Session not in needs_review state or not found",
        )
    session = get_session(application_id)
    assert session is not None
    return ApplyStatusResponse(**session.to_dict())


@router.post("/reject/{application_id}", response_model=ApplyStatusResponse)
async def reject_apply(application_id: str) -> ApplyStatusResponse:
    """User cancels — browser closes without submitting."""
    ok = await reject_application(application_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Session not found or already completed",
        )
    # Update card status back to tailored (applied)
    try:
        await db.update_application(application_id, {"status": "applied"})
    except Exception:
        pass
    session = get_session(application_id)
    assert session is not None
    return ApplyStatusResponse(**session.to_dict())


@router.get("/sessions")
async def active_sessions() -> list[dict[str, Any]]:
    """List all active/recent apply sessions (admin view)."""
    return list_sessions()


@router.get("/vault")
async def get_vault() -> dict[str, Any]:
    """Get apply-profile settings with secrets redacted."""
    return redact_vault(load_vault())


@router.put("/vault")
async def update_vault(vault: dict[str, Any]) -> dict[str, Any]:
    """Merge apply-profile updates. Blank/******** secrets keep the stored value."""
    merged = merge_vault(load_vault(), vault)
    save_vault(merged)
    return redact_vault(merged)
