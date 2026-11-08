"""Firecrawl helpers for the job scraper.

Listing scans use scrape (markdown + links) — no LLM.
Job detail extraction uses v2 scrape with a JSON format object (the supported
replacement for the deprecated /v1/extract endpoint). Falls back to extract()
if the JSON format returns nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from firecrawl import FirecrawlApp

logger = logging.getLogger(__name__)

LISTING_WAIT_MS = 5000
JOB_WAIT_MS = 3500


def _as_urls(links: Any) -> list[str]:
    out: list[str] = []
    if not links:
        return out
    for item in links:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("url"):
            out.append(str(item["url"]))
        else:
            url = getattr(item, "url", None)
            if url:
                out.append(str(url))
    return out


def _canonical(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.split("?")[0].split("#")[0]
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def scrape_listing_urls(
    app: FirecrawlApp,
    page_url: str,
    job_url_re: re.Pattern[str],
    *,
    headers: Optional[dict[str, str]] = None,
    wait_for: int = LISTING_WAIT_MS,
) -> list[dict]:
    """Phase 1: scrape a search page and return unique job URLs matching job_url_re."""
    try:
        doc = app.scrape(
            page_url,
            formats=["markdown", "links"],
            wait_for=wait_for,
            skip_tls_verification=True,
            headers=headers,
        )
    except Exception as e:
        logger.warning(f"listing scrape failed [{page_url.split('?')[0]}]: {e}")
        return []

    status = getattr(getattr(doc, "metadata", None), "status_code", None) or getattr(
        getattr(doc, "metadata", None), "statusCode", None
    )
    if status and int(status) >= 400:
        logger.warning(f"listing scrape HTTP {status} [{page_url.split('?')[0]}]")

    candidates = _as_urls(getattr(doc, "links", None))
    markdown = getattr(doc, "markdown", None) or ""
    if markdown:
        candidates.extend(m.group(0) for m in job_url_re.finditer(markdown))

    seen: set[str] = set()
    listings: list[dict] = []
    for raw in candidates:
        match = job_url_re.search(raw)
        if not match:
            continue
        url = _canonical(match.group(0))
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        listings.append(
            {
                "url": url,
                "title": "",
                "company": "",
                "salary_snippet": "",
                "location": "",
            }
        )
    logger.info(f"  listing scrape: {len(listings)} job URLs from {page_url.split('?')[0]}")
    return listings


def extract_job_json(
    app: FirecrawlApp,
    url: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    headers: Optional[dict[str, str]] = None,
    wait_for: int = JOB_WAIT_MS,
) -> Optional[dict]:
    """Phase 2: structured extraction via v2 scrape json format, then /extract fallback."""
    try:
        doc = app.scrape(
            url,
            formats=[{"type": "json", "prompt": prompt, "schema": schema}],
            wait_for=wait_for,
            skip_tls_verification=True,
            headers=headers,
        )
        data = getattr(doc, "json", None)
        if isinstance(data, dict) and (data.get("title") or data.get("jobs")):
            return _coerce_job_dict(data)
        if data:
            logger.debug(f"json scrape returned no title [{url}]: {type(data)}")
    except Exception as e:
        logger.warning(f"json scrape failed [{url}]: {e}")

    try:
        result = app.extract([url], prompt=prompt, schema=schema)
        data = getattr(result, "data", None)
        if data is None and isinstance(result, dict):
            data = result.get("data")
        if isinstance(data, dict) and (data.get("title") or data.get("jobs")):
            return _coerce_job_dict(data)
        logger.warning(f"extract fallback empty [{url}]: {type(data).__name__}")
    except Exception as e:
        logger.warning(f"extract fallback failed [{url}]: {e}")
    return None


def _coerce_job_dict(data: dict) -> dict:
    out = dict(data)
    for key in ("skills", "responsibilities"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = [value] if value.strip() else []
        elif value is None:
            out[key] = []
    for key in (
        "salary_text",
        "equity_text",
        "location_text",
        "sponsorship_text",
        "apply_url",
        "posting_date",
        "req_id",
        "description",
        "company",
        "title",
    ):
        if out.get(key) is None:
            out[key] = ""
    return out
