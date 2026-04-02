"""
Apollo.io Enrichment Connector
Enriches company and contact data via the Apollo v1 REST API before ICP scoring.

Endpoints used:
  POST https://api.apollo.io/v1/organizations/enrich  — org data by domain / name
  POST https://api.apollo.io/v1/people/match          — contact enrichment by name / email

All methods return a flat dict that can be passed directly to
ICPQualificationAgent.evaluate(enrichment=...).
If the API key is missing or a call fails, an empty dict is returned so the
pipeline continues gracefully with the data already in the CSV.
"""

import logging
import re
import requests

log = logging.getLogger("calyxr")

APOLLO_API_BASE = "https://api.apollo.io/v1"

# EHR/EMR keywords Apollo might surface in technologies or description
_EHR_KEYWORDS = {
    "epic", "athenahealth", "athena", "eclinicalworks", "nextgen",
    "cerner", "meditech", "allscripts", "drchrono", "kareo",
    "advancedmd", "practice fusion", "modernizing medicine", "ehr", "emr",
}


def _clean_domain(url: str) -> str:
    """Strip protocol and path from a URL to get a bare domain."""
    url = (url or "").strip().lower()
    url = re.sub(r"^https?://", "", url)
    url = url.split("/")[0]  # drop any path
    return url


class ApolloConnector:
    """Thin wrapper around the Apollo.io v1 REST API."""

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type":  "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key":     self.api_key,
        })

    # ── Public methods ─────────────────────────────────────────────────────────

    def enrich_organization(self, domain: str = "", name: str = "") -> dict:
        """
        Enrich an organization by website domain or company name.
        Returns a flat dict of cleaned fields ready for the ICP prompt.
        """
        if not self.api_key:
            return {}

        payload: dict = {}
        clean = _clean_domain(domain)
        if clean:
            payload["domain"] = clean
        if name:
            payload["name"] = name.strip()
        if not payload:
            return {}

        try:
            resp = self._session.post(
                f"{APOLLO_API_BASE}/organizations/enrich",
                json=payload,
                timeout=12,
            )
            resp.raise_for_status()
            org = (resp.json() or {}).get("organization") or {}
            enriched = self._parse_org(org)
            if enriched:
                log.info(
                    f"[APOLLO] ✓ Org enriched: {enriched.get('apollo_name') or name} "
                    f"| employees={enriched.get('apollo_employees')} "
                    f"| revenue={enriched.get('apollo_revenue')} "
                    f"| state={enriched.get('apollo_state')}"
                )
            return enriched

        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response else "?"
            log.warning(f"[APOLLO] Org enrichment HTTP {code} for '{clean or name}': {exc}")
            return {}
        except Exception as exc:
            log.warning(f"[APOLLO] Org enrichment error for '{clean or name}': {exc}")
            return {}

    def match_contact(
        self,
        name: str = "",
        email: str = "",
        domain: str = "",
    ) -> dict:
        """
        Match and enrich a contact by name / email / domain.
        Returns a flat dict of cleaned contact fields.
        """
        if not self.api_key:
            return {}

        payload: dict = {}
        parts = (name or "").strip().split(" ", 1)
        if parts[0]:
            payload["first_name"] = parts[0]
        if len(parts) > 1 and parts[1]:
            payload["last_name"] = parts[1]
        if email:
            payload["email"] = email.strip()
        clean = _clean_domain(domain)
        if clean:
            payload["organization_domain"] = clean
        if not payload:
            return {}

        def _do_match(reveal_phone: bool) -> dict:
            p = {**payload}
            if reveal_phone:
                # reveal_phone_number is a paid Apollo feature; only sent on
                # first attempt — if the plan doesn't support it the API
                # returns 403, and we fall back to a call without it.
                p["reveal_phone_number"] = True
            resp = self._session.post(
                f"{APOLLO_API_BASE}/people/match",
                json=p,
                timeout=12,
            )
            resp.raise_for_status()
            return (resp.json() or {}).get("person") or {}

        try:
            # ── Attempt 1: with phone reveal ──────────────────────────────
            try:
                person = _do_match(reveal_phone=True)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 403:
                    # Plan doesn't support phone reveal — retry without it
                    log.warning(
                        f"[APOLLO] Phone reveal not available on this plan "
                        f"(403) — retrying without reveal_phone_number"
                    )
                    person = _do_match(reveal_phone=False)
                else:
                    raise  # re-raise non-403 HTTP errors

            enriched = self._parse_contact(person)
            if enriched:
                log.info(
                    f"[APOLLO] ✓ Contact enriched: {name or email} "
                    f"| title={enriched.get('apollo_contact_title')} "
                    f"| email={enriched.get('apollo_contact_email')} "
                    f"| phone={enriched.get('apollo_contact_phone')} "
                    f"| linkedin={enriched.get('apollo_contact_linkedin')}"
                )
            return enriched

        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response else "?"
            log.warning(f"[APOLLO] Contact match HTTP {code} for '{name or email}': {exc}")
            return {}
        except Exception as exc:
            log.warning(f"[APOLLO] Contact match error for '{name or email}': {exc}")
            return {}

    # ── Private parsers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_org(org: dict) -> dict:
        """Map raw Apollo org JSON → clean enrichment dict."""
        if not org:
            return {}

        # Revenue — prefer the printed string; fall back to building one from int
        rev_raw    = org.get("annual_revenue") or 0
        rev_str    = (org.get("annual_revenue_printed") or "").strip()
        if not rev_str and rev_raw:
            if rev_raw >= 1_000_000:
                rev_str = f"${rev_raw / 1_000_000:.1f}M"
            elif rev_raw >= 1_000:
                rev_str = f"${rev_raw / 1_000:.0f}K"
            else:
                rev_str = f"${rev_raw:,}"

        # Keywords + technologies
        keywords = [str(k).strip() for k in (org.get("keywords") or []) if k]
        techs    = [
            t.get("name", "").strip()
            for t in (org.get("technologies") or [])
            if t.get("name")
        ]

        # EHR signal from technologies / keywords / description
        tech_str = " ".join(techs + keywords + [org.get("short_description", "")]).lower()
        ehr_found = [k for k in _EHR_KEYWORDS if k in tech_str]

        return {
            # Core fields (override CSV if richer data available)
            "apollo_name":          (org.get("name") or "").strip(),
            "apollo_domain":        (org.get("primary_domain") or "").strip(),
            "apollo_phone":         (org.get("phone") or "").strip(),
            "apollo_linkedin":      (org.get("linkedin_url") or "").strip(),
            # Industry + classification
            "apollo_industry":      (org.get("industry") or "").strip(),
            "apollo_sub_industry":  (org.get("subindustry") or "").strip(),
            "apollo_sic_codes":     ", ".join(str(s) for s in (org.get("sic_codes") or [])),
            # Size
            "apollo_employees":     str(org.get("estimated_num_employees") or ""),
            "apollo_employee_range":str(org.get("employee_count") or ""),
            # Financials
            "apollo_revenue":       rev_str,
            # Location
            "apollo_city":          (org.get("city") or "").strip(),
            "apollo_state":         (org.get("state") or "").strip(),
            "apollo_country":       (org.get("country") or "").strip(),
            # Company intelligence
            "apollo_description":   (org.get("short_description") or "")[:500].strip(),
            "apollo_keywords":      ", ".join(keywords[:12]),
            "apollo_technologies":  ", ".join(techs[:20]),
            "apollo_ehr_signals":   ", ".join(ehr_found) if ehr_found else "",
            "apollo_founded":       str(org.get("founded_year") or ""),
            "apollo_num_locations": str(org.get("num_suborganizations") or ""),
        }

    @staticmethod
    def _parse_contact(person: dict) -> dict:
        """Map raw Apollo person JSON → clean enrichment dict."""
        if not person:
            return {}

        depts = person.get("departments") or []
        # Guard against None entries inside the list
        depts = [d for d in depts if d]

        # Phone — take first available number from phone_numbers array.
        # Validate it is actually a list to avoid iterating over a stray string.
        phones_raw = person.get("phone_numbers")
        phones = phones_raw if isinstance(phones_raw, list) else []
        phone_str = ""
        for ph in phones:
            if not isinstance(ph, dict):
                continue
            num = (ph.get("sanitized_number") or ph.get("raw_number") or "").strip()
            if num:
                phone_str = num
                break

        return {
            "apollo_contact_title":        (person.get("title") or "").strip(),
            "apollo_contact_seniority":    (person.get("seniority") or "").strip(),
            "apollo_contact_department":   str(depts[0]).strip() if depts else "",
            "apollo_contact_email":        (person.get("email") or "").strip(),
            "apollo_contact_phone":        phone_str,
            "apollo_contact_linkedin":     (person.get("linkedin_url") or "").strip(),
            "apollo_contact_city":         (person.get("city") or "").strip(),
            "apollo_contact_state":        (person.get("state") or "").strip(),
            "apollo_contact_country":      (person.get("country") or "").strip(),
            "apollo_contact_email_status": (person.get("email_status") or "").strip(),
        }
