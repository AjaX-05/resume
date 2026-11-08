import html
import re
from typing import Optional

from pydantic import BaseModel, Field

import dedup
from job_meta import classify_seniority, parse_posted
from models import Job, SponsorshipStatus


# ---------------------------------------------------------------------------
# Firecrawl extraction schema — passed as JSON Schema to Firecrawl's LLM extract
# ---------------------------------------------------------------------------

class ExtractedJob(BaseModel):
    title: str = Field(description="Job title / position name")
    company: str = Field(description="Company name")
    description: str = Field(description="Full job description text")
    skills: list[str] = Field(default_factory=list, description="Required and preferred technical skills")
    responsibilities: list[str] = Field(default_factory=list, description="Key responsibilities or duties")
    salary_text: str = Field(default="", description="Salary range as written, e.g. '$180k–$260k/year'")
    equity_text: str = Field(default="", description="Equity or stock options text if mentioned")
    location_text: str = Field(default="", description="Location and remote info as written")
    sponsorship_text: str = Field(default="", description="Any text about visa sponsorship or work authorization")
    apply_url: str = Field(default="", description="Direct URL to apply for this job")
    posting_date: str = Field(default="", description="Posting or updated date if visible")
    req_id: str = Field(default="", description="Requisition or job ID if shown")


# Listing-page schema: extract multiple jobs at once from a search results page
class JobListingItem(BaseModel):
    title: str = Field(description="Job title")
    company: str = Field(description="Company name")
    url: str = Field(description="URL to the full job posting")
    salary_snippet: str = Field(default="", description="Any salary info shown on the listing")
    location: str = Field(default="", description="Location shown on the listing")


class ListingPageResult(BaseModel):
    jobs: list[JobListingItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sponsorship classifier
# ---------------------------------------------------------------------------

_NO_SPONSOR = [
    r"(we\s+do\s+not|cannot|unable\s+to|will\s+not)\s+(provide|offer|support|consider)?\s*(visa\s+)?sponsorship",
    r"no\s+visa\s+sponsorship",
    r"sponsorship\s+(is\s+)?not\s+(available|provided|offered)",
    r"must\s+be\s+(authorized|eligible)\s+to\s+work.{0,60}without\s+sponsorship",
    r"does\s+not\s+sponsor",
    r"not\s+eligible\s+for\s+sponsorship",
    r"we\s+are\s+not\s+able\s+to\s+sponsor",
    r"no\s+sponsorship\s+(will\s+be\s+)?provided",
]

_YES_SPONSOR = [
    r"(will|can|do)\s+(provide|offer|consider|support)\s*(visa\s+)?sponsorship",
    r"(h[-\s]?1b|visa)\s+sponsorship\s+(available|provided|offered|supported|considered|welcome)",
    r"h-?1b\s+sponsor\s+likely",
    r"open\s+to\s+sponsor",
    r"sponsorship\s+(is\s+)?available",
    r"we\s+sponsor\b",
    r"visa\s+transfer",
    r"h1b\s+transfer",
]


def classify_sponsorship(text: str) -> tuple[SponsorshipStatus, str]:
    lower = text.lower()
    for pat in _NO_SPONSOR:
        if m := re.search(pat, lower):
            return SponsorshipStatus.EXPLICITLY_NOT_AVAILABLE, m.group(0)
    for pat in _YES_SPONSOR:
        if m := re.search(pat, lower):
            return SponsorshipStatus.AVAILABLE, m.group(0)
    if re.search(r"\b(sponsor|visa|h[-\s]?1b|work\s+authoriz|immigration|ead|opt|cpt)\b", lower):
        return SponsorshipStatus.AMBIGUOUS, ""
    return SponsorshipStatus.NOT_MENTIONED, ""


# ---------------------------------------------------------------------------
# Salary parser
# ---------------------------------------------------------------------------

def parse_salary(salary_text: str) -> tuple[Optional[int], Optional[int]]:
    if not salary_text:
        return None, None
    text = salary_text.lower().replace(",", "")
    # find patterns like 180k, $180,000, 180000
    nums = re.findall(r"\$?([\d]+)(\s*k)?", text)
    values: list[int] = []
    for num, k in nums:
        v = int(num)
        if k or v < 1000:
            v *= 1000
        if 50_000 <= v <= 2_000_000:  # sanity range
            values.append(v)
    if not values:
        return None, None
    return min(values), max(values)


# ---------------------------------------------------------------------------
# Remote + US detector
# ---------------------------------------------------------------------------

def strip_markup(text: str) -> str:
    """Turn Greenhouse HTML (or messy markdown) into plain text for matching."""
    if not text:
        return ""
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.I)
    if re.search(r"</?[a-z][\s\S]*?>", cleaned, re.I):
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


_US_STATE_RE = re.compile(
    r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO"
    r"|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b"
)
_NON_US_LOC_RE = re.compile(
    r"\b(london|united kingdom|\buk\b|england|europe|canada|india|"
    r"singapore|sydney|australia|berlin|paris|dublin|apac|emea)\b",
    re.I,
)


def is_remote_us(location_text: str, description: str = "") -> tuple[bool, list[str]]:
    loc = strip_markup(location_text)
    loc_l = loc.lower()
    desc = strip_markup(description).lower()
    states = list(set(_US_STATE_RE.findall(loc.upper() + " " + desc.upper())))

    us_in_loc = bool(
        re.search(r"\b(united states|\bu\.s\.a?\.?\b)\b", loc_l)
    ) or bool(_US_STATE_RE.search(loc.upper()))
    if _NON_US_LOC_RE.search(loc) and not us_in_loc:
        return False, []

    # Trust the location field. Greenhouse boards put "Remote" / "Remote-Friendly"
    # there; scanning the full JD matches Anthropic boilerplate on office roles.
    if re.search(r"\bremote", loc_l):
        return True, states

    if re.search(
        r"\b(remote[- ]friendly,?\s+united states|"
        r"remote in the (us|u\.s\.?|united states)|"
        r"us remote|remote us|remote,?\s+united states|"
        r"work from home anywhere in the (us|united states)|"
        r"anywhere in the (us|u\.s\.?|united states))\b",
        desc,
    ):
        return True, states

    return False, []


# ---------------------------------------------------------------------------
# Convert ExtractedJob → Job
# ---------------------------------------------------------------------------

def to_job(extracted: ExtractedJob, source: str, source_url: str) -> Job:
    description = strip_markup(extracted.description) or extracted.description
    full_text = description + " " + extracted.sponsorship_text
    spons_status, spons_evidence = classify_sponsorship(full_text)
    salary_min, salary_max = parse_salary(extracted.salary_text)
    is_remote, remote_states = is_remote_us(extracted.location_text, description)

    apply = extracted.apply_url or source_url
    fp = dedup.make_fingerprint(extracted.company, extracted.title, apply)
    ch = dedup.make_content_hash(description)
    posted_iso, _ = parse_posted(extracted.posting_date or description[:800])

    return Job(
        title=extracted.title,
        company=extracted.company,
        description=description,
        skills=extracted.skills,
        responsibilities=extracted.responsibilities,
        salary_text=extracted.salary_text,
        salary_min=salary_min,
        salary_max=salary_max,
        equity_text=extracted.equity_text,
        location_text=extracted.location_text,
        is_remote=is_remote,
        remote_states=remote_states,
        sponsorship_status=spons_status,
        sponsorship_evidence=spons_evidence,
        apply_url=apply,
        source_url=source_url,
        source=source,
        posting_date=posted_iso,
        seniority=classify_seniority(extracted.title, description),
        req_id=extracted.req_id or None,
        fingerprint=fp,
        content_hash=ch,
    )
