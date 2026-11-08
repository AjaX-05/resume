"""Unit tests for apply-URL canonicalization and employer-URL extraction."""

import threading

from app.services.applier_service import ApplicationSession
from app.services.apply_urls import (
    canonical_apply_url,
    effective_apply_url,
    extract_employer_apply_url,
    extract_listed_apply_url,
    extract_mailto,
    is_original_employer_url,
    pick_original_job_post_url,
    titles_match,
    upsert_employer_apply_notes,
)
from app.services.pipeline_service import detect_ats


def test_canonical_careerpuck_to_greenhouse() -> None:
    raw = "https://app.careerpuck.com/job-board/lyft/job/8689127002?gh_jid=8689127002"
    assert canonical_apply_url(raw) == (
        "https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8689127002"
    )


def test_detect_ats_careerpuck_is_greenhouse() -> None:
    ats, supported = detect_ats(
        "https://app.careerpuck.com/job-board/lyft/job/8689127002?gh_jid=8689127002"
    )
    assert ats == "greenhouse"
    assert supported is True


def test_detect_ats_jobright_still_supported() -> None:
    ats, supported = detect_ats("https://jobright.ai/jobs/info/abc")
    assert ats == "jobright"
    assert supported is True


def test_detect_ats_workday_is_supported() -> None:
    ats, supported = detect_ats(
        "https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/job/x_R1"
    )
    assert ats == "workday"
    assert supported is True


def test_detect_ats_privateai_email() -> None:
    ats, supported = detect_ats("https://privateai.com/jobs/artificial-intelligence-engineer")
    assert ats == "email"
    assert supported is True


def test_extract_mailto_from_anchor() -> None:
    href = "mailto:admin@privateai.com?subject=Job%20application%20-%20PrivateAI"
    assert extract_mailto(href).startswith("mailto:admin@privateai.com")


def test_extract_listed_vs_employer() -> None:
    notes = (
        "Source: jobright | https://jobright.ai/jobs/info/abc\n"
        "Apply: https://jobright.ai/jobs/info/abc\n"
        "EmployerApply: https://job-boards.greenhouse.io/lyft/jobs/8689127002\n"
    )
    assert extract_listed_apply_url(notes) == "https://jobright.ai/jobs/info/abc"
    assert "token=8689127002" in extract_employer_apply_url(notes)
    assert "token=8689127002" in effective_apply_url(notes)


def test_upsert_employer_apply_notes_insert_and_replace() -> None:
    notes = "Apply: https://jobright.ai/jobs/info/abc\n"
    once = upsert_employer_apply_notes(notes, "https://example.myworkdayjobs.com/job/1")
    assert once.count("EmployerApply:") == 1
    twice = upsert_employer_apply_notes(once, "https://job-boards.greenhouse.io/lyft/jobs/9")
    assert twice.count("EmployerApply:") == 1
    assert "token=9" in twice
    assert "jobright.ai" in twice


def test_original_job_post_prefers_labeled_employer_link() -> None:
    labeled = [
        ("APPLY WITH AUTOFILL", "https://jobright.ai/jobs/info/abc"),
        ("Original Job Post", "https://boseallaboutme.wd503.myworkdayjobs.com/en-US/Bose_Careers/job/R29335"),
        ("Crunchbase", "https://www.crunchbase.com/organization/bose"),
    ]
    url = pick_original_job_post_url(labeled)
    assert "myworkdayjobs.com" in url
    assert is_original_employer_url(url)
    assert not is_original_employer_url("https://jobright.ai/jobs/info/abc")
    assert not is_original_employer_url("https://www.crunchbase.com/organization/bose")


def test_titles_match_allows_subtitle() -> None:
    assert titles_match("Machine Learning Engineer", "Machine Learning Engineer, Fulfillment")
    assert not titles_match("Machine Learning Engineer", "Account Executive Intern")


def test_lookup_greenhouse_uses_public_board(monkeypatch) -> None:
    from app.services import apply_urls

    def fake_json(url: str):
        if "boards-api.greenhouse.io/v1/boards/lyft/jobs" in url:
            return {
                "jobs": [
                    {
                        "id": 8689127002,
                        "title": "Machine Learning Engineer",
                        "absolute_url": (
                            "https://app.careerpuck.com/job-board/lyft/job/8689127002"
                            "?gh_jid=8689127002"
                        ),
                    }
                ]
            }
        return None

    monkeypatch.setattr(apply_urls, "fetch_json", fake_json)
    url = apply_urls.lookup_public_apply_url("Lyft", "Machine Learning Engineer")
    assert url == "https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8689127002"


def test_apply_session_approval_is_thread_safe() -> None:
    session = ApplicationSession("app-1", "https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=1")
    assert isinstance(session._approval_thread_event, threading.Event)
    assert session._approval_thread_event.is_set() is False
