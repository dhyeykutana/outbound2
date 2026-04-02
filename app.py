"""
Calyxr Outbound Intelligence Engine — Web Dashboard
Flask API server that wraps the multi-agent pipeline with a real-time UI.

Database: MySQL (configure credentials in .env — see .env.example)

Run: python app.py
Then open: http://localhost:5000
"""

import os
import sys
import json
import time
import threading
import queue
import io
import logging
import logging.handlers
import traceback
import concurrent.futures
from datetime import datetime, timezone

import pymysql
import pandas as pd
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_from_directory, redirect
from flask_login import login_required, current_user
from secrets_loader import load_secrets

# Force UTF-8 on Windows
sys.stdout.reconfigure(encoding="utf-8")

# Load secrets: AWS Secrets Manager on EC2, .env file locally
load_secrets()

# Add current dir to path so agent imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.icp_agent          import ICPQualificationAgent,  SYSTEM as _ICP_SYS
from agents.research_agent     import CompanyResearchAgent,    SYSTEM as _RESEARCH_SYS
from agents.pain_agent         import PainSignalAgent,         SYSTEM as _PAIN_SYS
from agents.contact_agent      import ContactIntelligenceAgent, SYSTEM as _CONTACT_SYS
from agents.personalization_agent import PersonalizationAgent, SYSTEM as _PERSONAL_SYS
from agents.email_agent        import EmailGenerationAgent,    SYSTEM as _EMAIL_SYS
from agents.apollo_connector   import ApolloConnector
from database                  import db, AppConfig, AgentPrompt, PipelineRun, PipelineResult, Campaign, CampaignPrompt
from auth                      import auth_bp, init_login_manager, seed_roles, permission_required


# ══════════════════════════════════════════════════════════════════════════════
#  DEFAULT AGENT PROMPTS  (used to seed DB on first run)
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_AGENT_PROMPTS = {
    "icp": {
        "name":        "Agent 1 — ICP Qualification",
        "description": "Evaluates whether a company fits the Ideal Customer Profile",
        "default":     _ICP_SYS,
    },
    "research": {
        "name":        "Agent 2 — Company Research",
        "description": "Analyses the practice website and returns operational signals",
        "default":     _RESEARCH_SYS,
    },
    "pain": {
        "name":        "Agent 3 — Pain Signal Detection",
        "description": "Detects likely RCM and operational pain points",
        "default":     _PAIN_SYS,
    },
    "contact": {
        "name":        "Agent 4 — Contact Intelligence",
        "description": "Evaluates the best contact and their decision-making likelihood",
        "default":     _CONTACT_SYS,
    },
    "personalization": {
        "name":        "Agent 5 — Personalization",
        "description": "Generates highly specific outreach insights",
        "default":     _PERSONAL_SYS,
    },
    "email": {
        "name":        "Agent 6 — Email Generation",
        "description": "Writes outbound emails and LinkedIn DMs",
        "default":     _EMAIL_SYS,
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING SETUP
# ══════════════════════════════════════════════════════════════════════════════

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR   = os.path.join(_BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_today   = datetime.now().strftime("%Y-%m-%d")
LOG_FILE = os.path.join(LOG_DIR, f"calyxr_{_today}.log")

_LOG_FMT = logging.Formatter(
    fmt="[%(asctime)s] [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_hdl = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=30, encoding="utf-8",
)
_file_hdl.setFormatter(_LOG_FMT)
_file_hdl.setLevel(logging.DEBUG)
_file_hdl.suffix = "%Y-%m-%d"

_con_hdl = logging.StreamHandler(sys.stdout)
_con_hdl.setFormatter(_LOG_FMT)
_con_hdl.setLevel(logging.INFO)

log = logging.getLogger("calyxr")
log.setLevel(logging.DEBUG)
log.propagate = False
log.addHandler(_file_hdl)
log.addHandler(_con_hdl)

for _wz in ("werkzeug", "flask.app"):
    logging.getLogger(_wz).addHandler(_file_hdl)

log.info("=" * 70)
log.info("Calyxr Outbound Intelligence Engine — startup")
log.info(f"Log file  : {LOG_FILE}")
log.info("=" * 70)


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  (API keys + MySQL)
# ══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
HUBSPOT_API_KEY   = os.getenv("HUBSPOT_API_KEY",   "")
APOLLO_API_KEY    = os.getenv("APOLLO_API_KEY",    "")
SECRET_KEY        = os.getenv("SECRET_KEY", "calyxr-change-this-in-production-env")

# Apollo connector (None when no key — pipeline skips enrichment gracefully)
apollo_connector = ApolloConnector(APOLLO_API_KEY) if APOLLO_API_KEY else None

MYSQL_HOST     = os.getenv("MYSQL_HOST",     "localhost")
MYSQL_PORT     = os.getenv("MYSQL_PORT",     "3306")
MYSQL_USER     = os.getenv("MYSQL_USER",     "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "calyxr_db")

_DB_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

# Mutable app config (mirrors AppConfig DB row; updated on POST /api/config)
app_config = {
    "app_name":      "OutreachIQ Outbound Intelligence Engine",
    "company_name":  "OutreachIQ",
    "model":         "claude-sonnet-4-20250514",
    "icp_threshold": 40,
    "delay_between": 1.5,
    "max_accounts":  0,
    "footer_text":   "© 2026 OutreachIQ Outbound Intelligence Engine",
}

log.info(f"Model     : {app_config['model']}")
log.info(f"API key   : {'✓ loaded' if ANTHROPIC_API_KEY else '✗ MISSING'}")
log.info(f"MySQL     : {MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}")


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK APP  +  SQLAlchemy
# ══════════════════════════════════════════════════════════════════════════════

_DIST_DIR = os.path.join(_BASE_DIR, "templates", "dist")

app = Flask(__name__, static_folder=_DIST_DIR, static_url_path="/dist")
app.config["SECRET_KEY"]                 = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"]        = 16 * 1024 * 1024
app.config["SQLALCHEMY_DATABASE_URI"]   = _DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# ── Session lifetime ───────────────────────────────────────────────────────
# Hard server-side limit: session cookie expires after 2 hours regardless.
# The client-side idle timer (15 min) will always fire well before this.
from datetime import timedelta
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
app.config["SESSION_COOKIE_HTTPONLY"]    = True
app.config["SESSION_COOKIE_SAMESITE"]   = "Lax"
app.config["SESSION_COOKIE_SECURE"]     = os.getenv("FLASK_ENV", "development") == "production"
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping":    True,
    "pool_recycle":     3600,
    "connect_args":     {"connect_timeout": 10},
}

db.init_app(app)
init_login_manager(app)

# Register blueprints
app.register_blueprint(auth_bp)


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE INITIALISATION  (called once at startup)
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_mysql_database():
    """Create the MySQL database + run pre-ORM column migrations."""
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=int(MYSQL_PORT),
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            charset="utf8mb4",
            connect_timeout=10,
        )
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.execute(f"USE `{MYSQL_DATABASE}`")

            # ── Pre-ORM column migrations ──────────────────────────────────
            # These must run BEFORE SQLAlchemy loads any model that references
            # the new columns, otherwise ORM SELECTs fail on startup.
            _pre_migrations = [
                # agent_prompts
                ("agent_prompts",
                 "ALTER TABLE agent_prompts "
                 "ADD COLUMN IF NOT EXISTS is_enabled TINYINT(1) NOT NULL DEFAULT 1"),
                # roles
                ("roles",
                 "ALTER TABLE roles "
                 "ADD COLUMN IF NOT EXISTS can_manage_campaigns TINYINT(1) NOT NULL DEFAULT 0"),
            ]
            for tbl, sql in _pre_migrations:
                try:
                    cur.execute(sql)
                    log.debug(f"DB pre-migration OK: {tbl}")
                except Exception as _me:
                    log.debug(f"DB pre-migration skipped ({tbl}): {_me}")

        conn.commit()
        conn.close()
        log.info(f"MySQL database '{MYSQL_DATABASE}' ready.")
    except Exception as exc:
        log.error(f"MySQL setup failed: {exc}")
        log.error(
            "Check that MySQL is running and your credentials in .env are correct.\n"
            "Required .env keys: MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE"
        )
        raise


def _init_db():
    """Create tables + seed default rows inside app context."""
    global app_config

    _ensure_mysql_database()

    with app.app_context():
        db.create_all()

        # ── Seed / load AppConfig ────────────────────────────────────────
        cfg = AppConfig.query.first()
        if cfg is None:
            cfg = AppConfig(**{k: v for k, v in app_config.items()})
            db.session.add(cfg)
            db.session.commit()
            log.info("DB: Created default AppConfig row.")
        else:
            app_config["app_name"]      = cfg.app_name
            app_config["company_name"]  = cfg.company_name
            app_config["model"]         = cfg.model
            app_config["icp_threshold"] = cfg.icp_threshold
            app_config["delay_between"] = cfg.delay_between
            app_config["max_accounts"]  = cfg.max_accounts
            app_config["footer_text"]   = cfg.footer_text
            log.info("DB: Loaded AppConfig from database.")

        # ── Seed AgentPrompts (insert defaults if not present) ───────────
        for key, info in _DEFAULT_AGENT_PROMPTS.items():
            if not AgentPrompt.query.filter_by(agent_key=key).first():
                db.session.add(AgentPrompt(
                    agent_key=key,
                    agent_name=info["name"],
                    description=info["description"],
                    system_prompt=info["default"],
                    updated_at=datetime.now(timezone.utc),
                ))
        db.session.commit()
        log.info("DB: Agent prompts seeded / verified.")

        # ── Add any new columns that may be missing from existing tables ──
        # Safe: ADD COLUMN IF NOT EXISTS is a no-op when the column already exists.
        _new_cols = [
            "e3_subject VARCHAR(300) NULL",
            "icp_data_sources VARCHAR(200) NULL",
            "research_sources VARCHAR(200) NULL",
            "agent1_sources TEXT NULL",
            "agent2_sources TEXT NULL",
            "agent3_sources TEXT NULL",
            "agent4_sources TEXT NULL",
            "agent5_sources TEXT NULL",
            "agent6_sources TEXT NULL",
            # Apollo — company (original set)
            "apollo_company VARCHAR(200) NULL",
            "apollo_revenue VARCHAR(100) NULL",
            "apollo_employees VARCHAR(50) NULL",
            "apollo_technologies TEXT NULL",
            "apollo_ehr_signals VARCHAR(300) NULL",
            "apollo_state VARCHAR(100) NULL",
            "apollo_country VARCHAR(100) NULL",
            "apollo_company_linkedin VARCHAR(300) NULL",
            "apollo_company_phone VARCHAR(100) NULL",
            # Apollo — contact (original set)
            "apollo_contact_email VARCHAR(200) NULL",
            "apollo_contact_phone VARCHAR(100) NULL",
            "apollo_contact_linkedin VARCHAR(300) NULL",
            # Apollo — company (extended fields)
            "apollo_domain VARCHAR(300) NULL",
            "apollo_industry VARCHAR(200) NULL",
            "apollo_keywords TEXT NULL",
            "apollo_description TEXT NULL",
            "apollo_city VARCHAR(100) NULL",
            "apollo_founded VARCHAR(10) NULL",
            "apollo_num_locations VARCHAR(20) NULL",
            # Apollo — contact (extended fields)
            "apollo_contact_title VARCHAR(200) NULL",
            "apollo_contact_seniority VARCHAR(100) NULL",
            "apollo_contact_department VARCHAR(100) NULL",
            "apollo_contact_city VARCHAR(100) NULL",
            "apollo_contact_state VARCHAR(100) NULL",
            "apollo_contact_country VARCHAR(100) NULL",
            # LinkedIn DM sequence (3 personalised messages)
            "li_dm1 LONGTEXT NULL",
            "li_dm2 LONGTEXT NULL",
            "li_dm3 LONGTEXT NULL",
        ]
        try:
            with db.engine.connect() as conn:
                for col_def in _new_cols:
                    conn.execute(db.text(
                        f"ALTER TABLE pipeline_results ADD COLUMN IF NOT EXISTS {col_def}"
                    ))
                conn.commit()
                log.debug("DB: column migrations applied OK")
        except Exception as _col_err:
            log.debug(f"DB: column migration skipped ({_col_err})")

        # ── Seed Roles ───────────────────────────────────────────────────
        seed_roles()


_init_db()


# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════════════

pipeline_state = {
    "status":        "idle",
    "results":       [],
    "total":         0,
    "current":       0,
    "stop_requested": False,
    "stats": {
        "total_accounts":   0,
        "icp_qualified":    0,
        "avg_icp_score":    0,
        "emails_generated": 0,
    },
}
progress_queue: queue.Queue = queue.Queue()
uploaded_df        = None
uploaded_csv_name  = None
active_campaign_id = None   # campaign selected for the current/next run
_run_user_id       = None   # user who triggered the current run (captured in request context)


# ── Instant-stop helpers ──────────────────────────────────────────────────────

class _StopPipeline(Exception):
    """Raised immediately when the user clicks Force Stop."""


# A dedicated thread-pool so each agent API call runs in its own thread.
# This lets the pipeline loop poll the stop flag every 250 ms and
# abandon the wait the instant stop is requested — without waiting for
# the blocking HTTP call to finish on its own.
_agent_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent")


def _run_agent(fn, *args, **kwargs):
    """
    Submit fn(*args, **kwargs) to the agent executor and wait for the result.
    Polls pipeline_state["stop_requested"] every 250 ms.
    Raises _StopPipeline immediately if a stop is requested, even while the
    underlying Claude API call is still in flight.
    """
    future = _agent_executor.submit(fn, *args, **kwargs)
    while True:
        if pipeline_state.get("stop_requested"):
            raise _StopPipeline()
        try:
            return future.result(timeout=0.25)
        except concurrent.futures.TimeoutError:
            continue  # keep polling


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _emit(type_: str, msg: str):
    """Push SSE event and mirror to log file."""
    progress_queue.put({"type": type_, "msg": msg})
    if type_ == "error":
        log.error(f"[PIPELINE] {msg}")
    elif type_ == "skip":
        log.warning(f"[PIPELINE] {msg}")
    elif type_ == "complete":
        log.info(f"[PIPELINE] {msg}")
    else:
        log.debug(f"[PIPELINE] {msg}")


def _domain_from_url(url: str) -> str:
    """Extract bare domain (no www) from a URL string."""
    if not url or url in ("nan", ""):
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return ""


# ── CSV Column Aliases ─────────────────────────────────────────────────────────
# Maps every known variation → standard field name used by the pipeline.
_COLUMN_ALIASES: dict[str, str] = {
    # Company
    "company":               "Company",
    "company name":          "Company",
    "company_name":          "Company",
    "account name":          "Company",
    "account":               "Company",
    "organization":          "Company",
    "organization name":     "Company",
    "organisation":          "Company",
    "business name":         "Company",
    "practice name":         "Company",
    "employer":              "Company",
    # Website
    "website":               "Website",
    "website url":           "Website",
    "website_url":           "Website",
    "url":                   "Website",
    "web":                   "Website",
    "homepage":              "Website",
    "domain":                "Website",
    "company website":       "Website",
    "company domain":        "Website",
    # Industry
    "industry":              "Industry",
    "industry name":         "Industry",
    "industry_name":         "Industry",
    "sector":                "Industry",
    "vertical":              "Industry",
    "company industry":      "Industry",
    # Employees
    "employees":             "Employees",
    "employee count":        "Employees",
    "employee_count":        "Employees",
    "# employees":           "Employees",
    "num employees":         "Employees",
    "number of employees":   "Employees",
    "headcount":             "Employees",
    "company size":          "Employees",
    "employees (all)":       "Employees",
    "size":                  "Employees",
    "staff count":           "Employees",
    "total employees":       "Employees",
    # Contact Name (full)
    "contact name":          "Contact Name",
    "contact_name":          "Contact Name",
    "full name":             "Contact Name",
    "full_name":             "Contact Name",
    "name":                  "Contact Name",
    "contact":               "Contact Name",
    "person name":           "Contact Name",
    "person_name":           "Contact Name",
    "prospect name":         "Contact Name",
    # First / Last handled separately below
    "first name":            "_First Name",
    "first_name":            "_First Name",
    "firstname":             "_First Name",
    "given name":            "_First Name",
    "last name":             "_Last Name",
    "last_name":             "_Last Name",
    "lastname":              "_Last Name",
    "surname":               "_Last Name",
    "family name":           "_Last Name",
    # Title
    "title":                 "Title",
    "job title":             "Title",
    "job_title":             "Title",
    "position":              "Title",
    "contact title":         "Title",
    "role":                  "Title",
    "job role":              "Title",
    "job position":          "Title",
    "designation":           "Title",
    "person title":          "Title",
    # Email
    "email":                 "Email",
    "email address":         "Email",
    "email_address":         "Email",
    "contact email":         "Email",
    "work email":            "Email",
    "work_email":            "Email",
    "email 1":               "Email",
    "primary email":         "Email",
    "business email":        "Email",
    # Specialty
    "specialty":             "Specialty",
    "speciality":            "Specialty",
    "medical specialty":     "Specialty",
    "medical_specialty":     "Specialty",
    "practice type":         "Specialty",
    "practice_type":         "Specialty",
    "subspecialty":          "Specialty",
    "practice specialty":    "Specialty",
    # State
    "state":                 "State",
    "company state":         "State",
    "province":              "State",
    "us state":              "State",
    "region":                "State",
    # Country
    "country":               "Country",
    "company country":       "Country",
    "nation":                "Country",
    "country code":          "Country",
    # Annual Revenue
    "annual revenue":        "Annual Revenue",
    "annual_revenue":        "Annual Revenue",
    "revenue":               "Annual Revenue",
    "annual revenue (usd)":  "Annual Revenue",
    "yearly revenue":        "Annual Revenue",
    "total revenue":         "Annual Revenue",
    "arr":                   "Annual Revenue",
    # Phone (contact)
    "phone":                 "Phone",
    "phone number":          "Phone",
    "phone_number":          "Phone",
    "mobile phone":          "Phone",
    "mobile_phone":          "Phone",
    "work phone":            "Phone",
    "direct phone":          "Phone",
    "work direct phone":     "Phone",
    "mobile phone number":   "Phone",
    "contact phone":         "Phone",
    "telephone":             "Phone",
    # LinkedIn URL (contact)
    "linkedin":              "LinkedIn",
    "linkedin url":          "LinkedIn",
    "linkedin_url":          "LinkedIn",
    "person linkedin url":   "LinkedIn",
    "contact linkedin":      "LinkedIn",
    "person linkedin":       "LinkedIn",
    # Company LinkedIn
    "company linkedin url":  "Company LinkedIn",
    "company linkedin":      "Company LinkedIn",
    "linkedin company url":  "Company LinkedIn",
    "organization linkedin": "Company LinkedIn",
    # Company Phone
    "company phone":         "Company Phone",
    "company phone number":  "Company Phone",
    "hq phone":              "Company Phone",
    "company hq phone":      "Company Phone",
    # Technologies
    "technologies":          "Technologies",
    "technology":            "Technologies",
    "tech stack":            "Technologies",
    "current technologies":  "Technologies",
    # Description
    "description":           "Description",
    "company description":   "Description",
    "short description":     "Description",
    "about":                 "Description",
    "overview":              "Description",
    # City
    "city":                  "City",
    "company city":          "City",
    "hq city":               "City",
    # Founded
    "founded":               "Founded",
    "founded year":          "Founded",
    "year founded":          "Founded",
    "founding year":         "Founded",
    # Keywords
    "keywords":              "Keywords",
    "company keywords":      "Keywords",
    "tags":                  "Keywords",
    # Extra employee aliases
    "# employees":           "Employees",
    "num employees":         "Employees",
    "headcount":             "Employees",
    "employee count":        "Employees",
    "number of employees":   "Employees",
}


def _normalize_csv_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Auto-detect and rename CSV columns to the standard field names the
    pipeline expects. Also combines First Name + Last Name → Contact Name.

    Returns (normalized_df, mapping_report) where mapping_report describes
    which source columns were mapped to which target columns.
    """
    df = df.copy()
    rename_map: dict[str, str] = {}   # original_col → standard_col
    mapping_report: dict[str, str] = {}

    for col in df.columns:
        key = col.strip().lower()
        if key in _COLUMN_ALIASES:
            target = _COLUMN_ALIASES[key]
            if target not in df.columns:          # don't clobber an existing standard col
                rename_map[col] = target

    df.rename(columns=rename_map, inplace=True)
    for orig, std in rename_map.items():
        mapping_report[orig] = std

    # ── Combine First + Last Name → Contact Name ─────────────────────────────
    has_first = "_First Name" in df.columns
    has_last  = "_Last Name"  in df.columns
    contact_missing = "Contact Name" not in df.columns or df["Contact Name"].astype(str).str.strip().replace("nan", "").eq("").all()

    if contact_missing and (has_first or has_last):
        first = df.get("_First Name", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
        last  = df.get("_Last Name",  pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
        df["Contact Name"] = (first + " " + last).str.strip()
        if has_first:
            mapping_report[rename_map.get("_First Name", "_First Name")] = "Contact Name (combined)"
        if has_last:
            mapping_report[rename_map.get("_Last Name",  "_Last Name")]  = "Contact Name (combined)"

    # Drop internal staging columns
    df.drop(columns=[c for c in ("_First Name", "_Last Name") if c in df.columns], inplace=True)

    # Fill any still-missing standard columns with empty string so downstream
    # row.get() calls never raise KeyError.
    for std_col in ("Company", "Website", "Industry", "Employees",
                    "Contact Name", "Title", "Email", "Specialty",
                    "State", "Country", "Annual Revenue"):
        if std_col not in df.columns:
            df[std_col] = ""

    return df, mapping_report


def _load_agent_prompts(campaign_id: int = None) -> dict:
    """
    Return {agent_key: system_prompt} always fresh from DB.
    If campaign_id is given, campaign-level prompts override global ones for
    any agent_key that has a custom prompt in that campaign.
    """
    try:
        db.session.expire_all()

        # 1. Start with global prompts
        rows = AgentPrompt.query.all()
        prompts = {}
        for r in rows:
            default_text = _DEFAULT_AGENT_PROMPTS.get(r.agent_key, {}).get("default", "")
            if not r.is_enabled:
                prompts[r.agent_key] = default_text
                log.info(f"[PROMPTS] Agent '{r.agent_key}' — ⏸ DISABLED (using built-in default)")
                continue
            is_custom = bool(r.system_prompt) and r.system_prompt != default_text
            prompts[r.agent_key] = r.system_prompt
            log.info(
                f"[PROMPTS] Agent '{r.agent_key}' — "
                f"{'✎ CUSTOM prompt' if is_custom else '⚙ default prompt'} "
                f"(len={len(r.system_prompt or '')})"
            )

        # 2. Overlay campaign-specific prompts (if a campaign is active)
        if campaign_id:
            camp_rows = CampaignPrompt.query.filter_by(campaign_id=campaign_id).all()
            for cp in camp_rows:
                prompts[cp.agent_key] = cp.system_prompt
                log.info(
                    f"[CAMPAIGN] Agent '{cp.agent_key}' overridden by "
                    f"campaign_id={campaign_id} (len={len(cp.system_prompt)})"
                )

        return prompts
    except Exception as exc:
        log.error(f"Failed to load agent prompts from DB: {exc} — falling back to built-in defaults")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE WORKER  (background thread)
# ══════════════════════════════════════════════════════════════════════════════

def pipeline_worker():
    global pipeline_state, uploaded_df, active_campaign_id, _run_user_id

    with app.app_context():
        run_start = time.time()
        log.info("─" * 60)
        log.info("PIPELINE START")

        # Snapshot the campaign id so the worker stays consistent for the
        # full run even if the user changes selection mid-run.
        _campaign_id = active_campaign_id

        pipeline_state["status"]        = "running"
        pipeline_state["results"]       = []
        pipeline_state["current"]       = 0
        pipeline_state["stop_requested"] = False
        pipeline_state["stats"]   = {
            "total_accounts": 0, "icp_qualified": 0,
            "avg_icp_score":  0, "emails_generated": 0,
        }

        # ── Create DB run record ─────────────────────────────────────────
        db_run = PipelineRun(
            started_at=datetime.now(timezone.utc),
            status="running",
            csv_filename=uploaded_csv_name,
            campaign_id=_campaign_id,
            user_id=_run_user_id,
        )
        db.session.add(db_run)
        db.session.commit()
        _db_run_id = db_run.id   # capture once — safe to use after any rollback

        try:
            df = uploaded_df.copy()
            pipeline_state["total"] = len(df)
            pipeline_state["stats"]["total_accounts"] = len(df)

            if app_config["max_accounts"] > 0:
                df = df.head(app_config["max_accounts"])
                pipeline_state["total"] = len(df)
                pipeline_state["stats"]["total_accounts"] = len(df)

            db_run.total_accounts = len(df)
            db.session.commit()

            log.info(f"Total accounts to process: {len(df)}")

            # ── Load custom prompts from DB ──────────────────────────────
            _prompts = _load_agent_prompts(campaign_id=_campaign_id)
            _model   = app_config["model"]
            log.debug(f"Initialising agents (model={_model}) …")

            icp        = ICPQualificationAgent(ANTHROPIC_API_KEY, _model,
                             system_prompt=_prompts.get("icp"))
            researcher = CompanyResearchAgent(ANTHROPIC_API_KEY, _model,
                             system_prompt=_prompts.get("research"))
            pain       = PainSignalAgent(ANTHROPIC_API_KEY, _model,
                             system_prompt=_prompts.get("pain"))
            contact    = ContactIntelligenceAgent(ANTHROPIC_API_KEY, _model,
                             system_prompt=_prompts.get("contact"))
            personal   = PersonalizationAgent(ANTHROPIC_API_KEY, _model,
                             system_prompt=_prompts.get("personalization"))
            emailer    = EmailGenerationAgent(ANTHROPIC_API_KEY, _model,
                             system_prompt=_prompts.get("email"))
            log.debug("All 6 agents initialised OK")

            results       = []
            icp_scores    = []
            skip_count    = 0
            err_count     = 0
            _user_stopped = False

            for idx, row in df.iterrows():
                # ── Check for user-requested stop ────────────────────
                if pipeline_state.get("stop_requested"):
                    log.warning("Pipeline stop requested by user — halting between accounts.")
                    _user_stopped = True
                    break

                pipeline_state["current"] = idx + 1
                acct_start = time.time()

                def _cell(val) -> str:
                    """Convert a pandas cell to a clean string, treating NaN as empty."""
                    s = str(val).strip()
                    return "" if s.lower() == "nan" else s

                company       = _cell(row.get("Company",      ""))
                website       = _cell(row.get("Website",      ""))
                industry      = _cell(row.get("Industry",     ""))
                employees     = _cell(row.get("Employees",    ""))
                contact_name  = _cell(row.get("Contact Name", ""))
                contact_title = _cell(row.get("Title",        ""))
                contact_email = _cell(row.get("Email",        ""))
                specialty     = _cell(row.get("Specialty",    ""))

                # Extra fields for richer ICP scoring
                state   = _cell(row.get("State",          row.get("Company State",   "")))
                country = _cell(row.get("Country",        row.get("Company Country", "")))
                revenue = _cell(row.get("Annual Revenue", row.get("Revenue",         "")))

                # Apollo-export / pre-enriched CSV fields
                # (present when user uploads a CSV already exported from Apollo)
                contact_phone    = _cell(row.get("Phone",            ""))
                contact_linkedin = _cell(row.get("LinkedIn",         ""))
                comp_linkedin    = _cell(row.get("Company LinkedIn",  ""))
                comp_phone       = _cell(row.get("Company Phone",     ""))
                technologies     = _cell(row.get("Technologies",      ""))
                description      = _cell(row.get("Description",       ""))
                city             = _cell(row.get("City",              ""))
                founded          = _cell(row.get("Founded",           ""))
                keywords         = _cell(row.get("Keywords",          ""))

                log.info(f"[{idx+1}/{len(df)}] Processing: {company} "
                         f"| {industry} | {employees} employees | {specialty}")
                _emit("header", f"[{idx+1}/{len(df)}] {company}")

                try:
                    # ── Apollo Enrichment (smart-skip if CSV already has data) ─
                    enrichment  = {}
                    apollo_used = False
                    domain = _domain_from_url(website)

                    # Determine what the CSV already provides
                    _has_contact_data = bool(contact_email or contact_phone)
                    _org_signal_count = sum([
                        bool(industry), bool(employees), bool(revenue),
                        bool(description), bool(technologies),
                    ])
                    _has_org_data = _org_signal_count >= 2   # ≥2 org fields = already enriched

                    if apollo_connector:
                        if _has_contact_data and _has_org_data:
                            # CSV is already fully enriched — skip all Apollo calls
                            _emit("step", "  → Apollo: ⚡ Skipped — CSV already has contact + org data")
                            log.info(f"  [APOLLO] Fully skipped for {company} — CSV is pre-enriched "
                                     f"(contact={'✓' if _has_contact_data else '✗'}, "
                                     f"org signals={_org_signal_count})")
                        elif domain:
                            # Partial skip — only call what's missing
                            if not _has_org_data:
                                _emit("step", "  → Apollo: Enriching company data")
                                try:
                                    org_data = apollo_connector.enrich_organization(
                                        domain=domain, name=company
                                    )
                                    enrichment.update(org_data)
                                    apollo_used = bool(org_data)
                                except Exception as _ap_err:
                                    log.warning(f"  [APOLLO] Org enrichment failed ({_ap_err})")
                            else:
                                _emit("step", "  → Apollo: ⚡ Company skip — org data in CSV")
                                log.info(f"  [APOLLO] Org skip for {company} — {_org_signal_count} org fields in CSV")

                            if not _has_contact_data:
                                _emit("step", "  → Apollo: Enriching contact data")
                                try:
                                    contact_data = apollo_connector.match_contact(
                                        name=contact_name, email=contact_email, domain=domain
                                    )
                                    enrichment.update(contact_data)
                                    apollo_used = apollo_used or bool(contact_data)
                                except Exception as _ap_err:
                                    log.warning(f"  [APOLLO] Contact match failed ({_ap_err})")
                            else:
                                _emit("step", "  → Apollo: ⚡ Contact skip — email/phone in CSV")
                                log.info(f"  [APOLLO] Contact skip for {company} — CSV has email/phone")

                            if bool(enrichment):
                                log.debug(f"  [APOLLO] enrichment OK — {len(enrichment)} fields")
                            else:
                                log.debug("  [APOLLO] no enrichment returned (unknown company)")

                    # ── Pre-populate enrichment from CSV for any skipped fields ─
                    # Ensures downstream agents have the data even when Apollo is skipped
                    if _has_contact_data:
                        if not enrichment.get("apollo_contact_email"):
                            enrichment["apollo_contact_email"]    = contact_email
                        if not enrichment.get("apollo_contact_phone"):
                            enrichment["apollo_contact_phone"]    = contact_phone
                        if not enrichment.get("apollo_contact_linkedin"):
                            enrichment["apollo_contact_linkedin"] = contact_linkedin
                        if not enrichment.get("apollo_contact_title"):
                            enrichment["apollo_contact_title"]    = contact_title
                    if _has_org_data:
                        if not enrichment.get("apollo_industry"):
                            enrichment["apollo_industry"]     = industry
                        if not enrichment.get("apollo_employees"):
                            enrichment["apollo_employees"]    = employees
                        if not enrichment.get("apollo_revenue"):
                            enrichment["apollo_revenue"]      = revenue
                        if not enrichment.get("apollo_state"):
                            enrichment["apollo_state"]        = state
                        if not enrichment.get("apollo_country"):
                            enrichment["apollo_country"]      = country
                        if not enrichment.get("apollo_city"):
                            enrichment["apollo_city"]         = city
                        if not enrichment.get("apollo_technologies"):
                            enrichment["apollo_technologies"] = technologies
                        if not enrichment.get("apollo_description"):
                            enrichment["apollo_description"]  = description
                        if not enrichment.get("apollo_founded"):
                            enrichment["apollo_founded"]      = founded
                        if not enrichment.get("apollo_keywords"):
                            enrichment["apollo_keywords"]     = keywords
                        if not enrichment.get("apollo_linkedin"):
                            enrichment["apollo_linkedin"]     = comp_linkedin
                        if not enrichment.get("apollo_phone"):
                            enrichment["apollo_phone"]        = comp_phone

                    # ── Build exact per-agent data-source labels ──────────
                    _model_label = f"Claude AI ({_model})"

                    # Determine enrichment source label
                    _csv_preenriched = _has_contact_data and _has_org_data
                    if _csv_preenriched:
                        _enrich_suffix = " + CSV (Apollo export, pre-enriched)"
                    elif apollo_used:
                        _enrich_suffix = " + Apollo.io Enrichment"
                    elif _has_contact_data or _has_org_data:
                        _enrich_suffix = " + CSV (partial Apollo export)"
                    else:
                        _enrich_suffix = ""

                    _icp_sources = (
                        "CSV (company, industry, employees, specialty, state, country, revenue, contact title)"
                        + _enrich_suffix
                        + f" → {_model_label}"
                    )
                    _research_sources = (
                        (f"Website Scrape ({website})" if (website and website != "nan") else "CSV (company, specialty)")
                        + _enrich_suffix
                        + f" → {_model_label}"
                    )
                    _pain_sources     = f"Agent 2 Research Summary + CSV (specialty, employees){_enrich_suffix} → {_model_label}"
                    _contact_sources  = f"CSV (contact name, title) + Agent 2 Research Summary{_enrich_suffix} → {_model_label}"
                    _personal_sources = f"CSV (company, website, specialty) + Agent 2 Summary + Agent 3 Top Pain{_enrich_suffix} → {_model_label}"
                    _email_sources    = f"Agent 5 Personalisation Insight + Agent 3 Top Pain + CSV (company, contact, specialty){_enrich_suffix} → {_model_label}"

                    # ── Agent 1 — ICP Qualification ──────────────────────
                    _emit("step", "  → Agent 1: ICP Qualification")
                    t0 = time.time()
                    icp_result = _run_agent(
                        icp.evaluate,
                        company, website, industry, employees, specialty,
                        state=state, country=country,
                        revenue=revenue, contact_title=contact_title,
                        enrichment=enrichment,
                    )
                    log.debug(f"  [ICP] {time.time()-t0:.2f}s | score={icp_result.get('icp_score')}")
                    time.sleep(app_config["delay_between"])

                    # Detect JSON parse failure: _call_json returns {"raw": ...} when
                    # it cannot parse the model's response.  Treat this as a transient
                    # error — do NOT default the score to 0 and silently skip the
                    # company; instead emit a warning and proceed with a neutral score.
                    if "icp_score" not in icp_result:
                        log.warning(f"  [ICP] JSON parse failure for {company} — raw={str(icp_result.get('raw',''))[:200]}")
                        _emit("step", "  ⚠ Agent 1: JSON parse error — proceeding with neutral ICP score")
                        icp_result = {
                            "icp_score": 50,
                            "icp_match": "MAYBE",
                            "tier": "C",
                            "reason": "ICP response could not be parsed — proceeding with neutral score",
                            "disqualifiers": [],
                            "high_fit_signals": [],
                            "low_fit_signals": [],
                            "contact_fit": "WEAK",
                            "product_fit": [],
                            "recommended_pitch_angle": "",
                        }

                    score = icp_result.get("icp_score", 0)
                    if score < app_config["icp_threshold"]:
                        skip_count += 1
                        skip_reason = (
                            f"ICP score {score} is below the threshold of "
                            f"{app_config['icp_threshold']}. "
                            + str(icp_result.get("reason", ""))
                        )
                        log.warning(f"  [ICP] SKIPPED {company} — score {score} < {app_config['icp_threshold']}")
                        _emit("skip", f"  ✗ Skipped — ICP score {score} < {app_config['icp_threshold']}")

                        # ── Record skipped company in results so it's visible in the UI ──
                        skipped_record = {
                            "Company":              company,
                            "Website":              website,
                            "Contact Name":         contact_name,
                            "Contact Title":        contact_title,
                            "Contact Email":        contact_email,
                            "Specialty":            specialty,
                            "ICP Match":            "Skipped",
                            "ICP Score":            score,
                            "ICP Reason":           skip_reason,
                            "Practice Summary":     "—",
                            "Practice Size Signal": "—",
                            "Billing Complexity":   "—",
                            "Top Pain Point":       "—",
                            "Pain Signals":         "—",
                            "RCM Risk Level":       "—",
                            "Persona Fit":          "—",
                            "Decision Likelihood":  "—",
                            "AI Insight":           "—",
                            "Recommended Hook":     "—",
                            "Email 1 Subject":      "—",
                            "Email 1 Body":         "—",
                            "Email 2 Subject":      "—",
                            "Email 2 Body":         "—",
                            "Email 3 Subject":      "—",
                            "Email 3 Body":         "—",
                            "LinkedIn DM 1":        "—",
                            "LinkedIn DM 2":        "—",
                            "LinkedIn DM 3":        "—",
                            "Agent 1 (ICP) Sources":              _icp_sources,
                            "Agent 2 (Research) Sources":         "Skipped — ICP below threshold",
                            "Agent 3 (Pain) Sources":             "Skipped — ICP below threshold",
                            "Agent 4 (Contact) Sources":          "Skipped — ICP below threshold",
                            "Agent 5 (Personalisation) Sources":  "Skipped — ICP below threshold",
                            "Agent 6 (Email) Sources":            "Skipped — ICP below threshold",
                            # Apollo fields (whatever we already fetched)
                            "Apollo Company":          enrichment.get("apollo_name", ""),
                            "Apollo Domain":           enrichment.get("apollo_domain", ""),
                            "Apollo Industry":         enrichment.get("apollo_industry", ""),
                            "Apollo Revenue":          enrichment.get("apollo_revenue", ""),
                            "Apollo Employees":        str(enrichment.get("apollo_employees", "")),
                            "Apollo Technologies":     enrichment.get("apollo_technologies", ""),
                            "Apollo EHR Signals":      enrichment.get("apollo_ehr_signals", ""),
                            "Apollo Keywords":         enrichment.get("apollo_keywords", ""),
                            "Apollo Description":      enrichment.get("apollo_description", ""),
                            "Apollo City":             enrichment.get("apollo_city", ""),
                            "Apollo State":            enrichment.get("apollo_state", ""),
                            "Apollo Country":          enrichment.get("apollo_country", ""),
                            "Apollo Founded":          enrichment.get("apollo_founded", ""),
                            "Apollo Num Locations":    enrichment.get("apollo_num_locations", ""),
                            "Apollo Company LinkedIn": enrichment.get("apollo_linkedin", ""),
                            "Apollo Company Phone":    enrichment.get("apollo_phone", ""),
                            "Apollo Contact Email":      enrichment.get("apollo_contact_email", ""),
                            "Apollo Contact Phone":      enrichment.get("apollo_contact_phone", ""),
                            "Apollo Contact LinkedIn":   enrichment.get("apollo_contact_linkedin", ""),
                            "Apollo Contact Title":      enrichment.get("apollo_contact_title", ""),
                            "Apollo Contact Seniority":  enrichment.get("apollo_contact_seniority", ""),
                            "Apollo Contact Department": enrichment.get("apollo_contact_department", ""),
                            "Apollo Contact City":       enrichment.get("apollo_contact_city", ""),
                            "Apollo Contact State":      enrichment.get("apollo_contact_state", ""),
                            "Apollo Contact Country":    enrichment.get("apollo_contact_country", ""),
                        }
                        results.append(skipped_record)
                        pipeline_state["results"] = results

                        # Save skipped record to DB
                        try:
                            db_skipped = PipelineResult(
                                run_id=_db_run_id,        # use captured id — safe after any rollback
                                company=company,
                                website=website,
                                contact_name=contact_name,
                                contact_title=contact_title,
                                contact_email=contact_email,
                                specialty=specialty,
                                icp_match="Skipped",
                                icp_score=int(score or 0),
                                icp_reason=skip_reason,
                                icp_data_sources=(_icp_sources or "")[:198],  # hard limit for String(200)
                                agent1_sources=_icp_sources,
                            )
                            db.session.add(db_skipped)
                            db.session.commit()
                            log.info(f"  [DB] Skipped record saved for {company} (run_id={_db_run_id})")
                        except Exception as _db_skip_err:
                            log.error(f"  [DB] Skipped record save FAILED for {company}: {type(_db_skip_err).__name__}: {_db_skip_err}")
                            db.session.rollback()

                        continue

                    icp_scores.append(score)

                    # ── Agent 2 — Company Research ───────────────────────
                    _emit("step", "  → Agent 2: Company Research")
                    t0 = time.time()
                    research_result = _run_agent(
                        researcher.analyze,
                        company, website, specialty,
                        # BUG FIX: previously only company/website/specialty were
                        # passed. When scraping failed and Apollo had no data, the
                        # model had nothing company-specific → identical summaries.
                        # Now we pass all CSV fields so there is always unique context.
                        industry=industry,
                        employees=employees,
                        state=state,
                        country=country,
                        revenue=revenue,
                        contact_title=contact_title,
                        enrichment=enrichment,
                    )
                    log.debug(f"  [RESEARCH] {time.time()-t0:.2f}s")

                    # BUG FIX: if research JSON failed to parse (returns {"raw":...}),
                    # downstream agents get empty strings and produce identical generic
                    # output. Build a minimal fallback from available CSV data so the
                    # rest of the pipeline still has something meaningful to work with.
                    if "summary" not in research_result:
                        log.warning(f"  [RESEARCH] JSON parse failure for {company} — using CSV fallback summary")
                        _emit("step", "  ⚠ Agent 2: parse error — using CSV data as fallback")
                        _fallback_desc = " | ".join(filter(None, [
                            f"Specialty: {specialty}" if specialty else "",
                            f"Industry: {industry}" if industry else "",
                            f"Employees: {employees}" if employees else "",
                            f"Revenue: {revenue}" if revenue else "",
                            f"State: {state}" if state else "",
                        ])) or "Healthcare practice"
                        research_result = {
                            "summary": f"{company} is a healthcare practice. {_fallback_desc}.",
                            "specialty": specialty or "Unknown",
                            "services": specialty or "Healthcare services",
                            "size_signal": (
                                "large" if employees and str(employees).replace(",", "").isdigit() and int(str(employees).replace(",", "")) > 200
                                else "mid-size" if employees and str(employees).replace(",", "").isdigit() and int(str(employees).replace(",", "")) > 20
                                else "small"
                            ) if employees else "unknown",
                            "billing_complexity": "medium",
                            "emr_mentioned": enrichment.get("apollo_ehr_signals", "Unknown") or "Unknown",
                            "insurance_heavy": False,
                            "multi_location": bool(enrichment.get("apollo_num_locations")),
                            "automation_signals": enrichment.get("apollo_technologies", "None") or "None",
                            "admin_pain_indicators": "None",
                        }
                    time.sleep(app_config["delay_between"])

                    # ── Agent 3 — Pain Signal Detection ─────────────────
                    _emit("step", "  → Agent 3: Pain Signal Detection")
                    t0 = time.time()
                    pain_result = _run_agent(
                        pain.detect,
                        company, research_result.get("summary", ""), specialty, employees,
                        enrichment=enrichment,
                    )
                    log.debug(f"  [PAIN] {time.time()-t0:.2f}s")

                    if "top_pain" not in pain_result:
                        log.warning(f"  [PAIN] JSON parse failure for {company} — using specialty-based fallback")
                        _emit("step", "  ⚠ Agent 3: parse error — using specialty fallback")
                        pain_result = {
                            "top_pain": f"Manual administrative workflows and insurance verification overhead common in {specialty or 'healthcare'} practices.",
                            "pain_category": "Workflow Automation",
                            "signals": f"{specialty}, {employees} employees" if employees else specialty,
                            "rcm_risk": "medium",
                            "recommended_product": "Package 2: Workflow Automation",
                            "talk_track": f"Calyxr reduces manual front-desk burden for {specialty or 'healthcare'} practices.",
                        }
                    time.sleep(app_config["delay_between"])

                    # ── Agent 4 — Contact Intelligence ───────────────────
                    _emit("step", "  → Agent 4: Contact Intelligence")
                    t0 = time.time()
                    contact_result = _run_agent(
                        contact.evaluate,
                        company, contact_name, contact_title,
                        research_result.get("summary", ""),
                        enrichment=enrichment,
                    )
                    log.debug(f"  [CONTACT] {time.time()-t0:.2f}s")
                    time.sleep(app_config["delay_between"])

                    # ── Agent 5 — Personalization ────────────────────────
                    _emit("step", "  → Agent 5: Personalization")
                    t0 = time.time()
                    personal_result = _run_agent(
                        personal.generate,
                        company, website, specialty,
                        research_result.get("summary", ""),
                        pain_result.get("top_pain", ""),
                        contact_title,
                        enrichment=enrichment,
                    )
                    log.debug(f"  [PERSONAL] {time.time()-t0:.2f}s")

                    if "insight" not in personal_result:
                        log.warning(f"  [PERSONAL] JSON parse failure for {company} — using pain-based fallback")
                        _emit("step", "  ⚠ Agent 5: parse error — using pain-based fallback")
                        personal_result = {
                            "insight": pain_result.get("top_pain", f"Operational overhead in {specialty or 'healthcare'} practices often starts at the front desk."),
                            "hook": pain_result.get("pain_category", "Workflow Automation"),
                            "specificity_score": 3,
                        }
                    time.sleep(app_config["delay_between"])

                    # ── Agent 6 — Email Generation ───────────────────────
                    _emit("step", "  → Agent 6: Email Generation")
                    t0 = time.time()
                    email_result = _run_agent(
                        emailer.write,
                        company, contact_name, contact_title,
                        personal_result.get("insight", ""),
                        pain_result.get("top_pain", ""),
                        specialty,
                        enrichment=enrichment,
                        research_summary=research_result.get("summary", ""),
                    )
                    log.debug(f"  [EMAIL] {time.time()-t0:.2f}s")
                    time.sleep(app_config["delay_between"])

                    # ── Build result record ──────────────────────────────
                    record = {
                        "Company":              company,
                        "Website":              website,
                        "Contact Name":         contact_name,
                        "Contact Title":        contact_title,
                        "Contact Email":        contact_email,
                        "Specialty":            specialty,
                        "ICP Match":            icp_result.get("icp_match", ""),
                        "ICP Score":            icp_result.get("icp_score", ""),
                        "ICP Reason":           icp_result.get("reason", ""),
                        "Practice Summary":     research_result.get("summary", ""),
                        "Practice Size Signal": research_result.get("size_signal", ""),
                        "Billing Complexity":   research_result.get("billing_complexity", ""),
                        "Top Pain Point":       pain_result.get("top_pain", ""),
                        "Pain Signals":         pain_result.get("signals", ""),
                        "RCM Risk Level":       pain_result.get("rcm_risk", ""),
                        "Persona Fit":          contact_result.get("persona_fit", ""),
                        "Decision Likelihood":  contact_result.get("decision_likelihood", ""),
                        "AI Insight":           personal_result.get("insight", ""),
                        "Recommended Hook":     personal_result.get("hook", ""),
                        "Email 1 Subject":      email_result.get("e1_subject", ""),
                        "Email 1 Body":         email_result.get("e1_body", ""),
                        "Email 2 Subject":      email_result.get("e2_subject", ""),
                        "Email 2 Body":         email_result.get("e2_body", ""),
                        "Email 3 Subject":      email_result.get("e3_subject", ""),
                        "Email 3 Body":         email_result.get("e3_body", ""),
                        "LinkedIn DM 1":        email_result.get("li_dm1", ""),
                        "LinkedIn DM 2":        email_result.get("li_dm2", ""),
                        "LinkedIn DM 3":        email_result.get("li_dm3", ""),
                        # ── Data Source tracking (exact per-agent) ───────
                        "Agent 1 (ICP) Sources":              _icp_sources,
                        "Agent 2 (Research) Sources":         _research_sources,
                        "Agent 3 (Pain) Sources":             _pain_sources,
                        "Agent 4 (Contact) Sources":          _contact_sources,
                        "Agent 5 (Personalisation) Sources":  _personal_sources,
                        "Agent 6 (Email) Sources":            _email_sources,
                        # Apollo — company (core)
                        "Apollo Company":          enrichment.get("apollo_name", ""),
                        "Apollo Domain":           enrichment.get("apollo_domain", ""),
                        "Apollo Industry":         enrichment.get("apollo_industry", ""),
                        "Apollo Revenue":          enrichment.get("apollo_revenue", ""),
                        "Apollo Employees":        str(enrichment.get("apollo_employees", "")),
                        "Apollo Technologies":     enrichment.get("apollo_technologies", ""),
                        "Apollo EHR Signals":      enrichment.get("apollo_ehr_signals", ""),
                        "Apollo Keywords":         enrichment.get("apollo_keywords", ""),
                        "Apollo Description":      enrichment.get("apollo_description", ""),
                        "Apollo City":             enrichment.get("apollo_city", ""),
                        "Apollo State":            enrichment.get("apollo_state", ""),
                        "Apollo Country":          enrichment.get("apollo_country", ""),
                        "Apollo Founded":          enrichment.get("apollo_founded", ""),
                        "Apollo Num Locations":    enrichment.get("apollo_num_locations", ""),
                        "Apollo Company LinkedIn": enrichment.get("apollo_linkedin", ""),
                        "Apollo Company Phone":    enrichment.get("apollo_phone", ""),
                        # Apollo — contact (core + extended)
                        "Apollo Contact Email":      enrichment.get("apollo_contact_email", ""),
                        "Apollo Contact Phone":      enrichment.get("apollo_contact_phone", ""),
                        "Apollo Contact LinkedIn":   enrichment.get("apollo_contact_linkedin", ""),
                        "Apollo Contact Title":      enrichment.get("apollo_contact_title", ""),
                        "Apollo Contact Seniority":  enrichment.get("apollo_contact_seniority", ""),
                        "Apollo Contact Department": enrichment.get("apollo_contact_department", ""),
                        "Apollo Contact City":       enrichment.get("apollo_contact_city", ""),
                        "Apollo Contact State":      enrichment.get("apollo_contact_state", ""),
                        "Apollo Contact Country":    enrichment.get("apollo_contact_country", ""),
                    }

                    results.append(record)
                    pipeline_state["results"] = results

                    # Live stats
                    pipeline_state["stats"]["icp_qualified"]    = len(icp_scores)
                    pipeline_state["stats"]["emails_generated"] = len(results)
                    pipeline_state["stats"]["avg_icp_score"]    = (
                        round(sum(icp_scores) / len(icp_scores)) if icp_scores else 0
                    )

                    # ── Save result to MySQL ─────────────────────────────
                    try:
                        db_result = PipelineResult(
                            run_id=_db_run_id,
                            company=company,
                            website=website,
                            contact_name=contact_name,
                            contact_title=contact_title,
                            contact_email=contact_email,
                            specialty=specialty,
                            icp_match=str(icp_result.get("icp_match", "")),
                            icp_score=int(icp_result.get("icp_score", 0) or 0),
                            icp_reason=str(icp_result.get("reason", "")),
                            practice_summary=str(research_result.get("summary", "")),
                            size_signal=str(research_result.get("size_signal", "")),
                            billing_complexity=str(research_result.get("billing_complexity", "")),
                            top_pain=str(pain_result.get("top_pain", "")),
                            pain_signals=str(pain_result.get("signals", "")),
                            rcm_risk=str(pain_result.get("rcm_risk", "")),
                            persona_fit=str(contact_result.get("persona_fit", "")),
                            decision_likelihood=str(contact_result.get("decision_likelihood", "")),
                            ai_insight=str(personal_result.get("insight", "")),
                            recommended_hook=str(personal_result.get("hook", "")),
                            e1_subject=str(email_result.get("e1_subject", "")),
                            e1_body=str(email_result.get("e1_body", "")),
                            e2_subject=str(email_result.get("e2_subject", "")),
                            e2_body=str(email_result.get("e2_body", "")),
                            e3_subject=str(email_result.get("e3_subject", "")),
                            e3_body=str(email_result.get("e3_body", "")),
                            li_dm1=str(email_result.get("li_dm1", "")),
                            li_dm2=str(email_result.get("li_dm2", "")),
                            li_dm3=str(email_result.get("li_dm3", "")),
                            # Data Sources (exact per-agent)
                            icp_data_sources=_icp_sources,
                            research_sources=_research_sources,
                            agent1_sources=_icp_sources,
                            agent2_sources=_research_sources,
                            agent3_sources=_pain_sources,
                            agent4_sources=_contact_sources,
                            agent5_sources=_personal_sources,
                            agent6_sources=_email_sources,
                            # Apollo — company (core)
                            apollo_company=str(enrichment.get("apollo_name", "")),
                            apollo_domain=str(enrichment.get("apollo_domain", "")),
                            apollo_industry=str(enrichment.get("apollo_industry", "")),
                            apollo_revenue=str(enrichment.get("apollo_revenue", "")),
                            apollo_employees=str(enrichment.get("apollo_employees", "")),
                            apollo_technologies=str(enrichment.get("apollo_technologies", "")),
                            apollo_ehr_signals=str(enrichment.get("apollo_ehr_signals", "")),
                            apollo_keywords=str(enrichment.get("apollo_keywords", "")),
                            apollo_description=str(enrichment.get("apollo_description", "")),
                            apollo_city=str(enrichment.get("apollo_city", "")),
                            apollo_state=str(enrichment.get("apollo_state", "")),
                            apollo_country=str(enrichment.get("apollo_country", "")),
                            apollo_founded=str(enrichment.get("apollo_founded", "")),
                            apollo_num_locations=str(enrichment.get("apollo_num_locations", "")),
                            apollo_company_linkedin=str(enrichment.get("apollo_linkedin", "")),
                            apollo_company_phone=str(enrichment.get("apollo_phone", "")),
                            # Apollo — contact (core + extended)
                            apollo_contact_email=str(enrichment.get("apollo_contact_email", "")),
                            apollo_contact_phone=str(enrichment.get("apollo_contact_phone", "")),
                            apollo_contact_linkedin=str(enrichment.get("apollo_contact_linkedin", "")),
                            apollo_contact_title=str(enrichment.get("apollo_contact_title", "")),
                            apollo_contact_seniority=str(enrichment.get("apollo_contact_seniority", "")),
                            apollo_contact_department=str(enrichment.get("apollo_contact_department", "")),
                            apollo_contact_city=str(enrichment.get("apollo_contact_city", "")),
                            apollo_contact_state=str(enrichment.get("apollo_contact_state", "")),
                            apollo_contact_country=str(enrichment.get("apollo_contact_country", "")),
                        )
                        db.session.add(db_result)
                        db.session.commit()
                    except Exception as db_err:
                        log.error(f"  DB save error for {company}: {db_err}")
                        db.session.rollback()

                    acct_elapsed = time.time() - acct_start
                    log.info(f"  ✓ {company} — ICP: {score} | completed in {acct_elapsed:.1f}s")
                    _emit("success",
                        f"  ✓ Done — ICP: {score} | "
                        f"{pain_result.get('top_pain','')[:55]}"
                    )

                except _StopPipeline:
                    # User clicked Force Stop mid-agent — halt immediately
                    log.warning(f"  [STOP] Force-stopped while processing {company}")
                    _user_stopped = True
                    break

                except Exception as e:
                    err_count += 1
                    log.error(f"  ✗ ERROR on {company}: {e}")
                    log.error(traceback.format_exc())
                    _emit("error", f"  ✗ Error on {company}: {e}")
                    continue

            # ── Pipeline complete ────────────────────────────────────────
            total_elapsed = time.time() - run_start
            pipeline_state["status"] = "complete"

            # processed = fully emailed accounts (skipped_record entries are NOT "processed")
            _processed_count = len(results) - skip_count

            db_run.status           = "complete"
            db_run.completed_at     = datetime.now(timezone.utc)
            db_run.processed        = _processed_count
            db_run.skipped          = skip_count
            db_run.errors           = err_count
            db_run.duration_seconds = round(total_elapsed, 1)
            db.session.commit()

            summary = (
                f"Pipeline {'stopped' if _user_stopped else 'complete'} | "
                f"processed={_processed_count} skipped={skip_count} errors={err_count} "
                f"total_time={total_elapsed:.1f}s"
            )
            log.info(summary)
            log.info("─" * 60)
            if _user_stopped:
                _emit("complete",
                    f"⚠️ Pipeline force-stopped! "
                    f"{_processed_count} processed, {skip_count} skipped."
                )
            else:
                _emit("complete",
                    f"✅ Pipeline complete! "
                    f"{_processed_count} processed · {skip_count} skipped · {err_count} errors."
                )

        except Exception as exc:
            pipeline_state["status"] = "error"
            try:
                db_run.status           = "error"
                db_run.completed_at     = datetime.now(timezone.utc)
                db_run.duration_seconds = round(time.time() - run_start, 1)
                db.session.commit()
            except Exception:
                db.session.rollback()
            log.critical(f"FATAL pipeline error: {exc}")
            log.critical(traceback.format_exc())
            _emit("error", f"Fatal pipeline error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

# ── Serve static assets from dist/assets/ at /assets/ ────────────────────────
@app.route("/assets/<path:filename>")
def serve_assets(filename):
    assets_dir = os.path.join(_DIST_DIR, "assets")
    return send_from_directory(assets_dir, filename)


# ── HTML pages ────────────────────────────────────────────────────────────────
_TEMPLATES_DIR = os.path.join(_BASE_DIR, "templates")

@app.route("/")
@login_required
def index():
    return send_from_directory(_TEMPLATES_DIR, "index.html")


@app.route("/login")
def serve_login():
    if current_user.is_authenticated:
        return redirect("/")
    return send_from_directory(_DIST_DIR, "login.html")


@app.route("/register")
def serve_register():
    if current_user.is_authenticated:
        return redirect("/")
    return send_from_directory(_DIST_DIR, "register.html")


@app.route("/users")
@login_required
def serve_users():
    # Users panel is now embedded in the main dashboard
    return redirect("/")


# ── Upload ────────────────────────────────────────────────────────────────────
@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    global uploaded_df, uploaded_csv_name

    log.info(f"[API] POST /api/upload from {request.remote_addr}")

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only .csv files are accepted"}), 400

    try:
        raw_df            = pd.read_csv(file)
        uploaded_csv_name = file.filename

        # Auto-detect & normalize column names so the pipeline always gets
        # standard field names regardless of how the CSV was exported.
        uploaded_df, col_mapping = _normalize_csv_columns(raw_df)

        if col_mapping:
            log.info(f"[API] Column mapping applied: {col_mapping}")
        log.info(f"[API] CSV uploaded OK — filename={file.filename} "
                 f"rows={len(uploaded_df)} cols={list(uploaded_df.columns)}")

        # Replace NaN/None with empty string before JSON serialisation —
        # NaN is not valid JSON and will cause a SyntaxError in the browser.
        preview = (
            uploaded_df.head(5)
            .fillna("")
            .to_dict(orient="records")
        )

        return jsonify({
            "success":        True,
            "rows":           len(uploaded_df),
            "columns":        list(uploaded_df.columns),
            "column_mapping": col_mapping,
            "preview":        preview,
        })

    except Exception as exc:
        log.error(f"[API] Upload parse error: {exc}")
        return jsonify({"error": str(exc)}), 400


# ── Run ───────────────────────────────────────────────────────────────────────
@app.route("/api/run", methods=["POST"])
@login_required
def run():
    global active_campaign_id, _run_user_id
    # Capture user ID NOW while we still have a request context.
    # background threads cannot access current_user.
    _run_user_id = current_user.id if current_user.is_authenticated else None
    log.info(f"[API] POST /api/run from {request.remote_addr}")

    if uploaded_df is None:
        return jsonify({"error": "Please upload a CSV file first"}), 400
    if pipeline_state["status"] == "running":
        return jsonify({"error": "Pipeline is already running"}), 400

    # Accept optional campaign_id from the request body
    data = request.get_json(silent=True) or {}
    cid  = data.get("campaign_id")
    if cid:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid campaign_id — must be an integer"}), 400
        camp = db.session.get(Campaign, cid)
        if not camp or not camp.is_active:
            return jsonify({"error": "Selected campaign not found"}), 404
        active_campaign_id = camp.id
        log.info(f"[API] Using campaign: '{camp.name}' (id={camp.id})")
    else:
        active_campaign_id = None
        log.info("[API] No campaign selected — using global prompts")

    drained = 0
    while not progress_queue.empty():
        try:
            progress_queue.get_nowait(); drained += 1
        except queue.Empty:
            break
    if drained:
        log.debug(f"[API] Drained {drained} stale SSE events")

    t = threading.Thread(target=pipeline_worker, daemon=True)
    t.start()
    log.info(f"[API] Pipeline thread started — accounts={len(uploaded_df)}")
    return jsonify({"success": True})


# ── Stop ──────────────────────────────────────────────────────────────────────
@app.route("/api/stop", methods=["POST"])
@login_required
def stop_pipeline():
    log.info(f"[API] POST /api/stop from {request.remote_addr}")
    if pipeline_state["status"] != "running":
        return jsonify({"error": "Pipeline is not running"}), 400
    pipeline_state["stop_requested"] = True
    log.warning("[API] Stop signal received — will halt after current account")
    return jsonify({"success": True, "message": "Stop signal sent — finishing current account"})


# ── Pipeline Reset (called on logout so every new session starts clean) ────────
@app.route("/api/pipeline/reset", methods=["POST"])
@login_required
def pipeline_reset():
    """
    Wipes the in-memory pipeline state and uploaded CSV so the next
    session (or the same user after a sign-out/sign-in) always gets a
    fresh, empty dashboard.  Must NOT be called while a pipeline is running.
    """
    global uploaded_df, uploaded_csv_name

    if pipeline_state["status"] == "running":
        return jsonify({"error": "Cannot reset while pipeline is running"}), 400

    pipeline_state["status"]         = "idle"
    pipeline_state["results"]        = []
    pipeline_state["total"]          = 0
    pipeline_state["current"]        = 0
    pipeline_state["stop_requested"] = False
    pipeline_state["stats"] = {
        "total_accounts":   0,
        "icp_qualified":    0,
        "avg_icp_score":    0,
        "emails_generated": 0,
    }

    uploaded_df       = None
    uploaded_csv_name = None

    log.info(f"[API] Pipeline state reset by {current_user.email}")
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════════
#  CAMPAIGN API  —  CRUD + prompt management
# ══════════════════════════════════════════════════════════════════════════════

AGENT_KEYS = ["icp", "research", "pain", "contact", "personalization", "email"]


@app.route("/api/campaigns", methods=["GET"])
@login_required
def list_campaigns():
    """Return all active campaigns (newest first), without prompts."""
    campaigns = (
        Campaign.query
        .filter_by(is_active=True)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return jsonify([c.to_dict() for c in campaigns])


@app.route("/api/campaigns", methods=["POST"])
@permission_required("can_manage_campaigns")
def create_campaign():
    """Create a new campaign, optionally copying global prompts as defaults."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Campaign name is required"}), 400

    campaign = Campaign(
        name=name,
        description=str(data.get("description", "")).strip(),
        created_by=current_user.id,
    )
    db.session.add(campaign)
    db.session.flush()   # get campaign.id before committing

    # Seed each agent prompt from the global AgentPrompt table
    global_prompts = {p.agent_key: p.system_prompt for p in AgentPrompt.query.all()}
    for key in AGENT_KEYS:
        if key in global_prompts:
            db.session.add(CampaignPrompt(
                campaign_id=campaign.id,
                agent_key=key,
                system_prompt=global_prompts[key],
            ))

    db.session.commit()
    log.info(f"[CAMPAIGN] Created: '{name}' (id={campaign.id}) by {current_user.email}")
    return jsonify({"success": True, "campaign": campaign.to_dict(include_prompts=True)}), 201


@app.route("/api/campaigns/<int:campaign_id>", methods=["GET"])
@login_required
def get_campaign(campaign_id):
    """Return a single campaign including all its agent prompts."""
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign or not campaign.is_active:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify(campaign.to_dict(include_prompts=True))


@app.route("/api/campaigns/<int:campaign_id>", methods=["PUT"])
@permission_required("can_manage_campaigns")
def update_campaign(campaign_id):
    """Update campaign name / description and/or its per-agent prompts."""
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign or not campaign.is_active:
        return jsonify({"error": "Campaign not found"}), 404

    data = request.get_json(silent=True) or {}

    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return jsonify({"error": "Campaign name cannot be empty"}), 400
        campaign.name = name

    if "description" in data:
        campaign.description = str(data["description"]).strip()

    # Update per-agent prompts if provided
    # Expects: { "prompts": { "icp": "...", "email": "..." } }
    prompts_data = data.get("prompts") or {}
    existing = {p.agent_key: p for p in campaign.prompts}
    for key, text in prompts_data.items():
        if key not in AGENT_KEYS:
            continue
        text = str(text).strip()
        if not text:
            continue
        if key in existing:
            existing[key].system_prompt = text
        else:
            db.session.add(CampaignPrompt(
                campaign_id=campaign.id,
                agent_key=key,
                system_prompt=text,
            ))

    campaign.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    log.info(f"[CAMPAIGN] Updated: '{campaign.name}' (id={campaign_id}) by {current_user.email}")
    return jsonify({"success": True, "campaign": campaign.to_dict(include_prompts=True)})


@app.route("/api/campaigns/<int:campaign_id>", methods=["DELETE"])
@permission_required("can_manage_campaigns")
def delete_campaign(campaign_id):
    """Soft-delete a campaign (marks is_active=False)."""
    campaign = db.session.get(Campaign, campaign_id)
    if not campaign or not campaign.is_active:
        return jsonify({"error": "Campaign not found"}), 404
    campaign.is_active = False
    db.session.commit()
    log.info(f"[CAMPAIGN] Deleted: '{campaign.name}' (id={campaign_id}) by {current_user.email}")
    return jsonify({"success": True})


# ── SSE Stream ────────────────────────────────────────────────────────────────
@app.route("/api/stream")
@login_required
def stream():
    def generate():
        try:
            while True:
                try:
                    event = progress_queue.get(timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "complete":
                        break
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        except GeneratorExit:
            # Client disconnected — stop the generator cleanly
            log.debug("[SSE] Client disconnected — closing stream")

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Status ────────────────────────────────────────────────────────────────────
@app.route("/api/status")
@login_required
def status():
    return jsonify({
        "status":       pipeline_state["status"],
        "current":      pipeline_state["current"],
        "total":        pipeline_state["total"],
        "stats":        pipeline_state["stats"],
        "result_count": len(pipeline_state["results"]),
        # Let the UI restore the uploaded file label after a page refresh
        "csv_name":     uploaded_csv_name or "",
        "csv_rows":     len(uploaded_df) if uploaded_df is not None else 0,
    })


# ── Results (current run in-memory) ──────────────────────────────────────────
@app.route("/api/results")
@login_required
def results():
    return jsonify(pipeline_state["results"])


# ── Export CSV (current run) ──────────────────────────────────────────────────
@app.route("/api/export")
@login_required
def export():
    log.info(f"[API] GET /api/export")
    if not pipeline_state["results"]:
        return jsonify({"error": "No results to export"}), 400

    df  = pd.DataFrame(pipeline_state["results"])
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=calyxr_hubspot_ready.csv"},
    )


# ── Logs — tail ───────────────────────────────────────────────────────────────
@app.route("/api/logs")
@login_required
def get_logs():
    n = min(int(request.args.get("lines", 200)), 1000)
    today_file = os.path.join(LOG_DIR, f"calyxr_{datetime.now().strftime('%Y-%m-%d')}.log")
    target = today_file if os.path.exists(today_file) else LOG_FILE
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        tail = [l.rstrip("\n") for l in all_lines[-n:]]
        return jsonify({"file": os.path.basename(target), "total": len(all_lines), "lines": tail})
    except FileNotFoundError:
        return jsonify({"file": "", "total": 0, "lines": []})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Logs — list files ─────────────────────────────────────────────────────────
@app.route("/api/logs/files")
@login_required
def list_log_files():
    try:
        files = sorted([f for f in os.listdir(LOG_DIR) if f.endswith(".log")], reverse=True)
        result = []
        for f in files:
            p = os.path.join(LOG_DIR, f)
            result.append({"name": f, "size_kb": round(os.path.getsize(p) / 1024, 1), "size_raw": os.path.getsize(p)})
        return jsonify({"files": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Logs — download ───────────────────────────────────────────────────────────
@app.route("/api/logs/download")
@login_required
def download_log():
    filename  = request.args.get("file", os.path.basename(LOG_FILE))
    safe_name = os.path.basename(filename)
    full_path = os.path.join(LOG_DIR, safe_name)
    if not os.path.exists(full_path):
        return jsonify({"error": "Log file not found"}), 404
    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    return Response(content, mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment; filename={safe_name}"})


# ══════════════════════════════════════════════════════════════════════════════
#  APP CONFIG API
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    return jsonify({
        **app_config,
        "api_key_set":     bool(ANTHROPIC_API_KEY),
        "api_key_preview": (ANTHROPIC_API_KEY[:8] + "••••••••" + ANTHROPIC_API_KEY[-4:]) if ANTHROPIC_API_KEY else "",
    })


@app.route("/api/config", methods=["POST"])
@permission_required("can_manage_config")
def update_config():
    global app_config
    data    = request.get_json(silent=True) or {}
    allowed = {"app_name", "company_name", "icp_threshold", "delay_between", "max_accounts", "footer_text"}

    for key in allowed:
        if key in data:
            val = data[key]
            try:
                if key == "icp_threshold":
                    parsed = int(val)
                    app_config[key] = max(0, min(100, parsed))   # clamp 0–100
                elif key == "max_accounts":
                    parsed = int(val)
                    app_config[key] = max(0, parsed)             # 0 = unlimited, no negative
                elif key == "delay_between":
                    parsed = float(val)
                    app_config[key] = max(0.0, min(60.0, parsed))  # clamp 0–60 s
                else:
                    app_config[key] = str(val).strip()
            except (TypeError, ValueError):
                log.warning(f"[CONFIG] Invalid value for '{key}': {val!r} — skipped")

    # Persist to MySQL
    try:
        cfg = AppConfig.query.first()
        if cfg is None:
            cfg = AppConfig()
            db.session.add(cfg)
        for k, v in app_config.items():
            setattr(cfg, k, v)
        db.session.commit()
    except Exception as exc:
        log.error(f"[CONFIG] DB save failed: {exc}")
        db.session.rollback()

    log.info(f"[CONFIG] Updated: {app_config}")
    return jsonify({"success": True, "config": app_config})


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT PROMPTS API
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/prompts", methods=["GET"])
@login_required
def get_prompts():
    """Return all agent prompts with default-comparison flag."""
    rows   = AgentPrompt.query.all()
    result = {}
    for r in rows:
        default_prompt = _DEFAULT_AGENT_PROMPTS.get(r.agent_key, {}).get("default", "")
        result[r.agent_key] = {
            "agent_key":    r.agent_key,
            "agent_name":   r.agent_name,
            "description":  r.description or "",
            "system_prompt": r.system_prompt,
            "updated_at":   r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else None,
            "is_default":   r.system_prompt == default_prompt,
            "is_enabled":   r.is_enabled,
        }
    return jsonify(result)


@app.route("/api/prompts/<agent_key>", methods=["POST"])
@permission_required("can_manage_prompts")
def update_prompt(agent_key):
    """Update a single agent's system prompt."""
    if agent_key not in _DEFAULT_AGENT_PROMPTS:
        return jsonify({"error": f"Unknown agent key: {agent_key}"}), 400

    data       = request.get_json(silent=True) or {}
    new_prompt = data.get("system_prompt", "").strip()
    if not new_prompt:
        return jsonify({"error": "Prompt cannot be empty"}), 400

    record = AgentPrompt.query.filter_by(agent_key=agent_key).first()
    if record is None:
        record = AgentPrompt(
            agent_key=agent_key,
            agent_name=_DEFAULT_AGENT_PROMPTS[agent_key]["name"],
            description=_DEFAULT_AGENT_PROMPTS[agent_key]["description"],
        )
        db.session.add(record)

    record.system_prompt = new_prompt
    record.updated_at    = datetime.now(timezone.utc)
    db.session.commit()

    default_prompt = _DEFAULT_AGENT_PROMPTS[agent_key]["default"]
    log.info(f"[PROMPTS] Updated prompt for '{agent_key}'")
    return jsonify({
        "success":    True,
        "is_default": new_prompt == default_prompt,
        "updated_at": record.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/prompts/<agent_key>/reset", methods=["POST"])
@permission_required("can_manage_prompts")
def reset_prompt(agent_key):
    """Reset an agent's prompt to the built-in default."""
    if agent_key not in _DEFAULT_AGENT_PROMPTS:
        return jsonify({"error": f"Unknown agent key: {agent_key}"}), 400

    record = AgentPrompt.query.filter_by(agent_key=agent_key).first()
    if record:
        record.system_prompt = _DEFAULT_AGENT_PROMPTS[agent_key]["default"]
        record.updated_at    = datetime.now(timezone.utc)
        db.session.commit()

    log.info(f"[PROMPTS] Reset prompt for '{agent_key}' to default")
    return jsonify({"success": True})


@app.route("/api/prompts/<agent_key>/toggle", methods=["POST"])
@permission_required("can_manage_prompts")
def toggle_prompt(agent_key):
    """Enable or disable the global prompt for an agent.

    Body (JSON): { "is_enabled": true | false }
    When disabled the pipeline falls back to the built-in hardcoded default.
    """
    if agent_key not in _DEFAULT_AGENT_PROMPTS:
        return jsonify({"error": f"Unknown agent key: {agent_key}"}), 400

    data = request.get_json(silent=True) or {}
    if "is_enabled" not in data:
        return jsonify({"error": "Missing 'is_enabled' field"}), 400

    record = AgentPrompt.query.filter_by(agent_key=agent_key).first()
    if record is None:
        return jsonify({"error": "Prompt record not found"}), 404

    record.is_enabled = bool(data["is_enabled"])
    record.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    state = "enabled" if record.is_enabled else "disabled"
    log.info(f"[PROMPTS] Global prompt for '{agent_key}' {state}")
    return jsonify({"success": True, "agent_key": agent_key, "is_enabled": record.is_enabled})



# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE HISTORY API
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/history", methods=["GET"])
@login_required
def get_history():
    """List the last 100 pipeline runs."""
    runs = PipelineRun.query.order_by(PipelineRun.started_at.desc()).limit(100).all()
    return jsonify([{
        "id":               r.id,
        "started_at":       r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else None,
        "completed_at":     r.completed_at.strftime("%Y-%m-%d %H:%M:%S") if r.completed_at else None,
        "status":           r.status,
        "total_accounts":   r.total_accounts,
        "processed":        r.processed,
        "skipped":          r.skipped,
        "errors":           r.errors,
        "duration_seconds": r.duration_seconds,
        "csv_filename":     r.csv_filename or "—",
        # Who ran it
        "user_id":          r.user_id,
        "user_name":        r.user.name  if r.user     else "—",
        "user_email":       r.user.email if r.user     else "—",
        # Which campaign (None = no campaign / default prompts)
        "campaign_id":      r.campaign_id,
        "campaign_name":    r.campaign.name if r.campaign else "—",
    } for r in runs])


@app.route("/api/history/<int:run_id>/results", methods=["GET"])
@login_required
def get_history_results(run_id):
    """Return all results for a specific past run."""
    run = db.session.get(PipelineRun, run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    return jsonify([r.to_dict() for r in run.results])


@app.route("/api/history/<int:run_id>/export", methods=["GET"])
@login_required
def export_history_run(run_id):
    """Download a past run's results as CSV."""
    run = db.session.get(PipelineRun, run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404

    results = [r.to_dict() for r in run.results]
    if not results:
        return jsonify({"error": "No results for this run"}), 400

    df  = pd.DataFrame(results)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)

    ts   = run.started_at.strftime("%Y%m%d_%H%M%S") if run.started_at else "unknown"
    fname = f"calyxr_run{run_id}_{ts}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(e):
    log.warning(f"[HTTP] 404 — {request.method} {request.path}")
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large (max 16 MB)"}), 413

@app.errorhandler(500)
def server_error(e):
    log.error(f"[HTTP] 500 — {e}")
    return jsonify({"error": "Internal server error"}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n🚀  Calyxr Outbound Dashboard  →  http://localhost:5000")
    print(f"📄  Logs directory             →  {LOG_DIR}\n")
    app.run(debug=False, port=5000, threaded=True)
