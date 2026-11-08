from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SponsorshipStatus(str, Enum):
    AVAILABLE = "available"
    NOT_MENTIONED = "not_mentioned"
    AMBIGUOUS = "ambiguous"
    EXPLICITLY_NOT_AVAILABLE = "explicitly_not_available"


@dataclass
class Job:
    title: str
    company: str
    description: str
    apply_url: str
    source_url: str
    source: str

    skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)

    salary_text: str = ""
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    equity_text: str = ""

    location_text: str = ""
    is_remote: bool = False
    remote_states: list[str] = field(default_factory=list)

    sponsorship_status: SponsorshipStatus = SponsorshipStatus.NOT_MENTIONED
    sponsorship_evidence: str = ""

    posting_date: Optional[str] = None
    req_id: Optional[str] = None
    linkedin_contacts: list[str] = field(default_factory=list)

    first_seen_at: str = ""
    last_seen_at: str = ""
    content_hash: str = ""
    fingerprint: str = ""
