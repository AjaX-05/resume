"""
YC / Work at a Startup source — filter-based search, two-phase scraping.
"""
import logging
import sys
from pathlib import Path

from firecrawl import FirecrawlApp
from firecrawl.v2.types import ScrapeOptions

sys.path.insert(0, str(Path(__file__).parent.parent))

from extract import ExtractedJob, ListingPageResult, to_job
from models import Job

logger = logging.getLogger(__name__)

# Filter-based URLs — ml + engineering roles, remote, newest first
SEARCH_URLS = [
    "https://www.workatastartup.com/jobs?role=ml&remote=true",
    "https://www.workatastartup.com/jobs?role=eng&remote=true",
    "https://www.workatastartup.com/jobs?role=data&remote=true",
]

_LISTING_PROMPT = (
    "Extract all job listings on this Work at a Startup (YC) jobs page. "
    "For each listing return: the exact job URL, job title, company name, "
    "salary range if shown, and location."
)

_JOB_PROMPT = (
    "Extract the complete job posting: title, company, full description, "
    "skills, responsibilities, salary, equity, location, sponsorship info, apply URL, posting date."
)

_OPTS = ScrapeOptions(skip_tls_verification=True, wait_for=2500)


def paged_url(base: str, page: int) -> str:
    return base if page == 1 else f"{base}&page={page}"


def scan_listings(app: FirecrawlApp, url: str) -> list[dict]:
    """Phase 1: cheap listing scan."""
    try:
        result = app.extract(
            [url],
            prompt=_LISTING_PROMPT,
            schema=ListingPageResult.model_json_schema(),
            scrape_options=_OPTS,
        )
        return (result.data or {}).get("jobs", []) if result.data else []
    except Exception as e:
        logger.warning(f"YC listing scan failed [{url}]: {e}")
        return []


def scrape_job(app: FirecrawlApp, url: str) -> Job | None:
    """Phase 2: full scrape for a single new job URL."""
    try:
        result = app.extract(
            [url],
            prompt=_JOB_PROMPT,
            schema=ExtractedJob.model_json_schema(),
            scrape_options=_OPTS,
        )
        data = result.data
        if not data or not data.get("title"):
            return None
        return to_job(ExtractedJob(**data), source="yc_jobs", source_url=url)
    except Exception as e:
        logger.warning(f"YC job scrape failed [{url}]: {e}")
        return None
