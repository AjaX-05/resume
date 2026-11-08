"""
Wellfound source — filter-based search matching your Jobright constraints.

Filters:
  - Remote, United States
  - Minimum salary $140k (where URL supports it)
  - Sorted by recently added (newest first)
  - AI/ML/Data/Software roles
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

# Wellfound uses role slugs + remote + optional salary filter
# All sorted by recently_added (newest first)
SEARCH_URLS = [
    # Core ML/AI roles
    "https://wellfound.com/jobs?role=machine-learning-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=applied-ai-scientist&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=data-scientist&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=data-engineer&remote=true&locationSlug=United-States",
    # Software Engineering roles
    "https://wellfound.com/jobs?role=software-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=backend-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=full-stack-engineer&remote=true&locationSlug=United-States",
    # Architecture / Leadership
    "https://wellfound.com/jobs?role=solutions-architect&remote=true&locationSlug=United-States",
]

_LISTING_PROMPT = (
    "Extract all job listings visible on this Wellfound job search page. "
    "For each listing return: the exact full job URL, job title, company name, "
    "salary range if shown, and location. Include every visible listing."
)

_JOB_PROMPT = (
    "Extract the complete job posting: title, company, full description text, "
    "required and preferred skills, key responsibilities, salary/compensation range, "
    "equity or stock options, location and remote details, any text about visa "
    "sponsorship or work authorization, the direct apply URL, and posting date."
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
        logger.warning(f"Wellfound listing scan failed [{url.split('?')[0]}]: {e}")
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
        return to_job(ExtractedJob(**data), source="wellfound", source_url=url)
    except Exception as e:
        logger.warning(f"Wellfound job scrape failed [{url}]: {e}")
        return None
