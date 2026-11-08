"""
Wellfound source — filter-based search matching your Jobright constraints.

Filters:
  - Remote, United States
  - Minimum salary $140k (where URL supports it)
  - Sorted by recently added (newest first)
  - AI/ML/Data/Software roles
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
    "https://wellfound.com/jobs?role=machine-learning-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=applied-ai-scientist&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=data-scientist&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=data-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=software-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=backend-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=full-stack-engineer&remote=true&locationSlug=United-States",
    "https://wellfound.com/jobs?role=solutions-architect&remote=true&locationSlug=United-States",
]

JOB_URL_RE = re.compile(r"https://(?:www\.)?wellfound\.com/jobs/\d+[a-z0-9-]*", re.I)

_JOB_PROMPT = (
    "Extract the complete job posting: title, company, full description text, "
    "required and preferred skills, key responsibilities, salary/compensation range, "
    "equity or stock options, location and remote details, any text about visa "
    "sponsorship or work authorization, the direct apply URL, and posting date."
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
        return to_job(ExtractedJob(**data), source="wellfound", source_url=url)
    except Exception as e:
        logger.warning(f"Wellfound job parse failed [{url}]: {e}")
        return None
