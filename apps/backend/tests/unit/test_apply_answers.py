from app.services.apply_answers import (
    answer_for,
    attach_resume,
    merge_vault,
    normalize_question,
    redact_vault,
    resume_derived,
    work_history,
)

RESUME = {
    "personalInfo": {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "555-0100",
        "location": "Richardson, TX",
        "linkedin": "https://linkedin.com/in/ada",
    },
    "summary": "AI engineer who ships agentic systems.",
    "workExperience": [
        {
            "company": "NMDP",
            "title": "AI/ML Engineering Consultant",
            "years": "May 2026 - Present",
            "description": [
                "Built an HLA automation graph model.",
                "Shipped donor re-verification ML.",
            ],
        }
    ],
    "education": [
        {
            "institution": "UT Dallas",
            "degree": "MS Information Technology",
            "years": "Aug 2023 - May 2025",
        }
    ],
    "personalProjects": [
        {"name": "Archon", "description": ["Knowledge-graph extractor."]}
    ],
}


def test_normalize_question_strips_required_markers() -> None:
    assert normalize_question("LinkedIn Profile *") == "linkedin profile"
    assert normalize_question("How did you hear about this job?") == (
        "how did you hear about this job"
    )


def test_resume_derived_project_and_employment() -> None:
    derived = resume_derived(RESUME, {"first_name": "Ada", "last_name": "Lovelace"})
    assert derived["full_name"] == "Ada Lovelace"
    assert derived["current_company"] == "NMDP"
    assert "HLA automation" in derived["project"]
    assert derived["school"] == "UT Dallas"
    assert derived["current_role"] == "Yes"
    assert derived["start_date"].startswith("May")


def test_work_history_parses_present() -> None:
    rows = work_history(RESUME)
    assert rows[0]["company"] == "NMDP"
    assert rows[0]["current"] == "Yes"
    assert rows[0]["end"] == ""


def test_answer_for_prefers_learned_then_alias() -> None:
    vault = attach_resume(
        {
            "personal": {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "linkedin": "https://li/ada",
            },
            "work_auth": {"authorized_us": True, "requires_sponsorship": True},
            "answers": {"how did you hear about this job": "Jobright"},
            "eeo": {},
        },
        RESUME,
    )
    assert answer_for(vault, "How did you hear about this job?") == "Jobright"
    assert answer_for(vault, "LinkedIn Profile *") == "https://li/ada"
    assert answer_for(vault, "Signature") == "Ada Lovelace"
    assert (
        answer_for(vault, "Tell us about a project you led")
        == vault["_derived"]["project"]
    )
    assert (
        answer_for(vault, "Will you now or in the future require sponsorship?") == "Yes"
    )
    assert "decline" in answer_for(vault, "Gender *").lower()
    assert answer_for(vault, "I have read the Lyft Extended Family Policy") == "Yes"


def test_linkedin_alias_copied_to_linkedin_url() -> None:
    vault = attach_resume({"personal": {"linkedin": "https://li/ada"}}, None)
    assert vault["personal"]["linkedin_url"] == "https://li/ada"


def test_merge_vault_keeps_blank_secrets_and_answers() -> None:
    current = {
        "answers": {"linkedin": "https://li/ada"},
        "credentials": {"gmail_app_password": "real-secret"},
        "personal": {"email": "ada@example.com"},
    }
    merged = merge_vault(
        current,
        {
            "personal": {"phone": "555"},
            "credentials": {"gmail_app_password": "********"},
            "answers": {"how did you hear": "Jobright"},
        },
    )
    assert merged["credentials"]["gmail_app_password"] == "real-secret"
    assert merged["answers"]["linkedin"] == "https://li/ada"
    assert merged["answers"]["how did you hear"] == "Jobright"
    assert merged["personal"]["phone"] == "555"


def test_redact_vault_hides_secrets() -> None:
    redacted = redact_vault(
        {
            "credentials": {"gmail_app_password": "secret"},
            "jobright": {"email": "a@b.c", "password": "pw", "session_cookie": "abc"},
            "answers": {"linkedin": "x"},
        }
    )
    assert redacted["gmail_configured"] is True
    assert redacted["jobright_configured"] is True
    assert redacted["credentials"]["gmail_app_password"] == "********"
    assert redacted["answers_count"] == 1
