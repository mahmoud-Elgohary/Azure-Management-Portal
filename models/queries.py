"""
Read-only data-access layer used by Flask routes.
All DB reads go through here; routes never call the Azure SDK.
"""

from datetime import datetime, timedelta, timezone
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


def get_vm_by_name(resource_group: str, name: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM vms WHERE LOWER(resource_group)=LOWER(?) AND LOWER(name)=LOWER(?)",
        (resource_group, name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_vm_metrics(vm_id: str, metric: str, hours: int = 24) -> list[dict]:
    conn = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT timestamp, value FROM vm_metrics WHERE vm_id=? AND metric=? AND timestamp>=? ORDER BY timestamp",
        (vm_id, metric, cutoff),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_available_metrics(vm_id: str) -> list[str]:
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT metric FROM vm_metrics WHERE vm_id=? ORDER BY metric",
        (vm_id,),
    ).fetchall()
    conn.close()
    return [r["metric"] for r in rows]


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


def get_sql_server_by_name(resource_group: str, name: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sql_servers WHERE LOWER(resource_group)=LOWER(?) AND LOWER(name)=LOWER(?)",
        (resource_group, name),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


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


def get_cost_by_resource_group() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT resource_group, SUM(cost) AS total FROM cost_daily GROUP BY resource_group ORDER BY total DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def get_advisor_for_resource(resource_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM advisor_recs WHERE resource_id LIKE ? ORDER BY impact",
        (f"%{resource_id}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def get_backup_for_vm(vm_name: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM backup_status WHERE LOWER(vm_name)=LOWER(?)",
        (vm_name,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def backup_problem_count() -> int:
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


def get_health_for_resource(resource_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM resource_health WHERE resource_id LIKE ?",
        (f"%{resource_id}%",),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def health_summary() -> dict:
    conn = get_db()
    rows = conn.execute(
        "SELECT availability_state, COUNT(*) AS cnt FROM resource_health GROUP BY availability_state"
    ).fetchall()
    conn.close()
    return {r["availability_state"]: r["cnt"] for r in rows}


# ── Alerts ────────────────────────────────────────────────────────────────────

def get_alerts(severity: str = None, state: str = None) -> list[dict]:
    conn = get_db()
    tbl_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()
    if not tbl_exists:
        conn.close()
        return []
    sql = "SELECT * FROM alerts WHERE 1=1"
    params = []
    if severity:
        sql += " AND severity = ?"
        params.append(severity)
    if state:
        sql += " AND monitor_condition = ?"
        params.append(state)
    sql += " ORDER BY fired_time DESC LIMIT 500"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def alert_summary() -> dict:
    conn = get_db()
    tbl_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()
    if not tbl_exists:
        conn.close()
        return {}
    rows = conn.execute(
        "SELECT severity, COUNT(*) AS cnt FROM alerts GROUP BY severity"
    ).fetchall()
    conn.close()
    return {r["severity"]: r["cnt"] for r in rows}


# ── Topology graph ────────────────────────────────────────────────────────────

def get_topology_graph() -> dict:
    """
    Returns {nodes: [...], edges: [...]} for Cytoscape.js rendering.
    Built from whatever network tables exist in the DB (graceful if missing).
    """
    conn = get_db()
    nodes = []
    edges = []

    def _table_exists(name):
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    # VMs
    for vm in conn.execute("SELECT vm_id, name, resource_group, location, power_state, vm_size FROM vms").fetchall():
        nodes.append({
            "data": {
                "id": vm["vm_id"],
                "label": vm["name"],
                "type": "vm",
                "rg": vm["resource_group"],
                "location": vm["location"],
                "power_state": vm["power_state"],
                "vm_size": vm["vm_size"],
                "url": f"/vms/{vm['resource_group']}/{vm['name']}",
            }
        })

    # SQL servers
    for srv in conn.execute("SELECT server_id, name, resource_group, location FROM sql_servers").fetchall():
        nodes.append({
            "data": {
                "id": srv["server_id"],
                "label": srv["name"],
                "type": "sql",
                "rg": srv["resource_group"],
                "url": f"/sql/{srv['resource_group']}/{srv['name']}",
            }
        })

    # VNets
    if _table_exists("vnets"):
        for vn in conn.execute("SELECT vnet_id, name, resource_group, location, address_space FROM vnets").fetchall():
            nodes.append({
                "data": {
                    "id": vn["vnet_id"],
                    "label": vn["name"],
                    "type": "vnet",
                    "rg": vn["resource_group"],
                    "address_space": vn["address_space"],
                }
            })

    # Subnets (child nodes of VNets)
    if _table_exists("subnets"):
        for sn in conn.execute("SELECT subnet_id, name, vnet_id, address_prefix FROM subnets").fetchall():
            nodes.append({
                "data": {
                    "id": sn["subnet_id"],
                    "label": sn["name"],
                    "type": "subnet",
                    "parent": sn["vnet_id"],
                    "address_prefix": sn["address_prefix"],
                }
            })

    # NICs — edges: NIC→VM and NIC→Subnet
    if _table_exists("nics"):
        for nic in conn.execute("SELECT nic_id, name, vm_id, subnet_id, private_ip, public_ip_id FROM nics").fetchall():
            nodes.append({
                "data": {
                    "id": nic["nic_id"],
                    "label": nic["name"],
                    "type": "nic",
                    "private_ip": nic["private_ip"],
                }
            })
            if nic["vm_id"]:
                edges.append({"data": {"id": f"e-nic-vm-{nic['nic_id']}", "source": nic["nic_id"], "target": nic["vm_id"], "type": "api", "label": "attached to"}})
            if nic["subnet_id"]:
                edges.append({"data": {"id": f"e-nic-sn-{nic['nic_id']}", "source": nic["nic_id"], "target": nic["subnet_id"], "type": "api", "label": "in subnet"}})

    # Public IPs — edges: IP→NIC
    if _table_exists("public_ips"):
        for pip in conn.execute("SELECT pip_id, name, ip_address, nic_id FROM public_ips").fetchall():
            nodes.append({
                "data": {
                    "id": pip["pip_id"],
                    "label": pip["ip_address"] or pip["name"],
                    "type": "pip",
                    "ip": pip["ip_address"],
                }
            })
            if pip["nic_id"]:
                edges.append({"data": {"id": f"e-pip-{pip['pip_id']}", "source": pip["pip_id"], "target": pip["nic_id"], "type": "api", "label": "public IP"}})

    # NSGs — edges: NSG→Subnet
    if _table_exists("nsgs"):
        for nsg in conn.execute("SELECT nsg_id, name, resource_group FROM nsgs").fetchall():
            nodes.append({
                "data": {
                    "id": nsg["nsg_id"],
                    "label": nsg["name"],
                    "type": "nsg",
                    "rg": nsg["resource_group"],
                }
            })

    # VNet peerings
    if _table_exists("vnet_peerings"):
        for p in conn.execute("SELECT peering_id, src_vnet_id, dst_vnet_id, state FROM vnet_peerings").fetchall():
            edges.append({
                "data": {
                    "id": p["peering_id"],
                    "source": p["src_vnet_id"],
                    "target": p["dst_vnet_id"],
                    "type": "api",
                    "label": "peering",
                    "peering_state": p["state"],
                }
            })

    conn.close()

    # Load logical (config-derived) edges from topology_config.json
    import json, os
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "topology_config.json")
    try:
        with open(cfg_path) as f:
            topo_cfg = json.load(f)
        for n in topo_cfg.get("nodes", []):
            nodes.append({"data": {**n, "logical": True}})
        for e in topo_cfg.get("edges", []):
            edges.append({"data": {**e, "type": "logical"}})
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return {"nodes": nodes, "edges": edges}


# ── Global search ─────────────────────────────────────────────────────────────

def search_resources(q: str) -> list[dict]:
    conn = get_db()
    q_like = f"%{q}%"
    results = []

    for row in conn.execute(
        "SELECT name, resource_group, location, power_state FROM vms WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 10",
        (q_like, q_like),
    ).fetchall():
        results.append({
            "name": row["name"],
            "type": "VM",
            "sub": f"{row['resource_group']} · {row['power_state']}",
            "url": f"/vms/{row['resource_group']}/{row['name']}",
        })

    for row in conn.execute(
        "SELECT name, resource_group, state FROM sql_servers WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 5",
        (q_like, q_like),
    ).fetchall():
        results.append({
            "name": row["name"],
            "type": "SQL Server",
            "sub": row["resource_group"],
            "url": f"/sql/{row['resource_group']}/{row['name']}",
        })

    for row in conn.execute(
        "SELECT short_description, category, impact FROM advisor_recs WHERE short_description LIKE ? ORDER BY impact LIMIT 5",
        (q_like,),
    ).fetchall():
        results.append({
            "name": (row["short_description"] or "")[:60],
            "type": f"Advisor ({row['category']})",
            "sub": f"Impact: {row['impact']}",
            "url": f"/advisor?category={row['category']}",
        })

    conn.close()
    return results[:20]


# ── Shared helpers ────────────────────────────────────────────────────────────

def distinct_resource_groups() -> list[str]:
    conn = get_db()
    rgs = set()
    for tbl in ("vms", "sql_servers", "elastic_pools"):
        rows = conn.execute(f"SELECT DISTINCT resource_group FROM {tbl}").fetchall()
        rgs.update(r["resource_group"] for r in rows if r["resource_group"])
    conn.close()
    return sorted(rgs)
