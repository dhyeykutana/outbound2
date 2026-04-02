"""
SQLAlchemy database models for Calyxr Outbound Intelligence Engine.
Database backend: MySQL (via PyMySQL driver).
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ══════════════════════════════════════════════════════════════════════════════
#  ROLES  (super_admin / admin / analyst / viewer)
# ══════════════════════════════════════════════════════════════════════════════

class Role(db.Model):
    __tablename__ = "roles"

    id                 = db.Column(db.Integer, primary_key=True)
    name               = db.Column(db.String(50), unique=True, nullable=False)
    description        = db.Column(db.String(200), nullable=True)

    # Permission flags
    can_manage_users      = db.Column(db.Boolean, default=False)   # super_admin only
    can_manage_config     = db.Column(db.Boolean, default=False)   # admin+
    can_manage_prompts    = db.Column(db.Boolean, default=False)   # admin+ (global prompts)
    can_manage_campaigns  = db.Column(db.Boolean, default=False)   # analyst+ (campaigns & campaign prompts)
    can_run_pipeline      = db.Column(db.Boolean, default=False)   # analyst+
    can_view_results      = db.Column(db.Boolean, default=True)    # all
    can_export            = db.Column(db.Boolean, default=False)   # analyst+

    users = db.relationship("User", backref="role", lazy=True)

    def to_dict(self):
        return {
            "id":                    self.id,
            "name":                  self.name,
            "description":           self.description or "",
            "can_manage_users":      self.can_manage_users,
            "can_manage_config":     self.can_manage_config,
            "can_manage_prompts":    self.can_manage_prompts,
            "can_manage_campaigns":  self.can_manage_campaigns,
            "can_run_pipeline":      self.can_run_pipeline,
            "can_view_results":      self.can_view_results,
            "can_export":            self.can_export,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  USERS
# ══════════════════════════════════════════════════════════════════════════════

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(200), nullable=False)
    email         = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role_id       = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    last_login    = db.Column(db.DateTime, nullable=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id":         self.id,
            "name":       self.name,
            "email":      self.email,
            "role":       self.role.name if self.role else None,
            "role_id":    self.role_id,
            "is_active":  self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "last_login": self.last_login.strftime("%Y-%m-%d %H:%M:%S") if self.last_login else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  APP CONFIGURATION  (single-row settings table)
# ══════════════════════════════════════════════════════════════════════════════

class AppConfig(db.Model):
    __tablename__ = "app_config"

    id            = db.Column(db.Integer, primary_key=True)
    app_name      = db.Column(db.String(200), nullable=False,
                              default="Calyxr Outbound Intelligence Engine")
    company_name  = db.Column(db.String(100), nullable=False, default="Calyxr")
    model         = db.Column(db.String(100), nullable=False,
                              default="claude-sonnet-4-20250514")
    icp_threshold = db.Column(db.Integer,  nullable=False, default=40)
    delay_between = db.Column(db.Float,    nullable=False, default=1.5)
    max_accounts  = db.Column(db.Integer,  nullable=False, default=0)
    footer_text   = db.Column(db.String(500), nullable=False,
                              default="© 2025 Calyxr Outbound Intelligence Engine — Powered by Claude AI")


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT SYSTEM PROMPTS  (editable per agent)
# ══════════════════════════════════════════════════════════════════════════════

class AgentPrompt(db.Model):
    __tablename__ = "agent_prompts"

    id            = db.Column(db.Integer, primary_key=True)
    agent_key     = db.Column(db.String(50), unique=True, nullable=False)   # e.g. "icp"
    agent_name    = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.String(300), nullable=True)
    system_prompt = db.Column(db.Text, nullable=False)
    is_enabled    = db.Column(db.Boolean, nullable=False, default=True)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow)


# ══════════════════════════════════════════════════════════════════════════════
#  CAMPAIGNS  (named outreach campaigns with per-agent prompt overrides)
# ══════════════════════════════════════════════════════════════════════════════

class Campaign(db.Model):
    __tablename__ = "campaigns"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    created_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)
    is_active   = db.Column(db.Boolean, default=True)

    prompts     = db.relationship(
        "CampaignPrompt", backref="campaign", lazy=True,
        cascade="all, delete-orphan"
    )
    runs        = db.relationship("PipelineRun", backref="campaign", lazy=True)

    def to_dict(self, include_prompts: bool = False) -> dict:
        d = {
            "id":          self.id,
            "name":        self.name,
            "description": self.description or "",
            "created_by":  self.created_by,
            "created_at":  self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "updated_at":  self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else None,
            "is_active":   self.is_active,
        }
        if include_prompts:
            d["prompts"] = {p.agent_key: p.system_prompt for p in self.prompts}
        return d


class CampaignPrompt(db.Model):
    """
    Per-agent system-prompt override scoped to a campaign.
    If an agent_key has no row here the global AgentPrompt is used instead.
    """
    __tablename__ = "campaign_prompts"

    id            = db.Column(db.Integer, primary_key=True)
    campaign_id   = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    agent_key     = db.Column(db.String(50), nullable=False)   # icp / research / pain / contact / personalization / email
    system_prompt = db.Column(db.Text, nullable=False)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("campaign_id", "agent_key", name="uq_campaign_agent"),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE RUNS  (one row per pipeline execution)
# ══════════════════════════════════════════════════════════════════════════════

class PipelineRun(db.Model):
    __tablename__ = "pipeline_runs"

    id               = db.Column(db.Integer, primary_key=True)
    started_at       = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at     = db.Column(db.DateTime, nullable=True)
    status           = db.Column(db.String(20), default="running")   # running / complete / error
    total_accounts   = db.Column(db.Integer, default=0)
    processed        = db.Column(db.Integer, default=0)
    skipped          = db.Column(db.Integer, default=0)
    errors           = db.Column(db.Integer, default=0)
    duration_seconds = db.Column(db.Float, nullable=True)
    csv_filename     = db.Column(db.String(300), nullable=True)
    campaign_id      = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=True)
    user_id          = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    user    = db.relationship("User",           backref="runs",    lazy=True, foreign_keys=[user_id])
    results = db.relationship(
        "PipelineResult", backref="run", lazy=True, cascade="all, delete-orphan"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE RESULTS  (one row per processed account)
# ══════════════════════════════════════════════════════════════════════════════

class PipelineResult(db.Model):
    __tablename__ = "pipeline_results"

    id                  = db.Column(db.Integer, primary_key=True)
    run_id              = db.Column(db.Integer, db.ForeignKey("pipeline_runs.id"), nullable=False)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    # Contact / company
    company             = db.Column(db.String(200))
    website             = db.Column(db.String(300))
    contact_name        = db.Column(db.String(200))
    contact_title       = db.Column(db.String(200))
    contact_email       = db.Column(db.String(200))
    specialty           = db.Column(db.String(200))

    # ICP Qualification (Agent 1)
    icp_match           = db.Column(db.String(50))
    icp_score           = db.Column(db.Integer)
    icp_reason          = db.Column(db.Text)

    # Company Research (Agent 2)
    practice_summary    = db.Column(db.Text)
    size_signal         = db.Column(db.String(100))
    billing_complexity  = db.Column(db.String(100))

    # Pain Signals (Agent 3)
    top_pain            = db.Column(db.Text)
    pain_signals        = db.Column(db.Text)
    rcm_risk            = db.Column(db.String(50))

    # Contact Intelligence (Agent 4)
    persona_fit         = db.Column(db.String(100))
    decision_likelihood = db.Column(db.String(50))

    # Personalization (Agent 5)
    ai_insight          = db.Column(db.Text)
    recommended_hook    = db.Column(db.Text)

    # Email Generation (Agent 6)
    e1_subject          = db.Column(db.String(300))
    e1_body             = db.Column(db.Text)
    e2_subject          = db.Column(db.String(300))
    e2_body             = db.Column(db.Text)
    e3_subject          = db.Column(db.String(300))
    e3_body             = db.Column(db.Text)
    linkedin_dm         = db.Column(db.Text)   # legacy — kept for backward compat
    li_dm1              = db.Column(db.Text)   # LinkedIn DM 1: connection request (pattern interrupt)
    li_dm2              = db.Column(db.Text)   # LinkedIn DM 2: follow-up (Calyxr + proof)
    li_dm3              = db.Column(db.Text)   # LinkedIn DM 3: final (ROI + low-commitment close)

    # Data Sources — legacy short labels (kept for backward compat)
    icp_data_sources    = db.Column(db.String(200))
    research_sources    = db.Column(db.String(200))
    # Data Sources — exact per-agent strings (include model name)
    agent1_sources      = db.Column(db.Text)
    agent2_sources      = db.Column(db.Text)
    agent3_sources      = db.Column(db.Text)
    agent4_sources      = db.Column(db.Text)
    agent5_sources      = db.Column(db.Text)
    agent6_sources      = db.Column(db.Text)
    # Apollo enrichment — company
    apollo_company          = db.Column(db.String(200))
    apollo_domain           = db.Column(db.String(300))
    apollo_industry         = db.Column(db.String(200))
    apollo_revenue          = db.Column(db.String(100))
    apollo_employees        = db.Column(db.String(50))
    apollo_technologies     = db.Column(db.Text)
    apollo_ehr_signals      = db.Column(db.String(300))
    apollo_keywords         = db.Column(db.Text)
    apollo_description      = db.Column(db.Text)
    apollo_city             = db.Column(db.String(100))
    apollo_state            = db.Column(db.String(100))
    apollo_country          = db.Column(db.String(100))
    apollo_founded          = db.Column(db.String(10))
    apollo_num_locations    = db.Column(db.String(20))
    apollo_company_linkedin = db.Column(db.String(300))
    apollo_company_phone    = db.Column(db.String(100))
    # Apollo enrichment — contact
    apollo_contact_email      = db.Column(db.String(200))
    apollo_contact_phone      = db.Column(db.String(100))
    apollo_contact_linkedin   = db.Column(db.String(300))
    apollo_contact_title      = db.Column(db.String(200))
    apollo_contact_seniority  = db.Column(db.String(100))
    apollo_contact_department = db.Column(db.String(100))
    apollo_contact_city       = db.Column(db.String(100))
    apollo_contact_state      = db.Column(db.String(100))
    apollo_contact_country    = db.Column(db.String(100))

    def to_dict(self) -> dict:
        """Return a flat dict matching the pipeline result record format."""
        return {
            "Company":              self.company             or "",
            "Website":              self.website             or "",
            "Contact Name":         self.contact_name        or "",
            "Contact Title":        self.contact_title       or "",
            "Contact Email":        self.contact_email       or "",
            "Specialty":            self.specialty           or "",
            "ICP Match":            self.icp_match           or "",
            "ICP Score":            self.icp_score           or 0,
            "ICP Reason":           self.icp_reason          or "",
            "Practice Summary":     self.practice_summary    or "",
            "Practice Size Signal": self.size_signal         or "",
            "Billing Complexity":   self.billing_complexity  or "",
            "Top Pain Point":       self.top_pain            or "",
            "Pain Signals":         self.pain_signals        or "",
            "RCM Risk Level":       self.rcm_risk            or "",
            "Persona Fit":          self.persona_fit         or "",
            "Decision Likelihood":  self.decision_likelihood or "",
            "AI Insight":           self.ai_insight          or "",
            "Recommended Hook":     self.recommended_hook    or "",
            "Email 1 Subject":      self.e1_subject          or "",
            "Email 1 Body":         self.e1_body             or "",
            "Email 2 Subject":      self.e2_subject          or "",
            "Email 2 Body":         self.e2_body             or "",
            "Email 3 Subject":      self.e3_subject          or "",
            "Email 3 Body":         self.e3_body             or "",
            "LinkedIn DM 1":        self.li_dm1              or "",
            "LinkedIn DM 2":        self.li_dm2              or "",
            "LinkedIn DM 3":        self.li_dm3              or "",
            # Exact per-agent data sources (model included)
            "Agent 1 (ICP) Sources":             self.agent1_sources   or "",
            "Agent 2 (Research) Sources":        self.agent2_sources   or "",
            "Agent 3 (Pain) Sources":            self.agent3_sources   or "",
            "Agent 4 (Contact) Sources":         self.agent4_sources   or "",
            "Agent 5 (Personalisation) Sources": self.agent5_sources   or "",
            "Agent 6 (Email) Sources":           self.agent6_sources   or "",
            # Apollo enrichment — company
            "Apollo Company":          self.apollo_company          or "",
            "Apollo Domain":           self.apollo_domain           or "",
            "Apollo Industry":         self.apollo_industry         or "",
            "Apollo Revenue":          self.apollo_revenue          or "",
            "Apollo Employees":        self.apollo_employees        or "",
            "Apollo Technologies":     self.apollo_technologies     or "",
            "Apollo EHR Signals":      self.apollo_ehr_signals      or "",
            "Apollo Keywords":         self.apollo_keywords         or "",
            "Apollo Description":      self.apollo_description      or "",
            "Apollo City":             self.apollo_city             or "",
            "Apollo State":            self.apollo_state            or "",
            "Apollo Country":          self.apollo_country          or "",
            "Apollo Founded":          self.apollo_founded          or "",
            "Apollo Num Locations":    self.apollo_num_locations    or "",
            "Apollo Company LinkedIn": self.apollo_company_linkedin or "",
            "Apollo Company Phone":    self.apollo_company_phone    or "",
            # Apollo enrichment — contact
            "Apollo Contact Email":      self.apollo_contact_email      or "",
            "Apollo Contact Phone":      self.apollo_contact_phone      or "",
            "Apollo Contact LinkedIn":   self.apollo_contact_linkedin   or "",
            "Apollo Contact Title":      self.apollo_contact_title      or "",
            "Apollo Contact Seniority":  self.apollo_contact_seniority  or "",
            "Apollo Contact Department": self.apollo_contact_department or "",
            "Apollo Contact City":       self.apollo_contact_city       or "",
            "Apollo Contact State":      self.apollo_contact_state      or "",
            "Apollo Contact Country":    self.apollo_contact_country    or "",
        }
