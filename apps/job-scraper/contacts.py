import logging

from firecrawl import FirecrawlApp

from config import settings

logger = logging.getLogger(__name__)

_app: FirecrawlApp | None = None


def _fc() -> FirecrawlApp:
    global _app
    if _app is None:
        _app = FirecrawlApp(api_key=settings.firecrawl_api_key)
    return _app


def find_linkedin_contacts(company: str, max_results: int = 3) -> list[str]:
    """
    Search Google for public LinkedIn profiles of recruiters / eng managers at this company.
    Returns a list of public linkedin.com/in/ URLs only — no emails, no private data.
    Requires Firecrawl's search feature (paid tiers). Returns [] if unavailable.
    """
    query = (
        f'site:linkedin.com/in "{company}" '
        f'(recruiter OR "engineering manager" OR "hiring manager") '
        f'("machine learning" OR "AI" OR "data science")'
    )
    try:
        result = _fc().search(query, limit=max_results)
        urls: list[str] = []
        items = result if isinstance(result, list) else result.get("data", [])
        for item in items:
            url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
            if "linkedin.com/in/" in url:
                urls.append(url)
        return urls[:max_results]
    except Exception as e:
        logger.debug(f"LinkedIn contact search unavailable for {company!r}: {e}")
        return []
