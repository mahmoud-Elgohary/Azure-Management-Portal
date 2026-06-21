import os
import sys
import json
import re
import secrets
import threading
import time
import logging

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

log = logging.getLogger("wts.app")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ── Session lifetime ──────────────────────────────────────────────────────────
from datetime import timedelta
app.permanent_session_lifetime = timedelta(minutes=config.SESSION_TIMEOUT_MINUTES)

# ── Sync rate-limit state ─────────────────────────────────────────────────────
_last_sync_time: float = 0.0
_sync_lock = threading.Lock()

QUERIES_FILE = os.path.join(os.path.dirname(__file__), "queries.json")


# ── Session & request hooks ───────────────────────────────────────────────────

@app.before_request
def _renew_session():
    """Mark session as permanent so Flask applies the lifetime, and refresh activity."""
    if session.get("user"):
        session.permanent = True
        session.modified = True


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
        from azure_client.cost import detect_anomalies
        stats = queries.dashboard_stats()
        daily = queries.get_cost_daily()
        ctx = {
            "sync": queries.last_sync_info(),
            "stats": stats,
            "mtd_cost": queries.get_mtd_total(),
            "budget": config.MONTHLY_BUDGET,
            "advisor_cats": queries.advisor_category_summary(),
            "top_cost_rgs": queries.get_top_cost_resource_groups(6),
            "recent_activity": queries.get_recent_activity(8),
            "resource_counts": queries.get_resource_counts_by_type(),
            "health_summary": queries.health_summary(),
            "security_score": queries.calculate_security_score(),
            "top_risks": queries.get_top_security_risks(8),
            "recent_changes": queries.get_recent_changes_summary(),
            "cost_anomalies": detect_anomalies(daily),
            "currency": queries.get_cost_currency(),
            "reservation_summary": queries.get_reservation_summary(),
            "devops_summary": queries.get_devops_summary(),
        }
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        ctx = {"sync": queries.last_sync_info(), "stats": {}, "mtd_cost": 0, "budget": config.MONTHLY_BUDGET, "cost_anomalies": [], "currency": "EUR", "reservation_summary": {}, "devops_summary": {}}
    return render_template("dashboard.html", **ctx)


# ── VMs ───────────────────────────────────────────────────────────────────────

@app.route("/vms")
@login_required
def vms():
    rg    = request.args.get("rg")
    tag   = request.args.get("tag")
    state = request.args.get("state")
    try:
        vm_list = queries.get_vms_with_ips(resource_group=rg, tag_filter=tag, power_state=state)
        for vm in vm_list:
            cpu = queries.latest_vm_cpu(vm["vm_id"])
            vm["cpu_pct"] = round(cpu, 1) if cpu is not None else None
            vm["cpu_status"] = queries.cpu_status(cpu)
        power_summary = queries.vm_power_summary()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        vm_list, power_summary = [], {}
    return render_template(
        "vms.html",
        vms=vm_list,
        power_summary=power_summary,
        resource_groups=queries.distinct_resource_groups(),
        selected_rg=rg,
        selected_tag=tag,
        selected_state=state,
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
        nics         = queries.get_nics_for_vm(vm["vm_id"])
        nsg_rules    = queries.get_nsg_rules_for_vm(vm["vm_id"])
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("vms"))
    return render_template(
        "vm_detail.html",
        vm=vm,
        advisor_recs=advisor_recs,
        backup_item=backup_item,
        health_item=health_item,
        nics=nics,
        nsg_rules=nsg_rules,
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


# ── Elastic Pool detail ───────────────────────────────────────────────────────

@app.route("/sql/<resource_group>/<server_name>/pools/<pool_name>")
@login_required
def elastic_pool_detail(resource_group, server_name, pool_name):
    try:
        pool = queries.get_elastic_pool_by_name(resource_group, server_name, pool_name)
        if not pool:
            flash(f"Elastic Pool '{pool_name}' not found.", "warning")
            return redirect(url_for("sql_view"))
        databases = queries.get_databases_in_pool(pool["pool_id"])
        advisor_recs = queries.get_advisor_for_resource(pool["pool_id"])
        server = queries.get_sql_server_by_name(resource_group, server_name)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("sql_view"))
    return render_template(
        "elastic_pool_detail.html",
        pool=pool,
        databases=databases,
        advisor_recs=advisor_recs,
        server=server,
        sync=queries.last_sync_info(),
    )


# ── Cost ──────────────────────────────────────────────────────────────────────

@app.route("/cost")
@login_required
def cost_view():
    sub = request.args.get("sub")
    try:
        daily     = queries.get_cost_daily(subscription_id=sub)
        mtd       = queries.get_mtd_total()
        currency  = queries.get_cost_currency()
        from azure_client.cost import detect_anomalies
        anomalies = detect_anomalies(daily)
        cost_recs = queries.get_cost_advisor_recs()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        daily, mtd, anomalies, cost_recs, currency = [], 0, [], [], "EUR"
    return render_template(
        "cost.html",
        daily=daily,
        mtd=mtd,
        currency=currency,
        budget=config.MONTHLY_BUDGET,
        anomalies=anomalies,
        cost_recs=cost_recs,
        sub=sub,
        sync=queries.last_sync_info(),
    )


# ── Cost API ─────────────────────────────────────────────────────────────────

@app.route("/api/cost/trend")
@login_required
def api_cost_trend():
    return jsonify(queries.get_cost_trend_by_date())


@app.route("/api/cost/by-rg")
@login_required
def api_cost_by_rg():
    return jsonify(queries.get_cost_by_resource_group_mtd())


@app.route("/api/security/trend")
@login_required
def api_security_trend():
    days = int(request.args.get("days", 30))
    return jsonify(queries.get_security_score_trend(days))


# ── Advisor ───────────────────────────────────────────────────────────────────

@app.route("/advisor")
@login_required
def advisor_view():
    category = request.args.get("category")
    impact   = request.args.get("impact")
    try:
        recs = queries.get_advisor_recs(category=category)
        if impact:
            recs = [r for r in recs if r.get("impact") == impact]
        summary = queries.advisor_category_summary()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        recs, summary = [], {}
    return render_template(
        "advisor.html",
        recs=recs,
        summary=summary,
        selected_category=category,
        selected_impact=impact,
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
    workspaces = getattr(config, "LOG_ANALYTICS_WORKSPACES", [])
    workspace_id = workspaces[0]["id"] if workspaces else getattr(config, "LOG_ANALYTICS_WORKSPACE_ID", "")
    return render_template(
        "kql.html",
        blitz=blitz,
        workspace_id=workspace_id,
        workspaces=workspaces,
        sync=queries.last_sync_info(),
    )


@app.route("/api/kql/workspaces", methods=["GET"])
@login_required
def api_kql_workspaces():
    return jsonify(config.LOG_ANALYTICS_WORKSPACES)


@app.route("/api/kql/run", methods=["POST"])
@login_required
def api_kql_run():
    if not _check_csrf():
        return jsonify({"error": "Invalid CSRF token"}), 403

    body = request.get_json(silent=True) or {}
    kql  = (body.get("query") or "").strip()
    workspace_id = (body.get("workspace_id") or "").strip() or getattr(config, "LOG_ANALYTICS_WORKSPACE_ID", "")

    if not kql:
        return jsonify({"error": "Query is empty"}), 400
    if not workspace_id:
        return jsonify({"error": "LOG_ANALYTICS_WORKSPACE_ID not configured — add it to .env"}), 503

    try:
        from azure_client.logs import run_kql
        result = run_kql(workspace_id, kql, timeout_seconds=30, max_rows=1000)
        queries.save_kql_history(
            query=kql,
            workspace_id=workspace_id,
            row_count=result.get("row_count", 0),
            elapsed_ms=result.get("elapsed_ms", 0),
            had_error="error" in result,
        )
        return jsonify(result)
    except Exception as exc:
        queries.save_kql_history(kql, workspace_id, 0, 0, True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/kql/history", methods=["GET"])
@login_required
def api_kql_history():
    return jsonify(queries.get_kql_history())


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


# ── PostgreSQL ────────────────────────────────────────────────────────────────

@app.route("/postgresql")
@login_required
def postgresql_view():
    rg = request.args.get("rg")
    try:
        servers = queries.get_postgresql_servers(resource_group=rg)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        servers = []
    return render_template(
        "postgresql.html",
        servers=servers,
        resource_groups=queries.distinct_resource_groups(),
        selected_rg=rg,
        sync=queries.last_sync_info(),
    )


@app.route("/postgresql/<resource_group>/<server_name>")
@login_required
def postgresql_detail(resource_group, server_name):
    try:
        server = queries.get_postgresql_server_by_name(resource_group, server_name)
        if not server:
            flash(f"PostgreSQL server '{server_name}' not found.", "warning")
            return redirect(url_for("postgresql_view"))
        advisor_recs = queries.get_advisor_for_resource(server["server_id"])
        health_item  = queries.get_health_for_resource(server["server_id"])
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("postgresql_view"))
    return render_template(
        "postgresql_detail.html",
        server=server,
        advisor_recs=advisor_recs,
        health_item=health_item,
        sync=queries.last_sync_info(),
    )


# ── Security ──────────────────────────────────────────────────────────────────

@app.route("/security")
@login_required
def security_view():
    try:
        summary           = queries.get_security_summary()
        open_ports        = queries.get_open_nsg_ports()
        high_risk         = queries.get_high_risk_ports()
        score             = queries.calculate_security_score()
        advisor_sec       = queries.get_advisor_recs(category="Security")
        nsg_rules         = queries.get_nsg_rules()
        gateways          = queries.get_app_gateways()
        public_ip_exposure = queries.get_public_ip_exposure()
        sec_trend          = queries.get_security_score_trend(days=30)
        # fall back to approximate history if no real scores yet
        sec_history        = sec_trend if sec_trend else queries.get_security_history(days=14)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        summary, open_ports, high_risk, score, advisor_sec, nsg_rules, gateways = {}, [], [], {}, [], [], []
        sec_history, public_ip_exposure, sec_trend = [], [], []
    return render_template(
        "security.html",
        summary=summary,
        open_ports=open_ports,
        high_risk=high_risk,
        score=score,
        advisor_security=advisor_sec,
        nsg_rules=nsg_rules,
        gateways=gateways,
        sec_history=sec_history,
        sec_trend=sec_trend,
        public_ip_exposure=public_ip_exposure,
        sync=queries.last_sync_info(),
    )


@app.route("/api/security/summary")
@login_required
def api_security_summary():
    return jsonify(queries.get_security_summary())


# ── WAF Center ────────────────────────────────────────────────────────────────

@app.route("/waf")
@login_required
def waf_view():
    try:
        gateways = queries.get_app_gateways()
        waf_rules = queries.get_waf_rules()
        summary = queries.get_waf_summary()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        gateways, waf_rules, summary = [], [], {}
    return render_template(
        "waf.html",
        gateways=gateways,
        waf_rules=waf_rules,
        summary=summary,
        sync=queries.last_sync_info(),
    )


@app.route("/waf/<resource_group>/<gw_name>")
@login_required
def appgateway_detail(resource_group, gw_name):
    try:
        gw = queries.get_appgateway_by_name(resource_group, gw_name)
        if not gw:
            flash(f"App Gateway '{gw_name}' not found.", "warning")
            return redirect(url_for("waf_view"))
        waf_rules = queries.get_waf_rules(gw_id=gw["gw_id"])
        advisor_recs = queries.get_advisor_for_resource(gw["gw_id"])
        health_item = queries.get_health_for_resource(gw["gw_id"])
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("waf_view"))
    return render_template(
        "appgateway_detail.html",
        gw=gw,
        waf_rules=waf_rules,
        advisor_recs=advisor_recs,
        health_item=health_item,
        sync=queries.last_sync_info(),
    )


# ── Generic resource viewer (Storage, Key Vault, etc.) ───────────────────────

@app.route("/resources")
@login_required
def resources_view():
    rtype = request.args.get("type")
    rg = request.args.get("rg")
    q = request.args.get("q")
    try:
        items = queries.get_generic_resources(type_filter=rtype, rg_filter=rg, search_q=q)
        type_counts = queries.get_generic_resource_type_counts()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        items, type_counts = [], {}
    return render_template(
        "resources.html",
        items=items,
        type_counts=type_counts,
        selected_type=rtype,
        selected_rg=rg,
        selected_q=q,
        resource_groups=queries.distinct_resource_groups(),
        sync=queries.last_sync_info(),
    )


@app.route("/resources/<resource_group>/<path:resource_id_path>")
@login_required
def resource_detail(resource_group, resource_id_path):
    try:
        resource = queries.get_generic_resource_by_path(resource_group, resource_id_path)
        if not resource:
            flash("Resource not found in cache.", "warning")
            return redirect(url_for("resources_view"))
        advisor_recs = queries.get_advisor_for_resource(resource.get("resource_id", ""))
        health_item = queries.get_health_for_resource(resource.get("resource_id", ""))
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("resources_view"))
    return render_template(
        "resource_detail.html",
        resource=resource,
        advisor_recs=advisor_recs,
        health_item=health_item,
        sync=queries.last_sync_info(),
    )


# ── Activity Center ───────────────────────────────────────────────────────────

@app.route("/activity")
@login_required
def activity_view():
    rg = request.args.get("rg")
    caller = request.args.get("caller")
    status = request.args.get("status")
    op_filter = request.args.get("op")
    limit = int(request.args.get("limit", 500))
    try:
        events = queries.get_activity_log(
            resource_group=rg, caller=caller, status=status,
            op_prefix=op_filter, limit=limit,
        )
        summary = queries.activity_summary()
        op_breakdown = queries.get_activity_op_breakdown()
        daily_trend = queries.get_activity_daily_trend(7)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        events, summary, op_breakdown, daily_trend = [], {}, [], []
    return render_template(
        "activity.html",
        events=events,
        summary=summary,
        op_breakdown=op_breakdown,
        daily_trend=daily_trend,
        selected_rg=rg,
        selected_caller=caller,
        selected_status=status,
        selected_op=op_filter,
        resource_groups=queries.distinct_resource_groups(),
        sync=queries.last_sync_info(),
    )


@app.route("/api/activity/summary")
@login_required
def api_activity_summary():
    return jsonify(queries.activity_summary())


# ── Topology ──────────────────────────────────────────────────────────────────

@app.route("/topology")
@login_required
def topology_view():
    return render_template("topology.html", sync=queries.last_sync_info())


@app.route("/api/topology")
@login_required
def api_topology():
    try:
        rg = request.args.get("rg")
        view = request.args.get("view", "all")
        graph = queries.get_topology_graph(view=view)
        # Optional resource group filter — keep nodes in RG + their direct neighbors
        if rg:
            keep_ids = set()
            for n in graph["nodes"]:
                if (n["data"].get("rg") or "").lower() == rg.lower():
                    keep_ids.add(n["data"]["id"])
            # Add one hop of neighbors
            for e in graph["edges"]:
                if e["data"]["source"] in keep_ids or e["data"]["target"] in keep_ids:
                    keep_ids.add(e["data"]["source"])
                    keep_ids.add(e["data"]["target"])
            graph["nodes"] = [n for n in graph["nodes"] if n["data"]["id"] in keep_ids]
            graph["edges"] = [e for e in graph["edges"]
                              if e["data"]["source"] in keep_ids and e["data"]["target"] in keep_ids]
            graph["node_count"] = len(graph["nodes"])
            graph["edge_count"] = len(graph["edges"])
        return jsonify(graph)
    except Exception as exc:
        return jsonify({"error": str(exc), "nodes": [], "edges": []}), 200


# ── NSGs ──────────────────────────────────────────────────────────────────────

@app.route("/nsgs")
@login_required
def nsgs_view():
    rg_filter = request.args.get("rg")
    try:
        nsgs = queries.get_nsgs_with_rule_counts()
        if rg_filter:
            nsgs = [n for n in nsgs if n["resource_group"] == rg_filter]
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        nsgs = []
    return render_template(
        "nsgs.html",
        nsgs=nsgs,
        rg_filter=rg_filter,
        resource_groups=queries.distinct_resource_groups(),
        sync=queries.last_sync_info(),
    )


@app.route("/nsgs/<resource_group>/<nsg_name>")
@login_required
def nsg_detail(resource_group, nsg_name):
    try:
        nsg   = queries.get_nsg_by_name(resource_group, nsg_name)
        if not nsg:
            flash(f"NSG '{nsg_name}' not found.", "warning")
            return redirect(url_for("nsgs_view"))
        rules = queries.get_nsg_rules(nsg_id=nsg["nsg_id"])
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        return redirect(url_for("nsgs_view"))
    return render_template(
        "nsg_detail.html",
        nsg=nsg,
        rules=rules,
        sync=queries.last_sync_info(),
    )


# ── CMDB ──────────────────────────────────────────────────────────────────────

@app.route("/cmdb")
@login_required
def cmdb_view():
    type_filter = request.args.get("type")
    rg_filter   = request.args.get("rg")
    search_q    = request.args.get("q", "").strip()
    try:
        resources  = queries.get_cmdb_resources(type_filter, rg_filter, search_q or None)
        rg_list    = queries.distinct_resource_groups()
        type_list  = queries.get_cmdb_resource_types()
        counts     = queries.get_resource_counts_by_type()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        resources, rg_list, type_list, counts = [], [], [], {}
    return render_template(
        "cmdb.html",
        resources=resources,
        rg_list=rg_list,
        type_list=type_list,
        counts=counts,
        type_filter=type_filter,
        rg_filter=rg_filter,
        search_q=search_q,
        sync=queries.last_sync_info(),
    )


# ── Change Tracking ───────────────────────────────────────────────────────────

@app.route("/changes")
@login_required
def changes_view():
    try:
        snapshots = queries.get_resource_snapshots()
        diff      = queries.get_snapshot_diff()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        snapshots, diff = [], {"has_diff": False, "changes": []}
    return render_template(
        "changes.html",
        snapshots=snapshots,
        diff=diff,
        sync=queries.last_sync_info(),
    )


# ── Global search ─────────────────────────────────────────────────────────────

@app.route("/api/search")
@login_required
def api_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    results = queries.search_resources(q)
    return jsonify({"results": results})


# ── Health check ─────────────────────────────────────────────────────────────

@app.route("/healthz")
def healthz():
    """Lightweight health check — no auth required."""
    from models.db import get_db as _get_db
    try:
        conn = _get_db()
        row = conn.execute("SELECT synced_at, status FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return jsonify({
            "status": "ok",
            "db": "ok",
            "last_sync": row["synced_at"] if row else None,
            "sync_status": row["status"] if row else "never",
        })
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 500


# ── DevOps Center (Phase 12) ─────────────────────────────────────────────────

@app.route("/devops")
@login_required
def devops_view():
    project_id = request.args.get("project")
    try:
        projects  = queries.get_devops_projects()
        summary   = queries.get_devops_summary()
        pipelines = queries.get_devops_pipelines(project_id=project_id)
        builds    = queries.get_devops_builds(project_id=project_id, limit=50)
        repos     = queries.get_devops_repos(project_id=project_id)
        trend     = queries.get_devops_build_trend(days=14)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        projects, summary, pipelines, builds, repos, trend = [], {}, [], [], [], []
    # selected project name for display
    selected_project = next((p for p in projects if p["project_id"] == project_id), None)
    return render_template(
        "devops.html",
        projects=projects,
        summary=summary,
        pipelines=pipelines,
        builds=builds,
        repos=repos,
        trend=trend,
        selected_project=selected_project,
        selected_project_id=project_id,
        devops_enabled=bool(config.AZURE_DEVOPS_ORG),
        devops_org=config.AZURE_DEVOPS_ORG,
        sync=queries.last_sync_info(),
    )


# ── Reservations (Phase 11) ──────────────────────────────────────────────────

@app.route("/reservations")
@login_required
def reservations_view():
    state = request.args.get("state")
    try:
        items = queries.get_reservations(state_filter=state)
        summary = queries.get_reservation_summary()
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        items, summary = [], {}
    return render_template(
        "reservations.html",
        items=items,
        summary=summary,
        selected_state=state,
    )


# ── Reporting (Phase 16) ─────────────────────────────────────────────────────

@app.route("/report")
@login_required
def report_view():
    try:
        data = queries.get_report_data()
        score = data.get("security_score", {})
        cost_by_rg = data.get("cost_by_rg", [])
        mtd = data.get("mtd_cost", 0)
    except Exception as exc:
        flash(_rbac_error(exc), "danger")
        data, score, cost_by_rg, mtd = {}, {}, [], 0
    return render_template(
        "report.html",
        data=data,
        score=score,
        cost_by_rg=cost_by_rg,
        mtd=mtd,
        budget=config.MONTHLY_BUDGET,
        sync=queries.last_sync_info(),
    )


@app.route("/api/report/json")
@login_required
def api_report_json():
    """Full report data as JSON for programmatic consumption."""
    data = queries.get_report_data()
    resp = jsonify(data)
    resp.headers["Content-Disposition"] = "attachment; filename=wts_azure_report.json"
    return resp


@app.route("/api/report/csv/<section>")
@login_required
def api_report_csv(section):
    """Download a specific section of the report as CSV."""
    import csv
    import io
    data = queries.get_report_data()
    section_map = {
        "vms": ("vms", ["name", "resource_group", "location", "vm_size", "os_type", "power_state", "tags"]),
        "open_ports": ("open_ports", ["nsg_name", "resource_group", "rule_name", "dest_port", "source_prefix", "priority"]),
        "advisor": ("advisor_high", ["category", "impact", "short_description", "solution", "resource_id"]),
        "cost": ("cost_by_rg", ["resource_group", "total"]),
        "postgresql": ("pg_servers", ["name", "resource_group", "location", "version", "state", "sku_name", "storage_gb"]),
        "sql": ("sql_servers", ["name", "resource_group", "location", "state", "fqdn"]),
        "backup": ("backup_issues", ["vm_name", "vault_name", "last_backup_status", "last_backup_time", "resource_group"]),
    }
    if section not in section_map:
        return jsonify({"error": "unknown section"}), 400
    key, fields = section_map[section]
    rows = data.get(key, [])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    output = buf.getvalue()
    resp = app.response_class(output, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=wts_{section}_{data.get('generated_at','')[:10]}.csv"
    return resp


# ── Sync Now ──────────────────────────────────────────────────────────────────

@app.route("/sync", methods=["POST"])
@login_required
def trigger_sync():
    global _last_sync_time
    if not _check_csrf():
        flash("CSRF check failed — please try again.", "danger")
        return redirect(request.referrer or url_for("dashboard"))

    min_interval_secs = config.MIN_SYNC_INTERVAL_MINUTES * 60
    with _sync_lock:
        elapsed = time.time() - _last_sync_time
        if elapsed < min_interval_secs:
            wait = int((min_interval_secs - elapsed) / 60) + 1
            flash(f"Sync throttled — please wait {wait} min before triggering again.", "warning")
            return redirect(request.referrer or url_for("dashboard"))
        _last_sync_time = time.time()

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
