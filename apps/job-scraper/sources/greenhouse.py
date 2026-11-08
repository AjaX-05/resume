import logging
import sys
from pathlib import Path
from typing import Iterator

import httpx
from firecrawl import FirecrawlApp

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from extract import ExtractedJob, to_job
from models import Job

logger = logging.getLogger(__name__)

_GH_API = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"


def fetch_jobs(app: FirecrawlApp) -> Iterator[Job]:
    # Greenhouse has a free public JSON API — no Firecrawl credits needed
    for company in settings.greenhouse_companies:
        try:
            yield from _fetch_company(company)
        except Exception as e:
            logger.warning(f"Greenhouse error [{company}]: {e}")


def _fetch_company(company: str) -> Iterator[Job]:
    url = _GH_API.format(company=company)
    try:
        # verify=False handles corporate SSL proxy; truststore in main.py covers the rest
        with httpx.Client(timeout=15, verify=False) as client:
            resp = client.get(url)
        if resp.status_code == 404:
            return
        if resp.status_code != 200:
            logger.debug(f"Greenhouse {company}: HTTP {resp.status_code}")
            return
        data = resp.json()
    except Exception as e:
        logger.debug(f"Greenhouse API error [{company}]: {e}")
        return

    jobs = data.get("jobs", [])
    logger.info(f"Greenhouse [{company}]: {len(jobs)} jobs")

    for item in jobs:
        try:
            job = _parse(item, company)
            if job:
                yield job
        except Exception as e:
            logger.debug(f"Greenhouse parse error [{company}]: {e}")


def _parse(item: dict, company: str) -> Job | None:
    title = item.get("title", "")
    job_url = item.get("absolute_url", "")
    content = item.get("content", "")
    location = item.get("location", {}).get("name", "")
    updated_at = item.get("updated_at", "")

    if not title or not job_url:
        return None

    extracted = ExtractedJob(
        title=title,
        company=company.replace("-", " ").title(),
        description=content,
        location_text=location,
        apply_url=job_url,
        posting_date=updated_at[:10] if updated_at else "",
        req_id=str(item.get("id", "")),
    )
    return to_job(extracted, source="greenhouse", source_url=job_url)
