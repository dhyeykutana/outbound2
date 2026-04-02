"""
Calyxr Outbound Intelligence Engine
Multi-Agent Research & Personalization System

Run: python main.py --input apollo_export.csv --output hubspot_ready.csv
"""

import os
import sys
import time
import argparse
import pandas as pd
from secrets_loader import load_secrets

# Force UTF-8 output so emoji characters work on Windows terminals
sys.stdout.reconfigure(encoding="utf-8")

# Load secrets: AWS Secrets Manager on EC2, .env file locally
load_secrets()
from agents.icp_agent import ICPQualificationAgent
from agents.research_agent import CompanyResearchAgent
from agents.pain_agent import PainSignalAgent
from agents.contact_agent import ContactIntelligenceAgent
from agents.personalization_agent import PersonalizationAgent
from agents.email_agent import EmailGenerationAgent
from agents.crm_agent import CRMEnrichmentAgent

# ── Config ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HUBSPOT_API_KEY   = os.getenv("HUBSPOT_API_KEY",   "")
MODEL             = "claude-sonnet-4-20250514"
DELAY_BETWEEN     = 1.5   # seconds between API calls (rate limit safety)

if not ANTHROPIC_API_KEY:
    print("\n✗ Error: ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    sys.exit(1)

# ── Main Orchestrator ────────────────────────────────────────────────────────
def run_pipeline(input_csv: str, output_csv: str, push_to_hubspot: bool = False):
    try:
        df = pd.read_csv(input_csv)
    except FileNotFoundError:
        print(f"\n✗ Error: Input file not found: {input_csv}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n✗ Error reading CSV: {exc}")
        sys.exit(1)
    print(f"\n✅ Loaded {len(df)} accounts from {input_csv}")

    # Initialize all agents
    icp        = ICPQualificationAgent(ANTHROPIC_API_KEY, MODEL)
    researcher = CompanyResearchAgent(ANTHROPIC_API_KEY, MODEL)
    pain       = PainSignalAgent(ANTHROPIC_API_KEY, MODEL)
    contact    = ContactIntelligenceAgent(ANTHROPIC_API_KEY, MODEL)
    personal   = PersonalizationAgent(ANTHROPIC_API_KEY, MODEL)
    emailer    = EmailGenerationAgent(ANTHROPIC_API_KEY, MODEL)
    crm        = CRMEnrichmentAgent(HUBSPOT_API_KEY)

    results = []

    def _clean(val) -> str:
        """Convert a pandas cell value to str, treating NaN/None as empty."""
        return "" if pd.isna(val) else str(val).strip()

    for idx, row in df.iterrows():
        company       = _clean(row.get("Company",      ""))
        website       = _clean(row.get("Website",      ""))
        industry      = _clean(row.get("Industry",     ""))
        employees     = _clean(row.get("Employees",    ""))
        contact_name  = _clean(row.get("Contact Name", ""))
        contact_title = _clean(row.get("Title",        ""))
        contact_email = _clean(row.get("Email",        ""))
        specialty     = _clean(row.get("Specialty",    ""))

        print(f"\n[{idx+1}/{len(df)}] Processing: {company}")

        try:
            # Agent 1 — ICP Qualification
            print("  → Agent 1: ICP Qualification")
            icp_result = icp.evaluate(company, website, industry, employees, specialty)
            time.sleep(DELAY_BETWEEN)

            # Detect JSON parse failure (base._call_json returns {"raw": ...})
            if "icp_score" not in icp_result:
                print(f"  ⚠ Agent 1 JSON parse error for {company} — proceeding with neutral score")
                icp_result = {
                    "icp_score": 50, "icp_match": "MAYBE", "tier": "C",
                    "reason": "ICP response could not be parsed",
                }

            # Skip low-quality ICP matches (still record them so users can audit)
            if icp_result.get("icp_score", 0) < 40:
                print(f"  ✗ Skipped — ICP score too low ({icp_result.get('icp_score')})")
                results.append({
                    "Company":       company,
                    "Website":       website,
                    "Contact Name":  contact_name,
                    "Contact Title": contact_title,
                    "Contact Email": contact_email,
                    "Specialty":     specialty,
                    "ICP Match":     icp_result.get("icp_match", "NO"),
                    "ICP Score":     icp_result.get("icp_score", 0),
                    "ICP Reason":    icp_result.get("reason", ""),
                    "_status":       "SKIPPED_LOW_ICP",
                })
                continue

            # Agent 2 — Company Research
            print("  → Agent 2: Company Research")
            research_result = researcher.analyze(
                company, website, specialty,
                industry=industry,
                employees=employees,
            )
            time.sleep(DELAY_BETWEEN)

            # Fallback if research JSON failed to parse
            if "summary" not in research_result:
                print(f"  ⚠ Agent 2 JSON parse error for {company} — using CSV fallback")
                research_result = {
                    "summary": f"{company} — {specialty or industry or 'healthcare practice'}.",
                    "specialty": specialty or "Unknown",
                    "services": specialty or "Healthcare services",
                    "size_signal": "unknown",
                    "billing_complexity": "medium",
                    "emr_mentioned": "Unknown",
                    "insurance_heavy": False,
                    "multi_location": False,
                    "automation_signals": "None",
                    "admin_pain_indicators": "None",
                }

            # Agent 3 — Pain Signal Detection
            print("  → Agent 3: Pain Signal Detection")
            pain_result = pain.detect(company, research_result.get("summary", ""), specialty, employees)
            time.sleep(DELAY_BETWEEN)

            # Agent 4 — Contact Intelligence
            print("  → Agent 4: Contact Intelligence")
            contact_result = contact.evaluate(company, contact_name, contact_title, research_result.get("summary", ""))
            time.sleep(DELAY_BETWEEN)

            # Agent 5 — Personalization
            print("  → Agent 5: Personalization")
            personal_result = personal.generate(
                company, website, specialty,
                research_result.get("summary", ""),
                pain_result.get("top_pain", ""),
                contact_title
            )
            time.sleep(DELAY_BETWEEN)

            # Agent 6 — Email Generation
            print("  → Agent 6: Email Generation")
            email_result = emailer.write(
                company, contact_name, contact_title,
                personal_result.get("insight", ""),
                pain_result.get("top_pain", ""),
                specialty,
                research_summary=research_result.get("summary", ""),
            )
            time.sleep(DELAY_BETWEEN)

            # Compile row
            record = {
                "Company":              company,
                "Website":              website,
                "Contact Name":         contact_name,
                "Contact Title":        contact_title,
                "Contact Email":        contact_email,
                "Specialty":            specialty,
                # ICP
                "ICP Match":            icp_result.get("icp_match", ""),
                "ICP Score":            icp_result.get("icp_score", ""),
                "ICP Reason":           icp_result.get("reason", ""),
                # Research
                "Practice Summary":     research_result.get("summary", ""),
                "Practice Size Signal": research_result.get("size_signal", ""),
                "Billing Complexity":   research_result.get("billing_complexity", ""),
                # Pain
                "Top Pain Point":       pain_result.get("top_pain", ""),
                "Pain Signals":         pain_result.get("signals", ""),
                "RCM Risk Level":       pain_result.get("rcm_risk", ""),
                # Contact
                "Persona Fit":          contact_result.get("persona_fit", ""),
                "Decision Likelihood":  contact_result.get("decision_likelihood", ""),
                # Personalization
                "AI Insight":           personal_result.get("insight", ""),
                "Recommended Hook":     personal_result.get("hook", ""),
                # Emails
                "Email 1 Subject":      email_result.get("e1_subject", ""),
                "Email 1 Body":         email_result.get("e1_body", ""),
                "Email 2 Subject":      email_result.get("e2_subject", ""),
                "Email 2 Body":         email_result.get("e2_body", ""),
                "Email 3 Subject":      email_result.get("e3_subject", ""),
                "Email 3 Body":         email_result.get("e3_body", ""),
                "LinkedIn DM 1":        email_result.get("li_dm1", ""),
                "LinkedIn DM 2":        email_result.get("li_dm2", ""),
                "LinkedIn DM 3":        email_result.get("li_dm3", ""),
            }
            results.append(record)

            # Agent 7 — Push to HubSpot (optional)
            if push_to_hubspot and contact_email:
                print("  → Agent 7: HubSpot Push")
                crm.push(contact_email, record)

            print(f"  ✓ Done — ICP Score: {icp_result.get('icp_score')} | Pain: {pain_result.get('top_pain','')[:60]}")

        except Exception as e:
            print(f"  ✗ Error on {company}: {e}")
            continue

    # Save output
    out_df = pd.DataFrame(results)
    out_df.to_csv(output_csv, index=False)
    print(f"\n✅ Complete. {len(results)} accounts processed → saved to {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calyxr Outbound AI Engine")
    parser.add_argument("--input",          default="apollo_export.csv")
    parser.add_argument("--output",         default="hubspot_ready.csv")
    parser.add_argument("--push-hubspot",   action="store_true")
    args = parser.parse_args()

    run_pipeline(args.input, args.output, args.push_hubspot)
