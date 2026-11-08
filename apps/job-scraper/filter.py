import logging

from config import settings
from models import Job, SponsorshipStatus

logger = logging.getLogger(__name__)


def passes_filters(job: Job) -> tuple[bool, str]:
    """Returns (passes, rejection_reason). Empty reason means passed."""

    # Hard reject: explicitly no sponsorship
    if job.sponsorship_status == SponsorshipStatus.EXPLICITLY_NOT_AVAILABLE:
        return False, f"no sponsorship: {job.sponsorship_evidence!r}"

    # Hard reject: not remote (or not US-compatible remote)
    if not job.is_remote:
        return False, f"not remote: {job.location_text!r}"

    # Salary: reject only when we have a confirmed salary ceiling below threshold
    # Unknown salary = keep (marked for human review)
    if job.salary_max is not None and job.salary_max < settings.min_base_salary:
        return False, f"salary too low: {job.salary_text!r}"

    return True, ""
