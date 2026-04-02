"""
Agent 3: Pain Signal Detection Agent
Detects likely RCM and operational pain points specific to Calyxr's solutions.
"""

from .base import BaseAgent

SYSTEM = """You are a healthcare revenue cycle management (RCM) expert and sales engineer for Calyxr.

Calyxr solves these specific pain points for healthcare practices:
1. Patient Communication Automation — missed calls, no-shows, manual reminders
2. Workflow Automation — scheduling, intake forms, pre-visit prep, post-visit follow-up
3. Autonomous AI Agents — VOB (insurance eligibility), claim status checks (276/277), denial detection
4. Payer Portal Automation — manual login to insurance portals for eligibility/claims
5. Insurance Eligibility Verification — real-time and batch eligibility checks
6. Denial Detection and Routing — catching denials early, routing to correct staff
7. Staff Productivity — reducing administrative burden on billing coordinators

Given information about a practice, identify the MOST LIKELY pain points they face RIGHT NOW.

High-pain signals to look for:
- Insurance-heavy specialty (cardiology, oncology, pediatrics, multi-specialty)
- Multi-location = more claim volume, more staff coordination
- Small billing team relative to patient volume
- Accepting Medicare/Medicaid (more prior auth, more denials)
- High scheduling complexity
- Manual payer portal logins
- Legacy billing software

Return a JSON object with these exact keys:
{
  "top_pain": "single most acute pain point in one sentence",
  "pain_category": "Patient Communication / Workflow Automation / RCM / Insurance Verification / Denial Management",
  "signals": "comma-separated list of specific signals detected",
  "rcm_risk": "low / medium / high",
  "recommended_product": "Package 1: Communication Core / Package 2: Workflow Automation / Package 3: AI Agents",
  "talk_track": "one sentence positioning Calyxr's solution to this pain"
}"""


class PainSignalAgent(BaseAgent):
    def __init__(self, api_key: str, model: str, system_prompt: str = None):
        super().__init__(api_key, model)
        self.system = system_prompt or SYSTEM

    def detect(self, company: str, research_summary: str, specialty: str, employees: str,
               enrichment: dict = None) -> dict:
        enrichment = enrichment or {}

        # Build Apollo enrichment context block for richer pain detection
        apollo_lines = []
        if enrichment.get("apollo_employees"):
            apollo_lines.append(f"Apollo Employee Count: {enrichment['apollo_employees']}")
        if enrichment.get("apollo_revenue"):
            apollo_lines.append(f"Apollo Revenue: {enrichment['apollo_revenue']}")
        if enrichment.get("apollo_technologies"):
            apollo_lines.append(f"Apollo Technologies In Use: {enrichment['apollo_technologies']}")
        if enrichment.get("apollo_ehr_signals"):
            apollo_lines.append(f"Apollo EHR/EMR Signals Detected: {enrichment['apollo_ehr_signals']}")
        if enrichment.get("apollo_keywords"):
            apollo_lines.append(f"Apollo Keywords: {enrichment['apollo_keywords']}")
        if enrichment.get("apollo_num_locations"):
            apollo_lines.append(f"Apollo Number of Locations: {enrichment['apollo_num_locations']}")
        if enrichment.get("apollo_description"):
            apollo_lines.append(f"Apollo Company Description: {enrichment['apollo_description']}")

        apollo_block = (
            "\n\nApollo.io Enrichment Data (use this to sharpen pain detection):\n"
            + "\n".join(apollo_lines)
            if apollo_lines else ""
        )

        prompt = f"""Detect RCM and operational pain signals for this healthcare practice:

Company: {company}
Specialty: {specialty}
Employees: {employees}
Research Summary: {research_summary}{apollo_block}

Identify the most likely pain points and return JSON."""

        # BUG FIX: increased from 600 to 800 to prevent JSON truncation on
        # detailed pain-signal responses (6 fields, some with long text).
        return self._call_json(self.system, prompt, max_tokens=800)
