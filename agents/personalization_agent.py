"""
Agent 5: Personalization Agent
Generates highly specific outreach insights based on company research.
"""

from .base import BaseAgent

SYSTEM = """You are a senior healthcare sales strategist writing personalized outreach insights for Calyxr.

Calyxr is a HIPAA-compliant platform for healthcare practices offering:
- Patient communication automation (SMS, email, voice)
- Workflow automation (scheduling, intake, reminders, follow-up)
- Autonomous AI agents (insurance eligibility VOB, claim status 276/277, denial detection)

Your job: Write a single, highly specific personalization insight that:
1. References something REAL and SPECIFIC about this practice (specialty, services, patient volume signals, insurance mix)
2. Connects that observation naturally to a pain Calyxr solves
3. Reads like it came from someone who actually looked at their operation — not a generic vendor
4. Never uses: "AI", "artificial intelligence", "automation platform", "excited to", "reaching out"
5. Sounds like an insider observation, not a sales pitch

TONE: Confident, operationally grounded, peer-level. Like a colleague who noticed something.

Return a JSON object with these exact keys:
{
  "insight": "one specific personalization sentence (max 30 words)",
  "hook": "the emotional or operational angle being pulled (e.g. billing staff overload / eligibility rework / no-show revenue loss)",
  "specificity_score": integer 1-10 (how specific/personalized this insight is)
}"""


class PersonalizationAgent(BaseAgent):
    def __init__(self, api_key: str, model: str, system_prompt: str = None):
        super().__init__(api_key, model)
        self.system = system_prompt or SYSTEM

    def generate(self, company: str, website: str, specialty: str,
                 research_summary: str, top_pain: str, contact_title: str,
                 enrichment: dict = None) -> dict:
        enrichment = enrichment or {}

        # Build Apollo context to ground the personalization in real data
        apollo_lines = []
        if enrichment.get("apollo_description"):
            apollo_lines.append(f"Apollo Description: {enrichment['apollo_description']}")
        if enrichment.get("apollo_keywords"):
            apollo_lines.append(f"Apollo Keywords: {enrichment['apollo_keywords']}")
        if enrichment.get("apollo_technologies"):
            apollo_lines.append(f"Apollo Technologies: {enrichment['apollo_technologies']}")
        if enrichment.get("apollo_ehr_signals"):
            apollo_lines.append(f"Apollo EHR/EMR Signals: {enrichment['apollo_ehr_signals']}")
        if enrichment.get("apollo_revenue"):
            apollo_lines.append(f"Apollo Revenue: {enrichment['apollo_revenue']}")
        if enrichment.get("apollo_employees"):
            apollo_lines.append(f"Apollo Employee Count: {enrichment['apollo_employees']}")
        if enrichment.get("apollo_num_locations"):
            apollo_lines.append(f"Apollo Locations: {enrichment['apollo_num_locations']}")
        if enrichment.get("apollo_contact_title"):
            apollo_lines.append(f"Apollo Verified Contact Title: {enrichment['apollo_contact_title']}")

        apollo_block = (
            "\n\nApollo.io Enrichment Data (use this for specificity and grounding):\n"
            + "\n".join(apollo_lines)
            if apollo_lines else ""
        )

        prompt = f"""Generate a personalized outreach insight for this practice:

Company: {company}
Website: {website}
Specialty: {specialty}
Contact Title: {contact_title}
Research Summary: {research_summary}
Identified Top Pain: {top_pain}{apollo_block}

Write a single insight sentence that feels specific to THIS practice. Return JSON."""

        # BUG FIX: was max_tokens=300 — too tight; any preamble from the model
        # before the JSON caused truncation and a parse failure, resulting in
        # an empty insight that made every email identical.
        return self._call_json(self.system, prompt, max_tokens=600)
