"""
YC / Work at a Startup source — filter-based search, two-phase scraping.
"""
import logging
import re
import sys
from pathlib import Path

from firecrawl import FirecrawlApp

sys.path.insert(0, str(Path(__file__).parent.parent))

from extract import ExtractedJob, to_job
from firecrawl_io import extract_job_json, scrape_listing_urls
from models import Job

logger = logging.getLogger(__name__)

SEARCH_URLS = [
    "https://www.workatastartup.com/jobs?role=ml&remote=true",
    "https://www.workatastartup.com/jobs?role=eng&remote=true",
    "https://www.workatastartup.com/jobs?role=data&remote=true",
]

JOB_URL_RE = re.compile(r"https://(?:www\.)?workatastartup\.com/jobs/\d+", re.I)

_JOB_PROMPT = (
    "Extract the complete job posting: title, company, full description, "
    "skills, responsibilities, salary, equity, location, sponsorship info, apply URL, posting date."
)


def paged_url(base: str, page: int) -> str:
    return base if page == 1 else f"{base}&page={page}"


def scan_listings(app: FirecrawlApp, url: str) -> list[dict]:
    """Phase 1: scrape listing page links — no LLM."""
    return scrape_listing_urls(app, url, JOB_URL_RE)


def scrape_job(app: FirecrawlApp, url: str) -> Job | None:
    """Phase 2: structured JSON extract of a single job posting."""
    data = extract_job_json(app, url, _JOB_PROMPT, ExtractedJob.model_json_schema())
    if not data or not data.get("title"):
        return None
    try:
        return to_job(ExtractedJob(**data), source="yc_jobs", source_url=url)
    except Exception as e:
        logger.warning(f"YC job parse failed [{url}]: {e}")
        return None
