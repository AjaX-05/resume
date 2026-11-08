import logging
import re

from config import settings
from models import Job, SponsorshipStatus

logger = logging.getLogger(__name__)

# Greenhouse boards mix sales/ops with eng. Keep software/ML/data roles so the
# tracker isn't filled with Account Executive cards before we hit the per-source cap.
_TARGET_TITLE = re.compile(
    r"\b("
    r"software|engineer|developer|sre|swe|staff|principal|"
    r"data scien|data eng|machine learning|mlops|ml eng|"
    r"research|backend|frontend|full.?stack|scientist|"
    r"applied scien|platform|infrastructure|security eng|"
    r"llm|python|mle"
    r")\b",
    re.I,
)


def passes_filters(job: Job) -> tuple[bool, str]:
    """Returns (passes, rejection_reason). Empty reason means passed."""

    # Hard reject: explicitly no sponsorship
    if job.sponsorship_status == SponsorshipStatus.EXPLICITLY_NOT_AVAILABLE:
        return False, f"no sponsorship: {job.sponsorship_evidence!r}"

    # Hard reject: not remote (or not US-compatible remote)
    if not job.is_remote:
        return False, f"not remote: {job.location_text!r}"

    if not _TARGET_TITLE.search(job.title or ""):
        return False, f"title not a target role: {job.title!r}"

    # Salary: reject only when we have a confirmed salary ceiling below threshold
    # Unknown salary = keep (marked for human review)
    if job.salary_max is not None and job.salary_max < settings.min_base_salary:
        return False, f"salary too low: {job.salary_text!r}"

    return True, ""
