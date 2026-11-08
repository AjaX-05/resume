"""
Targeted Jobright test — logs in via Playwright, scans ML+AI+SWE search URLs,
scrapes up to 5 new job detail pages, shows which pass your constraints.

Run: uv run python test_jobright.py
Browser will open for login (auto-fills from .env), closes once logged in.
"""
import truststore
truststore.inject_into_ssl()

import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from config import settings
from dedup import init_db, is_url_seen, record_job, is_new_or_changed
from filter import passes_filters
from firecrawl import FirecrawlApp
from sources import jobright
from output import save_batch
from datetime import datetime, timezone


def main() -> None:
    app = FirecrawlApp(api_key=settings.firecrawl_api_key)
    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    conn = init_db(tmp_db)

    # Step 1: Jobright auth (browser opens, auto-fills, closes)
    print("\n=== Step 1: Jobright Login ===")
    ok = jobright.init_auth(app)
    if not ok:
        print("Auth failed — check JOBRIGHT_EMAIL/PASSWORD in .env")
        return
    print("Login successful.")

    # Step 2: Scan listing pages (Phase 1) — ML and AI categories only for this test
    test_urls = [
        "https://jobright.ai/jobs/Machine-Learning?locationName=United+States&isRemote=true&sortBy=date",
        "https://jobright.ai/jobs/Artificial-Intelligence?locationName=United+States&isRemote=true&sortBy=date",
        "https://jobright.ai/jobs/Software-Engineering?locationName=United+States&isRemote=true&sortBy=date",
    ]

    print(f"\n=== Step 2: Listing Scan (Phase 1) ===")
    all_new_urls: list[str] = []
    for url in test_urls:
        listings = jobright.scan_listings(app, url)
        cat = url.split("/jobs/")[1].split("?")[0]
        new_urls = [l.get("url", "") for l in listings if l.get("url") and not is_url_seen(conn, l.get("url", ""))]
        print(f"  {cat:30s}: {len(listings)} listings, {len(new_urls)} new")
        for l in listings[:3]:
            print(f"    • {l.get('title','?')!r} @ {l.get('company','?')!r} | {l.get('salary_snippet','') or 'salary not shown'}")
        all_new_urls.extend(new_urls)

    print(f"\nTotal new job URLs to scrape: {len(all_new_urls)}")

    # Step 3: Full scrape (Phase 2) — first 8 new URLs
    print(f"\n=== Step 3: Full Scrape (Phase 2) — first 8 new jobs ===")
    passed: list = []
    rejected: list = []

    for url in all_new_urls[:8]:
        job = jobright.scrape_job(app, url)
        if not job:
            print(f"  SKIP (extract failed): {url[:60]}")
            continue

        ok, reason = passes_filters(job)
        now = datetime.now(timezone.utc).isoformat()
        job.first_seen_at = now
        job.last_seen_at = now
        record_job(conn, job)

        status = "PASS" if ok else f"FAIL ({reason.split(':')[0]})"
        print(f"  [{status}] {job.title} @ {job.company}")
        print(f"    Salary  : {job.salary_text or 'not mentioned'} | min={job.salary_min} max={job.salary_max}")
        print(f"    Remote  : {job.is_remote} | Location: {job.location_text[:60]}")
        print(f"    Sponsor : {job.sponsorship_status.value} — {job.sponsorship_evidence[:60] or 'no evidence'}")
        print(f"    Apply   : {job.apply_url[:70]}")
        print()

        if ok:
            passed.append(job)
        else:
            rejected.append((job.title, job.company, reason))

    print(f"\n=== Results ===")
    print(f"  Passed filters : {len(passed)}")
    print(f"  Rejected       : {len(rejected)}")

    if rejected:
        print("\nRejection breakdown:")
        for title, company, reason in rejected:
            print(f"  {title} @ {company}: {reason}")

    if passed:
        out = save_batch(passed)
        print(f"\nPassing jobs saved to: {out}")
        print("\nPassing job titles:")
        for j in passed:
            print(f"  • {j.title} @ {j.company} | {j.salary_text or 'salary unknown'} | {j.sponsorship_status.value}")

    conn.close()
    tmp_db.unlink()


if __name__ == "__main__":
    main()
