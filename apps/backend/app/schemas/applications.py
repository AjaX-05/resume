"""Pydantic schemas for the Kanban application tracker."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema


class ApplicationStatus(str, Enum):
    """Tracker columns — core 7 + auto-applier states."""

    saved = "saved"
    applied = "applied"
    no_response = "no_response"
    response = "response"
    interview = "interview"
    accepted = "accepted"
    rejected = "rejected"
    # Auto-applier pipeline states (hidden from Kanban board columns but
    # used to track in-flight Playwright apply sessions)
    ready_to_apply = "ready_to_apply"
    needs_human_review = "needs_human_review"
    submission_failed = "submission_failed"


# Columns shown on the Kanban board (the original 7 — auto-applier states
# are internal and not rendered as board columns).
APPLICATION_STATUS_ORDER: list[str] = [
    ApplicationStatus.saved.value,
    ApplicationStatus.applied.value,
    ApplicationStatus.no_response.value,
    ApplicationStatus.response.value,
    ApplicationStatus.interview.value,
    ApplicationStatus.accepted.value,
    ApplicationStatus.rejected.value,
]


class ApplicationResponse(BaseModel):
    """A single tracker card."""

    application_id: str
    job_id: str
    resume_id: str
    master_resume_id: str | None = None
    status: ApplicationStatus
    company: str | None = None
    role: str | None = None
    applied_at: str | None = None
    notes: str | None = None
    position: int
    created_at: str
    updated_at: str


class ApplicationDetailResponse(ApplicationResponse):
    """A card plus the embedded job description and applied resume.

    ``resume`` is null when the referenced resume has been deleted — the modal
    renders "resume unavailable" rather than 500ing.
    """

    job_content: str | None = None
    resume: dict[str, Any] | None = None


class ApplicationListResponse(BaseModel):
    """Applications grouped by column. All seven keys are always present."""

    columns: dict[str, list[ApplicationResponse]]


class ManualApplicationCreate(BaseModel):
    """Create a card from a pasted JD (no prior tailoring).

    The router creates the job from ``job_description`` then the application.
    ``company``/``role`` are optional overrides; when omitted the router runs a
    best-effort extraction.
    """

    resume_id: str
    job_description: str = Field(min_length=1)
    company: str | None = None
    role: str | None = None
    status: ApplicationStatus = ApplicationStatus.applied
    notes: str | None = None


class ApplicationUpdate(BaseModel):
    """Partial update — every field optional."""

    status: ApplicationStatus | SkipJsonSchema[None] = None
    position: int | None = None
    notes: str | None = None
    company: str | None = None
    role: str | None = None
    applied_at: str | None = None
    # When set, re-points this card at a tailored resume (used after the
    # tailor flow creates a new child resume).
    resume_id: str | None = None

    @field_validator("status")
    @classmethod
    def reject_null_status(cls, value: ApplicationStatus | None) -> ApplicationStatus:
        """Omission preserves status; an explicitly supplied null is invalid."""
        if value is None:
            raise ValueError("Status cannot be null")
        return value


class BulkStatusUpdate(BaseModel):
    """Move many cards to one column."""

    application_ids: list[str] = Field(min_length=1)
    status: ApplicationStatus


class BulkDelete(BaseModel):
    """Delete many cards."""

    application_ids: list[str] = Field(min_length=1)


class ApplicationActionResponse(BaseModel):
    """Generic acknowledgement for bulk/destructive actions."""

    message: str
    affected: int
