"""Deterministic verification of the 6 prompt+code improvements.

Each test runs in isolation with no LLM calls. If any assertion fires, we know
the production tailor flow will exhibit the regression. Run with:

    cd apps/backend && uv run pytest tests/unit/test_prompt_improvements.py -v
"""

from app.prompts.refinement import AI_PHRASE_BLACKLIST, AI_PHRASE_REPLACEMENTS
from app.prompts.templates import (
    _RULE_PROTECTED_FIELDS,
    _RULE_SUMMARY_MIRROR,
    _RULE_TECH_VOCAB_DISTRIBUTION,
    IMPROVE_RESUME_PROMPTS,
    IMPROVE_SCHEMA_EXAMPLE,
)
from app.routers.resumes import _title_blend_is_safe
from app.services.refiner import _force_jd_skills_into_array, _scrub_employer_leakage
from app.services.writing_quality import (
    _MIN_LAST_LINE_FILL_PCT,
    _last_line_fill_pct,
    _line_fill_advice,
    check_writing,
)

# =============================================================================
# #1 — Last-line fill rule
# =============================================================================


class TestLastLineFill:
    """A bullet of length N wraps to ceil(N/85) lines. The remainder on the
    final line, as a percentage of 85 chars, must be >= 40%."""

    def test_single_line_is_safe(self) -> None:
        """Bullets under line width have no orphan risk."""
        assert _last_line_fill_pct("A" * 50) == 100
        assert _last_line_fill_pct("A" * 84) == 100

    def test_orphan_detected(self) -> None:
        """100 chars = 1 full line + 15 orphan chars (17%) → below 40%."""
        text = "A" * 100
        pct = _last_line_fill_pct(text)
        assert pct < _MIN_LAST_LINE_FILL_PCT, f"100ch should be <40%, got {pct}%"

    def test_two_line_safe(self) -> None:
        """150 chars = 1 line + 65-char second line (76% fill)."""
        text = "A" * 150
        pct = _last_line_fill_pct(text)
        assert pct >= _MIN_LAST_LINE_FILL_PCT, f"150ch should be >=40%, got {pct}%"

    def test_three_line_orphan(self) -> None:
        """275 chars = 3 lines where the 3rd is only 20 chars (24%) → BAD."""
        text = "A" * 275
        pct = _last_line_fill_pct(text)
        assert pct < _MIN_LAST_LINE_FILL_PCT, f"275ch should be <40%, got {pct}%"

    def test_three_line_safe(self) -> None:
        """290 chars = 3 lines where the 3rd is 35 chars (41%) → OK."""
        text = "A" * 290
        pct = _last_line_fill_pct(text)
        assert pct >= _MIN_LAST_LINE_FILL_PCT

    def test_advice_includes_targets(self) -> None:
        """Repair advice must mention both an expand and shrink target so the LLM
        has concrete numbers to hit."""
        advice = _line_fill_advice("A" * 100)
        assert "expand" in advice.lower()
        assert "shrink" in advice.lower()
        assert "100" in advice  # current chars

    def test_rule_fires_in_check_writing(self) -> None:
        """check_writing must emit a HARD low_last_line_fill finding on orphans."""
        resume = {
            "summary": "A test summary.",
            "workExperience": [
                {
                    "id": 1,
                    "title": "Senior Engineer",
                    "description": [
                        # 100 chars: 1 line + 15 chars orphan = 17% → BAD
                        "Architected a distributed system for high-throughput messaging across teams and clusters.",
                        # 155 chars: well-fit two-liner
                        "Designed and shipped the authentication service handling 50M daily requests with 99.99 percent availability using Kubernetes and PostgreSQL today.",
                    ],
                    "descriptionStyles": ["bullet", "bullet"],
                }
            ],
        }
        findings = check_writing(resume, "en")
        orphan_findings = [f for f in findings if f.rule == "low_last_line_fill"]
        assert len(orphan_findings) >= 1, (
            "expected an orphan finding on the 100-char bullet"
        )
        assert all(f.severity == "hard" for f in orphan_findings)


# =============================================================================
# #2 — Title blending
# =============================================================================


class TestTitleBlending:
    def test_safe_blend_preserves_seniority(self) -> None:
        assert _title_blend_is_safe(
            "Senior AI Engineer", "Senior AI / Software Engineer"
        )
        assert _title_blend_is_safe("Lead ML Engineer", "Lead ML / Applied AI Engineer")

    def test_rejects_dropped_seniority(self) -> None:
        """Dropping Senior from the title must be rejected."""
        assert not _title_blend_is_safe("Senior AI Engineer", "AI / Software Engineer")
        assert not _title_blend_is_safe("Staff ML Engineer", "ML Engineer")

    def test_rejects_domain_swap(self) -> None:
        """Backend → Frontend is a domain swap, must be rejected."""
        assert not _title_blend_is_safe("Backend Engineer", "Frontend Engineer")

    def test_rejects_oversize(self) -> None:
        assert not _title_blend_is_safe("Senior AI Engineer", "A" * 200)

    def test_rejects_empty(self) -> None:
        assert not _title_blend_is_safe("Senior AI Engineer", "")

    def test_allows_blended_when_shared_noun(self) -> None:
        """Must share at least one meaningful noun."""
        assert _title_blend_is_safe("AI Engineer", "AI Software Engineer")
        assert _title_blend_is_safe("Data Scientist", "Data Scientist / ML Engineer")

    def test_schema_example_includes_title(self) -> None:
        """Prompt schema example must advertise personalInfo.title as an output."""
        assert "personalInfo" in IMPROVE_SCHEMA_EXAMPLE
        assert "title" in IMPROVE_SCHEMA_EXAMPLE

    def test_protected_fields_rule_allows_title(self) -> None:
        """The protected-fields rule must explicitly allow title edits."""
        assert "TITLE BLENDING" in _RULE_PROTECTED_FIELDS
        # Still protects name/email/phone
        assert "personalInfo.name" in _RULE_PROTECTED_FIELDS
        assert "personalInfo.email" in _RULE_PROTECTED_FIELDS


# =============================================================================
# #3 — Skills-array enforcement
# =============================================================================


class TestSkillsArrayEnforcement:
    def test_adds_missing_jd_skill_present_in_master(self) -> None:
        """When the JD requires Kubernetes and the master mentions Kubernetes
        but the tailored skills array doesn't, force-add it."""
        master = {
            "summary": "Engineer who deployed services on Kubernetes.",
            "additional": {"technicalSkills": ["Python", "AWS"]},
        }
        tailored = {
            "summary": "Engineer.",
            "additional": {"technicalSkills": ["Python"]},
        }
        jd = {"required_skills": ["Kubernetes"], "preferred_skills": []}
        out = _force_jd_skills_into_array(tailored, master, jd)
        skills = out["additional"]["technicalSkills"]
        assert "Kubernetes" in skills

    def test_preserves_existing_order(self) -> None:
        """Existing skills must stay in their original order."""
        master = {"summary": "Used Kubernetes."}
        tailored = {"additional": {"technicalSkills": ["Python", "AWS", "Docker"]}}
        out = _force_jd_skills_into_array(
            tailored, master, {"required_skills": ["Kubernetes"]}
        )
        skills = out["additional"]["technicalSkills"]
        assert skills[:3] == ["Python", "AWS", "Docker"]
        assert "Kubernetes" in skills

    def test_deduplicates_case_insensitive(self) -> None:
        """Should NOT add kubernetes when Kubernetes is already present."""
        master = {"summary": "Used kubernetes a lot."}
        tailored = {"additional": {"technicalSkills": ["Kubernetes"]}}
        out = _force_jd_skills_into_array(
            tailored, master, {"required_skills": ["kubernetes"]}
        )
        skills = out["additional"]["technicalSkills"]
        # Original casing preserved, no duplicate
        assert skills == ["Kubernetes"]

    def test_skips_skill_absent_everywhere(self) -> None:
        """Must NOT fabricate skills — if neither master nor tailored
        evidences COBOL, don't add it."""
        master = {"summary": "Python engineer."}
        tailored = {"additional": {"technicalSkills": ["Python"]}}
        out = _force_jd_skills_into_array(
            tailored, master, {"required_skills": ["COBOL"]}
        )
        assert "COBOL" not in out["additional"]["technicalSkills"]


# =============================================================================
# #4 — Softened AI-phrase blacklist
# =============================================================================


class TestBlacklist:
    def test_senior_power_verbs_allowed(self) -> None:
        """spearheaded and championed are senior-signal verbs and must NOT be
        on the hard blacklist or in the auto-replace map."""
        assert "spearheaded" not in AI_PHRASE_BLACKLIST
        assert "championed" not in AI_PHRASE_BLACKLIST
        assert "spearheaded" not in AI_PHRASE_REPLACEMENTS
        assert "championed" not in AI_PHRASE_REPLACEMENTS

    def test_corporate_filler_still_banned(self) -> None:
        """utilized, leveraged and other weak corporate speak stays blacklisted."""
        for banned in (
            "utilized",
            "leveraged",
            "synergy",
            "cutting-edge",
            "game-changing",
            "best-in-class",
            "pioneered",
        ):
            assert banned in AI_PHRASE_BLACKLIST, f"{banned} should stay banned"


# =============================================================================
# #5 — Summary mirroring + #6 bullet reorder
# =============================================================================


class TestSummaryMirroringAndReorder:
    def test_summary_rule_mentions_responsibilities(self) -> None:
        assert "key_responsibilities" in _RULE_SUMMARY_MIRROR
        assert "years of experience" in _RULE_SUMMARY_MIRROR

    def test_full_mode_includes_summary_mirror_and_reorder(self) -> None:
        """FULL variant must now carry the summary-mirror rule AND bullet reorder."""
        full = IMPROVE_RESUME_PROMPTS["full"]
        assert "SUMMARY MIRRORING" in full
        assert "BULLET REORDERING" in full

    def test_heavy_mode_includes_summary_mirror(self) -> None:
        heavy = IMPROVE_RESUME_PROMPTS["heavy"]
        assert "SUMMARY MIRRORING" in heavy

    def test_tech_vocab_rule_mandates_skills_coverage(self) -> None:
        assert "MANDATORY SKILLS ARRAY COVERAGE" in _RULE_TECH_VOCAB_DISTRIBUTION


# =============================================================================
# #7 — Employer context isolation (added after Atlassian/Rovo leakage)
# =============================================================================


class TestEmployerLeakageScrub:
    def test_scrubs_company_name_from_summary(self) -> None:
        """Atlassian's name should not appear in the candidate's summary."""
        tailored = {
            "summary": "Senior engineer building GenAI platforms at Atlassian scale.",
            "workExperience": [],
        }
        master = {"workExperience": []}
        jd = {"company": "Atlassian"}
        out = _scrub_employer_leakage(tailored, jd, master)
        assert "Atlassian" not in out["summary"]

    def test_scrubs_company_from_bullets(self) -> None:
        tailored = {
            "workExperience": [
                {
                    "description": [
                        "Built platforms similar to Atlassian GenAI stack.",
                        "Delivered 50M requests per day.",
                    ],
                }
            ],
        }
        master = {"workExperience": []}
        out = _scrub_employer_leakage(tailored, {"company": "Atlassian"}, master)
        assert "Atlassian" not in out["workExperience"][0]["description"][0]
        # Bullet without the company name stays unchanged
        assert (
            out["workExperience"][0]["description"][1]
            == "Delivered 50M requests per day."
        )

    def test_preserves_company_when_master_lists_it(self) -> None:
        """If the candidate ACTUALLY worked at the hiring company, keep refs."""
        tailored = {"summary": "Returning Atlassian engineer with 5 years."}
        master = {
            "workExperience": [{"company": "Atlassian", "description": ["prior role"]}]
        }
        out = _scrub_employer_leakage(tailored, {"company": "Atlassian"}, master)
        assert "Atlassian" in out["summary"]

    def test_no_company_in_jd_is_noop(self) -> None:
        """When JD has no company field, no scrubbing happens."""
        tailored = {"summary": "An engineer."}
        out = _scrub_employer_leakage(tailored, {}, {"workExperience": []})
        assert out["summary"] == "An engineer."

    def test_scrubs_proprietary_product_names(self) -> None:
        """Rovo, Jira, Confluence — anything in employer_mentions — gets stripped."""
        tailored = {
            "summary": "Built GenAI platforms analogous to Rovo for enterprise use.",
            "workExperience": [
                {"description": ["Integrated with Confluence and Jira workflows."]}
            ],
        }
        master = {"workExperience": []}
        jd = {
            "company": "Atlassian",
            "employer_mentions": ["Rovo", "Confluence", "Jira"],
        }
        out = _scrub_employer_leakage(tailored, jd, master)
        for forbidden in ("Rovo", "Confluence", "Jira", "Atlassian"):
            assert forbidden not in out["summary"], f"{forbidden} should be gone"
            assert forbidden not in out["workExperience"][0]["description"][0]

    def test_scrubs_team_names(self) -> None:
        """Internal team names in employer_mentions get stripped."""
        tailored = {
            "summary": "Joined the GenAI Platform team to build infrastructure.",
            "workExperience": [],
        }
        jd = {"employer_mentions": ["GenAI Platform team"]}
        out = _scrub_employer_leakage(tailored, jd, {"workExperience": []})
        assert "GenAI Platform team" not in out["summary"]

    def test_keeps_mention_when_master_references_it(self) -> None:
        """If the candidate's own resume uses 'Jira', keep it — it's their skill."""
        tailored = {"summary": "Senior engineer who built Jira plugins."}
        master = {
            "summary": "I used Jira daily",
            "workExperience": [],
        }
        jd = {"employer_mentions": ["Jira"]}
        out = _scrub_employer_leakage(tailored, jd, master)
        assert "Jira" in out["summary"]

    def test_handles_dangling_punctuation(self) -> None:
        """After stripping 'Rovo', dangling spaces/commas should be cleaned up."""
        tailored = {"summary": "Built GenAI platforms analogous to Rovo, and more."}
        jd = {"employer_mentions": ["Rovo"]}
        out = _scrub_employer_leakage(tailored, jd, {"workExperience": []})
        # No double space, no " ," pattern
        assert "  " not in out["summary"]
        assert " ," not in out["summary"]
        assert "Rovo" not in out["summary"]


class TestTitleBlendingRuleStrengthened:
    """After the Atlassian failure, we strengthened MAY→MUST and added
    an EMPLOYER CONTEXT ISOLATION rule to each tailor prompt variant."""

    def test_full_variant_mentions_required_blend(self) -> None:
        from app.prompts.templates import IMPROVE_RESUME_PROMPTS

        full = IMPROVE_RESUME_PROMPTS["full"]
        assert "REQUIRED output when JD title differs" in full
        assert "EMPLOYER CONTEXT ISOLATION" in full
