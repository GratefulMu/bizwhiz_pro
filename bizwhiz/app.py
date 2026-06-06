"""
app.py -- BizWhiz Flask application
"""
import csv, io, threading, os

from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, flash, Response)
from flask_login import LoginManager, login_required, current_user

import db as DB
from search import get_coordinates, search_nearby_businesses, find_email_on_website
from mailer import send_email as smtp_send
from auth   import auth as auth_blueprint

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bizwhiz-dev-key")

login_manager = LoginManager(app)
login_manager.login_view    = "auth.login"
login_manager.login_message = "Please log in to access BizWhiz."
login_manager.login_message_category = "warning"

@login_manager.user_loader
def load_user(user_id):
    data = DB.get_user_by_id(int(user_id))
    return DB.User(data) if data else None

app.register_blueprint(auth_blueprint)

STAGES = ["New","Contacted","Proposal Sent","Negotiating","Closed - Won","Closed - Lost"]
STAGE_COLORS = {
    "New":"#6c757d","Contacted":"#0d6efd","Proposal Sent":"#fd7e14",
    "Negotiating":"#6f42c1","Closed - Won":"#198754","Closed - Lost":"#dc3545",
}
ACTIVITY_ICONS = {
    "note":"bi-journal-text","email":"bi-envelope","call":"bi-telephone",
    "stage_change":"bi-arrow-left-right","update":"bi-pencil",
}

_search_lock  = threading.Lock()
_search_state = {"running":False,"progress":"","error":"","done":False,"count":0}

@app.template_filter("stage_color")
def stage_color(s): return STAGE_COLORS.get(s, "#6c757d")

@app.template_filter("activity_icon")
def activity_icon(t): return ACTIVITY_ICONS.get(t, "bi-circle")

@app.before_request
def _setup():
    DB.init_db()

@app.before_request
def _require_verified():
    from flask import request as req
    if req.endpoint and (
        req.endpoint.startswith("auth.")
        or req.endpoint == "static"
        or req.endpoint == "landing"
    ):
        return
    if current_user.is_authenticated and not current_user.email_verified:
        return redirect(url_for("auth.unverified"))

@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/dashboard")
@login_required
def dashboard():
    stats     = DB.get_stats()
    bs        = stats.get("by_stage", {})
    total     = stats.get("total", 0)
    contacted = sum(bs.get(s,0) for s in ["Contacted","Proposal Sent","Negotiating","Closed - Won","Closed - Lost"])
    won       = bs.get("Closed - Won", 0)
    metrics   = {
        "total":        total,
        "contacted":    contacted,
        "won":          won,
        "in_progress":  sum(bs.get(s,0) for s in ["Contacted","Proposal Sent","Negotiating"]),
        "outreach_pct": round(contacted/total*100) if total else 0,
        "win_rate":     round(won/contacted*100)   if contacted else 0,
    }
    return render_template("dashboard.html", stages=STAGES, stats=stats,
                           metrics=metrics, recent=DB.get_recent_activities(15))

@app.route("/leads")
@login_required
def leads():
    stage = request.args.get("stage",""); q = request.args.get("q","")
    return render_template("leads.html",
                           leads=DB.get_all_leads(stage or None, q or None),
                           stages=STAGES, current_stage=stage, search=q)

@app.route("/leads/export")
@login_required
def export_leads():
    rows = DB.get_all_leads(request.args.get("stage") or None, request.args.get("q") or None)
    buf  = io.StringIO()
    w    = csv.DictWriter(buf, fieldnames=["id","name","website","phone","emails",
                                           "address","business_type","zip_code","stage","created_at"])
    w.writeheader(); w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=bizwhiz_leads.csv"})

@app.route("/leads/<int:lid>")
@login_required
def lead_detail(lid):
    lead = DB.get_lead(lid)
    if not lead:
        flash("Lead not found.","warning"); return redirect(url_for("leads"))
    return render_template("lead_detail.html", lead=lead, stages=STAGES,
                           activities=DB.get_activities(lid),
                           templates=DB.get_all_templates())

@app.route("/leads/<int:lid>/update", methods=["POST"])
@login_required
def update_lead_route(lid):
    DB.update_lead(lid, request.form.to_dict())
    DB.add_activity(lid, "update", "Lead information updated.")
    flash("Lead saved.","success"); return redirect(url_for("lead_detail", lid=lid))

@app.route("/leads/<int:lid>/stage", methods=["POST"])
@login_required
def update_stage(lid):
    stage = (request.json or {}).get("stage","")
    if stage not in STAGES: return jsonify({"error":"Invalid stage"}), 400
    old = DB.get_lead(lid)
    if not old: return jsonify({"error":"Not found"}), 404
    DB.update_lead(lid, {"stage": stage})
    DB.add_activity(lid, "stage_change", 'Stage changed to "' + stage + '"')
    return jsonify({"ok":True,"stage":stage,"color":STAGE_COLORS.get(stage)})

@app.route("/leads/<int:lid>/note", methods=["POST"])
@login_required
def add_note(lid):
    content = (request.json or {}).get("content","").strip()
    if not content: return jsonify({"error":"Note cannot be empty."}), 400
    DB.add_activity(lid, "note", content)
    return jsonify({"ok":True})

@app.route("/leads/<int:lid>/delete", methods=["POST"])
@login_required
def delete_lead_route(lid):
    DB.delete_lead(lid); flash("Lead deleted.","info")
    return redirect(url_for("leads"))

@app.route("/leads/<int:lid>/email", methods=["POST"])
@login_required
def send_lead_email(lid):
    lead = DB.get_lead(lid)
    if not lead: return jsonify({"error":"Lead not found"}), 404
    p       = request.json or {}
    to_addr = p.get("to_email","").strip()
    if not to_addr: return jsonify({"error":"Recipient email required."}), 400
    ctx = {"name":lead["name"],"website":lead["website"],"phone":lead["phone"],
           "address":lead["address"],"emails":lead["emails"]}
    try:
        subject = p.get("subject","").format_map(ctx)
        body    = p.get("body","").format_map(ctx)
    except Exception:
        subject = p.get("subject",""); body = p.get("body","")
    cfg = DB.get_all_settings()
    try:
        smtp_send(cfg.get("smtp_host",""), int(cfg.get("smtp_port",587) or 587),
                  cfg.get("smtp_user",""), cfg.get("smtp_password",""),
                  cfg.get("from_email") or cfg.get("smtp_user",""),
                  to_addr, subject, body)
        DB.add_activity(lid, "email", 'Sent to ' + to_addr + ' -- "' + subject + '"')
        if lead["stage"] == "New":
            DB.update_lead(lid, {"stage":"Contacted"})
            DB.add_activity(lid, "stage_change", 'Auto-advanced to "Contacted" after first email.')
        return jsonify({"ok":True})
    except Exception as exc:
        return jsonify({"error":str(exc)}), 500

@app.route("/pipeline")
@login_required
def pipeline():
    board = {s:[] for s in STAGES}
    for lead in DB.get_all_leads():
        board.setdefault(lead.get("stage","New"), []).append(lead)
    return render_template("pipeline.html", board=board, stages=STAGES)

@app.route("/templates")
@login_required
def email_templates():
    return render_template("email_templates.html", templates=DB.get_all_templates())

@app.route("/templates/new", methods=["POST"])
@login_required
def create_template_route():
    name = request.form.get("name","").strip()
    if not name:
        flash("Template name required.","danger"); return redirect(url_for("email_templates"))
    DB.create_template(name, request.form.get("subject",""), request.form.get("body",""))
    flash('Template "' + name + '" created.',"success"); return redirect(url_for("email_templates"))

@app.route("/templates/<int:tid>")
@login_required
def get_template_json(tid):
    t = DB.get_template(tid)
    return jsonify(t) if t else (jsonify({"error":"Not found"}), 404)

@app.route("/templates/<int:tid>/update", methods=["POST"])
@login_required
def update_template_route(tid):
    DB.update_template(tid, request.form.get("name",""),
                       request.form.get("subject",""), request.form.get("body",""))
    flash("Template updated.","success"); return redirect(url_for("email_templates"))

@app.route("/templates/<int:tid>/delete", methods=["POST"])
@login_required
def delete_template_route(tid):
    DB.delete_template(tid); flash("Template deleted.","info")
    return redirect(url_for("email_templates"))

@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    if request.method == "POST":
        for k in ["smtp_host","smtp_port","smtp_user","smtp_password","from_email","from_name"]:
            DB.set_setting(k, request.form.get(k,""))
        flash("Settings saved.","success"); return redirect(url_for("settings"))
    return render_template("settings.html", cfg=DB.get_all_settings())

@app.route("/settings/test-email", methods=["POST"])
@login_required
def test_email():
    cfg = DB.get_all_settings()
    to  = (request.json or {}).get("to_email") or cfg.get("smtp_user","")
    try:
        smtp_send(cfg.get("smtp_host",""), int(cfg.get("smtp_port",587) or 587),
                  cfg.get("smtp_user",""), cfg.get("smtp_password",""),
                  cfg.get("from_email") or cfg.get("smtp_user",""),
                  to, "BizWhiz Test Email", "Your SMTP settings are working correctly!")
        return jsonify({"ok":True})
    except Exception as exc:
        return jsonify({"error":str(exc)}), 500

@app.route("/api/search/start", methods=["POST"])
@login_required
def search_start():
    global _search_state
    with _search_lock:
        if _search_state["running"]:
            return jsonify({"error":"Search already running."}), 400
    p        = request.json or {}
    zip_code = p.get("zip_code","").strip()
    bt       = p.get("business_type","").strip()
    if not zip_code or not bt:
        return jsonify({"error":"zip_code and business_type required."}), 400
    radius = float(p.get("radius", 5))
    with _search_lock:
        _search_state = {"running":True,"progress":"Starting...","error":"","done":False,"count":0}
    threading.Thread(target=_run_search, args=(zip_code, radius, bt), daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/search/status")
@login_required
def search_status():
    with _search_lock: return jsonify(dict(_search_state))

@app.route("/api/stats")
@login_required
def api_stats(): return jsonify(DB.get_stats())

def _run_search(zip_code, radius_miles, business_type):
    global _search_state
    def _set(**kw):
        with _search_lock: _search_state.update(kw)
    try:
        _set(progress="Geocoding zip code...")
        lat, lon   = get_coordinates(zip_code)
        _set(progress="Querying OpenStreetMap...")
        businesses = search_nearby_businesses(lat, lon, radius_miles*1609.34, business_type)
        for i, biz in enumerate(businesses):
            _set(progress="Scraping emails (" + str(i+1) + "/" + str(len(businesses)) + "): " + biz['name'] + "...")
            biz["emails"] = ", ".join(find_email_on_website(biz["website"])) if biz.get("website") else ""
        count = DB.bulk_create_leads(businesses, zip_code, business_type)
        _set(running=False, progress="Done -- " + str(count) + " new lead(s) added.",
             done=True, count=count, error="")
    except Exception as exc:
        _set(running=False, progress="", done=True, count=0, error=str(exc))

@app.route("/leads/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_leads_route():
    ids = (request.json or {}).get("ids", [])
    if not ids: return jsonify({"error": "No IDs provided"}), 400
    deleted = DB.bulk_delete_leads_by_id([int(i) for i in ids])
    return jsonify({"ok": True, "deleted": deleted})

@app.route("/leads/delete-all", methods=["POST"])
@login_required
def delete_all_leads_route():
    DB.delete_all_leads()
    return jsonify({"ok": True})

@app.route("/leads/import", methods=["POST"])
@login_required
def import_leads_route():
    f = request.files.get("csv_file")
    if not f or not f.filename.endswith(".csv"):
        flash("Please upload a valid .csv file.", "danger")
        return redirect(url_for("leads"))
    try:
        text    = f.read().decode("utf-8-sig")
        reader  = csv.DictReader(io.StringIO(text))
        records = []
        for row in reader:
            name = (row.get("name") or row.get("Name") or "").strip()
            if not name: continue
            records.append({
                "name":          name,
                "website":       (row.get("website") or "").strip(),
                "phone":         (row.get("phone")   or "").strip(),
                "emails":        (row.get("emails")  or "").strip(),
                "address":       (row.get("address") or "").strip(),
                "business_type": (row.get("business_type") or "").strip(),
                "zip_code":      (row.get("zip_code") or "").strip(),
                "stage":         (row.get("stage")   or "New").strip(),
            })
        count = DB.bulk_create_leads(records)
        flash(str(count) + " lead(s) imported successfully.", "success")
    except Exception as exc:
        flash("Import failed: " + str(exc), "danger")
    return redirect(url_for("leads"))

if __name__ == "__main__":
    DB.init_db()
    print("\n  BizWhiz  ->  http://localhost:5000\n")
    app.run(debug=True, port=5000, use_reloader=False)
