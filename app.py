import os
import sys
import threading

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

# Ensure project root is on path when run directly
sys.path.insert(0, os.path.dirname(__file__))

from werkzeug.middleware.proxy_fix import ProxyFix

import config
from models.db import init_db
from models import queries

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _rbac_error(exc: Exception) -> str:
    msg = str(exc)
    if "AuthorizationFailed" in msg or "does not have authorization" in msg:
        return (
            "Permission denied. The service principal requires 'Reader' and "
            "'Cost Management Reader' roles on the target subscriptions. "
            f"Azure error: {msg}"
        )
    if "AADSTS" in msg:
        return f"Authentication failed. Check AZURE_CLIENT_ID / AZURE_CLIENT_SECRET. Azure error: {msg}"
    return f"Azure error: {msg}"


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
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
        ctx = {}
    return render_template("dashboard.html", **ctx)


# ── VMs ───────────────────────────────────────────────────────────────────────

@app.route("/vms")
def vms():
    rg = request.args.get("rg")
    tag = request.args.get("tag")
    try:
        vm_list = queries.get_vms(resource_group=rg, tag_filter=tag)
        # Attach latest CPU + status light
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


@app.route("/vms/<path:vm_id>/metrics")
def vm_metrics(vm_id):
    metric = request.args.get("metric", "Percentage CPU")
    data = queries.get_vm_metrics(vm_id, metric, hours=24)
    return jsonify(data)


# ── SQL / Elastic Pools ────────────────────────────────────────────────────────

@app.route("/sql")
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


# ── Cost ──────────────────────────────────────────────────────────────────────

@app.route("/cost")
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


# ── Sync Now ──────────────────────────────────────────────────────────────────

@app.route("/sync", methods=["POST"])
def trigger_sync():
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
