"""
auth.py -- Authentication Blueprint for BizWhiz
"""
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import db as DB
from mailer import send_email as smtp_send

auth = Blueprint("auth", __name__, url_prefix="/auth")

PLANS = {
    "solo":   {"name": "Solo",   "price": 29,  "leads": "500",   "seats": 1,  "badge": ""},
    "pro":    {"name": "Pro",    "price": 79,  "leads": "2,500", "seats": 5,  "badge": "Most Popular"},
    "agency": {"name": "Agency", "price": 149, "leads": "10,000","seats": 20, "badge": "Best Value"},
}

def _smtp_cfg():
    return DB.get_all_settings()

def _smtp_ready(cfg):
    return bool(cfg.get("smtp_host") and cfg.get("smtp_user") and cfg.get("smtp_password"))

def _send_verification_email(email, full_name, token):
    cfg = _smtp_cfg()
    if not _smtp_ready(cfg): return False
    verify_url = url_for("auth.verify_email", token=token, _external=True)
    body = ("Hi " + full_name + ",\n\nWelcome to BizWhiz! Please verify your email:\n\n"
            + verify_url + "\n\n-- The BizWhiz Team")
    try:
        smtp_send(cfg["smtp_host"], int(cfg.get("smtp_port",587) or 587),
                  cfg["smtp_user"], cfg["smtp_password"],
                  cfg.get("from_email") or cfg["smtp_user"],
                  email, "Verify your BizWhiz email address", body)
        return True
    except Exception as exc:
        print("[auth] Verification email failed:", exc); return False

def _send_reset_email(email, full_name, token):
    cfg = _smtp_cfg()
    if not _smtp_ready(cfg): return False
    reset_url = url_for("auth.reset_password", token=token, _external=True)
    body = ("Hi " + full_name + ",\n\nClick to reset your password (expires in 1 hour):\n\n"
            + reset_url + "\n\n-- The BizWhiz Team")
    try:
        smtp_send(cfg["smtp_host"], int(cfg.get("smtp_port",587) or 587),
                  cfg["smtp_user"], cfg["smtp_password"],
                  cfg.get("from_email") or cfg["smtp_user"],
                  email, "Reset your BizWhiz password", body)
        return True
    except Exception as exc:
        print("[auth] Reset email failed:", exc); return False

@auth.route("/choose-plan")
def choose_plan():
    if current_user.is_authenticated: return redirect(url_for("dashboard"))
    return render_template("choose_plan.html", plans=PLANS)

@auth.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("dashboard"))
    if request.method == "POST":
        email    = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        remember = bool(request.form.get("remember"))
        user_data = DB.get_user_by_email(email)
        if not user_data or not check_password_hash(user_data["password_hash"], password):
            flash("Incorrect email or password.", "danger")
            return render_template("login.html", email=email)
        user = DB.User(user_data)
        login_user(user, remember=remember)
        DB.update_user_last_login(user_data["id"])
        if not user_data["email_verified"]:
            return redirect(url_for("auth.unverified"))
        next_page = request.args.get("next")
        return redirect(next_page or url_for("dashboard"))
    return render_template("login.html")

@auth.route("/register", methods=["GET","POST"])
def register():
    if current_user.is_authenticated: return redirect(url_for("dashboard"))
    plan_key = (request.form.get("plan") or request.args.get("plan","")).strip().lower()
    if plan_key not in PLANS: return redirect(url_for("auth.choose_plan"))
    plan = PLANS[plan_key]
    if request.method == "POST":
        full_name = request.form.get("full_name","").strip()
        email     = request.form.get("email","").strip().lower()
        company   = request.form.get("company","").strip()
        phone     = request.form.get("phone","").strip()
        password  = request.form.get("password","")
        confirm   = request.form.get("confirm_password","")
        agreed    = request.form.get("terms")
        errors = []
        if not full_name: errors.append("Full name is required.")
        if not email or "@" not in email: errors.append("A valid email address is required.")
        if len(password) < 8: errors.append("Password must be at least 8 characters.")
        if password != confirm: errors.append("Passwords do not match.")
        if not agreed: errors.append("You must agree to the Terms of Service.")
        if not errors and DB.get_user_by_email(email):
            errors.append("An account with that email already exists.")
        if errors:
            for e in errors: flash(e, "danger")
            return render_template("register.html", full_name=full_name, email=email,
                                   company=company, phone=phone, plan_key=plan_key, plan=plan)
        token = secrets.token_urlsafe(32)
        DB.create_user(full_name=full_name, email=email,
                       password_hash=generate_password_hash(password),
                       company=company, phone=phone, verify_token=token, plan=plan_key)
        sent = _send_verification_email(email, full_name, token)
        if sent:
            flash("Account created! Check your inbox to verify your email.", "success")
        else:
            user_data = DB.get_user_by_email(email)
            if user_data: DB.verify_user_email(user_data["id"])
            flash("Account created! You can log in now.", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html", plan_key=plan_key, plan=plan)

@auth.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been logged out.", "info")
    return redirect(url_for("auth.login"))

@auth.route("/verify/<token>")
def verify_email(token):
    user_data = DB.get_user_by_verify_token(token)
    if not user_data:
        flash("That verification link is invalid or has already been used.", "danger")
        return redirect(url_for("auth.login"))
    DB.verify_user_email(user_data["id"])
    flash("Email verified! You're all set.", "success")
    return redirect(url_for("auth.login"))

@auth.route("/unverified")
@login_required
def unverified():
    if current_user.email_verified: return redirect(url_for("dashboard"))
    return render_template("verify_email.html")

@auth.route("/resend-verification", methods=["POST"])
@login_required
def resend_verification():
    user_data = DB.get_user_by_email(current_user.email)
    if user_data and not user_data["email_verified"]:
        sent = _send_verification_email(current_user.email, current_user.full_name, user_data["verify_token"])
        if sent:
            flash("Verification email resent!", "success")
        else:
            flash("Could not send email -- configure SMTP in Settings first.", "warning")
    return redirect(url_for("auth.unverified"))

@auth.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        user_data = DB.get_user_by_email(email)
        if user_data:
            token   = secrets.token_urlsafe(32)
            expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
            DB.set_reset_token(user_data["id"], token, expires)
            _send_reset_email(email, user_data["full_name"], token)
        flash("If that email is registered, you'll receive a reset link shortly.", "info")
        return redirect(url_for("auth.forgot_password"))
    return render_template("forgot_password.html")

@auth.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    user_data = DB.get_user_by_reset_token(token)
    if not user_data:
        flash("That reset link is invalid or has already been used.", "danger")
        return redirect(url_for("auth.forgot_password"))
    expires = user_data.get("reset_expires","")
    if expires:
        try:
            if datetime.utcnow() > datetime.fromisoformat(expires):
                flash("That reset link has expired. Please request a new one.", "danger")
                return redirect(url_for("auth.forgot_password"))
        except ValueError:
            pass
    if request.method == "POST":
        password = request.form.get("password","")
        confirm  = request.form.get("confirm_password","")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("reset_password.html", token=token)
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", token=token)
        DB.update_password(user_data["id"], generate_password_hash(password))
        DB.clear_reset_token(user_data["id"])
        flash("Password updated! You can now log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("reset_password.html", token=token)
