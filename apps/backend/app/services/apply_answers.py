"""Reusable apply answers: personal fields, learned questions, resume-derived text.

Every application asks the same core questions (name, phone, work auth, LinkedIn,
"how did you hear", signature). We answer them from the vault first, then from
the tailored resume, then from answers learned on earlier forms.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VAULT_PATH = Path(__file__).parents[2] / "data" / "vault.json"
_SKIP_REMEMBER = {
    "resume",
    "site",
    "note",
    "auth note",
    "first_name",
    "last_name",
    "email",
    "phone",
    "location",
    "address",
}

_PUNCT = re.compile(r"[^a-z0-9]+")

# label substring → vault path (dotted) or a literal.
_ALIAS: list[tuple[list[str], str]] = [
    (["first name", "given name"], "personal.first_name"),
    (["last name", "family name", "surname"], "personal.last_name"),
    (["full name", "legal name", "your name"], "derived.full_name"),
    (["email", "e-mail"], "personal.email"),
    (["phone", "mobile", "telephone", "cell"], "personal.phone"),
    (["linkedin"], "personal.linkedin"),
    (["github"], "personal.github"),
    (["website", "portfolio", "personal site"], "personal.website"),
    (["city"], "personal.city"),
    (["state / province", "state/province", "province"], "personal.state"),
    (["zip", "postal"], "personal.zip"),
    (["country"], "personal.country"),
    (
        ["street", "address line", "mailing address", "physical address"],
        "personal.line1",
    ),
    (
        ["location (city", "current location", "where are you", "city, state"],
        "derived.location",
    ),
    (["signature", "type your name", "electronic signature"], "derived.full_name"),
    (
        [
            "authorized to work",
            "legally authorized",
            "eligible to work",
            "work in the united states",
        ],
        "derived.authorized",
    ),
    (
        ["sponsorship", "require an employment visa", "will you now or in the future"],
        "derived.sponsorship",
    ),
    (
        ["visa status", "work authorization status", "immigration"],
        "work_auth.visa_status",
    ),
    (
        ["how did you hear", "how did you find", "how you heard", "referred by"],
        "derived.how_heard",
    ),
    (["gender identity", "describe your gender", "gender"], "eeo.gender"),
    (["hispanic", "latino"], "derived.hispanic"),
    (["racial", "ethnic", "race/ethni", "african american", "black"], "eeo.ethnicity"),
    (["veteran", "armed forces", "military service"], "eeo.veteran_status"),
    (["disability", "chronic condition"], "eeo.disability"),
    (["sexual orientation"], "eeo.sexual_orientation"),
    (
        ["current company", "most recent employer", "employer name", "company name"],
        "derived.current_company",
    ),
    (
        ["current title", "job title", "most recent title", "position title"],
        "derived.current_title",
    ),
    (["start date", "from date"], "derived.start_date"),
    (["end date"], "derived.end_date"),
    (["degree", "highest education"], "derived.degree"),
    (["school", "university", "college", "institution"], "derived.school"),
    (
        [
            "tell us about a project",
            "describe a project",
            "relevant project",
            "project you",
        ],
        "derived.project",
    ),
    (
        ["tell us about yourself", "cover letter", "why do you want", "why this role"],
        "derived.summary",
    ),
    (["years of experience", "years experience"], "derived.years_experience"),
]


def normalize_question(text: str) -> str:
    raw = (text or "").lower()
    raw = raw.replace("*", " ").replace("required", " ")
    return " ".join(_PUNCT.sub(" ", raw).split())


def _dotted(mapping: dict[str, Any], path: str) -> str:
    cur: Any = mapping
    for part in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    if cur is None or isinstance(cur, (dict, list)):
        return ""
    if isinstance(cur, bool):
        return "Yes" if cur else "No"
    return str(cur).strip()


def _parse_years(years: str) -> tuple[str, str, bool]:
    text = (years or "").strip()
    if not text:
        return "", "", False
    current = bool(re.search(r"present|current|now", text, re.I))
    parts = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
    start = parts[0].strip() if parts else ""
    end = "" if current else (parts[1].strip() if len(parts) > 1 else "")
    return start, end, current


def _bullets(value: Any) -> list[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("description") or "").strip()
                if text:
                    out.append(text)
        return out
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def resume_derived(
    resume_data: dict[str, Any] | None, personal: dict[str, Any]
) -> dict[str, str]:
    """Turn the tailored resume into answers for project / employment questions."""
    data = resume_data if isinstance(resume_data, dict) else {}
    info = (
        data.get("personalInfo") if isinstance(data.get("personalInfo"), dict) else {}
    )
    work = (
        data.get("workExperience")
        if isinstance(data.get("workExperience"), list)
        else []
    )
    education = data.get("education") if isinstance(data.get("education"), list) else []
    projects = (
        data.get("personalProjects")
        if isinstance(data.get("personalProjects"), list)
        else []
    )

    first = personal.get("first_name") or ""
    last = personal.get("last_name") or ""
    full = f"{first} {last}".strip() or str(info.get("name") or "").strip()
    city = personal.get("city") or ""
    state = personal.get("state") or ""
    location = str(info.get("location") or "").strip() or ", ".join(
        p for p in (city, state) if p
    )

    current_company = current_title = start_date = end_date = ""
    current_role = False
    project_bits: list[str] = []
    if work:
        latest = work[0] if isinstance(work[0], dict) else {}
        current_company = str(latest.get("company") or "").strip()
        current_title = str(latest.get("title") or latest.get("position") or "").strip()
        start_date, end_date, current_role = _parse_years(
            str(latest.get("years") or "")
        )
        project_bits.extend(_bullets(latest.get("description"))[:3])
    for proj in projects[:2]:
        if not isinstance(proj, dict):
            continue
        title = str(proj.get("name") or proj.get("title") or "").strip()
        bits = _bullets(proj.get("description"))
        if title and bits:
            project_bits.append(f"{title}: {bits[0]}")
        elif bits:
            project_bits.extend(bits[:1])
        elif title:
            project_bits.append(title)

    school = degree = ""
    if education and isinstance(education[0], dict):
        school = str(
            education[0].get("institution") or education[0].get("school") or ""
        ).strip()
        degree = str(education[0].get("degree") or "").strip()

    years_exp = ""
    if len(work) >= 2:
        years_exp = str(max(len(work) * 2, 3))
    elif work:
        years_exp = "3"

    summary = str(data.get("summary") or "").strip()
    project = " ".join(project_bits)[:1200]

    return {
        "full_name": full,
        "location": location,
        "current_company": current_company,
        "current_title": current_title,
        "start_date": start_date,
        "end_date": end_date,
        "current_role": "Yes" if current_role else "No",
        "degree": degree,
        "school": school,
        "project": project,
        "summary": summary[:1200],
        "years_experience": years_exp,
        "how_heard": "Online job board",
        "authorized": "Yes",
        "sponsorship": "Yes",
        "hispanic": "No",
        "decline_eeo": "Decline to self-identify",
    }


def work_history(resume_data: dict[str, Any] | None) -> list[dict[str, str]]:
    data = resume_data if isinstance(resume_data, dict) else {}
    rows: list[dict[str, str]] = []
    work = (
        data.get("workExperience")
        if isinstance(data.get("workExperience"), list)
        else []
    )
    for item in work:
        if not isinstance(item, dict):
            continue
        start, end, current = _parse_years(str(item.get("years") or ""))
        rows.append(
            {
                "company": str(item.get("company") or "").strip(),
                "title": str(item.get("title") or item.get("position") or "").strip(),
                "start": start,
                "end": end,
                "current": "Yes" if current else "No",
                "location": str(item.get("location") or "").strip(),
            }
        )
    return rows


def _alias_answer(vault: dict[str, Any], question: str) -> str:
    derived = dict(vault.get("_derived") or {})
    for patterns, path in _ALIAS:
        if any(p in question for p in patterns):
            if path.startswith("derived."):
                value = derived.get(path.split(".", 1)[1], "")
            else:
                value = _dotted(vault, path)
            if value:
                return value
            if path.startswith("eeo."):
                # Empty EEO → decline rather than inventing demographics.
                return derived.get("decline_eeo") or "Decline to self-identify"
    return ""


def answer_for(vault: dict[str, Any], question: str) -> str:
    """Return the best stored / derived answer for a form label."""
    key = normalize_question(question)
    if not key:
        return ""
    learned = vault.get("answers") if isinstance(vault.get("answers"), dict) else {}
    if key in learned and str(learned[key]).strip():
        return str(learned[key]).strip()
    for stored_key, stored_val in learned.items():
        if (
            stored_key
            and (stored_key in key or key in stored_key)
            and str(stored_val).strip()
        ):
            return str(stored_val).strip()
    aliased = _alias_answer(vault, key)
    if aliased:
        return aliased
    # Required acknowledgements the applicant must check to submit.
    if any(
        p in key
        for p in (
            "i agree",
            "i have read",
            "i acknowledge",
            "i certify",
            "i identify",
            "terms and conditions",
            "privacy policy",
            "electronic signature",
            "family policy",
        )
    ):
        return "Yes"
    return ""


def attach_resume(
    vault: dict[str, Any], resume_data: dict[str, Any] | None
) -> dict[str, Any]:
    """Runtime-only: derived answers + work history. Not written back to disk."""
    out = dict(vault)
    personal = dict(out.get("personal") or {})
    derived = resume_derived(resume_data, personal)
    auth = out.get("work_auth") if isinstance(out.get("work_auth"), dict) else {}
    derived["authorized"] = "Yes" if auth.get("authorized_us", True) else "No"
    derived["sponsorship"] = "Yes" if auth.get("requires_sponsorship") else "No"
    std = (
        out.get("standard_answers")
        if isinstance(out.get("standard_answers"), dict)
        else {}
    )
    if std.get("how_did_you_hear"):
        derived["how_heard"] = str(std["how_did_you_hear"])
    out["_derived"] = derived
    out["_resume"] = resume_data or {}
    out["_work_history"] = work_history(resume_data)
    linkedin = personal.get("linkedin") or personal.get("linkedin_url") or ""
    github = personal.get("github") or personal.get("github_url") or ""
    personal["linkedin"] = linkedin
    personal["linkedin_url"] = linkedin
    personal["github"] = github
    personal["github_url"] = github
    personal["address_line1"] = (
        personal.get("address_line1") or personal.get("line1") or ""
    )
    out["personal"] = personal
    return out


def persist_filled_answers(filled: dict[str, str]) -> None:
    """Remember non-PII-path answers so the next form can reuse them."""
    if not filled:
        return
    try:
        vault = (
            json.loads(_VAULT_PATH.read_text(encoding="utf-8"))
            if _VAULT_PATH.exists()
            else {}
        )
    except Exception:
        vault = {}
    answers = dict(vault.get("answers") or {})
    changed = False
    for raw_key, raw_val in filled.items():
        key = normalize_question(str(raw_key))
        val = str(raw_val or "").strip()
        if not key or not val or key in _SKIP_REMEMBER:
            continue
        if (
            val.lower().endswith(".pdf")
            or "\\temp\\" in val.lower()
            or "/tmp" in val.lower()
        ):
            continue
        if answers.get(key) == val:
            continue
        answers[key] = val
        changed = True
    if not changed:
        return
    vault["answers"] = answers
    try:
        _VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _VAULT_PATH.write_text(json.dumps(vault, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not persist apply answers: %s", exc)


def merge_vault(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge vault updates without wiping learned answers or blanking secrets."""
    out = dict(current)
    for key, value in (incoming or {}).items():
        if key in {"gmail_app_password", "password", "session_cookie", "app_password"}:
            if value in (None, "", "********"):
                continue
        if (
            key == "answers"
            and isinstance(value, dict)
            and isinstance(out.get("answers"), dict)
        ):
            merged = dict(out["answers"])
            merged.update(value)
            out["answers"] = merged
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_vault(out[key], value)
        else:
            out[key] = value
    return out


def redact_vault(vault: dict[str, Any]) -> dict[str, Any]:
    """Return vault for the UI with secrets replaced by a set/empty flag."""
    out = json.loads(json.dumps(vault))  # cheap deepcopy of JSON-safe data
    creds = out.get("credentials") if isinstance(out.get("credentials"), dict) else {}
    gmail = creds.get("gmail_app_password") or out.get("gmail_app_password") or ""
    out["gmail_configured"] = bool(gmail)
    if isinstance(out.get("credentials"), dict):
        if out["credentials"].get("gmail_app_password"):
            out["credentials"]["gmail_app_password"] = "********"
        if out["credentials"].get("password"):
            out["credentials"]["password"] = "********"
    jobright = out.get("jobright") if isinstance(out.get("jobright"), dict) else {}
    out["jobright_configured"] = bool(
        jobright.get("session_cookie")
        or (jobright.get("email") and jobright.get("password"))
    )
    if isinstance(out.get("jobright"), dict):
        if out["jobright"].get("password"):
            out["jobright"]["password"] = "********"
        if out["jobright"].get("session_cookie"):
            out["jobright"]["session_cookie"] = "********"
    answers = out.get("answers") if isinstance(out.get("answers"), dict) else {}
    out["answers_count"] = len(answers)
    return out


def gmail_credentials(vault: dict[str, Any]) -> tuple[str, str]:
    personal = vault.get("personal") if isinstance(vault.get("personal"), dict) else {}
    creds = (
        vault.get("credentials") if isinstance(vault.get("credentials"), dict) else {}
    )
    email = str(creds.get("gmail") or personal.get("email") or "").strip()
    password = str(
        creds.get("gmail_app_password") or vault.get("gmail_app_password") or ""
    ).strip()
    if password == "********":
        password = ""
    return email, password
