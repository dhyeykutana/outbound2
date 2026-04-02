"""
Agent 4: Contact Intelligence Agent
Evaluates the best contact person to target and their decision-making likelihood.
"""

from .base import BaseAgent

SYSTEM = """You are a B2B sales expert specializing in healthcare SaaS.

Calyxr's primary decision-maker personas (in priority order):
1. COO / VP of Operations / Director of Operations — HIGHEST priority, owns workflow and automation decisions
2. Director of Revenue Cycle / RCM Director — key for billing/insurance automation
3. Director of Patient Access — key for scheduling and communication automation
4. Office Manager / Practice Administrator — gatekeeper, often influencer
5. CEO / Physician Owner — final sign-off at small practices
6. CIO / Director of IT — technical evaluator, rarely the champion
7. CFO / Finance Director — budget holder, not usually champion

Evaluate the given contact and return:
- Whether this is the RIGHT person to target
- Their likely role in the decision (champion, economic buyer, gatekeeper, influencer)
- A recommended approach tone
- Whether we should find a different contact

Return a JSON object with these exact keys:
{
  "persona_fit": "Champion / Economic Buyer / Gatekeeper / Influencer / Wrong Contact",
  "priority_tier": "P1 / P2 / P3",
  "decision_likelihood": "high / medium / low",
  "role_insight": "one sentence about their likely pain and influence",
  "find_better_contact": true or false,
  "ideal_title_to_find": "suggested title if we should find someone better, else null"
}"""


class ContactIntelligenceAgent(BaseAgent):
    def __init__(self, api_key: str, model: str, system_prompt: str = None):
        super().__init__(api_key, model)
        self.system = system_prompt or SYSTEM

    def evaluate(self, company: str, contact_name: str, contact_title: str,
                 research_summary: str, enrichment: dict = None) -> dict:
        enrichment = enrichment or {}

        # Build Apollo contact enrichment block for more accurate evaluation
        apollo_lines = []
        if enrichment.get("apollo_contact_title"):
            apollo_lines.append(f"Apollo Verified Title: {enrichment['apollo_contact_title']}")
        if enrichment.get("apollo_contact_seniority"):
            apollo_lines.append(f"Apollo Seniority Level: {enrichment['apollo_contact_seniority']}")
        if enrichment.get("apollo_contact_department"):
            apollo_lines.append(f"Apollo Department: {enrichment['apollo_contact_department']}")
        if enrichment.get("apollo_contact_email"):
            apollo_lines.append(f"Apollo Verified Email: {enrichment['apollo_contact_email']}")
        if enrichment.get("apollo_contact_linkedin"):
            apollo_lines.append(f"Apollo LinkedIn: {enrichment['apollo_contact_linkedin']}")
        if enrichment.get("apollo_employees"):
            apollo_lines.append(f"Apollo Employee Count: {enrichment['apollo_employees']}")
        if enrichment.get("apollo_revenue"):
            apollo_lines.append(f"Apollo Revenue: {enrichment['apollo_revenue']}")

        apollo_block = (
            "\n\nApollo.io Enrichment Data (use this to verify and enhance contact evaluation):\n"
            + "\n".join(apollo_lines)
            if apollo_lines else ""
        )

        prompt = f"""Evaluate this contact for Calyxr outreach:

Company: {company}
Contact Name: {contact_name}
Contact Title: {contact_title}
Practice Overview: {research_summary}{apollo_block}

Assess their fit as a sales target and return JSON."""

        return self._call_json(self.system, prompt, max_tokens=500)
