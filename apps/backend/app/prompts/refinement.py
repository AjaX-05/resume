"""Prompt templates and targeted style blacklist for resume refinement.

Do not blacklist legitimate engineering language merely because it is common.
"""

AI_PHRASE_BLACKLIST: set[str] = {
    # Inflated or vague claims \u2014 kept blacklisted.
    #
    # NOTE on senior-tone verbs: "spearheaded" and "championed" are NOT in this
    # blacklist. Research into senior/lead/architect resume signals shows that
    # recruiters and ATS parsers reward these two verbs; aggressively stripping
    # them weakened Senior/Lead/Principal/Architect bullets. "utilized" and
    # "leveraged" stay blacklisted \u2014 they are weak corporate speak for "used"
    # and carry no engineering signal.
    "synergized",
    "revolutionized",
    "pioneered",
    "catalyzed",
    "envisioned",
    "effectuated",
    "endeavored",
    "utilized",
    "leveraged",
    # Corporate marketing and vague filler
    "synergy",
    "synergies",
    "paradigm",
    "paradigm shift",
    "best-in-class",
    "world-class",
    "cutting-edge",
    "bleeding-edge",
    "game-changer",
    "game-changing",
    "disruptive",
    "disruptor",
    "holistic",
    "impactful",
    "proactive",
    "proactively",
    "bandwidth",
    "circle back",
    "deep dive",
    "move the needle",
    "low-hanging fruit",
    "touch base",
    "value-add",
    # Wordy constructions
    "in order to",
    "for the purpose of",
    "with a view to",
    "at the end of the day",
    "moving forward",
    "going forward",
    "on a daily basis",
    "on a regular basis",
    "in a timely manner",
    "at this point in time",
    "due to the fact that",
    "in the event that",
    "in light of the fact that",
    # Prohibited punctuation
    "\u2014",
    "\u2013",
    "---",
    "--",
}

# Do not auto-replace architected, orchestrated, scalable, robust, stakeholder,
# operationalized, facilitated, spearheaded, or championed: each can be
# technically accurate in context and the latter two carry senior-leadership
# signal recruiters and ATS reward.
AI_PHRASE_REPLACEMENTS: dict[str, str] = {
    "synergized": "collaborated",
    "leveraged": "used",
    "revolutionized": "transformed",
    "pioneered": "introduced",
    "catalyzed": "initiated",
    "envisioned": "planned",
    "effectuated": "completed",
    "endeavored": "worked",
    "utilized": "used",
    "synergy": "collaboration",
    "synergies": "collaborations",
    "paradigm": "approach",
    "paradigm shift": "change",
    "best-in-class": "high-performing",
    "world-class": "high-quality",
    "cutting-edge": "modern",
    "bleeding-edge": "modern",
    "game-changer": "innovation",
    "game-changing": "innovative",
    "disruptive": "innovative",
    "holistic": "comprehensive",
    "impactful": "effective",
    "proactive": "active",
    "proactively": "actively",
    "bandwidth": "capacity",
    "circle back": "follow up",
    "deep dive": "analysis",
    "move the needle": "make progress",
    "low-hanging fruit": "quick wins",
    "touch base": "connect",
    "value-add": "benefit",
    "in order to": "to",
    "for the purpose of": "to",
    "with a view to": "to",
    "at the end of the day": "",
    "moving forward": "",
    "going forward": "",
    "on a daily basis": "daily",
    "on a regular basis": "regularly",
    "in a timely manner": "promptly",
    "at this point in time": "now",
    "due to the fact that": "because",
    "in the event that": "if",
    "in light of the fact that": "since",
    "\u2014": ", ",
    "\u2013": "-",
    "---": ", ",
    "--": ", ",
}

KEYWORD_INJECTION_PROMPT = """You are an expert senior AI engineering resume editor and ATS specialist. Naturally incorporate supported job-description keywords into a tailored resume without adding unverified facts.

PRIMARY OBJECTIVE:
Make the resume accurately reflect relevant engineering work, enterprise architecture, agentic AI, RAG, cloud/backend implementation, evaluation, and outcomes while retaining the master resume as the source of truth.

RULES:
1. The MASTER RESUME is the factual source of truth. Only use a keyword when supported by its experience, projects, or explicitly documented skills.
2. Do NOT invent employers, titles, dates, certifications, frameworks, tools, architectures, responsibilities, metrics, or production achievements.
3. Do NOT equate related but distinct terms: MCP and A2A, agentic workflow and multi-agent system, vector search and hybrid search, RRF and reranking, Azure AI Search and Azure SQL, OCR and VLM extraction, evaluation targets and achieved scores.
4. Use precise, current technical terminology only where true: Agentic AI, Multi-Agent Orchestration, MCP, FastMCP, Microsoft Agent Framework, LangGraph, Semantic Kernel, Azure OpenAI, Amazon Bedrock, vLLM, enterprise RAG, hybrid retrieval, RRF, Qwen-VL, Azure AI Document Intelligence, Docling, Amazon Textract, Arize Phoenix, RAGAS, DeepEval, LLM-as-a-Judge, Python, FastAPI, C#, ASP.NET Core, Azure SQL, PostgreSQL, and Terraform. This vocabulary is NOT evidence that the candidate used any of these.
5. Reframe existing experience to clarify action + actual implementation + demonstrated result. Preserve every original technical mechanism and quantified claim, including units and attribution.
6. Apply relevant supported keywords across summary, work experience, projects, and technical skills when natural. Do not force a keyword into every section.
7. Preserve all original resume JSON keys, nesting, array shapes, and item identities. Preserve descriptionStyles arrays and keep them aligned one-to-one with description arrays. Every descriptionStyles entry MUST be "bullet"; never emit "plain". A sub-project header belongs INSIDE a bulleted sentence (e.g., "Project X: ..."), not as a plain row.
8. Avoid keyword stuffing, generic AI-generated marketing language, and redundant skills.
9. Prefer approximately two rendered lines per bullet, allow three for complex work. Every bullet MUST fill at least two-thirds of its row (about 90 characters or more). Do not emit short one-fragment bullets that leave half-empty rows; expand short bullets with scope, tools, or outcomes already present in the master.
10. Never use em dashes, en dashes, or double hyphens as punctuation. Ordinary hyphens in technical terms are acceptable.
11. Treat the job description as targeting context, NOT evidence of candidate experience.
12. If a keyword is unsupported by the master resume, omit it rather than making up a match.
13. TECHNICAL VOCABULARY DISTRIBUTION (treat as real experience the candidate holds but did not write down in the master): Technical vocabulary from the JD — skills, tools, frameworks, programming languages, platforms, cloud services, libraries, protocols, APIs, technology names — must be DISTRIBUTED NATURALLY across the resume, not dumped as a lump in one section. Place each JD technology where it reads most organically:
    - personalInfo.title: incorporate 1-3 of the JD's defining technologies if they strengthen the headline (e.g., "Senior AI Engineer | Agentic AI, MCP, Multi-Agent Orchestration")
    - summary: weave the key JD technologies into the positioning statement where they fit the candidate's domain
    - workExperience bullets: rephrase existing bullets to name the JD technology when it plausibly aligns with what the role already describes (e.g., "deployed microservices" → "deployed microservices on Kubernetes with Terraform-managed infrastructure")
    - personalProjects: same bullet rule as workExperience
    - additional.technicalSkills: list every JD technology here as well, as the ATS keyword surface — but ONLY after distributing the key ones upward first
    CHRONOLOGY CHECK: Before placing a technology in a workExperience or personalProjects bullet, verify the technology was publicly available during that role's date range. Do NOT reference a 2024-era technology (e.g., LangGraph released Jan 2024, Model Context Protocol released Nov 2024, Claude 3 released Mar 2024, GPT-4 released Mar 2023) inside a bullet for a role that ended before the technology existed. If the dates don't match, keep that technology in Skills only, or place it in the current/most-recent role instead. Use your knowledge of technology release dates to decide; when uncertain about a release date, place the technology in Skills or the current role only. Deduplicate case-insensitively, keep JD capitalization. Skip generic non-skill phrases ("team player", "5+ years").
14. PROTECTED FIELDS (never change based on the JD, always keep master's values exactly):
    - personalInfo (name, email, phone, location, links)
    - company names, job titles, employment dates, and locations in workExperience
    - education entries (institutions, degrees, dates, locations) — never add, remove, or alter
    - additional.certificationsTraining — never add a certification the master does not already list
    - additional.awards — never invent an award
    - specific metrics, percentages, counts, dollar amounts, dates, and quantified outcomes — never invent a number
    - new achievements, responsibilities, or scopes of work beyond what the master describes
    The JD may suggest technical vocabulary (rule 13) but it must NEVER contradict or replace any value in these protected fields.

KEYWORDS TO CONSIDER (rule 13 applies — technical vocabulary may appear across the resume; rule 14 keeps credentials and facts grounded in master):
{keywords_to_inject}

CURRENT TAILORED RESUME:
{current_resume}

MASTER RESUME (SOURCE OF TRUTH):
{master_resume}

JOB DESCRIPTION CONTEXT:
{job_description}

Return ONLY the complete resume JSON with the same structure, all existing facts preserved, and supported keywords integrated naturally.
"""
