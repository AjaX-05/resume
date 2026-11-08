"""Quick smoke tests — run before the full scraper to validate API shape."""
import json
import sys
import truststore
truststore.inject_into_ssl()  # use Windows certificate store (handles corporate SSL proxies)
import httpx
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import settings


def test_greenhouse() -> None:
    print("\n=== Greenhouse (free JSON API) ===")
    url = "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=false"
    r = httpx.get(url, timeout=15)
    jobs = r.json().get("jobs", [])
    print(f"Anthropic Greenhouse: {len(jobs)} jobs found")
    if jobs:
        j = jobs[0]
        print(f"  Sample: {j.get('title')} | {j.get('location', {}).get('name')} | {j.get('absolute_url', '')[:60]}")


def test_firecrawl_scrape() -> None:
    print("\n=== Firecrawl scrape (markdown) ===")
    from firecrawl import FirecrawlApp
    app = FirecrawlApp(api_key=settings.firecrawl_api_key)

    # Simple markdown scrape first — validate key works
    result = app.scrape_url("https://www.ycombinator.com/jobs", formats=["markdown"])
    if hasattr(result, "markdown"):
        md = result.markdown or ""
    else:
        md = result.get("markdown", "") if isinstance(result, dict) else str(result)[:200]

    print(f"YC Jobs markdown length: {len(md)} chars")
    print(f"First 300 chars: {md[:300]}")
    print(f"Response type: {type(result).__name__}")
    print(f"Response keys/attrs: {list(result.__dict__.keys()) if hasattr(result, '__dict__') else list(result.keys()) if isinstance(result, dict) else 'N/A'}")


def test_firecrawl_extract() -> None:
    print("\n=== Firecrawl v4 extract (LLM) ===")
    from firecrawl import FirecrawlApp
    from pydantic import BaseModel, Field

    class JobItem(BaseModel):
        title: str
        company: str
        url: str = ""

    class ListResult(BaseModel):
        jobs: list[JobItem] = Field(default_factory=list)

    app = FirecrawlApp(api_key=settings.firecrawl_api_key)

    # v4: app.extract() with schema as JSON schema dict (not Pydantic class)
    print("\n-- Trying app.extract() with model_json_schema() --")
    try:
        result = app.extract(
            ["https://www.ycombinator.com/jobs"],
            prompt="Extract up to 3 job listings with title, company name, and job URL.",
            schema=ListResult.model_json_schema(),
        )
        print(f"app.extract() success: {type(result)}")
        print(f"Result: {json.dumps(result if isinstance(result, dict) else str(result), indent=2)[:600]}")
    except Exception as e:
        print(f"app.extract() with dict schema failed: {e}")

    # v4: app.scrape() with formats=["json"] + json_options
    print("\n-- Trying app.scrape() with formats=['json'] --")
    try:
        result = app.scrape(
            "https://www.ycombinator.com/jobs",
            formats=["json"],
            json_options={"schema": ListResult.model_json_schema(), "prompt": "Extract up to 3 job listings with title, company, and URL."},
        )
        print(f"scrape json: {type(result)}")
        j = result.json if hasattr(result, "json") else (result.get("json") if isinstance(result, dict) else None)
        print(f"json field: {json.dumps(j, indent=2)[:600] if j else 'None'}")
    except Exception as e:
        print(f"scrape formats=['json'] failed: {e}")

    # Check what scrape() accepts
    print("\n-- Checking scrape() signature --")
    import inspect
    try:
        sig = inspect.signature(app.scrape)
        print(f"scrape() params: {list(sig.parameters.keys())}")
    except Exception as e:
        print(f"Could not inspect: {e}")


if __name__ == "__main__":
    test_greenhouse()
    test_firecrawl_scrape()
    test_firecrawl_extract()
