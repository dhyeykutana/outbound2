"""
Authentication & Role Management Blueprint for Calyxr.

Routes:
  POST /api/auth/register   — Create new account (first user → super_admin)
  POST /api/auth/login      — Login and start session
  POST /api/auth/logout     — Destroy session
  GET  /api/auth/me         — Current user info + permissions
  GET  /api/users           — List all users          (super_admin)
  POST /api/users           — Create user             (super_admin)
  PUT  /api/users/<id>      — Update role / status    (super_admin)
  DELETE /api/users/<id>    — Delete user             (super_admin)
  GET  /api/roles           — List all roles          (any logged-in user)
"""

import logging
from datetime import datetime
from functools import wraps

from flask import Blueprint, request, jsonify, redirect, url_for, session
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)

from database import db, User, Role

log = logging.getLogger("calyxr")

auth_bp = Blueprint("auth", __name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Default roles seeded at startup
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_ROLES = [
    {
        "name":                  "super_admin",
        "description":           "Full access including user management",
        "can_manage_users":      True,
        "can_manage_config":     True,
        "can_manage_prompts":    True,
        "can_manage_campaigns":  True,
        "can_run_pipeline":      True,
        "can_view_results":      True,
        "can_export":            True,
    },
    {
        "name":                  "admin",
        "description":           "Manage pipeline, configuration, prompts, and campaigns",
        "can_manage_users":      False,
        "can_manage_config":     True,
        "can_manage_prompts":    True,
        "can_manage_campaigns":  True,
        "can_run_pipeline":      True,
        "can_view_results":      True,
        "can_export":            True,
    },
    {
        "name":                  "analyst",
        "description":           "Run pipeline, manage campaigns and campaign prompts, view/export results",
        "can_manage_users":      False,
        "can_manage_config":     False,
        "can_manage_prompts":    False,
        "can_manage_campaigns":  True,
        "can_run_pipeline":      True,
        "can_view_results":      True,
        "can_export":            True,
    },
    {
        "name":                  "viewer",
        "description":           "Read-only access to results",
        "can_manage_users":      False,
        "can_manage_config":     False,
        "can_manage_prompts":    False,
        "can_manage_campaigns":  False,
        "can_run_pipeline":      False,
        "can_view_results":      True,
        "can_export":            False,
    },
]

# Fields that seed_roles syncs on every startup (excludes name which is the key)
_ROLE_PERM_FIELDS = [
    "description",
    "can_manage_users",
    "can_manage_config",
    "can_manage_prompts",
    "can_manage_campaigns",
    "can_run_pipeline",
    "can_view_results",
    "can_export",
]


def seed_roles():
    """Insert default roles if missing, and update permission fields on existing rows."""
    for r in DEFAULT_ROLES:
        existing = Role.query.filter_by(name=r["name"]).first()
        if existing is None:
            db.session.add(Role(**r))
        else:
            for field in _ROLE_PERM_FIELDS:
                if field in r:
                    setattr(existing, field, r[field])
    db.session.commit()
    log.info("DB: Roles seeded / verified.")


# ─────────────────────────────────────────────────────────────────────────────
#  Flask-Login setup
# ─────────────────────────────────────────────────────────────────────────────

login_manager = LoginManager()


def init_login_manager(app):
    login_manager.init_app(app)
    login_manager.login_view = "serve_login"       # redirect unauthenticated page requests
    login_manager.login_message = "Please log in to access this page."


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    """Return 401 for API calls; redirect to /login for page requests."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required", "redirect": "/login"}), 401
    return redirect("/login")


# ─────────────────────────────────────────────────────────────────────────────
#  Permission decorator
# ─────────────────────────────────────────────────────────────────────────────

def permission_required(permission: str):
    """
    Decorator that checks a boolean permission flag on the current user's role.
    Usage: @permission_required('can_manage_users')
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not getattr(current_user.role, permission, False):
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
#  Auth routes
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    data     = request.get_json(silent=True) or {}
    name     = str(data.get("name",     "")).strip()
    email    = str(data.get("email",    "")).strip().lower()
    password = str(data.get("password", "")).strip()

    if not name or not email or not password:
        return jsonify({"error": "name, email, and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409

    # First registered user automatically becomes super_admin.
    # To avoid a TOCTOU race where two simultaneous registrations both see
    # count()==0, we flush the new user first, then re-check the count under
    # the same transaction before committing.
    super_admin_role = Role.query.filter_by(name="super_admin").first()
    viewer_role      = Role.query.filter_by(name="viewer").first()
    if super_admin_role is None or viewer_role is None:
        return jsonify({"error": "Role configuration missing — contact administrator"}), 500

    # Optimistically assign super_admin; re-check count after flush
    user = User(name=name, email=email, role_id=super_admin_role.id)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.flush()   # assigns user.id; keeps transaction open
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Email already registered"}), 409

    # If more than one user now exists, this user lost the race → viewer
    if db.session.query(User).count() > 1:
        user.role_id = viewer_role.id
        role_name = "viewer"
    else:
        role_name = "super_admin"

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Registration failed — please try again"}), 500

    login_user(user, remember=True)
    user.last_login = datetime.utcnow()
    db.session.commit()

    log.info(f"[AUTH] Registered new user: {email} | role={role_name}")
    return jsonify({
        "success": True,
        "user":    user.to_dict(),
        "message": "Account created successfully",
    }), 201


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = str(data.get("email",    "")).strip().lower()
    password = str(data.get("password", "")).strip()
    remember = bool(data.get("remember", False))

    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    user = User.query.filter_by(email=email).first()
    if user is None or not user.check_password(password):
        log.warning(f"[AUTH] Failed login attempt for: {email}")
        return jsonify({"error": "Invalid email or password"}), 401

    if not user.is_active:
        return jsonify({"error": "Account is disabled — contact administrator"}), 403

    session.permanent = True          # honour PERMANENT_SESSION_LIFETIME
    login_user(user, remember=remember)
    user.last_login = datetime.utcnow()
    db.session.commit()

    log.info(f"[AUTH] Login: {email} | role={user.role.name}")
    return jsonify({
        "success": True,
        "user":    user.to_dict(),
    })


@auth_bp.route("/api/auth/logout", methods=["POST"])
@login_required
def logout():
    log.info(f"[AUTH] Logout: {current_user.email}")
    logout_user()
    return jsonify({"success": True})


@auth_bp.route("/api/auth/change-password", methods=["POST"])
@login_required
def change_password():
    """Any logged-in user can change their own password (must supply current password)."""
    data         = request.get_json(silent=True) or {}
    current_pw   = str(data.get("current_password", "")).strip()
    new_pw       = str(data.get("new_password",     "")).strip()
    confirm_pw   = str(data.get("confirm_password", "")).strip()

    if not current_pw or not new_pw or not confirm_pw:
        return jsonify({"error": "All three fields are required"}), 400
    if not current_user.check_password(current_pw):
        return jsonify({"error": "Current password is incorrect"}), 401
    if new_pw != confirm_pw:
        return jsonify({"error": "New passwords do not match"}), 400
    if len(new_pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    current_user.set_password(new_pw)
    db.session.commit()
    log.info(f"[AUTH] Password changed by user: {current_user.email}")
    return jsonify({"success": True, "message": "Password changed successfully"})


@auth_bp.route("/api/auth/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    """
    Called by the client-side idle timer on every user activity burst.
    Touching the session dict marks it modified so Flask resets the cookie
    expiry — effectively sliding the server-side session window forward.
    """
    session.modified = True
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/me", methods=["GET"])
@login_required
def me():
    role = current_user.role
    return jsonify({
        "user":        current_user.to_dict(),
        "permissions": role.to_dict() if role else {},
    })


# ─────────────────────────────────────────────────────────────────────────────
#  User management (super_admin only)
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/api/users", methods=["GET"])
@permission_required("can_manage_users")
def list_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify([u.to_dict() for u in users])


@auth_bp.route("/api/users", methods=["POST"])
@permission_required("can_manage_users")
def create_user():
    data     = request.get_json(silent=True) or {}
    name     = str(data.get("name",     "")).strip()
    email    = str(data.get("email",    "")).strip().lower()
    password = str(data.get("password", "")).strip()
    role_id  = data.get("role_id")

    if not name or not email or not password or not role_id:
        return jsonify({"error": "name, email, password, and role_id are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "Email already registered"}), 409

    role = db.session.get(Role, int(role_id))
    if role is None:
        return jsonify({"error": "Invalid role_id"}), 400

    user = User(name=name, email=email, role_id=role.id)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    log.info(f"[AUTH] Admin created user: {email} | role={role.name}")
    return jsonify({"success": True, "user": user.to_dict()}), 201


@auth_bp.route("/api/users/<int:user_id>", methods=["PUT"])
@permission_required("can_manage_users")
def update_user(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    # Prevent removing super_admin from the last super_admin
    data = request.get_json(silent=True) or {}

    if "role_id" in data:
        role = db.session.get(Role, int(data["role_id"]))
        if role is None:
            return jsonify({"error": "Invalid role_id"}), 400
        # Guard: don't demote the last super_admin
        if user.role.name == "super_admin" and role.name != "super_admin":
            sa_count = (
                User.query
                .join(Role)
                .filter(Role.name == "super_admin", User.is_active == True)
                .count()
            )
            if sa_count <= 1:
                return jsonify({"error": "Cannot demote the last super_admin"}), 400
        user.role_id = role.id

    if "is_active" in data:
        user.is_active = bool(data["is_active"])

    if "name" in data:
        user.name = str(data["name"]).strip() or user.name

    if "password" in data and data["password"]:
        if len(data["password"]) < 8:
            return jsonify({"error": "Password must be at least 8 characters"}), 400
        user.set_password(data["password"])

    db.session.commit()
    log.info(f"[AUTH] Updated user id={user_id}: {user.email}")
    return jsonify({"success": True, "user": user.to_dict()})


@auth_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@permission_required("can_manage_users")
def delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "Cannot delete your own account"}), 400

    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    if user.role.name == "super_admin":
        sa_count = (
            User.query
            .join(Role)
            .filter(Role.name == "super_admin", User.is_active == True)
            .count()
        )
        if sa_count <= 1:
            return jsonify({"error": "Cannot delete the last super_admin"}), 400

    log.info(f"[AUTH] Deleted user id={user_id}: {user.email}")
    db.session.delete(user)
    db.session.commit()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────────
#  Admin password reset (super_admin or admin)
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/api/users/<int:user_id>/password", methods=["PUT"])
@login_required
def set_user_password(user_id):
    """Super admin (or any role with can_manage_users) can set any user's password."""
    role = current_user.role
    if not getattr(role, "can_manage_users", False):
        return jsonify({"error": "Insufficient permissions"}), 403

    target = db.session.get(User, user_id)
    if target is None:
        return jsonify({"error": "User not found"}), 404

    # Admin cannot change a super_admin's password (only super_admin can)
    if target.role.name == "super_admin" and not getattr(role, "can_manage_users", False):
        return jsonify({"error": "Admins cannot change a Super Admin's password"}), 403

    data       = request.get_json(silent=True) or {}
    new_pw     = str(data.get("new_password",     "")).strip()
    confirm_pw = str(data.get("confirm_password", "")).strip()

    if not new_pw or not confirm_pw:
        return jsonify({"error": "new_password and confirm_password are required"}), 400
    if new_pw != confirm_pw:
        return jsonify({"error": "Passwords do not match"}), 400
    if len(new_pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    target.set_password(new_pw)
    db.session.commit()
    log.info(f"[AUTH] Password for user id={user_id} ({target.email}) reset by {current_user.email}")
    return jsonify({"success": True, "message": f"Password for {target.name} updated successfully"})


# ─────────────────────────────────────────────────────────────────────────────
#  Roles listing + permission update (super_admin only for writes)
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/api/roles", methods=["GET"])
@login_required
def list_roles():
    roles = Role.query.order_by(Role.id).all()
    return jsonify([r.to_dict() for r in roles])


_EDITABLE_PERMS = {
    "can_manage_users",
    "can_manage_config",
    "can_manage_prompts",
    "can_manage_campaigns",
    "can_run_pipeline",
    "can_view_results",
    "can_export",
}

@auth_bp.route("/api/roles/<int:role_id>", methods=["PUT"])
@permission_required("can_manage_users")
def update_role(role_id):
    """Update permission flags for a role (super_admin only). super_admin role is locked."""
    role = db.session.get(Role, role_id)
    if role is None:
        return jsonify({"error": "Role not found"}), 404
    if role.name == "super_admin":
        return jsonify({"error": "The super_admin role cannot be modified"}), 403

    data = request.get_json(silent=True) or {}
    for perm in _EDITABLE_PERMS:
        if perm in data:
            setattr(role, perm, bool(data[perm]))

    db.session.commit()
    log.info(f"[AUTH] Role '{role.name}' (id={role_id}) permissions updated by {current_user.email}")
    return jsonify({"success": True, "role": role.to_dict()})
