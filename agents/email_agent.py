"""
Agent 6: Email Generation Agent
Writes outbound emails and LinkedIn DMs following Calyxr's proven copy framework.
"""

from .base import BaseAgent

# ── Calyxr Email Copy Rules (from memory + best-performing templates) ────────
SYSTEM = """You are the outbound copywriter for Calyxr, a healthcare operations platform.

COPY RULES — apply every time, no exceptions:
1. NEVER start with "I" or "We"
2. ONE pain point per email only — never mix topics
3. Email 1: pure observation/problem (do NOT mention Calyxr)
4. Email 2: introduce Calyxr + solution + one social proof line
5. Email 3: brief case or ROI stat + low-commitment ask
6. NEVER say: "book a call", "schedule a demo", "let's connect", "circle back", "touch base"
7. Close with a curiosity soft question, not a hard CTA
8. 50-120 words max per email body, 5 sentences max
9. Plain human language — no dashes (use commas), no bullet points, no bold text in body
10. NEVER use: "AI", "artificial intelligence", "machine learning", "automation platform", "excited to", "I hope this finds you", "reaching out"
11. Non-accusatory tone ("many practices face..." not "your practice has a problem")
# 12. Risk reversal in E3 (e.g., "no long contracts", "live in days")
13. Subject lines: 4-6 words, lowercase preferred, no clickbait

SEQUENCE STRUCTURE:
- E1: Pattern interrupt — name the pain as an observation, no solution yet
- E2: Introduce Calyxr as the reason you're writing, name the solution, one proof point
- E3: ROI/cost-of-inaction framing + low-commitment offer

LINKEDIN DM SEQUENCE RULES (all 3 must be DIFFERENT — never repeat the same angle):
- LI DM 1 (Connection Request Note): 40-60 words. Pure pattern interrupt — reference something
  specific you observed about their practice/role. No pitch. No Calyxr mention. End with a
  genuine observation question that invites a reply. Must feel like a peer noticing something,
  not a cold pitch.
- LI DM 2 (Follow-up after connect): 50-70 words. Bridge from the pain angle in DM 1.
  Briefly introduce Calyxr as context for why you're reaching out. Include one concrete proof
  point (stat, outcome, or named result). End with a soft curiosity question.
- LI DM 3 (Final touchpoint): 40-60 words. ROI or cost-of-inaction angle. Reference their
  specific specialty or practice signal. Low-commitment offer (e.g., "happy to share a
  one-pager", "takes 15 min to see if it applies"). Risk reversal (no contracts, live in days).
  End with a soft close, not a hard CTA.
- Each DM must use a DIFFERENT opening hook, angle, and phrasing
- Use the contact's first name naturally (once per DM, not at every sentence start)
- NEVER use: "I saw your profile", "just following up", "checking in", "hope you're well",
  "I wanted to", "synergy", "game-changer", "revolutionary", "AI-powered"

Return a JSON object with these exact keys:
{
  "e1_subject": "email 1 subject line",
  "e1_body": "email 1 body (observation only, no Calyxr mention)",
  "e2_subject": "email 2 subject line",
  "e2_body": "email 2 body (introduce Calyxr + solution + proof)",
  "e3_subject": "email 3 subject line",
  "e3_body": "email 3 body (ROI framing + low-commitment ask)",
  "li_dm1": "LinkedIn connection request note (40-60 words, pattern interrupt, no Calyxr)",
  "li_dm2": "LinkedIn follow-up DM (50-70 words, introduce Calyxr + proof point)",
  "li_dm3": "LinkedIn final DM (40-60 words, ROI angle + low-commitment close)"
}"""


class EmailGenerationAgent(BaseAgent):
    def __init__(self, api_key: str, model: str, system_prompt: str = None):
        super().__init__(api_key, model)
        self.system = system_prompt or SYSTEM

    def write(self, company: str, contact_name: str, contact_title: str,
              insight: str, top_pain: str, specialty: str,
              enrichment: dict = None,
              research_summary: str = "") -> dict:
        _name_parts = contact_name.split() if (contact_name and contact_name != "nan") else []
        first_name = _name_parts[0] if _name_parts else "there"
        enrichment = enrichment or {}

        # Build Apollo contact context to inform the copy
        apollo_lines = []
        if enrichment.get("apollo_contact_email"):
            apollo_lines.append(f"Apollo Verified Contact Email: {enrichment['apollo_contact_email']}")
        if enrichment.get("apollo_contact_phone"):
            apollo_lines.append(f"Apollo Contact Phone: {enrichment['apollo_contact_phone']}")
        if enrichment.get("apollo_contact_linkedin"):
            apollo_lines.append(f"Apollo Contact LinkedIn: {enrichment['apollo_contact_linkedin']}")
        if enrichment.get("apollo_contact_title"):
            apollo_lines.append(f"Apollo Verified Title: {enrichment['apollo_contact_title']}")
        if enrichment.get("apollo_contact_seniority"):
            apollo_lines.append(f"Apollo Seniority: {enrichment['apollo_contact_seniority']}")
        if enrichment.get("apollo_revenue"):
            apollo_lines.append(f"Apollo Revenue: {enrichment['apollo_revenue']}")
        if enrichment.get("apollo_employees"):
            apollo_lines.append(f"Apollo Employee Count: {enrichment['apollo_employees']}")
        if enrichment.get("apollo_technologies"):
            apollo_lines.append(f"Apollo Technologies: {enrichment['apollo_technologies']}")
        if enrichment.get("apollo_description"):
            apollo_lines.append(f"Apollo Company Description: {enrichment['apollo_description']}")
        if enrichment.get("apollo_keywords"):
            apollo_lines.append(f"Apollo Keywords: {enrichment['apollo_keywords']}")
        if enrichment.get("apollo_num_locations"):
            apollo_lines.append(f"Apollo Number of Locations: {enrichment['apollo_num_locations']}")
        if enrichment.get("apollo_ehr_signals"):
            apollo_lines.append(f"Apollo EHR/EMR Signals: {enrichment['apollo_ehr_signals']}")

        apollo_block = (
            "\n\nApollo.io Enrichment Data (use for added context and specificity):\n"
            + "\n".join(apollo_lines)
            if apollo_lines else ""
        )

        # Research summary block for LinkedIn DM personalization
        research_block = (
            f"\n\nPractice Research Summary (use to personalize LinkedIn DMs):\n{research_summary}"
            if research_summary else ""
        )

        prompt = f"""Write a 3-email outbound sequence AND a 3-message LinkedIn DM sequence for this healthcare prospect.

Contact: {first_name} ({contact_title})
Company: {company}
Specialty: {specialty}
Top Pain Point: {top_pain}
Personalization Insight: {insight}{research_block}{apollo_block}

CRITICAL for LinkedIn DMs:
- Each DM must open with a DIFFERENT hook — DM 1 uses the pain observation angle,
  DM 2 uses the solution/proof angle, DM 3 uses ROI/cost-of-inaction angle
- Reference specific details from the practice research and Apollo data to make each DM
  feel personally written for {first_name} at {company}, not a template
- DM 1 must NOT mention Calyxr
- All 3 DMs must feel like they were written on 3 different days by someone who knows the practice well

Build the entire sequence around the single pain point above. Follow all copy rules strictly.
Return JSON with all email bodies, subject lines, and all 3 LinkedIn DMs."""

        return self._call_json(self.system, prompt, max_tokens=1800)
