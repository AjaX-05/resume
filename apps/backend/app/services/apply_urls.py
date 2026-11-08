"""Apply-URL helpers: extract, canonicalize, and look up employer ATS pages.

Jobright/Wellfound hide the real Greenhouse/Lever/Ashby/Workday URL behind a
login wall. We keep the aggregator link on the card for provenance and store
the employer URL separately so Auto-Apply can open a real form.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

ATS_HINTS = (
    "greenhouse.io",
    "grnh.se",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "jobs.gem.com",
    "dover.com",
    "rippling.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "myworkdayjobs.com",
    "icims.com",
    "careerpuck.com",
    "successfactors.com",
    "oraclecloud.com",
    "taleo.net",
    "jobvite.com",
    "brassring.com",
    "lanl.jobs",
    "careers.gene.com",
    "careers.planet-pharma.com",
    "privateai.com/jobs",
    "applytojob.com",
    "paylocity.com",
    "ultipro.com",
    "adp.com",
)

# Hosts that appear on Jobright pages but are never the employer posting.
_ORIGINAL_SKIP_HOSTS = (
    "jobright.ai",
    "wellfound.com",
    "crunchbase.com",
    "accounts.google.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

COMPANY_SLUGS: dict[str, list[str]] = {
    "lyft": ["lyft"],
    "cognitive minds": ["cognitiveminds", "cognitive-minds"],
    "capital one": ["capitalone", "capital-one"],
    "privateai": ["privateai", "private-ai"],
    "genentech": ["gene", "genentech", "roche"],
    "los alamos national laboratory": ["lanl", "losalamos"],
    "planet pharma": ["planetpharma", "planet-pharma"],
    "bose corporation": ["bose"],
    "genesys": ["genesys"],
    "omnicom": ["omnicom", "omnicomhealth", "omnicomgroup", "interpublic"],
}

EMPLOYER_APPLY_RE = re.compile(
    r"^\s*EmployerApply:\s*(https?://\S+)", re.MULTILINE | re.IGNORECASE
)
APPLY_RE = re.compile(r"^\s*Apply:\s*(https?://\S+)", re.MULTILINE)
CONTENT_APPLY_RE = re.compile(r"\*\*Apply:\*\*\s*(https?://\S+)")
MAILTO_RE = re.compile(r"mailto:[^\s\"'<>]+", re.IGNORECASE)


def fetch_text(url: str, timeout: int = 18) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> Any:
    try:
        return json.loads(fetch_text(url))
    except Exception:
        return None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def titles_match(role: str, candidate: str) -> bool:
    a, b = _norm(role), _norm(candidate)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    aw, bw = set(a.split()), set(b.split())
    return bool(aw) and len(aw & bw) / len(aw) >= 0.75


def company_slugs(company: str) -> list[str]:
    key = company.strip().lower()
    out = list(COMPANY_SLUGS.get(key, []))
    compact = re.sub(r"[^a-z0-9]+", "", key)
    dashed = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    for slug in (compact, dashed):
        if slug and slug not in out:
            out.append(slug)
    return out


def greenhouse_job_url(board: str, job_id: int | str) -> str:
    """Direct Greenhouse application form (avoids CareerPuck wrappers)."""
    return f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={job_id}"


def canonical_apply_url(url: str) -> str:
    """Normalize CareerPuck / Greenhouse wrappers to a fillable Greenhouse URL."""
    if not url:
        return url
    parsed = urllib.parse.urlparse(url.strip())
    host = parsed.netloc.lower()
    qs = urllib.parse.parse_qs(parsed.query)
    if "careerpuck.com" in host:
        gh = (qs.get("gh_jid") or [""])[0]
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        board = ""
        jid = gh
        if "job-board" in parts:
            i = parts.index("job-board")
            if i + 1 < len(parts):
                board = parts[i + 1]
        if "job" in parts:
            j = parts.index("job")
            if j + 1 < len(parts) and not jid:
                jid = parts[j + 1]
        if board and jid:
            return greenhouse_job_url(board, jid)
    if "greenhouse.io" in host:
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        # /lyft/jobs/8689127002  or /embed/job_app?for=lyft&token=...
        board = (qs.get("for") or [""])[0]
        token = (qs.get("token") or [""])[0]
        if "jobs" in parts:
            i = parts.index("jobs")
            if i + 1 < len(parts) and i > 0 and not token:
                board = board or parts[i - 1]
                token = parts[i + 1]
        if board and token and "/embed/job_app" not in parsed.path:
            return greenhouse_job_url(board, token)
    return url.strip()


def extract_mailto(text: str) -> str:
    """First mailto: href in a URL or HTML/markdown blob."""
    if not text:
        return ""
    m = MAILTO_RE.search(text)
    return urllib.parse.unquote(m.group(0)) if m else ""


def is_email_apply_url(url: str) -> bool:
    low = (url or "").lower()
    return low.startswith("mailto:") or "privateai.com/jobs" in low


def is_aggregator_url(url: str) -> bool:
    low = (url or "").lower()
    return "jobright.ai" in low or "wellfound.com/jobs" in low


def is_ats_url(url: str) -> bool:
    low = (url or "").lower()
    return any(hint in low for hint in ATS_HINTS) and not is_aggregator_url(url)


def is_original_employer_url(url: str) -> bool:
    """True for an employer career/ATS page — not Jobright, social, or Crunchbase."""
    if not url:
        return False
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if is_aggregator_url(url):
        return False
    if "linkedin.com" in host:
        return "/jobs/" in path or "/job/" in path
    return not any(skip in host for skip in _ORIGINAL_SKIP_HOSTS)


def pick_original_job_post_url(labeled_links: list[tuple[str, str]]) -> str:
    """Prefer the Jobright 'Original Job Post' control over Autofill / Apply Now."""
    for text, href in labeled_links:
        blob = f"{text or ''} {href or ''}".lower()
        if "original job post" not in blob and "original job" not in (text or "").lower():
            continue
        href = (href or "").strip()
        if href and (is_ats_url(href) or is_original_employer_url(href)):
            return canonical_apply_url(href)
    return ""


def extract_employer_apply_url(notes: str) -> str:
    if not notes:
        return ""
    m = EMPLOYER_APPLY_RE.search(notes)
    return canonical_apply_url(m.group(1).strip()) if m else ""


def extract_listed_apply_url(notes: str, job_content: str = "") -> str:
    """The Apply: line the scraper stored (often a Jobright aggregator URL)."""
    notes = notes or ""
    job_content = job_content or ""
    m = APPLY_RE.search(notes)
    if m:
        return canonical_apply_url(m.group(1).strip())
    m = CONTENT_APPLY_RE.search(job_content)
    if m:
        return canonical_apply_url(m.group(1).strip())
    return ""


def effective_apply_url(notes: str, job_content: str = "") -> str:
    """Employer ATS URL when we have one, otherwise the listed Apply: URL."""
    return extract_employer_apply_url(notes) or extract_listed_apply_url(notes, job_content)


def upsert_employer_apply_notes(notes: str, url: str) -> str:
    url = canonical_apply_url(url)
    if not url:
        return notes or ""
    notes = notes or ""
    line = f"EmployerApply: {url}"
    if EMPLOYER_APPLY_RE.search(notes):
        return EMPLOYER_APPLY_RE.sub(line, notes, count=1)
    return notes.rstrip() + ("\n" if notes.strip() else "") + line + "\n"


def lookup_public_apply_url(company: str, role: str) -> str:
    """Find a public employer apply URL from Greenhouse/Lever/Ashby/DuckDuckGo."""
    if not company or not role:
        return ""
    for finder in (_search_greenhouse, _search_lever, _search_ashby, _search_duckduckgo):
        try:
            found = finder(company, role)
        except Exception as exc:
            logger.debug("%s lookup failed for %s / %s: %s", finder.__name__, company, role, exc)
            found = ""
        if found:
            return canonical_apply_url(found)
    return ""


def _search_greenhouse(company: str, role: str) -> str:
    for slug in company_slugs(company):
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if not isinstance(data, dict):
            continue
        for job in data.get("jobs") or []:
            title = str(job.get("title") or "")
            if not titles_match(role, title):
                continue
            jid = job.get("id")
            abs_url = str(job.get("absolute_url") or "")
            if is_ats_url(abs_url) and "careerpuck.com" not in abs_url.lower():
                return abs_url.split("?")[0]
            if jid:
                return greenhouse_job_url(slug, jid)
            if abs_url:
                return abs_url
    return ""


def _search_lever(company: str, role: str) -> str:
    for slug in company_slugs(company):
        data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if not isinstance(data, list):
            continue
        for job in data:
            title = str(job.get("text") or job.get("title") or "")
            if not titles_match(role, title):
                continue
            hosted = str(job.get("hostedUrl") or job.get("applyUrl") or "")
            if hosted:
                return hosted.split("?")[0]
    return ""


def _search_ashby(company: str, role: str) -> str:
    for slug in company_slugs(company):
        data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        jobs: list[Any] = []
        if isinstance(data, dict):
            raw = data.get("jobs") or data.get("postings") or []
            if isinstance(raw, list):
                jobs = raw
        for job in jobs:
            title = str(job.get("title") or job.get("jobTitle") or "")
            if not titles_match(role, title):
                continue
            url = str(job.get("jobUrl") or job.get("applyUrl") or "")
            if url:
                return url.split("?")[0]
    return ""


def _search_duckduckgo(company: str, role: str) -> str:
    q = f'"{role}" "{company}" apply'
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
    html = fetch_text(url)
    hrefs = re.findall(r'href="([^"]+)"', html)
    decoded: list[str] = []
    for href in hrefs:
        if "uddg=" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            real = parsed.get("uddg", [""])[0]
            if real:
                decoded.append(urllib.parse.unquote(real))
        elif href.startswith("http"):
            decoded.append(href)
    for href in decoded:
        if is_ats_url(href):
            return href
    return ""
