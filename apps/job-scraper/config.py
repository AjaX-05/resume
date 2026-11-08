from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    firecrawl_api_key: str

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

    # Per run: how many listing pages to check per search URL (page 1 = newest)
    max_listing_pages: int = 2

    # Stop paginating when this many consecutive already-seen listings are found
    early_stop_threshold: int = 5

    # Seconds to wait between Phase 2 (full job scrape) calls to stay under Firecrawl rate limits.
    # Free tier ≈ 3 req/min → use 22s.  Paid Standard plan → use 5s.
    request_delay_seconds: int = 22


settings = Settings()
