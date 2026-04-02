"""
Agent 7: CRM Enrichment Agent
Pushes enriched account data to HubSpot via API.
"""

import logging
import requests

log = logging.getLogger("calyxr")


HUBSPOT_BASE = "https://api.hubapi.com"

# Map our enriched fields to HubSpot contact property names
# These must be created as custom properties in HubSpot first
FIELD_MAP = {
    "ICP Match":           "calyxr_icp_match",
    "ICP Score":           "calyxr_icp_score",
    "ICP Reason":          "calyxr_icp_reason",
    "Top Pain Point":      "calyxr_top_pain",
    "Pain Signals":        "calyxr_pain_signals",
    "RCM Risk Level":      "calyxr_rcm_risk",
    "AI Insight":          "calyxr_ai_insight",
    "Recommended Hook":    "calyxr_recommended_hook",
    "Practice Summary":    "calyxr_practice_summary",
    "Persona Fit":         "calyxr_persona_fit",
    "Email 1 Subject":     "calyxr_email1_subject",
    "Email 1 Body":        "calyxr_email1_body",
    "Email 2 Subject":     "calyxr_email2_subject",
    "Email 2 Body":        "calyxr_email2_body",
    "Email 3 Subject":     "calyxr_email3_subject",
    "Email 3 Body":        "calyxr_email3_body",
    "LinkedIn DM":         "calyxr_linkedin_dm",
}


class CRMEnrichmentAgent:
    def __init__(self, hubspot_api_key: str):
        self.api_key = hubspot_api_key
        self.headers = {
            "Authorization": f"Bearer {hubspot_api_key}",
            "Content-Type": "application/json"
        }

    def _find_contact(self, email: str) -> str | None:
        """Find existing HubSpot contact by email. Returns contact ID or None."""
        url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search"
        payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email
                }]
            }]
        }
        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    return results[0]["id"]
            elif resp.status_code == 401:
                log.error("[CRM] HubSpot search: 401 Unauthorized — check API key")
            else:
                log.warning(f"[CRM] HubSpot search returned {resp.status_code} for {email}")
        except requests.RequestException as exc:
            log.warning(f"[CRM] HubSpot search request failed for {email}: {exc}")
        return None

    def _build_properties(self, record: dict) -> dict:
        """Convert our record into HubSpot property format."""
        props = {}
        for our_key, hs_key in FIELD_MAP.items():
            value = record.get(our_key, "")
            if value and str(value) != "nan":
                props[hs_key] = str(value)[:500]  # HubSpot field limit
        return props

    def push(self, email: str, record: dict) -> bool:
        """Update or create a HubSpot contact with enriched data."""
        if not email or email == "nan":
            return False

        properties = self._build_properties(record)
        contact_id = self._find_contact(email)

        try:
            if contact_id:
                # Update existing contact
                url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts/{contact_id}"
                resp = requests.patch(url, headers=self.headers, json={"properties": properties}, timeout=10)
                success = resp.status_code == 200
                if not success:
                    log.warning(f"[CRM] HubSpot UPDATE {resp.status_code} for {email}: {resp.text[:500]}")
                print(f"    HubSpot UPDATE: {email} → {'OK' if success else resp.status_code}")
                return success
            else:
                # Create new contact
                properties["email"] = email
                contact_name_raw = str(record.get("Contact Name") or "").strip()
                if contact_name_raw and contact_name_raw != "nan":
                    name_parts = contact_name_raw.split(" ", 1)
                    properties["firstname"] = name_parts[0]
                    properties["lastname"]  = name_parts[1] if len(name_parts) > 1 else ""
                if record.get("Company"):
                    properties["company"] = record["Company"]
                if record.get("Contact Title"):
                    properties["jobtitle"] = record["Contact Title"]

                url = f"{HUBSPOT_BASE}/crm/v3/objects/contacts"
                resp = requests.post(url, headers=self.headers, json={"properties": properties}, timeout=10)
                success = resp.status_code in (200, 201)
                if not success:
                    log.warning(f"[CRM] HubSpot CREATE {resp.status_code} for {email}: {resp.text[:500]}")
                print(f"    HubSpot CREATE: {email} → {'OK' if success else resp.status_code}")
                return success
        except requests.RequestException as exc:
            log.warning(f"[CRM] HubSpot push request failed for {email}: {exc}")
            print(f"    HubSpot ERROR: {email} → {exc}")
            return False
