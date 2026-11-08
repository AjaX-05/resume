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
from job_meta import format_lookback
from models import Job

logger = logging.getLogger(__name__)

# Cached resume ID — fetched once from the API on first use
_resume_id: str | None = None

# Incremental batch file for the current collection run
_batch_jobs: list[Job] = []
_batch_path: Path | None = None
_scrape_batch_id: str = ""
_lookback_minutes: int = 60
# Tracker cards already on the board — loaded once per scrape so we don't
# post the same Omnicom role twice (title with "@ Company" vs without).
_existing_fps: set[str] | None = None
_existing_urls: set[str] | None = None


def _normalize_url(url: str) -> str:
    return (url or "").strip().lower().rstrip("/")


def _card_fingerprint(company: str, role: str) -> str:
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


def _apply_url_from_notes(notes: str) -> str:
    for line in (notes or "").splitlines():
        if line.lower().startswith("apply:"):
            return _normalize_url(line.split(":", 1)[1])
    return ""


def reset_batch(*, lookback_minutes: int = 60) -> None:
    """Start a fresh discovered-jobs file for this collection run."""
    global _batch_jobs, _batch_path, _scrape_batch_id, _lookback_minutes
    global _existing_fps, _existing_urls
    _existing_fps = None
    _existing_urls = None
    _batch_jobs = []
    _batch_path = None
    _lookback_minutes = lookback_minutes
    _scrape_batch_id = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _as_resume_list(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def _get_resume_id(client: httpx.Client) -> str | None:
    """Return the master resume ID from config or auto-fetched from the API."""
    global _resume_id

    if settings.resume_matcher_resume_id:
        return settings.resume_matcher_resume_id

    if _resume_id:
        return _resume_id

    try:
        # /resumes/list hides masters unless include_master=true
        resp = client.get(
            f"{settings.resume_matcher_api_url}/api/v1/resumes/list",
            params={"include_master": "true"},
        )
        resp.raise_for_status()
        resumes = _as_resume_list(resp.json())
        for r in resumes:
            if r.get("is_default_master"):
                _resume_id = r["resume_id"]
                logger.info(f"Auto-detected default master resume: {_resume_id}")
                return _resume_id
        for r in resumes:
            if r.get("is_master"):
                _resume_id = r["resume_id"]
                logger.info(f"Using first master resume: {_resume_id}")
                return _resume_id
        if resumes:
            _resume_id = resumes[0]["resume_id"]
            logger.info(f"Using first resume: {_resume_id}")
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
    if job.posting_date:
        lines.append(f"Posted: {job.posting_date}")
    if job.seniority:
        lines.append(f"Seniority: {job.seniority}")
    if _scrape_batch_id:
        lines.append(f"Scrape-batch: {_scrape_batch_id}")
    lines.append(f"Lookback: {format_lookback(_lookback_minutes)}")
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
    if job.seniority:
        parts.append(f"**Level:** {job.seniority}")
    if job.posting_date:
        parts.append(f"**Posted:** {job.posting_date}")

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


def _load_board_index(client: httpx.Client) -> tuple[set[str], set[str]]:
    """Company+role fingerprints and apply URLs already on the tracker."""
    global _existing_fps, _existing_urls
    if _existing_fps is not None and _existing_urls is not None:
        return _existing_fps, _existing_urls
    fps: set[str] = set()
    urls: set[str] = set()
    try:
        resp = client.get(f"{settings.resume_matcher_api_url}/api/v1/applications")
        if resp.status_code == 200:
            payload = resp.json()
            columns = payload.get("columns") or {}
            if isinstance(payload.get("data"), dict):
                columns = payload["data"].get("columns") or columns
            if isinstance(columns, dict):
                for cards in columns.values():
                    if not isinstance(cards, list):
                        continue
                    for card in cards:
                        if not isinstance(card, dict):
                            continue
                        fp = _card_fingerprint(
                            str(card.get("company") or ""),
                            str(card.get("role") or ""),
                        )
                        if fp:
                            fps.add(fp)
                        url = _apply_url_from_notes(str(card.get("notes") or ""))
                        if url:
                            urls.add(url)
    except Exception as exc:
        logger.warning("Could not load existing tracker cards for dedupe: %s", exc)
    _existing_fps, _existing_urls = fps, urls
    return fps, urls


def persist_job(job: Job) -> bool:
    """Append one job to the batch file and post it to the tracker immediately.

    Returns True if a Kanban card was created. Called per-job so a timeout
    still leaves cards on the board.
    """
    global _batch_jobs, _batch_path
    _batch_jobs.append(job)
    now = datetime.now(timezone.utc)
    if _batch_path is None:
        batch_dir = settings.output_dir / now.strftime("%Y-%m-%d")
        batch_dir.mkdir(parents=True, exist_ok=True)
        _batch_path = batch_dir / f"batch-{now.strftime('%H-%M')}.json"
    _batch_path.write_text(
        json.dumps([_serialize(j) for j in _batch_jobs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    created = post_to_resume_matcher([job])
    return created > 0


def post_to_resume_matcher(jobs: list[Job]) -> int:
    """
    Push each discovered job into Resume Matcher as a Kanban card (status = 'saved').

    Uses POST /api/v1/applications which atomically creates:
      - the job record (stores the full JD for tailoring)
      - the application card on the Kanban board
    """
    if not settings.resume_matcher_api_url:
        return 0

    created = 0
    with httpx.Client(timeout=20, verify=False) as client:
        resume_id = _get_resume_id(client)
        if not resume_id:
            logger.warning(
                "No master resume found in Resume Matcher — "
                "start the backend and create a master resume first, "
                "or set RESUME_MATCHER_RESUME_ID in .env"
            )
            return 0

        fps, urls = _load_board_index(client)
        for job in jobs:
            fingerprint = _card_fingerprint(job.company, job.title)
            apply_url = _normalize_url(job.apply_url)
            if (fingerprint and fingerprint in fps) or (apply_url and apply_url in urls):
                logger.info("  skip duplicate: %s @ %s", job.title, job.company)
                continue
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
                if resp.status_code in (200, 201):
                    created += 1
                    data = resp.json()
                    if isinstance(data, dict) and isinstance(data.get("data"), dict):
                        data = data["data"]
                    logger.info(
                        f"  → Kanban card created: [{data.get('application_id','')}] "
                        f"{job.title} @ {job.company}"
                    )
                    if fingerprint:
                        fps.add(fingerprint)
                    if apply_url:
                        urls.add(apply_url)
                else:
                    logger.warning(
                        f"  Resume Matcher returned {resp.status_code} for "
                        f"{job.title} @ {job.company}: {resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"  Failed to post {job.title} @ {job.company}: {e}")

        if created:
            logger.info(f"  {created}/{len(jobs)} jobs added to Resume Matcher Kanban board")
    return created
