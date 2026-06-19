import os
import sys
import json
import re
import secrets
import threading

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, session, g
)
sys.path.insert(0, os.path.dirname(__file__))

from werkzeug.middleware.proxy_fix import ProxyFix

import config
from models.db import init_db
from models import queries
from auth.sso import get_auth_url, get_token_from_code, login_required, check_group

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

QUERIES_FILE = os.path.join(os.path.dirname(__file__), "queries.json")


# ── CSRF helpers ──────────────────────────────────────────────────────────────

def _get_csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(24)
    return session["_csrf"]


def _check_csrf():
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not token or token != session.get("_csrf"):
        return False
    return True


# ── Template context ──────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {
        "csrf_token": _get_csrf_token,
        "session": session,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rbac_error(exc: Exception) -> str:
    msg = str(exc)
    if "AuthorizationFailed" in msg or "does not have authorization" in msg:
        return (
            "Permission denied. The data service principal requires 'Reader', "
            "'Cost Management Reader', and 'Log Analytics Data Reader' roles "
            "on the target subscriptions. "
            f"Azure error: {msg}"
        )
    if "AADSTS" in msg:
        return f"Authentication failed. Check AZURE_CLIENT_ID / AZURE_CLIENT_SECRET. Azure error: {msg}"
    return f"Azure error: {msg}"


def _redirect_uri() -> str:
    return url_for("auth_callback", _external=True)


def _load_queries() -> list[dict]:
    """Load blitz queries from queries.json; return empty list on any error."""
    try:
        with open(QUERIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_queries(queries_list: list[dict]):
    with open(QUERIES_FILE, "w", encoding="utf-8") as f:
        json.dump(queries_list, f, indent=2)


# ── Login landing page ────────────────────────────────────────────────────────

@app.route("/login")
def login_page():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    return render_template("login.html")


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/auth/login")
def auth_login():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    return redirect(get_auth_url(_redirect_uri(), state))


@app.route("/auth/callback")
def auth_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Invalid OAuth state — possible CSRF. Please try again.", "danger")
        return redirect(url_for("auth_login"))

    error = request.args.get("error")
    if error:
        flash(f"Sign-in failed: {request.args.get('error_description', error)}", "danger")
        return redirect(url_for("auth_login"))

    result = get_token_from_code(request.args["code"], _redirect_uri())

    if "error" in result:
        flash(f"Token exchange failed: {result.get('error_description', result['error'])}", "danger")
        return redirect(url_for("auth_login"))

    claims = result.get("id_token_claims", {})
    if not check_group(claims):
        flash("Access denied — your account is not in the required group.", "danger")
        return redirect(url_for("auth_login"))

    session["user"] = {
        "name": claims.get("name") or claims.get("preferred_username", "Unknown"),
        "email": claims.get("preferred_username", ""),
        "oid": claims.get("oid", ""),
    }

    return redirect(session.pop("next", url_for("dashboard")))


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    logout_url = (
        f"{config.AUTHORITY}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={url_for('login_page', _external=True)}"
    )
    return redirect(logout_url)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    try:
        vm_power = queries.vm_power_summary()
        ctx = {
            "sync": queries.last_sync_info(),
            "vm_power": vm_power,
            "vm_total": sum(vm_power.values()),
            "pools": queries.get_elastic_pools(),
            "mtd_cost": queries.get_mtd_total(),
            "advisor_cats": queries.advisor_category_summary(),
            "backup_problems": queries.backup_problem_count(),
            "health_summary": queries.health_summary(),
        }
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        ctx = {"sync": queries.last_sync_info()}
    return render_template("dashboard.html", **ctx)


# ── VMs ───────────────────────────────────────────────────────────────────────

@app.route("/vms")
@login_required
def vms():
    rg = request.args.get("rg")
    tag = request.args.get("tag")
    try:
        vm_list = queries.get_vms(resource_group=rg, tag_filter=tag)
        for vm in vm_list:
            cpu = queries.latest_vm_cpu(vm["vm_id"])
            vm["cpu_pct"] = round(cpu, 1) if cpu is not None else None
            vm["cpu_status"] = queries.cpu_status(cpu)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        vm_list = []
    return render_template(
        "vms.html",
        vms=vm_list,
        resource_groups=queries.distinct_resource_groups(),
        selected_rg=rg,
        selected_tag=tag,
        sync=queries.last_sync_info(),
    )


@app.route("/vms/<resource_group>/<vm_name>")
@login_required
def vm_detail(resource_group, vm_name):
    try:
        vm = queries.get_vm_by_name(resource_group, vm_name)
        if not vm:
            flash(f"VM '{vm_name}' not found in resource group '{resource_group}'.", "warning")
            return redirect(url_for("vms"))
        advisor_recs = queries.get_advisor_for_resource(vm["vm_id"])
        backup_item  = queries.get_backup_for_vm(vm_name)
        health_item  = queries.get_health_for_resource(vm["vm_id"])
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("vms"))
    return render_template(
        "vm_detail.html",
        vm=vm,
        advisor_recs=advisor_recs,
        backup_item=backup_item,
        health_item=health_item,
        sync=queries.last_sync_info(),
    )


@app.route("/vms/<path:vm_id>/metrics")
@login_required
def vm_metrics(vm_id):
    metric = request.args.get("metric", "Percentage CPU")
    data = queries.get_vm_metrics(vm_id, metric, hours=24)
    return jsonify(data)


# ── SQL / Elastic Pools ────────────────────────────────────────────────────────

@app.route("/sql")
@login_required
def sql_view():
    rg = request.args.get("rg")
    try:
        servers = queries.get_sql_servers(resource_group=rg)
        pools = queries.get_elastic_pools()
        databases = queries.get_databases()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        servers, pools, databases = [], [], []
    return render_template(
        "sql.html",
        servers=servers,
        pools=pools,
        databases=databases,
        resource_groups=queries.distinct_resource_groups(),
        selected_rg=rg,
        sync=queries.last_sync_info(),
    )


@app.route("/sql/<resource_group>/<server_name>")
@login_required
def sql_detail(resource_group, server_name):
    try:
        server = queries.get_sql_server_by_name(resource_group, server_name)
        if not server:
            flash(f"SQL server '{server_name}' not found.", "warning")
            return redirect(url_for("sql_view"))
        databases = queries.get_databases(server_name=server_name)
        pools = queries.get_elastic_pools(server_name=server_name)
        advisor_recs = queries.get_advisor_for_resource(server["server_id"])
        health_item  = queries.get_health_for_resource(server["server_id"])
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("sql_view"))
    return render_template(
        "sql_detail.html",
        server=server,
        databases=databases,
        pools=pools,
        advisor_recs=advisor_recs,
        health_item=health_item,
        sync=queries.last_sync_info(),
    )


# ── Cost ──────────────────────────────────────────────────────────────────────

@app.route("/cost")
@login_required
def cost_view():
    sub = request.args.get("sub")
    try:
        daily = queries.get_cost_daily(subscription_id=sub)
        mtd = queries.get_mtd_total()
        from azure_client.cost import detect_anomalies
        anomalies = detect_anomalies(daily)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        daily, mtd, anomalies = [], 0, []
    return render_template(
        "cost.html",
        daily=daily,
        mtd=mtd,
        anomalies=anomalies,
        sub=sub,
        sync=queries.last_sync_info(),
    )


# ── Advisor ───────────────────────────────────────────────────────────────────

@app.route("/advisor")
@login_required
def advisor_view():
    category = request.args.get("category")
    try:
        recs = queries.get_advisor_recs(category=category)
        summary = queries.advisor_category_summary()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        recs, summary = [], {}
    return render_template(
        "advisor.html",
        recs=recs,
        summary=summary,
        selected_category=category,
        sync=queries.last_sync_info(),
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/health")
@login_required
def health_view():
    state = request.args.get("state")
    try:
        items = queries.get_resource_health(state=state)
        summary = queries.health_summary()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        items, summary = [], {}
    return render_template(
        "health.html",
        items=items,
        summary=summary,
        selected_state=state,
        sync=queries.last_sync_info(),
    )


# ── Backup ────────────────────────────────────────────────────────────────────

@app.route("/backup")
@login_required
def backup_view():
    try:
        items = queries.get_backup_status()
        problem_count = queries.backup_problem_count()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        items, problem_count = [], 0
    return render_template(
        "backup.html",
        items=items,
        problem_count=problem_count,
        sync=queries.last_sync_info(),
    )


# ── KQL Console ───────────────────────────────────────────────────────────────

@app.route("/kql")
@login_required
def kql_console():
    blitz = _load_queries()
    workspace_id = getattr(config, "LOG_ANALYTICS_WORKSPACE_ID", "")
    return render_template(
        "kql.html",
        blitz=blitz,
        workspace_id=workspace_id,
        sync=queries.last_sync_info(),
    )


@app.route("/api/kql/run", methods=["POST"])
@login_required
def api_kql_run():
    if not _check_csrf():
        return jsonify({"error": "Invalid CSRF token"}), 403

    body = request.get_json(silent=True) or {}
    kql  = (body.get("query") or "").strip()
    workspace_id = getattr(config, "LOG_ANALYTICS_WORKSPACE_ID", "")

    if not kql:
        return jsonify({"error": "Query is empty"}), 400
    if not workspace_id:
        return jsonify({"error": "LOG_ANALYTICS_WORKSPACE_ID not configured — add it to .env"}), 503

    try:
        from azure_client.logs import run_kql
        result = run_kql(workspace_id, kql, timeout_seconds=30, max_rows=1000)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/kql/queries", methods=["GET"])
@login_required
def api_kql_queries_list():
    return jsonify(_load_queries())


@app.route("/api/kql/queries", methods=["POST"])
@login_required
def api_kql_queries_save():
    if not _check_csrf():
        return jsonify({"error": "Invalid CSRF token"}), 403

    body = request.get_json(silent=True) or {}
    name  = (body.get("name") or "").strip()
    query = (body.get("query") or "").strip()
    desc  = (body.get("description") or "").strip()[:200]

    if not name or not query:
        return jsonify({"error": "name and query are required"}), 400

    # Validate name: alphanumeric, spaces, hyphens, underscores only
    if not re.match(r'^[\w\s\-]{1,80}$', name):
        return jsonify({"error": "Name must be 1–80 chars, letters/digits/spaces/hyphens only"}), 400

    existing = _load_queries()
    # Update if name already exists, otherwise append
    updated = False
    for item in existing:
        if item.get("name") == name:
            item["query"] = query
            item["description"] = desc
            updated = True
            break
    if not updated:
        existing.append({"name": name, "description": desc, "query": query})

    _save_queries(existing)
    return jsonify({"ok": True, "count": len(existing)})


@app.route("/api/kql/queries/<name>", methods=["DELETE"])
@login_required
def api_kql_queries_delete(name):
    if not _check_csrf():
        return jsonify({"error": "Invalid CSRF token"}), 403

    existing = _load_queries()
    filtered = [q for q in existing if q.get("name") != name]
    if len(filtered) == len(existing):
        return jsonify({"error": "Query not found"}), 404
    _save_queries(filtered)
    return jsonify({"ok": True})


# ── Alerts ────────────────────────────────────────────────────────────────────

@app.route("/alerts")
@login_required
def alerts_view():
    try:
        items   = queries.get_alerts()
        summary = queries.alert_summary()
    except Exception as exc:
        items, summary = [], {}
    return render_template(
        "alerts.html",
        items=items,
        summary=summary,
        sync=queries.last_sync_info(),
    )


# ── Topology ──────────────────────────────────────────────────────────────────

@app.route("/topology")
@login_required
def topology_view():
    return render_template("topology.html", sync=queries.last_sync_info())


@app.route("/api/topology")
@login_required
def api_topology():
    try:
        graph = queries.get_topology_graph()
        return jsonify(graph)
    except Exception as exc:
        return jsonify({"error": str(exc), "nodes": [], "edges": []}), 200


# ── Global search ─────────────────────────────────────────────────────────────

@app.route("/api/search")
@login_required
def api_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    results = queries.search_resources(q)
    return jsonify({"results": results})


# ── Sync Now ──────────────────────────────────────────────────────────────────

@app.route("/sync", methods=["POST"])
@login_required
def trigger_sync():
    if not _check_csrf():
        flash("CSRF check failed — please try again.", "danger")
        return redirect(request.referrer or url_for("dashboard"))

    def _run():
        from sync.sync_job import run_sync
        run_sync()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    flash("Sync started in background. Refresh in a moment to see updated data.", "info")
    next_url = request.referrer or url_for("dashboard")
    return redirect(next_url)


# ── Boot ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=config.APP_PORT, debug=False)
