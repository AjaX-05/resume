"""Company+role fingerprints collapse 'Role @ Company' clutter into one job."""

from app.services.pipeline_service import _role_stem, card_fingerprint


def test_strips_company_from_role_title():
    assert card_fingerprint("Omnicom", "Senior AI Engineer @ Omnicom") == card_fingerprint(
        "Omnicom", "Senior AI Engineer"
    )


def test_at_separator_and_whitespace():
    assert card_fingerprint("Stripe", "Staff Engineer  at   Stripe") == "stripe::staff engineer"


def test_unknown_cards_are_not_collapsed():
    assert card_fingerprint("", "") == ""
    assert card_fingerprint(None, None) == ""


def test_different_roles_stay_distinct():
    assert card_fingerprint("Omnicom", "Senior AI Engineer") != card_fingerprint(
        "Omnicom", "Principal AI Engineer"
    )


def test_role_stem_matches_jd_to_clone_title():
    assert _role_stem(
        "Capital One",
        "AI Engineer 5 (Gen AI SDK: Agentic AI, Guardrails, Evaluation)",
    ) == _role_stem("Capital One", "AI Engineer 5 (Gen AI SDK) @ Capital One")
    assert _role_stem(
        "Cognitive Minds", "Salesforce AI Developer, Remote"
    ) == _role_stem("Cognitive Minds", "Salesforce AI Developer @ Cognitive Minds")
    assert _role_stem(
        "Planet Pharma", "AI/ML Scientist, Agent Platform (P4021)"
    ) == _role_stem("Planet Pharma", "AI/ML Scientist, Agent Platform @ Planet Pharma")
