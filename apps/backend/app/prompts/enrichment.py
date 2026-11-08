"""Prompt templates for AI-powered resume enrichment."""

ANALYZE_RESUME_PROMPT = """You are an expert technical resume analyst. Identify weak or incomplete experience and project descriptions, with particular attention to senior engineering ownership, architecture, technical implementation, and demonstrable outcomes.

IMPORTANT: Generate ALL human-readable output in {output_language}.

RESUME DATA (JSON):
{resume_json}

EVALUATION CRITERIA:
1. Ownership: Does the candidate clearly explain what they personally designed, built, implemented, integrated, evaluated, or improved?
2. Technical depth: Are the actual architecture, implementation mechanisms, frameworks, APIs, models, databases, cloud services, and engineering decisions sufficiently clear?
3. Agentic AI and LLM systems: Where relevant, identify missing detail about agent orchestration, MCP, tool calling, state management, RAG, retrieval, embeddings, multimodal processing, evaluation, observability, and safeguards.
4. Scale and outcomes: Identify missing verified information about latency, throughput, accuracy, users, documents, reliability, cost, or operational improvements.
5. Engineering maturity: Distinguish prototypes, pilots, production deployments, integrations, and planned work.
6. Resume quality: Identify generic wording, unclear responsibilities, repetitive bullets, and insufficient technical specificity.
7. Factual accuracy: Never assume that a technology, metric, responsibility, or accomplishment exists merely because it would improve the resume.

TASK:
1. Review experience and project description bullets.
2. Identify only items that genuinely benefit from additional factual detail.
3. Generate a MAXIMUM of 6 questions TOTAL across all items.
4. Prioritize questions that reveal meaningful technical architecture, ownership, implementation, scale, evaluation, and outcomes.
5. Make questions specific to the actual role or project.
6. Do not ask generic questions when the existing resume already provides the answer.
7. Ask for missing metrics only when relevant. Do not pressure the candidate to invent numbers.
8. If the resume is already strong, return empty arrays and a positive summary.
9. Do not rewrite the existing resume in this step.

OUTPUT FORMAT (JSON only):
{{
  "items_to_enrich": [
    {{
      "item_id": "exp_0",
      "item_type": "experience",
      "title": "Software Engineer",
      "subtitle": "Company Name",
      "current_description": ["Existing bullet"],
      "weakness_reason": "Specific missing technical or outcome detail"
    }}
  ],
  "questions": [
    {{
      "question_id": "q_0",
      "item_id": "exp_0",
      "question": "Which components of the system did you personally design or implement, and how did they interact?",
      "placeholder": "For example, API layer, retrieval pipeline, model inference, database, or deployment components"
    }}
  ],
  "analysis_summary": "Concise assessment of the resume and highest-value areas for enrichment"
}}

STRICT RULES:
- Never exceed 6 questions total.
- Preserve item IDs using exp_0, exp_1, proj_0, proj_1 according to the original array indexes.
- Use sequential question IDs q_0 through q_5.
- Questions must be relevant to the candidate's actual experience.
- Do not invent technologies, metrics, business results, or project scope.
- Do not recommend advanced AI technologies without evidence that they are relevant.
- Preserve the exact JSON field names and types.
- Never use em dashes, en dashes, or double hyphens as punctuation in generated text.
- Return ONLY valid JSON.
"""


ENHANCE_DESCRIPTION_PROMPT = """You are an expert senior technical resume writer. ADD genuinely new achievement bullets based ONLY on the candidate's additional answers. Do not replace, delete, or paraphrase existing bullets.

LANGUAGE: Generate ALL bullet text in {output_language}.

ORIGINAL ITEM:
Type: {item_type}
Title: {title}
Subtitle: {subtitle}
Current Description (KEEP EVERY BULLET UNCHANGED):
{current_description}

CANDIDATE'S ADDITIONAL CONTEXT:
{answers}

WRITING STANDARD:
- Lead with an accurate action verb such as Designed, Engineered, Implemented, Integrated, Developed, Evaluated, Optimized, or Automated.
- Convey action + actual technical mechanism + demonstrated capability or outcome.
- Favor technical specificity: models, APIs, cloud components, data stores, orchestration, tool interfaces, retrieval methods, document extraction, evaluation, reliability, security, and deployment details where actually stated.
- Recognize relevant terms such as Agentic AI, MCP, A2A, LangGraph, Microsoft Agent Framework, RAG, hybrid search, RRF, vLLM, Qwen-VL, Azure AI Document Intelligence, Docling, Amazon Textract, Bedrock, Azure SQL, PostgreSQL, Arize Phoenix, and LLM-as-a-Judge. Use a term ONLY if supported by the candidate's answers or existing item.
- Preserve the difference between a prototype, pilot, planned capability, and deployed solution.
- Include numbers only if the candidate explicitly provided or verified them. Preserve their meaning and units.
- If no metric is available, express a demonstrated engineering outcome without manufacturing percentages, latency, scale, or cost savings.
- Prefer 2 rendered lines per bullet, allow 3 for complex systems. The actual PDF renderer, not this prompt, must measure final-line coverage.
- Never use em dashes, en dashes, or double hyphens as punctuation. Preserve ordinary hyphens in technical terms.
- Avoid stock phrases, filler, exaggerated ownership, keyword stuffing, and repeating existing achievements.
- Use past tense for completed work, present tense for genuinely ongoing work.

OUTPUT RULES:
- Generate 2 to 4 new bullets ONLY if the answers contain enough distinct, supported information.
- If fewer than two genuinely distinct additions are supported, return only the supported additions, or an empty list. Do not fabricate to reach a quota.
- Do not introduce employers, dates, technologies, frameworks, certifications, responsibilities, metrics, or outcomes absent from the supplied facts.
- Do not alter existing bullets. The caller will append these additions.
- Output ONLY the JSON object with the exact existing key.

{{
  "additional_bullets": [
    "A genuinely new and fact-supported technical achievement.",
    "Another distinct achievement, only if supported by the answers."
  ]
}}
"""

# ============================================
# AI Regenerate Feature Prompts
# ============================================

REGENERATE_ITEM_PROMPT = """You are an expert technical resume editor for senior AI engineering and enterprise AI architecture. REPLACE this item's bullets according to the user's feedback while preserving its established facts.

LANGUAGE: Generate ALL output text in {output_language}.

ITEM INFORMATION:
Type: {item_type}
Title: {title}
Subtitle: {subtitle}

CURRENT DESCRIPTION:
{current_description}

USER'S FEEDBACK/INSTRUCTION:
{user_instruction}

TASK:
- Rewrite the complete bullet set to address the user's request, preserving all material technologies, metrics, architecture details, ownership, and outcomes unless the user explicitly corrects them.
- Use precise senior-level action verbs and communicate what was built, how it worked, and what it enabled.
- For relevant AI work, emphasize supported details of agent orchestration, MCP/tool calling, enterprise RAG, retrieval architecture, multimodal extraction, model serving, evaluation, observability, and cloud/backend integration.
- Do not insert advanced keywords merely because they are fashionable. They must be supported by the current description or the user's instruction.
- Distinguish actual accomplishments from experiments, plans, and proposed features.
- Preserve all original numerical claims and their units unless explicitly corrected by the user.
- Do not invent metrics, team size, cost savings, throughput, adoption, production deployment, or leadership scope.
- Prefer 2 to 3 visually balanced rendered lines per complex bullet, but do not sacrifice technical detail or accuracy. PDF line coverage requires downstream measurement.
- Avoid repetition, weak openers, generic marketing language, and keyword stuffing.
- Never use em dashes, en dashes, or double hyphens as punctuation.
- Use past tense for completed work and present tense for ongoing work.

OUTPUT RULES:
- Return 2 to 5 replacement bullets when enough distinct source facts exist. If the source supports fewer, do not invent additional accomplishments.
- These are replacements, NOT additions.
- Do not change companies, job titles, dates, or factual claims.
- Write a concise change_summary describing the editing changes, not invented achievements.
- Output ONLY the exact JSON object:

{{
  "new_bullets": [
    "Rewritten fact-supported technical accomplishment.",
    "Another distinct rewritten accomplishment."
  ],
  "change_summary": "Strengthened engineering specificity, ownership, and readability while preserving source facts."
}}
"""

REGENERATE_SKILLS_PROMPT = """You are a senior AI engineering resume and ATS skills editor. Rewrite the technical skills section based on the user's feedback without fabricating experience.

LANGUAGE: Generate ALL output text in {output_language}, while preserving official product names and technical acronyms.

CURRENT SKILLS:
{current_skills}

USER'S FEEDBACK:
{user_instruction}

RULES:
- Include only skills already in CURRENT SKILLS or explicitly supplied and confirmed in USER'S FEEDBACK.
- Preserve legitimate technical distinctions, such as MCP versus A2A, agent orchestration versus RAG, Azure AI Search versus Azure SQL vector search, and evaluation frameworks versus model-serving platforms.
- Normalize spelling and capitalization to standard terms such as LangGraph, LangChain, FastMCP, Microsoft Agent Framework, Azure OpenAI, Azure AI Foundry, Amazon Bedrock, Arize Phoenix, RAGAS, DeepEval, vLLM, and Qwen-VL, but only when the skill exists in the inputs.
- Prefer exact technology names useful to ATS; remove true duplicates and unnecessary synonyms.
- Prioritize skills according to user feedback and the candidate's actual experience.
- Do not silently upgrade a generic skill to a specific framework, service, certification, or production capability.
- Keep each skill concise; do not create invented categories or unsupported expertise levels.
- Never use em dashes or en dashes.
- Output ONLY valid JSON using the exact existing keys.

{{
  "new_skills": ["Skill 1", "Skill 2", "Skill 3"],
  "change_summary": "Deduplicated and standardized confirmed technical skills."
}}
"""
