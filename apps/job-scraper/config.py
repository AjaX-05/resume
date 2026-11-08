from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    firecrawl_api_key: str = "local"
    firecrawl_api_url: str = "http://127.0.0.1:3002"

    # Jobright — Playwright auto-login (preferred, needs a non-locked-down machine)
    jobright_email: str = ""
    jobright_password: str = ""

    # Jobright — manual cookie fallback (paste from browser DevTools on office laptop)
    # DevTools → Application tab → Cookies → jobright.ai → copy ALL cookies as one string
    # e.g. "session=abc123; _ga=GA1.2.xyz; ..."
    jobright_manual_cookie: str = ""

    # Resume Matcher backend
    resume_matcher_api_url: str = "http://localhost:8000"

    # Master resume ID to attach discovered jobs to.
    # Leave blank to auto-fetch the default master resume on first run.
    resume_matcher_resume_id: str = ""

    output_dir: Path = BASE_DIR / "data" / "discovered"
    auth_dir: Path = BASE_DIR / "data" / "auth"
    db_path: Path = BASE_DIR / "data" / "jobs.db"

    min_base_salary: int = 140_000

    # Not used for scraping. Greenhouse is the apply ATS for postings found
    # on Jobright / Wellfound / YC. Kept so tests and older configs still load.
    greenhouse_companies: list[str] = [
        "anthropic",
        "openai",
        "databricks",
        "stripe",
        "airbnb",
        "discord",
        "reddit",
        "figma",
        "cloudflare",
        "notion",
        "andurilindustries",
        "anysphere",
        "huggingface",
        "scaleai",
        "togetherai",
    ]

    # Per run: how many listing pages to check per search URL (page 1 = newest)
    max_listing_pages: int = 2

    # Cap LLM-backed full-job extracts per source so a first run cannot stall for hours
    max_new_jobs_per_source: int = 20

    # Stop paginating when this many consecutive already-seen listings are found
    early_stop_threshold: int = 5

    # Seconds to wait between Phase 2 (full job scrape) calls.
    # Local Firecrawl does not need the hosted 22s gap; keep a small pause.
    request_delay_seconds: int = 3


settings = Settings()
