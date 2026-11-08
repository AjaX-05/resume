"""LLM prompt templates for resume processing."""

# Language code to full name mapping
LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "pt": "Brazilian Portuguese",
    "fr": "French",
    "ko": "Korean",
}


def get_language_name(code: str) -> str:
    """Get full language name from code."""
    return LANGUAGE_NAMES.get(code, "English")


# Schema with example values - used for prompts to show LLM expected format
RESUME_SCHEMA_EXAMPLE = """{
  "personalInfo": {
    "name": "John Doe",
    "title": "Software Engineer",
    "email": "john@example.com",
    "phone": "+1-555-0100",
    "location": "San Francisco, CA",
    "website": "https://johndoe.dev",
    "linkedin": "linkedin.com/in/johndoe",
    "github": "github.com/johndoe"
  },
  "summary": "Experienced software engineer with 5+ years...",
  "workExperience": [
    {
      "id": 1,
      "title": "Senior Software Engineer",
      "company": "Tech Corp",
      "location": "San Francisco, CA",
      "years": "Jan 2020 - Present",
      "description": [
        "Led development of microservices architecture",
        "Improved system performance by 40%"
      ],
      "descriptionStyles": ["bullet", "bullet"]
    }
  ],
  "education": [
    {
      "id": 1,
      "institution": "University of California",
      "degree": "B.S. Computer Science",
      "years": "2014 - 2018",
      "description": "Graduated with honors"
    }
  ],
  "personalProjects": [
    {
      "id": 1,
      "name": "Open Source Tool",
      "role": "Creator & Maintainer",
      "years": "Mar 2021 - Present",
      "description": [
        "Built CLI tool with 1000+ GitHub stars",
        "Used by 50+ companies worldwide"
      ],
      "descriptionStyles": ["bullet", "bullet"]
    }
  ],
  "additional": {
    "technicalSkills": ["Python", "JavaScript", "AWS", "Docker"],
    "languages": ["English (Native)", "Spanish (Conversational)"],
    "certificationsTraining": ["AWS Solutions Architect"],
    "awards": ["Employee of the Year 2022"]
  },
  "customSections": {
    "publications": {
      "sectionType": "itemList",
      "items": [
        {
          "id": 1,
          "title": "Paper Title",
          "subtitle": "Journal Name",
          "years": "Jun 2023",
          "description": ["Brief description of the publication"],
          "descriptionStyles": ["bullet"]
        }
      ]
    },
    "volunteer_work": {
      "sectionType": "text",
      "text": "Description of volunteer activities..."
    }
  }
}"""

# Schema for improve prompts - personalInfo.title is the ONLY personalInfo field
# the LLM may emit (per PERSONALINFO.TITLE BLENDING rule). All other personalInfo
# fields are automatically preserved from the master resume.
IMPROVE_SCHEMA_EXAMPLE = """{
  "personalInfo": {
    "title": "Senior AI / Software Engineer"
  },
  "summary": "Experienced software engineer with 5+ years...",
  "workExperience": [
    {
      "id": 1,
      "title": "Senior Software Engineer",
      "company": "Tech Corp",
      "location": "San Francisco, CA",
      "years": "Jan 2020 - Present",
      "description": [
        "Led development of microservices architecture",
        "Improved system performance by 40%"
      ],
      "descriptionStyles": ["bullet", "bullet"]
    }
  ],
  "education": [
    {
      "id": 1,
      "institution": "University of California",
      "degree": "B.S. Computer Science",
      "years": "2014 - 2018",
      "description": "Graduated with honors"
    }
  ],
  "personalProjects": [
    {
      "id": 1,
      "name": "Open Source Tool",
      "role": "Creator & Maintainer",
      "years": "Mar 2021 - Present",
      "description": [
        "Built CLI tool with 1000+ GitHub stars",
        "Used by 50+ companies worldwide"
      ],
      "descriptionStyles": ["bullet", "bullet"]
    }
  ],
  "additional": {
    "technicalSkills": ["Python", "JavaScript", "AWS", "Docker"],
    "languages": ["English (Native)", "Spanish (Conversational)"],
    "certificationsTraining": ["AWS Solutions Architect"],
    "awards": ["Employee of the Year 2022"]
  },
  "customSections": {
    "publications": {
      "sectionType": "itemList",
      "items": [
        {
          "id": 1,
          "title": "Paper Title",
          "subtitle": "Journal Name",
          "years": "Jun 2023",
          "description": ["Brief description of the publication"],
          "descriptionStyles": ["bullet"]
        }
      ]
    },
    "volunteer_work": {
      "sectionType": "text",
      "text": "Description of volunteer activities..."
    }
  }
}"""

PARSE_RESUME_PROMPT = """Parse this resume into JSON. Output ONLY the JSON object, no other text.

Map content to standard sections when possible. For non-standard sections (like Publications, Volunteer Work, Research, Hobbies), add them to customSections with an appropriate type.

Example output format:
{schema}

Custom section types:
- "text": Single text block (e.g., objective, statement)
- "itemList": List of items with title, subtitle, years, description (e.g., publications, research)
- "stringList": Simple list of strings (e.g., hobbies, interests)

Rules:
- Use "" for missing text fields, [] for missing arrays, null for optional fields
- Number IDs starting from 1
- For workExperience, personalProjects, and custom itemList items, include descriptionStyles with one "bullet" value for each description row. ALWAYS use "bullet" — never "plain". Every description row renders as a bulleted item. Sub-project headings (e.g., "Project X:") must be written as the first phrase of the bullet, not as a separate plain row.
- Every description bullet must fill at least two-thirds of the line (roughly 90 characters or more at default font size, up to about 35 words). Do not produce short one-fragment bullets that leave a half-empty row. If a bullet is naturally short, expand it with the scope, tools, or outcome already present in the source rather than inventing new facts.
- Format dates preserving the original precision. Keep months when present: "Jan 2020 - Dec 2023", "May 2021 - Present". Use "YYYY - YYYY" only when the source has no months.
- Use snake_case for custom section keys (e.g., "volunteer_work", "publications")
- Preserve the original section name as a descriptive key
- Normalize date separators: "2020-2021" → "2020 - 2021", "Current"/"Ongoing" → "Present". Do NOT discard months.
- For ambiguous dates like "3 years experience", infer approximate years from context or use "~YYYY"
- Flag overlapping dates (concurrent roles) by preserving both, don't merge
- Copy every skill, tool, framework, programming language and technology the resume lists into additional.technicalSkills, one item each, keeping the original wording. Split comma-separated lists into separate items. Never summarize, merge, shorten or drop skills, however long the list is. Spoken languages (English, Hindi, ...) go in additional.languages, not technicalSkills.

Resume to parse:
{resume_text}"""

EXTRACT_KEYWORDS_PROMPT = """Extract job requirements as JSON. Output ONLY the JSON object, no other text.

Example format:
{{
  "company": "Acme Corp",
  "role": "Senior Backend Engineer",
  "required_skills": ["Python", "AWS"],
  "preferred_skills": ["Kubernetes"],
  "experience_requirements": ["5+ years"],
  "education_requirements": ["Bachelor's in CS"],
  "key_responsibilities": ["Lead team"],
  "keywords": ["microservices", "agile"],
  "experience_years": 5,
  "seniority_level": "senior",
  "employer_mentions": ["Rovo", "GenAI Platform team", "Acme Marketplace"]
}}

Extract numeric years (e.g., "5+ years" → 5) and infer seniority level.
Set "company" to the hiring company name and "role" to the job title exactly as
written in the posting; use an empty string for either if it is not stated.

Set "employer_mentions" to the list of proprietary product names, internal team
names, codenames, and platform names that are UNIQUE to the hiring company and
should NEVER appear in a candidate's tailored resume. Include things like:
proprietary product names ("Rovo", "Confluence", "Jira", "Figma"), internal
team names ("GenAI Platform team", "Core Infrastructure"), codenames, and any
org-specific platform the JD mentions as the role's workplace. Do NOT include
public technology names a candidate could legitimately use elsewhere ("AWS
Bedrock", "Kubernetes", "LangChain", "PostgreSQL"). Also do NOT include the
hiring company's own name — it's already captured in "company". Use an empty
array if the JD does not mention any proprietary names.

Job description:
{job_description}"""

CRITICAL_TRUTHFULNESS_RULES_TEMPLATE = """CRITICAL TRUTHFULNESS RULES - NEVER VIOLATE:
1. DO NOT add any skill, tool, technology, or certification that is not explicitly mentioned in the original resume
2. DO NOT invent numeric achievements (e.g., "increased by 30%") unless they exist in original
3. DO NOT add company names, product names, or technical terms not in the original
4. DO NOT upgrade experience level (e.g., "Junior" -> "Senior")
5. DO NOT add languages, frameworks, or platforms the candidate hasn't used
6. DO NOT extend employment dates or change timelines. Copy date ranges exactly as they appear, including months.
7. {rule_7}
8. Preserve factual accuracy - only use information provided by the candidate
9. NEVER remove existing skills, certifications, languages, or awards. You may reorder by relevance, but every original item must remain.

Violation of these rules could cause serious problems for the candidate in job interviews.
"""


def _build_truthfulness_rules(rule_7: str) -> str:
    return CRITICAL_TRUTHFULNESS_RULES_TEMPLATE.format(rule_7=rule_7)


CRITICAL_TRUTHFULNESS_RULES = {
    "nudge": _build_truthfulness_rules(
        "DO NOT add new bullet points or content - only rephrase existing content"
    ),
    "keywords": _build_truthfulness_rules(
        "You may rephrase existing bullet points to include keywords, but do NOT add new bullet points"
    ),
    "full": _build_truthfulness_rules(
        "You may expand existing bullet points or add new ones that elaborate on existing work, but DO NOT invent entirely new responsibilities"
    ),
    "heavy": _build_truthfulness_rules(
        "You may restructure every bullet point, rewrite the summary and reorder bullets within a role, but DO NOT invent responsibilities, tools, metrics or outcomes"
    ),
}

# =====================================================================
# SHARED RULE BLOCKS
# Each constant below is one rule line (no leading or trailing newline).
# They are joined with "\n" into the Rules: section of each tailoring
# prompt. The compiled prompt text sent to the LLM is byte-identical to
# the previous inline version — this is pure deduplication so a single
# source of truth replaces four copy-pasted copies.
# =====================================================================

_RULE_TECH_VOCAB_DISTRIBUTION = """- TECHNICAL VOCABULARY DISTRIBUTION (treat as real experience the candidate holds but did not write down in the master): DISTRIBUTE JD technologies — skills, tools, frameworks, languages, cloud services, libraries, protocols, APIs — NATURALLY across the resume. Order of placement: (1) personalInfo.title may carry 1-2 of the JD's defining technology or role nouns in the headline per the TITLE BLENDING rule; (2) summary weaves 3-5 key JD technologies into the positioning statement; (3) workExperience bullets rephrase to name the JD technology when it plausibly aligns with what the bullet already describes (e.g., "deployed microservices" → "deployed microservices on Kubernetes with Terraform-managed infrastructure"); (4) personalProjects follows the same bullet rule. (5) MANDATORY SKILLS ARRAY COVERAGE: `additional.technicalSkills` MUST contain EVERY `required_skill` AND every `preferred_skill` from the JD keywords, verbatim with JD capitalization. This is the single biggest ATS keyword-match lever — do NOT omit any skill from the array even if you also wove it into bullets. If a JD skill is not evidenced anywhere in the master resume, still add it to the skills array ONLY when it belongs to the same technology family as something the candidate demonstrably knows (e.g., add "GCP" if master has "AWS, Azure"; do NOT add "COBOL" if master is a Python engineer). CHRONOLOGY CHECK before placing a technology in a workExperience or personalProjects bullet: the technology must have been publicly available during that role's date range. Do NOT reference a 2024-era technology (e.g., LangGraph Jan 2024, Model Context Protocol Nov 2024, Claude 3 Mar 2024, GPT-4 Mar 2023) in a role that ended before it existed; keep such a technology in Skills only, or place it in the current/most-recent role. Deduplicate case-insensitively, keep JD capitalization, skip generic non-skill phrases."""

_RULE_PROTECTED_FIELDS = """- PROTECTED FIELDS (never alter based on the JD; keep master's values exactly): personalInfo.name, personalInfo.email, personalInfo.phone, personalInfo.location, personalInfo.website, personalInfo.linkedin, personalInfo.github, company names, employment dates, locations, education entries, additional.certificationsTraining, additional.awards, and all specific metrics/numbers/outcomes. Never add a certification, award, employer, degree, or quantified achievement the master does not already contain.
- PERSONALINFO.TITLE BLENDING (REQUIRED output when JD title differs from master title): You MUST rewrite personalInfo.title to blend the master's role identity with the JD's role noun when the JD title adds a new technology, domain, or role noun not already in the first segment of the master title. This is the single biggest ATS title_match lever — a mismatched title drops the ATS title_match sub-score to ~25%. Rules: (a) ALWAYS preserve the master's seniority prefix exactly — Senior, Lead, Staff, Principal, Architect — never drop it and never add a seniority the master does not have. (b) Take ONLY the FIRST SEGMENT of the master title (everything before the first "|" or ","); drop the rest of the headline for the blend. Add 1-2 nouns from the JD title that are compatible with the master's domain, using "/" or " " to join. Example REQUIRED blends you MUST produce: master "Senior AI Engineer | Enterprise AI Architecture | ..." + JD "Senior ML System Engineer" → "Senior AI / ML System Engineer"; master "AI Engineer" + JD "Software Engineer" → "AI / Software Engineer"; master "Senior ML Engineer" + JD "Applied AI Scientist" → "Senior ML / Applied AI Engineer"; master "Senior AI Engineer" + JD "Senior Backend Engineer" → "Senior AI / Backend Engineer". (c) NEVER swap incompatible domains — do NOT turn "Backend Engineer" into "Frontend Engineer" or "Data Engineer" into "iOS Engineer". When domains clash, keep the master's first segment exactly. (d) Keep the final title under 60 chars; strip pipe-separated multi-tag headlines from the master before blending. (e) The title must still be a true, defensible description of what the candidate can do based on the master resume. (f) If the JD title is IDENTICAL to the master's first segment, keep the master's first segment exactly.
- EMPLOYER CONTEXT ISOLATION (never leak into candidate's resume): The JD contains the hiring company's name, product names, client names, internal team names, project codenames, and HR phrasing. NONE of these may appear in the candidate's resume — not in summary, bullets, title, or skills. They describe the EMPLOYER, not the candidate's experience. Specifically forbidden: the hiring company's name (e.g., "Atlassian"), the hiring company's product names (e.g., "Rovo", "Jira", "Confluence"), the hiring company's internal team names (e.g., "GenAI Platform team"), and any product/platform mentioned ONLY in the JD. If a technology name is both a JD-mentioned product AND a well-known public tool the candidate has demonstrably used (e.g., "AWS Bedrock", "Kubernetes", "LangChain"), it IS allowed. Rule of thumb: if the term is capitalized, appears only in the JD, and names something the hiring company builds or sells, do not use it."""

_RULE_DESCRIPTION_STYLES = """- Preserve descriptionStyles arrays and keep them aligned one-to-one with description arrays. Every entry MUST be "bullet" — never "plain". Rewrite any "plain" row as a bulleted sentence starting with its project or topic label (e.g., "Project X: ...")."""

_RULE_BULLET_LENGTH = """- Every rewritten bullet must fill at least two-thirds of its printed row (about 90 characters or more, up to roughly 35 words). Avoid short one-fragment bullets that leave half-empty rows. If a source bullet is short, expand it only with scope, tools, or outcomes already in the source — never invent facts."""

_RULE_CUSTOM_SECTIONS = """- For customSections: preserve exact structure, item count, titles, subtitles, and years. If an item's description is an empty array [] in the original, keep it empty []. Do NOT generate descriptions for items that had none."""

_RULE_YEARS_FIELD = """- Copy the "years" field values EXACTLY as they appear in the original resume (including any month prefixes like "Jan 2020 - Present"). Do not shorten, reformat, or drop months."""

_RULE_EM_DASH = """- Do NOT use em dash ("—") anywhere in the writing/output, even if it exists, remove it"""

_RULE_SUMMARY_MIRROR = """- SUMMARY MIRRORING: Rewrite the summary as a 2-4 sentence senior-level positioning statement that mirrors the JD. First sentence MUST open with role identity + total years of experience + the single most relevant domain word from the JD title (e.g., "Senior AI/ML Engineer with 7+ years building production ML systems"). Second sentence MUST name at least 3 of the JD's key_responsibilities, phrased as capabilities the candidate has demonstrated per the master resume (e.g., if JD asks for "building RAG pipelines", "deploying to Kubernetes", "mentoring teams" — surface those exact capabilities when the master evidences them). Third sentence weaves in 3-5 JD technologies from required_skills. Keep the summary under 500 characters and never invent experience. If the master evidence doesn't support a JD responsibility, skip it rather than fabricate. The summary MUST NOT mention the hiring company's name, product names, codenames, or internal team names from the JD (per the EMPLOYER CONTEXT ISOLATION rule) — do NOT write "building GenAI platforms analogous to Rovo", "working on Jira-style systems", or similar comparisons that pin the candidate to the employer's products."""


def _build_tailor_prompt(opening: str, specific_rules: list[str], placeholder_block: str) -> str:
    """Assemble a tailoring prompt from opening + ordered Rules + placeholder block.

    opening: everything before the "Rules:" line (task description, truthfulness
    rules placeholder, output-language note, personalInfo note).
    specific_rules: the ordered list of rule lines for this specific strategy —
    each already starts with "- ". Shared rule constants above are plain
    strings and may be inserted directly into this list.
    placeholder_block: the trailing block with {job_description}, {job_keywords},
    {original_resume}, {schema} placeholders — unchanged from the inline version.
    """
    rules_block = "\n".join(specific_rules)
    return f"""{opening}

Rules:
{rules_block}

{placeholder_block}"""


_TAILOR_OPENING_NUDGE = """Lightly nudge this resume toward the job description. Output ONLY the JSON object, no other text.

{critical_truthfulness_rules}

IMPORTANT: Generate ALL text content (summary, descriptions, skills) in {output_language}.
You MUST output personalInfo with the single field personalInfo.title, blended per the PERSONALINFO.TITLE BLENDING rule below. Do NOT include any other personalInfo field — all other personalInfo fields are preserved from the original resume automatically."""

_TAILOR_OPENING_KEYWORDS = """Enhance this resume with relevant keywords from the job description. Output ONLY the JSON object, no other text.

{critical_truthfulness_rules}

IMPORTANT: Generate ALL text content (summary, descriptions, skills) in {output_language}.
You MUST output personalInfo with the single field personalInfo.title, blended per the PERSONALINFO.TITLE BLENDING rule below. Do NOT include any other personalInfo field — all other personalInfo fields are preserved from the original resume automatically."""

_TAILOR_OPENING_FULL = """Tailor this resume for the job. Output ONLY the JSON object, no other text.

{critical_truthfulness_rules}

IMPORTANT: Generate ALL text content (summary, descriptions, skills) in {output_language}.
You MUST output personalInfo with the single field personalInfo.title, blended per the PERSONALINFO.TITLE BLENDING rule below. Do NOT include any other personalInfo field — all other personalInfo fields are preserved from the original resume automatically."""

_TAILOR_OPENING_HEAVY = """Rewrite this resume for the job. Output ONLY the JSON object, no other text.

{critical_truthfulness_rules}

IMPORTANT: Generate ALL text content (summary, descriptions, skills) in {output_language}.
You MUST output personalInfo with the single field personalInfo.title, blended per the PERSONALINFO.TITLE BLENDING rule below. Do NOT include any other personalInfo field — all other personalInfo fields are preserved from the original resume automatically."""

_TAILOR_KEYWORDS_PLACEHOLDER_NUDGE = """Job Description:
{job_description}

Keywords to emphasize (only if already supported by resume content):
{job_keywords}

Original Resume:
{original_resume}

Output in this JSON format:
{schema}"""

_TAILOR_KEYWORDS_PLACEHOLDER_STANDARD = """Job Description:
{job_description}

Keywords to emphasize:
{job_keywords}

Original Resume:
{original_resume}

Output in this JSON format:
{schema}"""


IMPROVE_RESUME_PROMPT_NUDGE = _build_tailor_prompt(
    opening=_TAILOR_OPENING_NUDGE,
    specific_rules=[
        "- Make minimal, conservative edits only where there is a clear existing match",
        "- Do NOT change the candidate's role, industry, or seniority level",
        "- Do NOT introduce new tools, technologies, or certifications not already present",
        _RULE_TECH_VOCAB_DISTRIBUTION,
        _RULE_PROTECTED_FIELDS,
        "- Do NOT add new bullet points or sections",
        "- Preserve original bullet count and ordering within each section",
        _RULE_DESCRIPTION_STYLES,
        _RULE_BULLET_LENGTH,
        "- Keep proper nouns (names, company names, locations) unchanged",
        _RULE_CUSTOM_SECTIONS,
        _RULE_YEARS_FIELD,
        "- If the resume is non-technical, do NOT add technical jargon",
        _RULE_EM_DASH,
    ],
    placeholder_block=_TAILOR_KEYWORDS_PLACEHOLDER_NUDGE,
)

IMPROVE_RESUME_PROMPT_KEYWORDS = _build_tailor_prompt(
    opening=_TAILOR_OPENING_KEYWORDS,
    specific_rules=[
        "- Strengthen alignment by weaving in relevant keywords where evidence already exists",
        "- You may rephrase bullet points to include keyword phrasing",
        "- Do NOT introduce new skills, tools, or certifications not in the resume",
        _RULE_TECH_VOCAB_DISTRIBUTION,
        _RULE_PROTECTED_FIELDS,
        "- Do NOT change role, industry, or seniority level",
        _RULE_DESCRIPTION_STYLES,
        _RULE_BULLET_LENGTH,
        _RULE_CUSTOM_SECTIONS,
        _RULE_YEARS_FIELD,
        "- If resume is non-technical, keep language non-technical while still aligning keywords",
        _RULE_EM_DASH,
    ],
    placeholder_block=_TAILOR_KEYWORDS_PLACEHOLDER_STANDARD,
)

IMPROVE_RESUME_PROMPT_FULL = _build_tailor_prompt(
    opening=_TAILOR_OPENING_FULL,
    specific_rules=[
        "- Make targeted adjustments to bullet points to align with job description phrasing. Preserve the candidate's original details and voice - adjust wording, do not rewrite entirely.",
        "- DO NOT invent new information",
        _RULE_TECH_VOCAB_DISTRIBUTION,
        _RULE_PROTECTED_FIELDS,
        _RULE_SUMMARY_MIRROR,
        "- Preserve existing action verbs. Do not invent quantifiable achievements not in the original.",
        "- Keep proper nouns (names, company names, locations) unchanged",
        "- Translate job titles, descriptions, and skills to {output_language}",
        _RULE_DESCRIPTION_STYLES,
        _RULE_BULLET_LENGTH,
        _RULE_CUSTOM_SECTIONS,
        "- Improve custom section content the same way as standard sections",
        _RULE_YEARS_FIELD,
        "- Calculate and emphasize total relevant experience duration when it matches requirements",
        "- BULLET REORDERING: Within each workExperience and personalProjects entry, reorder its bullets so the most JD-relevant bullet is first. Keep every original bullet, do not add or drop any. Only reorder.",
        _RULE_EM_DASH,
    ],
    placeholder_block=_TAILOR_KEYWORDS_PLACEHOLDER_STANDARD,
)

IMPROVE_RESUME_PROMPT_HEAVY = _build_tailor_prompt(
    opening=_TAILOR_OPENING_HEAVY,
    specific_rules=[
        "- Rewrite every bullet as Challenge, Action, Result: open with a strong action verb, state what changed because of the work. Keep every fact of the original bullet.",
        _RULE_SUMMARY_MIRROR,
        "- You may reorder bullets within a role so the strongest evidence for this job comes first.",
        "- Keep the same number of bullets in every entry. Do not merge, drop or add bullets.",
        "- Never invent metrics. When a bullet has no number, state the outcome or scope it already implies.",
        _RULE_TECH_VOCAB_DISTRIBUTION,
        _RULE_PROTECTED_FIELDS,
        "- Keep proper nouns (names, company names, locations) unchanged",
        _RULE_DESCRIPTION_STYLES,
        _RULE_BULLET_LENGTH,
        _RULE_CUSTOM_SECTIONS,
        _RULE_YEARS_FIELD,
        _RULE_EM_DASH,
    ],
    placeholder_block=_TAILOR_KEYWORDS_PLACEHOLDER_STANDARD,
)

IMPROVE_PROMPT_OPTIONS = [
    {
        "id": "nudge",
        "label": "Light nudge",
        "description": "Minimal edits to better align existing experience.",
    },
    {
        "id": "keywords",
        "label": "Keyword enhance",
        "description": "Blend in relevant keywords without changing role or scope.",
    },
    {
        "id": "full",
        "label": "Full tailor",
        "description": "Comprehensive tailoring using the job description.",
    },
    {
        "id": "heavy",
        "label": "Heavy rewrite",
        "description": "Rewrites every bullet and the summary. Bigger edits carry a higher risk of hallucination, so review every line.",
    },
]

IMPROVE_RESUME_PROMPTS = {
    "nudge": IMPROVE_RESUME_PROMPT_NUDGE,
    "keywords": IMPROVE_RESUME_PROMPT_KEYWORDS,
    "full": IMPROVE_RESUME_PROMPT_FULL,
    "heavy": IMPROVE_RESUME_PROMPT_HEAVY,
}

DEFAULT_IMPROVE_PROMPT_ID = "keywords"

# Backward-compatible alias
IMPROVE_RESUME_PROMPT = IMPROVE_RESUME_PROMPT_FULL

COVER_LETTER_PROMPT = """Write a tailored cover letter for this job application.

IMPORTANT: Write in {output_language}.

Job Description:
{job_description}

Candidate Resume (JSON):
{resume_data}

Plan silently before writing:
- Identify the target role, company, and the job's most important requirements.
- Infer regional conventions from the supplied job description and resume. Use US, UK, or AU/NZ terminology, greeting, and sign-off when the region is clear; otherwise use neutral professional conventions.
- Infer the employer type and match its tone: conversational and direct for startups/tech, measured for corporate roles, formal and criteria-led for government, personality-forward for creative agencies, and mission-aligned for nonprofits.
- Select the two or three strongest evidence-backed matches from the resume. Prioritize relevance over impressiveness.

Requirements:
- Keep the letter under one page, normally 200-300 words, in 3-4 concise paragraphs.
- Address a named hiring person only when one appears in the supplied inputs; otherwise use "Dear Hiring Manager". Never invent a name.
- For English letters, use "Kind regards" for AU/NZ; use "Sincerely" or "Kind regards" for US/UK. Localize the greeting and sign-off for other output languages.
- Opening hook: Explain why this role or its current challenge matters to the candidate, anchored in ONE specific detail from the job description. Never open with "I am writing to express my interest" or generic excitement about "the role."
- Match paragraph: Connect the selected experience examples directly to stated requirements. Add brief context and measurable results when the resume provides them. Reframe the work in the job's language/terminology where the candidate's proven experience supports it (for example, if the resume shows "built automated data pipelines" and the job says "ETL," describe that real work as ETL).
- Company-fit paragraph: Reference a specific product, mission, project, customer, culture signal, or challenge stated in the job description and explain why it matters to the candidate. If the inputs contain no such detail, connect to the company's stated goal or role challenge instead of inventing research or using generic flattery.
- Closing: Include a confident, specific call to action about how the candidate could support the team's stated work. Do not sound desperate.
- Add context and personality instead of rehashing resume bullets.
- If the supplied resume clearly indicates a career transition, employment gap, overqualification, or underqualification, address it briefly and honestly using transferable evidence or motivation. Do not speculate about circumstances that are not present.
- Extract the role and company name from the supplied inputs. Do not output placeholder brackets.
- Do NOT invent experience, skills, metrics, motivations, company facts, or information not in the supplied resume and job description.
- Avoid unsupported claims such as "perfect candidate," generic praise such as "innovative company," and weak phrasing such as "I helped with."
- Tone: Confident peer, factual and specific, calibrated to the employer.
- Do NOT use an em dash ("—") anywhere in the writing/output; replace it with punctuation appropriate to the language.

Output plain text only. No JSON, no markdown formatting."""

OUTREACH_MESSAGE_PROMPT = """Generate a cold outreach message for LinkedIn or email about this job opportunity.

IMPORTANT: Write in {output_language}.

Job Description:
{job_description}

Candidate Resume (JSON):
{resume_data}

Guidelines:
- 70-100 words maximum (shorter than a cover letter)
- First sentence: Reference specific detail from job description (team, product, technical challenge) - never open with "I'm reaching out" or "I saw your posting"
- One sentence on strongest matching qualification with a concrete metric if available
- End with low-friction ask: "Worth a quick chat?" not "I'd love the opportunity to discuss"
- Tone: How you'd message a former colleague, not a stranger
- Do NOT include placeholder brackets
- Do NOT use phrases like "excited about" or "passionate about"
- Do NOT use em dash ("—") anywhere in the writing/output, even if it exists, remove it

Output plain text only. No JSON, no markdown formatting."""

INTERVIEW_PREP_PROMPT = """Generate structured interview preparation for this tailored resume and job.

IMPORTANT: Write in {output_language}.
Do NOT translate JSON property names. Keep every JSON key exactly as shown in the schema; translate only string values.

Job Description:
{job_description}

Candidate Resume (JSON):
{resume_data}

Truthfulness guardrails:
- Use only evidence from the resume JSON and job description.
- Do NOT invent experience, tools, employers, metrics, certifications, skills, responsibilities, education, projects, or claims beyond the provided evidence.
- Do NOT imply the candidate has a skill or background unless it is present in the resume.
- Skill gaps are preparation targets only. They are not claimed candidate skills.
- If a job requirement is not evidenced by the resume, present it as something to prepare for or explain honestly.

Return ONLY a valid JSON object with exactly these top-level keys:
{{
  "role_fit_analysis": ["Short evidence-based role-fit observation"],
  "resume_questions": [
    {{
      "question": "Interview question grounded in the resume and job",
      "focus_area": "Resume evidence or job requirement being tested",
      "suggested_answer_points": ["Truthful point based on resume evidence"]
    }}
  ],
  "project_follow_ups": [
    {{
      "question": "Follow-up question about a real resume project or experience",
      "focus_area": "Project, impact, tradeoff, or implementation detail",
      "suggested_answer_points": ["Truthful point based on resume evidence"]
    }}
  ],
  "skill_gaps": [
    {{
      "skill": "Job-relevant skill or topic to prepare",
      "why_it_matters": "Why this topic may come up for this role",
      "preparation_suggestion": "How to prepare without claiming unsupported experience"
    }}
  ],
  "talking_points": ["Concise role-specific talking point grounded in the resume"]
}}

Content requirements:
- role_fit_analysis: 3-5 bullets.
- resume_questions: 5-8 questions.
- project_follow_ups: 3-6 questions.
- skill_gaps: 3-5 preparation targets.
- talking_points: 5-8 concise points.
- Keep all suggested answer points factual and resume-grounded.
- Do NOT use markdown fences or commentary outside the JSON."""

GENERATE_TITLE_PROMPT = """Extract the job title and company name from this job description.

IMPORTANT: Write in {output_language}.

Job Description:
{job_description}

Rules:
- Format: "Role @ Company" (e.g., "Senior Frontend Engineer @ Stripe")
- If the company name is not found, return just the role (e.g., "Senior Frontend Engineer")
- Maximum 60 characters
- Use the most specific role title mentioned
- Do not add any other text, quotes, or formatting

Output the title only, nothing else."""

# Diff-based improvement: outputs targeted changes instead of full resume

DIFF_STRATEGY_INSTRUCTIONS = {
    "nudge": "Make minimal edits. Only rephrase where there is a clear match. Do not add new bullet points.",
    "keywords": "Weave in relevant keywords where evidence already exists. You may rephrase bullets but do not add new ones.",
    "full": "Make targeted adjustments. You may rephrase bullets, add verified JD skills, and add new bullets that elaborate on existing work, but do not invent new responsibilities.",
    "heavy": 'Heavy rewrite. Rewrite every bullet as Challenge, Action, Result and rewrite the summary from scratch, keeping every fact of each original. You may reorder the bullets within an entry (action "reorder" on its description path) so the strongest evidence comes first. Do not add or append bullets and do not invent responsibilities, tools or metrics.',
}

# Appended to the strategy rule when the harness already chose the bullets (bullet selection).
DIFF_FIXED_ROWS_INSTRUCTION = (
    "The bullets of every {sections} entry are already chosen: do not use action "
    '"append" on their description paths; only rewrite the existing bullets.'
)

SKILL_TARGET_PLAN_PROMPT = """Build a concise skill target plan for tailoring this resume to the job.

Return ONLY a JSON object. Do not rewrite the resume.

Rules:
1. Prefer required and preferred JD skills.
2. Include existing resume skills that are highly relevant to the JD.
3. You may include JD skills that are missing from the resume skills list.
4. Do not include skills unrelated to the JD.
5. Do not include certifications.
6. Generate reasons in {output_language}.

Existing resume skills:
{existing_skills}

JD keywords and skills:
{job_keywords}

Job Description:
{job_description}

Resume JSON:
{original_resume}

Output this exact JSON format:
{{
  "target_skills": [
    {{
      "skill": "skill name",
      "reason": "why this skill should be emphasized"
    }}
  ],
  "strategy_notes": "brief notes for the next editing pass"
}}"""

BULLET_RELEVANCE_PROMPT = """Score how relevant each resume bullet is to this job.

Return ONLY a JSON object. Do not rewrite, merge, reorder, or invent bullets.

Rules:
1. Score every listed bullet from 0 (irrelevant) to 100 (directly proves a core requirement).
2. Reward concrete evidence of required skills, key responsibilities, and measurable impact.
3. Copy each bullet's path string exactly.
4. Never score bullets that are not listed.

Job description:
{job_description}

Extracted job keywords:
{job_keywords}

Bullets (path | role | text):
{bullets}

Output this exact JSON format:
{{
  "scores": [
    {{"path": "workExperience[0].description[0]", "score": 87}}
  ]
}}
"""

DIFF_IMPROVE_PROMPT = """Given this resume and job description, output a JSON object with targeted changes to better align the resume with the job.

RULES:
1. Only modify content; never change names, companies, dates, institutions, or degrees
2. Do not invent metrics or achievements not supported by the original resume text
3. Do not add new work entries, education entries, or project entries
4. {strategy_instruction}
5. Each change MUST include the original text (copied exactly) so it can be verified
6. For each change, explain WHY it helps match the job description
7. Generate all new text in {output_language}
8. Do not use em dash characters
9. Keep changes minimal and targeted; do not rewrite content that already aligns well
10. Exception to rule 2: you may add a skill only if it appears in the verified skill targets below
11. By DEFAULT, scan the summary and every work, project, and education description for content that already demonstrates a job-description keyword or skill, and reframe that text using the job description's terminology where it is not already phrased that way (per rule 9, leave content that already aligns well), while preserving the candidate's actual accomplishment. Do NOT add new work, metrics, or responsibilities; only restate existing content in the JD's language, and verify every reframe stays factually accurate.
12. Preserve original capitalization, especially for proper nouns, technical terms (e.g., REST, API, AWS), and acronyms. Do not change the casing of words that were capitalized in the original.

PATHS you can target:
- "summary" — the resume summary text
- "workExperience[i].description[j]" — a specific bullet (i = entry index, j = bullet index)
- "workExperience[i].description" — append a new bullet (action: "append"), or reorder that entry's bullets (action: "reorder", value = the entry's original bullets copied exactly in the new order; only when the strategy allows reordering)
- "personalProjects[i].description[j]" — a specific project bullet
- "personalProjects[i].description" — append a new project bullet (action: "append")
- "education[i].description" — the education entry's description text (replace only; it is a single string, not a list)
- "additional.technicalSkills" — reorder the skills list (action: "reorder") or add one verified skill (action: "add_skill")
- "additional.languages" — reorder the languages list (action: "reorder")
- "additional.certificationsTraining" — reorder the certifications list (action: "reorder")
- "additional.awards" — reorder the awards list (action: "reorder")

Do NOT target: personalInfo, dates/years, company names, education degree/institution/years, customSections.

Keywords to emphasize (only if already supported by resume content):
{job_keywords}

Verified skill targets:
{skill_targets}

Job Description:
{job_description}

Original Resume:
{original_resume}

Output this exact JSON format, nothing else:
{{
  "changes": [
    {{
      "path": "workExperience[0].description[1]",
      "action": "replace",
      "original": "the exact original text at this path",
      "value": "the improved text",
      "reason": "why this change helps"
    }},
    {{
      "path": "summary",
      "action": "replace",
      "original": "the current summary text",
      "value": "the improved summary",
      "reason": "why this change helps"
    }},
    {{
      "path": "additional.technicalSkills",
      "action": "reorder",
      "original": null,
      "value": ["most relevant skill first", "then next", "..."],
      "reason": "reordered to prioritize JD-relevant skills"
    }},
    {{
      "path": "additional.technicalSkills",
      "action": "add_skill",
      "original": null,
      "value": "verified skill target missing from the skills list",
      "reason": "added verified JD skill for review"
    }}
  ],
  "strategy_notes": "brief summary of the tailoring approach"
}}"""
