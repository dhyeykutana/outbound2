"""
Agent 2: Company Research Agent
Analyzes the practice website and returns structured operational signals.
"""

import logging
import requests
from bs4 import BeautifulSoup
from .base import BaseAgent

log = logging.getLogger("calyxr")

SYSTEM = """You are a healthcare operations analyst specializing in revenue cycle management (RCM) and practice administration research.

Analyze the provided information about a healthcare practice and extract:
1. What the practice does (specialty, services, patient population)
2. Estimated practice size and operational complexity
3. Billing workflow signals (EMR/EHR mentioned, insurance types, billing staff)
4. Any automation or technology already in use
5. Any indicators of administrative pain (complexity, multi-location, high volume)

Return a JSON object with these exact keys:
{
  "summary": "2-3 sentence overview of the practice",
  "specialty": "primary specialty",
  "services": "comma-separated key services",
  "size_signal": "small / mid-size / large",
  "billing_complexity": "low / medium / high",
  "emr_mentioned": "EMR name or Unknown",
  "insurance_heavy": true or false,
  "multi_location": true or false,
  "automation_signals": "any tech or automation mentioned, or None",
  "admin_pain_indicators": "specific pain indicators found, or None"
}"""


def _scrape_website(url: str) -> str:
    """
    Attempt to scrape website content.
    Returns extracted text (up to 3000 chars) or empty string on failure.
    Only returns content from successful (2xx) responses — never from
    error pages, Cloudflare blocks, or login redirects.
    """
    if not url or url.lower() in ("nan", ""):
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        resp = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
            allow_redirects=True,
        )
        # BUG FIX: only process successful responses.
        # Previously, error pages (403 Cloudflare blocks, 404 pages, etc.)
        # were parsed and sent to the model as if they were real content.
        if not resp.ok:
            log.warning(f"[SCRAPE] {url} returned HTTP {resp.status_code} — skipping")
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        # BUG FIX: was 1500 chars — too little for meaningful differentiation.
        # Increased to 3000 to give the model enough context to produce
        # company-specific (not generic) research summaries.
        return text[:3000]
    except Exception as exc:
        log.debug(f"[SCRAPE] Failed for {url}: {exc}")
        return ""


class CompanyResearchAgent(BaseAgent):
    def __init__(self, api_key: str, model: str, system_prompt: str = None):
        super().__init__(api_key, model)
        self.system = system_prompt or SYSTEM

    def analyze(
        self,
        company: str,
        website: str,
        specialty: str,
        # BUG FIX: research agent previously only received company/website/specialty.
        # When scraping failed and Apollo had no data, the model had nothing unique
        # per company → identical summaries for every row.
        # Now the full CSV row context is passed so there is always differentiating data.
        industry: str = "",
        employees: str = "",
        state: str = "",
        country: str = "",
        revenue: str = "",
        contact_title: str = "",
        enrichment: dict = None,
    ) -> dict:
        scraped = _scrape_website(website)
        enrichment = enrichment or {}

        # ── CSV context block (always present, even without Apollo / scraping) ─
        csv_lines = []
        if industry:
            csv_lines.append(f"Industry: {industry}")
        if employees:
            csv_lines.append(f"Employee Count (CSV): {employees}")
        if revenue:
            csv_lines.append(f"Annual Revenue (CSV): {revenue}")
        if state:
            csv_lines.append(f"State: {state}")
        if country:
            csv_lines.append(f"Country: {country}")
        if contact_title:
            csv_lines.append(f"Primary Contact Title: {contact_title}")

        csv_block = (
            "\n\nAdditional CSV Data:\n" + "\n".join(csv_lines)
            if csv_lines else ""
        )

        # ── Apollo enrichment block ────────────────────────────────────────────
        apollo_lines = []
        if enrichment.get("apollo_description"):
            apollo_lines.append(f"Apollo Description: {enrichment['apollo_description']}")
        if enrichment.get("apollo_employees"):
            apollo_lines.append(f"Apollo Employee Count: {enrichment['apollo_employees']}")
        if enrichment.get("apollo_revenue"):
            apollo_lines.append(f"Apollo Revenue: {enrichment['apollo_revenue']}")
        if enrichment.get("apollo_technologies"):
            apollo_lines.append(f"Apollo Technologies In Use: {enrichment['apollo_technologies']}")
        if enrichment.get("apollo_keywords"):
            apollo_lines.append(f"Apollo Keywords: {enrichment['apollo_keywords']}")
        if enrichment.get("apollo_ehr_signals"):
            apollo_lines.append(f"Apollo EHR/EMR Signals: {enrichment['apollo_ehr_signals']}")
        if enrichment.get("apollo_industry"):
            apollo_lines.append(f"Apollo Industry: {enrichment['apollo_industry']}")
        if enrichment.get("apollo_num_locations"):
            apollo_lines.append(f"Apollo Number of Locations: {enrichment['apollo_num_locations']}")
        if enrichment.get("apollo_city"):
            apollo_lines.append(f"Apollo City: {enrichment['apollo_city']}")
        if enrichment.get("apollo_founded"):
            apollo_lines.append(f"Founded Year: {enrichment['apollo_founded']}")

        apollo_block = (
            "\n\nApollo.io Enrichment Data (use this as additional context):\n"
            + "\n".join(apollo_lines)
            if apollo_lines else ""
        )

        # ── Website content or informative fallback ────────────────────────────
        if scraped:
            website_section = f"Website Content Snippet:\n{scraped}"
        else:
            website_section = (
                "Website Content Snippet:\n"
                "[Website could not be scraped — rely on the CSV data, "
                "Apollo enrichment, and the company name / specialty below "
                "to infer operational details. Be specific to this company, "
                "not generic.]"
            )

        prompt = f"""Research this healthcare practice for Calyxr sales qualification.
Use ALL data provided — CSV fields, Apollo enrichment, and website content — to produce
a specific, differentiated analysis. Do NOT produce generic output.

Company: {company}
Website: {website or 'Not provided'}
Declared Specialty: {specialty or 'Not provided'}{csv_block}{apollo_block}

{website_section}

Analyze and return JSON with operational insights specific to THIS practice."""

        # BUG FIX: was max_tokens=500 — caused truncation of the 10-field JSON
        # response, which led to parse failures and all research fields being empty.
        return self._call_json(self.system, prompt, max_tokens=1000)
