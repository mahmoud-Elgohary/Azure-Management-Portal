"""
Read-only data-access layer used by Flask routes.
All DB reads go through here; routes never call the Azure SDK.
"""

from datetime import datetime, timezone
from models.db import get_db
import config


# ── Sync metadata ─────────────────────────────────────────────────────────────

def last_sync_info() -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT synced_at, status FROM sync_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"synced_at": None, "status": "never", "age_minutes": None}
    synced_at = datetime.fromisoformat(row["synced_at"]).replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - synced_at).total_seconds() / 60
    return {"synced_at": row["synced_at"], "status": row["status"], "age_minutes": round(age, 1)}


# ── VMs ───────────────────────────────────────────────────────────────────────

def get_vms(resource_group: str = None, tag_filter: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM vms WHERE 1=1"
    params = []
    if resource_group:
        sql += " AND resource_group = ?"
        params.append(resource_group)
    if tag_filter:
        sql += " AND tags LIKE ?"
        params.append(f"%{tag_filter}%")
    sql += " ORDER BY name"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def vm_power_summary() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT power_state, COUNT(*) AS cnt FROM vms GROUP BY power_state"
    ).fetchall()
    conn.close()
    return {r["power_state"]: r["cnt"] for r in rows}


def get_vm_metrics(vm_id: str, metric: str, hours: int = 24) -> list[dict]:
    conn = get_db()
    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = (cutoff - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT timestamp, value FROM vm_metrics WHERE vm_id=? AND metric=? AND timestamp>=? ORDER BY timestamp",
        (vm_id, metric, cutoff),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def latest_vm_cpu(vm_id: str) -> float | None:
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM vm_metrics WHERE vm_id=? AND metric='Percentage CPU' ORDER BY timestamp DESC LIMIT 1",
        (vm_id,),
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def cpu_status(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    if pct >= config.CPU_RED_PCT:
        return "red"
    if pct >= config.CPU_AMBER_PCT:
        return "amber"
    return "green"


# ── SQL ───────────────────────────────────────────────────────────────────────

def get_sql_servers(resource_group: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM sql_servers WHERE 1=1"
    params = []
    if resource_group:
        sql += " AND resource_group = ?"
        params.append(resource_group)
    sql += " ORDER BY name"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_elastic_pools(server_name: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT ep.*, COUNT(d.db_id) AS db_count FROM elastic_pools ep LEFT JOIN sql_databases d ON d.elastic_pool_id LIKE '%' || ep.name || '%' "
    params = []
    if server_name:
        sql += "WHERE ep.server_name = ? "
        params.append(server_name)
    sql += "GROUP BY ep.pool_id ORDER BY ep.name"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_databases(server_name: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM sql_databases WHERE 1=1"
    params = []
    if server_name:
        sql += " AND server_name = ?"
        params.append(server_name)
    sql += " ORDER BY name"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


# ── Cost ──────────────────────────────────────────────────────────────────────

def get_cost_daily(subscription_id: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM cost_daily WHERE 1=1"
    params = []
    if subscription_id:
        sql += " AND subscription_id = ?"
        params.append(subscription_id)
    sql += " ORDER BY date"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_mtd_total() -> float:
    conn = get_db()
    row = conn.execute("SELECT SUM(cost) AS total FROM cost_daily").fetchone()
    conn.close()
    return round(row["total"] or 0, 2)


# ── Advisor ───────────────────────────────────────────────────────────────────

def get_advisor_recs(category: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM advisor_recs WHERE 1=1"
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY category, impact"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def advisor_category_summary() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT category, COUNT(*) AS cnt FROM advisor_recs GROUP BY category"
    ).fetchall()
    conn.close()
    return {r["category"]: r["cnt"] for r in rows}


# ── Backup ────────────────────────────────────────────────────────────────────

def get_backup_status() -> list[dict]:
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM backup_status ORDER BY vm_name").fetchall()]
    conn.close()
    return rows


def backup_problem_count() -> int:
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=config.BACKUP_STALE_HOURS)).isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM backup_status WHERE last_backup_status != 'Completed' OR last_backup_time < ? OR last_backup_time IS NULL",
        (cutoff,),
    ).fetchone()
    conn.close()
    return row["cnt"]


# ── Resource Health ───────────────────────────────────────────────────────────

def get_resource_health(state: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM resource_health WHERE 1=1"
    params = []
    if state:
        sql += " AND availability_state = ?"
        params.append(state)
    sql += " ORDER BY availability_state, resource_id"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def health_summary() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT availability_state, COUNT(*) AS cnt FROM resource_health GROUP BY availability_state"
    ).fetchall()
    conn.close()
    return {r["availability_state"]: r["cnt"] for r in rows}


# ── Shared helpers ────────────────────────────────────────────────────────────

def distinct_resource_groups() -> list[str]:
    conn = get_db()
    rgs = set()
    for tbl in ("vms", "sql_servers", "elastic_pools"):
        rows = conn.execute(f"SELECT DISTINCT resource_group FROM {tbl}").fetchall()
        rgs.update(r["resource_group"] for r in rows if r["resource_group"])
    conn.close()
    return sorted(rgs)
