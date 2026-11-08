"""
Output layer — saves discovered jobs locally AND pushes them into Resume Matcher
as Kanban application cards (status = "saved") via POST /api/v1/applications.

Each discovered job lands in the ATS tracker automatically so you can review,
tailor, and apply without copy-pasting anything.
"""
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import settings
from models import Job

logger = logging.getLogger(__name__)

# Cached resume ID — fetched once from the API on first use
_resume_id: str | None = None


def _get_resume_id(client: httpx.Client) -> str | None:
    """Return the master resume ID from config or auto-fetched from the API."""
    global _resume_id

    if settings.resume_matcher_resume_id:
        return settings.resume_matcher_resume_id

    if _resume_id:
        return _resume_id

    try:
        resp = client.get(f"{settings.resume_matcher_api_url}/api/v1/resumes/list")
        resp.raise_for_status()
        resumes = resp.json()
        # Prefer the default master resume
        for r in resumes:
            if r.get("is_default_master"):
                _resume_id = r["resume_id"]
                logger.info(f"Auto-detected default master resume: {_resume_id}")
                return _resume_id
        # Fall back to first master resume
        for r in resumes:
            if r.get("is_master"):
                _resume_id = r["resume_id"]
                logger.info(f"Using first master resume: {_resume_id}")
                return _resume_id
    except Exception as e:
        logger.warning(f"Could not fetch resume ID from Resume Matcher: {e}")

    return None


def _build_notes(job: Job) -> str:
    """Build a rich notes string that shows up on the Kanban card."""
    lines = [
        f"Source: {job.source} | {job.source_url}",
        f"Salary: {job.salary_text or 'not mentioned'}",
    ]
    if job.equity_text:
        lines.append(f"Equity: {job.equity_text}")
    lines.append(f"Sponsorship: {job.sponsorship_status.value}"
                 + (f" — {job.sponsorship_evidence}" if job.sponsorship_evidence else ""))
    lines.append(f"Remote: {job.location_text}")
    lines.append(f"Apply: {job.apply_url}")
    if job.linkedin_contacts:
        lines.append("LinkedIn: " + " | ".join(job.linkedin_contacts))
    lines.append(f"Discovered: {job.first_seen_at}")
    return "\n".join(lines)


def _build_jd_text(job: Job) -> str:
    """Build the full JD text that Resume Matcher will store and use for tailoring."""
    parts = [f"# {job.title} at {job.company}"]

    if job.salary_text:
        parts.append(f"**Compensation:** {job.salary_text}")
    if job.equity_text:
        parts.append(f"**Equity:** {job.equity_text}")
    if job.location_text:
        parts.append(f"**Location:** {job.location_text}")

    parts.append("")
    parts.append(job.description)

    if job.skills:
        parts.append("\n## Skills")
        parts.extend(f"- {s}" for s in job.skills)

    if job.responsibilities:
        parts.append("\n## Responsibilities")
        parts.extend(f"- {r}" for r in job.responsibilities)

    parts.append(f"\n**Apply:** {job.apply_url}")
    return "\n".join(parts)


def _serialize(job: Job) -> dict:
    d = asdict(job)
    d["sponsorship_status"] = job.sponsorship_status.value
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def save_batch(jobs: list[Job]) -> Path:
    """Write new jobs to a dated JSON file under data/discovered/."""
    now = datetime.now(timezone.utc)
    batch_dir = settings.output_dir / now.strftime("%Y-%m-%d")
    batch_dir.mkdir(parents=True, exist_ok=True)
    out_file = batch_dir / f"batch-{now.strftime('%H-%M')}.json"
    out_file.write_text(
        json.dumps([_serialize(j) for j in jobs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"Saved {len(jobs)} jobs → {out_file}")
    return out_file


def post_to_resume_matcher(jobs: list[Job]) -> None:
    """
    Push each discovered job into Resume Matcher as a Kanban card (status = 'saved').

    Uses POST /api/v1/applications which atomically creates:
      - the job record (stores the full JD for tailoring)
      - the application card on the Kanban board
    """
    if not settings.resume_matcher_api_url:
        return

    with httpx.Client(timeout=20, verify=False) as client:
        resume_id = _get_resume_id(client)
        if not resume_id:
            logger.warning(
                "No master resume found in Resume Matcher — "
                "start the backend and create a master resume first, "
                "or set RESUME_MATCHER_RESUME_ID in .env"
            )
            return

        created = 0
        for job in jobs:
            try:
                resp = client.post(
                    f"{settings.resume_matcher_api_url}/api/v1/applications",
                    json={
                        "resume_id": resume_id,
                        "job_description": _build_jd_text(job),
                        "company": job.company,
                        "role": job.title,
                        "status": "saved",          # "saved" = discovered, not yet applied
                        "notes": _build_notes(job),
                    },
                )
                if resp.status_code == 200:
                    created += 1
                    data = resp.json()
                    logger.info(
                        f"  → Kanban card created: [{data.get('application_id','')}] "
                        f"{job.title} @ {job.company}"
                    )
                else:
                    logger.warning(
                        f"  Resume Matcher returned {resp.status_code} for "
                        f"{job.title} @ {job.company}: {resp.text[:120]}"
                    )
            except Exception as e:
                logger.warning(f"  Failed to post {job.title} @ {job.company}: {e}")

        if created:
            logger.info(f"  {created}/{len(jobs)} jobs added to Resume Matcher Kanban board")
