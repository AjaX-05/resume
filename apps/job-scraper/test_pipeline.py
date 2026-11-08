"""
Live pipeline test — runs Greenhouse only (free JSON API, no Firecrawl credits).
Tests: fetch → filter → dedup → output JSON.
Run: uv run python test_pipeline.py
"""
import truststore
truststore.inject_into_ssl()

import sys
import json
import logging
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

from config import settings
from dedup import init_db, is_new_or_changed, record_job
from filter import passes_filters
from models import Job
from firecrawl import FirecrawlApp
from sources import greenhouse


def main() -> None:
    app = FirecrawlApp(api_key=settings.firecrawl_api_key)

    # Use a temp DB for testing
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = Path(f.name)
    conn = init_db(tmp_db)

    # Only test a few companies for speed
    test_companies = ["anthropic", "openai", "databricks"]
    original_companies = settings.greenhouse_companies
    settings.greenhouse_companies = test_companies

    new_jobs: list[Job] = []
    filtered_out: list[tuple[str, str]] = []

    for job in greenhouse.fetch_jobs(app):
        is_new, changed = is_new_or_changed(conn, job)
        passes, reason = passes_filters(job)

        if not passes:
            filtered_out.append((f"{job.title} @ {job.company}", reason))
            record_job(conn, job)
            continue

        if is_new or changed:
            record_job(conn, job)
            new_jobs.append(job)

    settings.greenhouse_companies = original_companies
    conn.close()
    tmp_db.unlink()

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(new_jobs)} jobs passed filters, {len(filtered_out)} filtered out")
    print(f"{'='*60}")

    if new_jobs:
        print(f"\nSample passing jobs (first 5):")
        for j in new_jobs[:5]:
            print(f"  [{j.source}] {j.title} @ {j.company}")
            print(f"    Location : {j.location_text}")
            print(f"    Salary   : {j.salary_text or 'not mentioned'}")
            print(f"    Spons    : {j.sponsorship_status.value}")
            print(f"    Remote   : {j.is_remote}")
            print(f"    Apply    : {j.apply_url[:80]}")
            print()

    if filtered_out:
        reasons: dict[str, int] = {}
        for _, reason in filtered_out:
            key = reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        print(f"Filter breakdown: {reasons}")

    # Save sample output
    if new_jobs:
        from output import save_batch
        out = save_batch(new_jobs[:10])
        print(f"\nSample output saved to: {out}")
        sample = json.loads(out.read_text(encoding="utf-8"))
        print(f"First record keys: {list(sample[0].keys())}")


if __name__ == "__main__":
    main()
