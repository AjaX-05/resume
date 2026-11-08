"""
Job Scraper — runs every 2 hours CST.

Two-phase collection loop per source:
  Phase 1  scan_listings()  one Firecrawl call per search page → brief listing items
  Phase 2  scrape_job()     one Firecrawl call per NEW job only → full details

Early stopping: when we see `early_stop_threshold` consecutive already-known listings
on a page, we skip the rest of that page and move on.  This means subsequent runs are
much cheaper — we only spend credits on genuinely new postings.

Usage:
    uv run python main.py          # start scheduler (runs every 2 h CST)
    uv run python main.py --once   # run one cycle now and exit (good for testing)
"""

# Must be first — makes Python use the Windows certificate store (handles VPN/corporate SSL)
import truststore
truststore.inject_into_ssl()

import logging
import sqlite3
import sys
import time
import types
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from firecrawl import FirecrawlApp

from config import settings
from dedup import init_db, is_new_or_changed, is_url_seen, record_job
from filter import passes_filters
from job_meta import coerce_lookback, format_lookback, job_cap_for, page_cap_for, within_lookback
from models import Job
from output import persist_job, reset_batch, save_batch
from sources import jobright, wellfound, yc_jobs

# Line-buffer logs so the tracker UI sees progress before the process exits.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

_firecrawl = FirecrawlApp(
    api_key=settings.firecrawl_api_key or "local",
    api_url=settings.firecrawl_api_url,
)
_db: sqlite3.Connection | None = None
_SOURCE_FILTER: str = "all"  # set by --source arg
_LOOKBACK_MINUTES: int = 60


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = init_db(settings.db_path)
    return _db


# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit aware scrape helper
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_with_retry(source: types.ModuleType, url: str, max_retries: int = 2) -> Job | None:
    """Call source.scrape_job(), retrying once after a 30s sleep on rate-limit errors."""
    for attempt in range(max_retries + 1):
        try:
            return source.scrape_job(_firecrawl, url)
        except Exception as e:
            msg = str(e).lower()
            if "rate limit" in msg or "rate_limit" in msg or "429" in msg:
                if attempt < max_retries:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"Rate limited — sleeping {wait}s then retrying {url[:60]}")
                    time.sleep(wait)
                    continue
            logger.warning(f"scrape_job failed [{url[:60]}]: {e}")
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Two-phase collection engine
# ─────────────────────────────────────────────────────────────────────────────

def _collect_from_source(
    source: types.ModuleType,
    source_name: str,
    conn: sqlite3.Connection,
    new_jobs: list[Job],
) -> None:
    """
    Run two-phase scan for all search URLs in a source module.
    Phase 1: scan listing page → get brief items (title, company, url)
    Phase 2: scrape full details only for URLs we haven't seen before
    """
    collected_new = 0
    job_cap = job_cap_for(_LOOKBACK_MINUTES)
    page_cap = page_cap_for(_LOOKBACK_MINUTES)
    for base_idx, base_url in enumerate(source.SEARCH_URLS):
        if collected_new >= job_cap:
            break
        if base_idx > 0:
            time.sleep(settings.request_delay_seconds)  # rate limit between listing scans

        for page in range(1, page_cap + 1):
            if collected_new >= job_cap:
                break
            if page > 1:
                time.sleep(settings.request_delay_seconds)

            url = source.paged_url(base_url, page)
            logger.info(f"  [{source_name}] scanning page {page}: {url.split('?')[0]}…")

            # ── Phase 1: listing scan via scrape+links (no LLM) ─────────────
            listings = source.scan_listings(_firecrawl, url)
            if not listings:
                logger.info(f"  [{source_name}] no listings returned, stopping pagination")
                break

            new_urls: list[str] = []
            consecutive_known = 0
            consecutive_old = 0
            remaining = job_cap - collected_new

            for item in listings:
                item_url = item.get("url", "")
                if not item_url:
                    continue
                posted_at = item.get("posted_at") or None
                if posted_at and not within_lookback(posted_at, _LOOKBACK_MINUTES):
                    consecutive_old += 1
                    if consecutive_old >= settings.early_stop_threshold:
                        logger.info(
                            f"  [{source_name}] early stop: listings older than "
                            f"{format_lookback(_LOOKBACK_MINUTES)}"
                        )
                        break
                    continue
                consecutive_old = 0
                if is_url_seen(conn, item_url):
                    consecutive_known += 1
                    if consecutive_known >= settings.early_stop_threshold:
                        logger.info(
                            f"  [{source_name}] early stop: {consecutive_known} consecutive "
                            f"known listings — page {page} is old"
                        )
                        break
                else:
                    consecutive_known = 0
                    new_urls.append(item_url)
                    if len(new_urls) >= remaining:
                        break

            logger.info(
                f"  [{source_name}] page {page}: {len(new_urls)} new URLs in last "
                f"{format_lookback(_LOOKBACK_MINUTES)}, "
                f"{len(listings) - len(new_urls)} skipped (seen or older)"
            )

            # ── Phase 2: structured extract — new URLs only ─────────────────
            for i, job_url in enumerate(new_urls):
                if i > 0:
                    time.sleep(settings.request_delay_seconds)

                job = _scrape_with_retry(source, job_url)
                if job:
                    before = len(new_jobs)
                    _process_job(conn, job, new_jobs)
                    if len(new_jobs) > before:
                        collected_new += 1

            # Stop pagination if this entire page was mostly known jobs
            known_on_page = len(listings) - len(new_urls)
            if known_on_page >= settings.early_stop_threshold:
                break


def _process_job(conn: sqlite3.Connection, job: Job, new_jobs: list[Job]) -> None:
    is_new, changed = is_new_or_changed(conn, job)

    if not within_lookback(job.posting_date, _LOOKBACK_MINUTES):
        logger.info(
            f"  SKIP {job.title} @ {job.company}: posted {job.posting_date} "
            f"outside last {format_lookback(_LOOKBACK_MINUTES)}"
        )
        return

    passes, reason = passes_filters(job)
    if not passes:
        logger.debug(f"  SKIP {job.title} @ {job.company}: {reason}")
        record_job(conn, job)
        return

    if not is_new and not changed:
        record_job(conn, job)
        return

    now = datetime.now(timezone.utc).isoformat()
    job.first_seen_at = now if is_new else job.first_seen_at
    job.last_seen_at = now

    posted = persist_job(job)
    # Only mark seen after a successful post so a killed run can retry.
    if posted:
        record_job(conn, job)
        new_jobs.append(job)

    flag = "NEW" if is_new else "UPDATED"
    logger.info(
        f"  [{flag}] [{job.source}] {job.title} @ {job.company} | "
        f"{job.seniority or 'Mid'} | posted {job.posting_date or 'unknown'} | "
        f"{job.salary_text or 'salary unknown'} | {job.sponsorship_status.value}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main collection run
# ─────────────────────────────────────────────────────────────────────────────

def run_collection() -> None:
    start = datetime.now(timezone.utc)
    logger.info(f"=== Collection run starting {start.strftime('%Y-%m-%d %H:%M UTC')} ===")

    reset_batch(lookback_minutes=_LOOKBACK_MINUTES)
    logger.info(
        "Lookback %s — cap %d jobs/source",
        format_lookback(_LOOKBACK_MINUTES),
        job_cap_for(_LOOKBACK_MINUTES),
    )
    conn = get_db()
    new_jobs: list[Job] = []

    # Search sources only: Jobright first, then Wellfound, then YC.
    # Greenhouse is the ATS we apply through — never a listing source.
    if _SOURCE_FILTER == "greenhouse":
        logger.warning(
            "Greenhouse is not a scrape source (it is the apply ATS). "
            "Use jobright, wellfound, ycjobs, or all."
        )
        return

    jobright_ready = False
    if _SOURCE_FILTER in ("all", "jobright"):
        jobright_ready = jobright.init_auth(_firecrawl)
        if not jobright_ready:
            logger.warning(
                "Jobright browser failed to start — Chrome/Edge must be installed. "
                "Continuing with other sources if this is an All run."
            )

    sources: list[tuple[types.ModuleType, str]] = []
    if _SOURCE_FILTER in ("all", "jobright") and jobright_ready:
        sources.append((jobright, "Jobright"))
    if _SOURCE_FILTER in ("all", "wellfound"):
        sources.append((wellfound, "Wellfound"))
    if _SOURCE_FILTER in ("all", "ycjobs"):
        sources.append((yc_jobs, "YC Jobs"))
    if _SOURCE_FILTER not in ("all", "jobright", "wellfound", "ycjobs"):
        logger.warning(f"Unknown source filter '{_SOURCE_FILTER}', skipping")

    try:
        for source_module, source_name in sources:
            logger.info(f"--- {source_name} ---")
            try:
                _collect_from_source(source_module, source_name, conn, new_jobs)
            except Exception as e:
                logger.error(f"{source_name} crashed: {e}", exc_info=True)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        if new_jobs:
            out_file = save_batch(new_jobs)
            logger.info(
                f"=== Done in {elapsed:.0f}s — {len(new_jobs)} new jobs → {out_file.name} ==="
            )
        else:
            logger.info(f"=== Done in {elapsed:.0f}s — no new jobs this cycle ===")
    finally:
        jobright.close_session()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    global _SOURCE_FILTER, _LOOKBACK_MINUTES
    if "--once" in sys.argv:
        # Optional: --source jobright|wellfound|yc (defaults to all)
        source_filter = next(
            (sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--source" and i + 1 < len(sys.argv)),
            "all",
        )
        lookback_raw = next(
            (
                sys.argv[i + 1]
                for i, a in enumerate(sys.argv)
                if a == "--lookback-minutes" and i + 1 < len(sys.argv)
            ),
            "60",
        )
        _SOURCE_FILTER = source_filter
        _LOOKBACK_MINUTES = coerce_lookback(lookback_raw)
        run_collection()
        return

    scheduler = BlockingScheduler(timezone="America/Chicago")
    # Every even hour CST: 6 AM, 8 AM, 10 AM … 4 AM (12 windows, full 24 h coverage)
    scheduler.add_job(
        run_collection,
        CronTrigger(hour="*/2", minute=0),
        id="job_collection",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    logger.info("Job scraper started — every 2 h CST. Running first cycle now…")
    run_collection()  # fire immediately on startup

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
