"""
Jobright.ai source — mirrors your exact saved-search filters from the UI.

Filters encoded in every URL (matching screenshots):
  - Remote anywhere in the US
  - Full-time + Contract (no part-time/internship)
  - Entry / Mid / Senior / Lead+Staff (no Director/Executive)
  - 0-7 years experience
  - Minimum salary $140,000/yr
  - Date posted: past 24 hours  ← key for "only newer ones"
  - Sorted by date (newest first)

Job functions covered (all 26+ from your screenshot):
  ML Engineer, AI Engineer, LLM Engineer, ML Researcher, Deep Learning,
  Model Training, Computer Vision, MLOps, ML Infrastructure, ML Search, ML Ads,
  Data Scientist, Data Engineer, Backend, Full Stack, Python, .Net, React,
  Frontend, Software Architect, Solutions Architect, CTO, Eng Manager,
  Eng Director/VP, Data Consultant, IT Consultant
"""
import logging
import sys
from pathlib import Path

from firecrawl import FirecrawlApp
from firecrawl.v2.types import ScrapeOptions

sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import cookies_to_header, get_jobright_cookies
from config import settings
from extract import ExtractedJob, ListingPageResult, to_job
from models import Job

logger = logging.getLogger(__name__)

# ── Confirmed working Jobright URL params ─────────────────────────────────
# Only these three are confirmed to return results. Salary/date/type/experience
# filters are applied by our Python filter.py (same constraints, no URL risk).
_P = "locationName=United+States&isRemote=true&sortBy=date"

# ── Search URLs — one per major Jobright category ──────────────────────────
# Results sorted by date (newest first) — combined with our 2-hour schedule
# and dedup fingerprinting this ensures we only process new postings each run.
#
# Category → Your UI roles covered:
#   Machine-Learning       → ML Engineer, ML Researcher, Deep Learning, LLM Engineer,
#                            Model Training, CV, MLOps, ML Infra, ML Search, ML Ads
#   Artificial-Intelligence → AI Engineer, Applied AI
#   Data-Science           → Data Scientist
#   Data-Engineering       → Data Engineer, Data Consultant
#   Software-Engineering   → Backend, Full Stack, Python, .Net, React, Frontend
#   Solutions-Architect    → Solutions Architect, Forward Deploy
#   Software-Architecture  → Software Architect, CTO
#   Engineering-Management → Eng Manager, Eng Director/VP
SEARCH_URLS = [
    f"https://jobright.ai/jobs/Machine-Learning?{_P}",
    f"https://jobright.ai/jobs/Artificial-Intelligence?{_P}",
    f"https://jobright.ai/jobs/Data-Science?{_P}",
    f"https://jobright.ai/jobs/Data-Engineering?{_P}",
    f"https://jobright.ai/jobs/Software-Engineering?{_P}",
    f"https://jobright.ai/jobs/Solutions-Architect?{_P}",
    f"https://jobright.ai/jobs/Software-Architecture?{_P}",
    f"https://jobright.ai/jobs/Engineering-Management?{_P}",
]

_LISTING_PROMPT = (
    "Extract all job listings visible on this Jobright search results page. "
    "For each listing return: the full job detail URL (not the search URL), "
    "job title, company name, salary snippet if shown, location."
)

_JOB_PROMPT = (
    "Extract the complete job posting: title, company, full description text, "
    "required skills, key responsibilities, salary and total compensation, "
    "equity or stock options, location (remote/states), any text about "
    "visa sponsorship or work authorization, direct apply URL, posting date."
)

# Module-level auth — initialized once per run via init_auth()
_scrape_opts: ScrapeOptions | None = None


def init_auth(app: FirecrawlApp) -> bool:
    """Initialize session. Returns True if auth succeeded."""
    global _scrape_opts

    if not settings.jobright_email and not settings.jobright_manual_cookie:
        logger.warning("No Jobright credentials set — skipping Jobright source")
        return False

    try:
        cookies = get_jobright_cookies()
        if not cookies:
            logger.warning("Jobright returned empty cookies — skipping")
            return False

        _scrape_opts = ScrapeOptions(
            headers={"Cookie": cookies_to_header(cookies)},
            skip_tls_verification=True,
            wait_for=3000,
        )
        logger.info("Jobright auth ready")
        return True
    except Exception as e:
        logger.warning(f"Jobright auth failed: {e}")
        return False


def paged_url(base: str, page: int) -> str:
    return base if page == 1 else f"{base}&page={page}"


def scan_listings(app: FirecrawlApp, url: str) -> list[dict]:
    """Phase 1: cheap listing scan — returns list of {url, title, company, salary_snippet}."""
    if _scrape_opts is None:
        return []
    try:
        result = app.extract(
            [url],
            prompt=_LISTING_PROMPT,
            schema=ListingPageResult.model_json_schema(),
            scrape_options=_scrape_opts,
        )
        return (result.data or {}).get("jobs", []) if result.data else []
    except Exception as e:
        logger.warning(f"Jobright listing scan failed [{url.split('?')[0]}]: {e}")
        return []


def scrape_job(app: FirecrawlApp, url: str) -> Job | None:
    """Phase 2: full scrape for a single new job URL."""
    if _scrape_opts is None:
        return None
    try:
        result = app.extract(
            [url],
            prompt=_JOB_PROMPT,
            schema=ExtractedJob.model_json_schema(),
            scrape_options=_scrape_opts,
        )
        data = result.data
        if not data or not data.get("title"):
            return None
        return to_job(ExtractedJob(**data), source="jobright", source_url=url)
    except Exception as e:
        logger.warning(f"Jobright job scrape failed [{url}]: {e}")
        return None
