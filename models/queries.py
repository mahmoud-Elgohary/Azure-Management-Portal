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
    month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
    conn = get_db()
    row = conn.execute(
        "SELECT SUM(cost) AS total FROM cost_daily WHERE date >= ?", (month_start,)
    ).fetchone()
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
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM backup_status WHERE last_backup_status != 'Completed' OR last_backup_time < ? OR last_backup_time IS NULL",
            (cutoff,),
        ).fetchone()
        return row["cnt"]
    finally:
        conn.close()


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

def get_topology_graph(view: str = "all") -> dict:
    """
    Returns {nodes: [...], edges: [...]} for Cytoscape.js.

    ARM IDs from the compute SDK use mixed-case resource group names while
    Resource Graph normalises everything to lowercase.  All IDs are coerced
    to lowercase here so edges always resolve correctly.
    """
    conn = get_db()
    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    def _table_exists(name: str) -> bool:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _add_node(data: dict):
        nid = (data.get("id") or "").lower()
        if not nid:
            return
        data["id"] = nid
        node_ids.add(nid)
        nodes.append({"data": data})

    def _add_edge(edge_id: str, source: str, target: str, etype: str, label: str, **extra):
        src = (source or "").lower()
        tgt = (target or "").lower()
        if not src or not tgt:
            return
        # defer validation until all nodes are added
        edges.append({"data": {"id": edge_id, "source": src, "target": tgt,
                                "type": etype, "label": label, **extra}})

    # ── VMs ──────────────────────────────────────────────────────────────────
    for vm in conn.execute(
        "SELECT vm_id, name, resource_group, location, power_state, vm_size FROM vms"
    ).fetchall():
        _add_node({
            "id": vm["vm_id"],
            "label": vm["name"],
            "type": "vm",
            "rg": vm["resource_group"],
            "location": vm["location"],
            "power_state": vm["power_state"] or "unknown",
            "vm_size": vm["vm_size"],
            "url": f"/vms/{vm['resource_group']}/{vm['name']}",
        })

    # ── SQL servers ───────────────────────────────────────────────────────────
    for srv in conn.execute(
        "SELECT server_id, name, resource_group, location FROM sql_servers"
    ).fetchall():
        _add_node({
            "id": srv["server_id"],
            "label": srv["name"],
            "type": "sql",
            "rg": srv["resource_group"],
            "url": f"/sql/{srv['resource_group']}/{srv['name']}",
        })

    # ── PostgreSQL servers ────────────────────────────────────────────────────
    if _table_exists("postgresql_servers"):
        try:
            for pg in conn.execute(
                "SELECT server_id, name, resource_group, location, state FROM postgresql_servers"
            ).fetchall():
                _add_node({
                    "id": pg["server_id"],
                    "label": pg["name"],
                    "type": "postgresql",
                    "rg": pg["resource_group"],
                    "state": pg["state"],
                    "url": f"/postgresql/{pg['resource_group']}/{pg['name']}",
                })
        except Exception:
            pass

    # ── App Gateways ──────────────────────────────────────────────────────────
    if _table_exists("app_gateways"):
        try:
            for gw in conn.execute(
                "SELECT gw_id, name, resource_group, waf_enabled, waf_mode FROM app_gateways"
            ).fetchall():
                _add_node({
                    "id": gw["gw_id"],
                    "label": gw["name"],
                    "type": "appgateway",
                    "rg": gw["resource_group"],
                    "waf_enabled": bool(gw["waf_enabled"]),
                })
        except Exception:
            pass

    # ── VNets ─────────────────────────────────────────────────────────────────
    if _table_exists("vnets"):
        for vn in conn.execute(
            "SELECT vnet_id, name, resource_group, address_space FROM vnets"
        ).fetchall():
            _add_node({
                "id": vn["vnet_id"],
                "label": vn["name"],
                "type": "vnet",
                "rg": vn["resource_group"],
                "address_space": vn["address_space"],
            })

    # ── Subnets ───────────────────────────────────────────────────────────────
    if _table_exists("subnets"):
        for sn in conn.execute(
            "SELECT subnet_id, name, vnet_id, address_prefix FROM subnets"
        ).fetchall():
            _add_node({
                "id": sn["subnet_id"],
                "label": sn["name"],
                "type": "subnet",
                "parent": (sn["vnet_id"] or "").lower(),
                "address_prefix": sn["address_prefix"],
            })

    # ── NICs ──────────────────────────────────────────────────────────────────
    if _table_exists("nics"):
        for nic in conn.execute(
            "SELECT nic_id, name, vm_id, subnet_id, private_ip FROM nics"
        ).fetchall():
            _add_node({
                "id": nic["nic_id"],
                "label": nic["name"],
                "type": "nic",
                "private_ip": nic["private_ip"],
            })
            if nic["vm_id"]:
                _add_edge(f"e-nic-vm-{nic['nic_id'][:40]}", nic["nic_id"], nic["vm_id"], "api", "attached to")
            if nic["subnet_id"]:
                _add_edge(f"e-nic-sn-{nic['nic_id'][:40]}", nic["nic_id"], nic["subnet_id"], "api", "in subnet")

    # ── Public IPs ────────────────────────────────────────────────────────────
    if _table_exists("public_ips"):
        for pip in conn.execute(
            "SELECT pip_id, name, ip_address, nic_id FROM public_ips"
        ).fetchall():
            _add_node({
                "id": pip["pip_id"],
                "label": pip["ip_address"] or pip["name"],
                "type": "pip",
                "ip": pip["ip_address"],
            })
            if pip["nic_id"]:
                _add_edge(f"e-pip-{pip['pip_id'][:40]}", pip["pip_id"], pip["nic_id"], "api", "public IP")

    # ── NSGs ──────────────────────────────────────────────────────────────────
    if _table_exists("nsgs"):
        for nsg in conn.execute(
            "SELECT nsg_id, name, resource_group FROM nsgs"
        ).fetchall():
            _add_node({
                "id": nsg["nsg_id"],
                "label": nsg["name"],
                "type": "nsg",
                "rg": nsg["resource_group"],
            })
        if _table_exists("subnets"):
            for sn in conn.execute(
                "SELECT subnet_id, nsg_id FROM subnets WHERE nsg_id IS NOT NULL AND nsg_id != ''"
            ).fetchall():
                _add_edge(f"e-nsg-sn-{sn['subnet_id'][:40]}", sn["nsg_id"], sn["subnet_id"], "api", "protects")

    # ── VNet peerings ─────────────────────────────────────────────────────────
    if _table_exists("vnet_peerings"):
        for p in conn.execute(
            "SELECT peering_id, src_vnet_id, dst_vnet_id, state FROM vnet_peerings"
        ).fetchall():
            _add_edge(
                (p["peering_id"] or "")[:60], p["src_vnet_id"], p["dst_vnet_id"],
                "api", "peering", peering_state=p["state"],
            )

    conn.close()

    # Load logical (config-derived) edges from topology_config.json
    import json as _json
    import os as _os
    cfg_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "topology_config.json")
    try:
        with open(cfg_path) as f:
            topo_cfg = _json.load(f)
        for n in topo_cfg.get("nodes", []):
            _add_node({**n, "logical": True})
        for e in topo_cfg.get("edges", []):
            src = (e.get("source") or "").lower()
            tgt = (e.get("target") or "").lower()
            if src and tgt:
                edges.append({"data": {**e, "source": src, "target": tgt, "type": "logical"}})
    except (FileNotFoundError, _json.JSONDecodeError):
        pass

    # Drop any edge whose source or target doesn't exist in our node set.
    # This prevents Cytoscape from throwing "Node/Edge mismatch" errors.
    valid_edges = [
        e for e in edges
        if e["data"]["source"] in node_ids and e["data"]["target"] in node_ids
    ]

    return {"nodes": nodes, "edges": valid_edges, "node_count": len(nodes), "edge_count": len(valid_edges)}


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


# ── Cost trend / charts ───────────────────────────────────────────────────────

def get_cost_trend_by_date() -> list[dict]:
    """Daily total cost across all resource groups — for trend charts."""
    conn = get_db()
    rows = conn.execute(
        "SELECT date, SUM(cost) AS total, currency FROM cost_daily "
        "GROUP BY date ORDER BY date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cost_by_resource_group_mtd() -> list[dict]:
    """MTD cost per resource group, current month only."""
    month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
    conn = get_db()
    rows = conn.execute(
        "SELECT resource_group, SUM(cost) AS total, currency "
        "FROM cost_daily WHERE date >= ? "
        "GROUP BY resource_group ORDER BY total DESC",
        (month_start,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_time_total() -> float:
    """Total cost across all synced history (for reference)."""
    conn = get_db()
    row = conn.execute("SELECT SUM(cost) AS total FROM cost_daily").fetchone()
    conn.close()
    return round(row["total"] or 0, 2)


# ── NSG rules ─────────────────────────────────────────────────────────────────

def get_nsg_rules(nsg_id: str = None, direction: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM nsg_rules WHERE 1=1"
    params = []
    if nsg_id:
        sql += " AND nsg_id = ?"
        params.append(nsg_id)
    if direction:
        sql += " AND direction = ?"
        params.append(direction)
    sql += " ORDER BY nsg_name, direction, priority"
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


def get_open_nsg_ports() -> list[dict]:
    """Return NSG rules that allow inbound from Any/Internet on non-standard ports."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM nsg_rules
               WHERE direction='Inbound' AND access='Allow'
                 AND (source_prefix IN ('*','Any','Internet','0.0.0.0/0'))
               ORDER BY priority""",
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def get_postgresql_servers(resource_group: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM postgresql_servers WHERE 1=1"
    params = []
    if resource_group:
        sql += " AND resource_group = ?"
        params.append(resource_group)
    sql += " ORDER BY name"
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


# ── KQL history ───────────────────────────────────────────────────────────────

def save_kql_history(query: str, workspace_id: str, row_count: int, elapsed_ms: int, had_error: bool):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO kql_history (query, workspace_id, executed_at, row_count, elapsed_ms, had_error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (query, workspace_id, datetime.now(timezone.utc).isoformat(), row_count, elapsed_ms, 1 if had_error else 0),
        )
        conn.execute("DELETE FROM kql_history WHERE id NOT IN (SELECT id FROM kql_history ORDER BY id DESC LIMIT 100)")
        conn.commit()
    finally:
        conn.close()


def get_kql_history(limit: int = 20) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, query, workspace_id, executed_at, row_count, elapsed_ms, had_error "
            "FROM kql_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Security summary ──────────────────────────────────────────────────────────

def get_security_summary() -> dict:
    """Quick-read security posture numbers for the security dashboard."""
    open_ports = get_open_nsg_ports()
    public_ips = []
    conn = get_db()
    try:
        pip_rows = conn.execute("SELECT COUNT(*) AS cnt FROM public_ips WHERE ip_address IS NOT NULL").fetchone()
        pip_count = pip_rows["cnt"] if pip_rows else 0
        advisor_sec = conn.execute(
            "SELECT COUNT(*) AS cnt FROM advisor_recs WHERE category='Security'"
        ).fetchone()
        sec_recs = advisor_sec["cnt"] if advisor_sec else 0
        unavailable = conn.execute(
            "SELECT COUNT(*) AS cnt FROM resource_health WHERE availability_state='Unavailable'"
        ).fetchone()
        unavail_count = unavailable["cnt"] if unavailable else 0
    finally:
        conn.close()
    return {
        "open_inbound_rules": len(open_ports),
        "public_ip_count": pip_count,
        "security_advisor_recs": sec_recs,
        "unavailable_resources": unavail_count,
    }


# ── PostgreSQL detail ─────────────────────────────────────────────────────────

def get_postgresql_server_by_name(resource_group: str, name: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM postgresql_servers WHERE LOWER(resource_group)=LOWER(?) AND LOWER(name)=LOWER(?)",
            (resource_group, name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── App Gateways / WAF ────────────────────────────────────────────────────────

def get_app_gateways(resource_group: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM app_gateways WHERE 1=1"
    params = []
    if resource_group:
        sql += " AND resource_group = ?"
        params.append(resource_group)
    sql += " ORDER BY name"
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


def get_waf_rules(gw_id: str = None) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM waf_rules WHERE 1=1"
    params = []
    if gw_id:
        sql += " AND gw_id = ?"
        params.append(gw_id)
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


def get_waf_summary() -> dict:
    conn = get_db()
    try:
        gw_row = conn.execute("SELECT COUNT(*) AS cnt, SUM(waf_enabled) AS waf_on FROM app_gateways").fetchone()
        return {
            "total_gateways": gw_row["cnt"] if gw_row else 0,
            "waf_enabled": gw_row["waf_on"] if gw_row else 0,
        }
    except Exception:
        return {"total_gateways": 0, "waf_enabled": 0}
    finally:
        conn.close()


# ── Activity Log ──────────────────────────────────────────────────────────────

def get_activity_log(
    resource_group: str = None,
    caller: str = None,
    status: str = None,
    limit: int = 200,
) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM activity_log WHERE 1=1"
    params: list = []
    if resource_group:
        sql += " AND resource_group = ?"
        params.append(resource_group)
    if caller:
        sql += " AND caller LIKE ?"
        params.append(f"%{caller}%")
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY event_timestamp DESC LIMIT ?"
    params.append(limit)
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


def activity_summary() -> dict:
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM activity_log").fetchone()
        failed = conn.execute(
            "SELECT COUNT(*) AS cnt FROM activity_log WHERE status IN ('Failed','Failure')"
        ).fetchone()
        recent_24h = conn.execute(
            "SELECT COUNT(*) AS cnt FROM activity_log WHERE event_timestamp >= datetime('now', '-1 day')"
        ).fetchone()
        callers = conn.execute(
            "SELECT caller, COUNT(*) AS cnt FROM activity_log GROUP BY caller ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        return {
            "total": total["cnt"] if total else 0,
            "failed": failed["cnt"] if failed else 0,
            "recent_24h": recent_24h["cnt"] if recent_24h else 0,
            "top_callers": [dict(r) for r in callers],
        }
    except Exception:
        return {"total": 0, "failed": 0, "recent_24h": 0, "top_callers": []}
    finally:
        conn.close()


# ── Dashboard statistics ───────────────────────────────────────────────────────

def dashboard_stats() -> dict:
    """Single-query stats for the redesigned dashboard."""
    conn = get_db()
    try:
        vm_total = conn.execute("SELECT COUNT(*) AS cnt FROM vms").fetchone()["cnt"]
        vm_running = conn.execute(
            "SELECT COUNT(*) AS cnt FROM vms WHERE power_state='running'"
        ).fetchone()["cnt"]
        sql_db_total = conn.execute("SELECT COUNT(*) AS cnt FROM sql_databases").fetchone()["cnt"]
        pg_total = conn.execute("SELECT COUNT(*) AS cnt FROM postgresql_servers").fetchone()["cnt"]
        open_ports_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM nsg_rules WHERE direction='Inbound' AND access='Allow' "
            "AND source_prefix IN ('*','Any','Internet','0.0.0.0/0')"
        ).fetchone()["cnt"]
        advisor_high = conn.execute(
            "SELECT COUNT(*) AS cnt FROM advisor_recs WHERE impact='High'"
        ).fetchone()["cnt"]
        backup_ok = conn.execute(
            "SELECT COUNT(*) AS cnt FROM backup_status WHERE last_backup_status='Completed'"
        ).fetchone()["cnt"]
        backup_total = conn.execute("SELECT COUNT(*) AS cnt FROM backup_status").fetchone()["cnt"]
        unavailable = conn.execute(
            "SELECT COUNT(*) AS cnt FROM resource_health WHERE availability_state='Unavailable'"
        ).fetchone()["cnt"]
        pip_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM public_ips WHERE ip_address IS NOT NULL"
        ).fetchone()["cnt"]
        failed_activity = conn.execute(
            "SELECT COUNT(*) AS cnt FROM activity_log WHERE status IN ('Failed','Failure') "
            "AND event_timestamp >= datetime('now', '-1 day')"
        ).fetchone()["cnt"]
        waf_row = conn.execute(
            "SELECT COUNT(*) AS cnt, SUM(waf_enabled) AS waf_on FROM app_gateways"
        ).fetchone()
        return {
            "vm_total": vm_total,
            "vm_running": vm_running,
            "sql_db_total": sql_db_total,
            "pg_total": pg_total,
            "open_ports": open_ports_count,
            "advisor_high": advisor_high,
            "backup_ok": backup_ok,
            "backup_total": backup_total,
            "unavailable": unavailable,
            "public_ips": pip_count,
            "failed_activity_24h": failed_activity,
            "waf_gateways": waf_row["cnt"] if waf_row else 0,
            "waf_enabled": int(waf_row["waf_on"] or 0) if waf_row else 0,
        }
    except Exception:
        return {}
    finally:
        conn.close()


def get_top_cost_resource_groups(limit: int = 8) -> list[dict]:
    """Top resource groups by all-time cost, for dashboard table."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT resource_group, SUM(cost) AS total, currency "
            "FROM cost_daily GROUP BY resource_group ORDER BY total DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_recent_activity(limit: int = 10) -> list[dict]:
    """Most recent activity log entries for dashboard."""
    return get_activity_log(limit=limit)


def get_resource_counts_by_type() -> dict:
    """High-level resource counts by major type for CMDB/dashboard."""
    conn = get_db()
    try:
        counts = {}
        counts["vms"] = conn.execute("SELECT COUNT(*) AS cnt FROM vms").fetchone()["cnt"]
        counts["sql_servers"] = conn.execute("SELECT COUNT(*) AS cnt FROM sql_servers").fetchone()["cnt"]
        counts["sql_databases"] = conn.execute("SELECT COUNT(*) AS cnt FROM sql_databases").fetchone()["cnt"]
        counts["elastic_pools"] = conn.execute("SELECT COUNT(*) AS cnt FROM elastic_pools").fetchone()["cnt"]
        counts["postgresql"] = conn.execute("SELECT COUNT(*) AS cnt FROM postgresql_servers").fetchone()["cnt"]
        counts["vnets"] = conn.execute("SELECT COUNT(*) AS cnt FROM vnets").fetchone()["cnt"]
        counts["subnets"] = conn.execute("SELECT COUNT(*) AS cnt FROM subnets").fetchone()["cnt"]
        counts["nsgs"] = conn.execute("SELECT COUNT(*) AS cnt FROM nsgs").fetchone()["cnt"]
        counts["public_ips"] = conn.execute(
            "SELECT COUNT(*) AS cnt FROM public_ips WHERE ip_address IS NOT NULL"
        ).fetchone()["cnt"]
        counts["app_gateways"] = conn.execute("SELECT COUNT(*) AS cnt FROM app_gateways").fetchone()["cnt"]
        return counts
    except Exception:
        return {}
    finally:
        conn.close()
