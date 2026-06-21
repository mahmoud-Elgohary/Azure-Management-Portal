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
    month_start = datetime.now(timezone.utc).strftime("%Y%m01")
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
        "SELECT name, resource_group, location, power_state FROM vms WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 8",
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
        "SELECT name, resource_group, state FROM postgresql_servers WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 4",
        (q_like, q_like),
    ).fetchall():
        results.append({
            "name": row["name"],
            "type": "PostgreSQL",
            "sub": f"{row['resource_group']} · {row['state'] or '—'}",
            "url": f"/postgresql/{row['resource_group']}/{row['name']}",
        })

    for row in conn.execute(
        "SELECT name, resource_group, location FROM nsgs WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 4",
        (q_like, q_like),
    ).fetchall():
        results.append({
            "name": row["name"],
            "type": "NSG",
            "sub": row["resource_group"],
            "url": f"/nsgs/{row['resource_group']}/{row['name']}",
        })

    for row in conn.execute(
        "SELECT name, resource_group, operational_state FROM app_gateways WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 3",
        (q_like, q_like),
    ).fetchall():
        results.append({
            "name": row["name"],
            "type": "App Gateway",
            "sub": f"{row['resource_group']} · {row['operational_state'] or '—'}",
            "url": f"/waf",
        })

    for row in conn.execute(
        "SELECT name, resource_group, address_space FROM vnets WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 3",
        (q_like, q_like),
    ).fetchall():
        results.append({
            "name": row["name"],
            "type": "VNet",
            "sub": f"{row['resource_group']} · {row['address_space'] or '—'}",
            "url": f"/topology",
        })

    for row in conn.execute(
        "SELECT short_description, category, impact FROM advisor_recs WHERE short_description LIKE ? ORDER BY impact LIMIT 4",
        (q_like,),
    ).fetchall():
        results.append({
            "name": (row["short_description"] or "")[:60],
            "type": f"Advisor ({row['category']})",
            "sub": f"Impact: {row['impact']}",
            "url": f"/advisor?category={row['category']}",
        })

    for row in conn.execute(
        "SELECT name, type, resource_group, location FROM resources WHERE name LIKE ? OR resource_group LIKE ? ORDER BY name LIMIT 8",
        (q_like, q_like),
    ).fetchall():
        type_short = (row["type"] or "").split("/")[-1].replace("-", " ").title()
        results.append({
            "name": row["name"],
            "type": type_short,
            "sub": row["resource_group"],
            "url": f"/resources/{row['resource_group']}/{row['name']}",
        })

    conn.close()
    return results[:30]


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
    month_start = datetime.now(timezone.utc).strftime("%Y%m01")
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


def get_appgateway_by_name(resource_group: str, name: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM app_gateways WHERE LOWER(resource_group)=LOWER(?) AND LOWER(name)=LOWER(?)",
            (resource_group, name),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ── Generic Resources (Storage, Key Vault, VPN, Bastion, etc.) ───────────────

def get_generic_resources(
    type_filter: str = None,
    rg_filter: str = None,
    search_q: str = None,
    limit: int = 1000,
) -> list[dict]:
    conn = get_db()
    sql = "SELECT * FROM resources WHERE 1=1"
    params: list = []
    if type_filter:
        sql += " AND LOWER(type) LIKE LOWER(?)"
        params.append(f"%{type_filter}%")
    if rg_filter:
        sql += " AND LOWER(resource_group) = LOWER(?)"
        params.append(rg_filter)
    if search_q:
        like = f"%{search_q}%"
        sql += " AND (name LIKE ? OR resource_group LIKE ? OR type LIKE ? OR tags LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY type, name LIMIT ?"
    params.append(limit)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def get_generic_resource_type_counts() -> dict:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT type, COUNT(*) AS cnt FROM resources GROUP BY type ORDER BY cnt DESC"
        ).fetchall()
        return {r["type"]: r["cnt"] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def get_generic_resource_by_path(resource_group: str, path: str) -> dict | None:
    """Look up a resource by resource_group + name extracted from URL path."""
    name = path.split("/")[-1]
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM resources WHERE LOWER(resource_group)=LOWER(?) AND LOWER(name)=LOWER(?)",
            (resource_group, name),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            "SELECT * FROM resources WHERE LOWER(resource_id) LIKE LOWER(?)",
            (f"%{path}%",),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ── Activity Log ──────────────────────────────────────────────────────────────

def get_activity_log(
    resource_group: str = None,
    caller: str = None,
    status: str = None,
    op_prefix: str = None,
    limit: int = 500,
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
    if op_prefix:
        sql += " AND (resource_type LIKE ? OR operation_name LIKE ?)"
        params.extend([f"{op_prefix}%", f"{op_prefix}%"])
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


def get_activity_op_breakdown() -> list[dict]:
    """Return operation category counts for the Activity Center chart."""
    conn = get_db()
    _CATEGORIES = [
        ("VM Operations",      "Microsoft.Compute/virtualMachines"),
        ("NSG Changes",        "Microsoft.Network/networkSecurityGroups"),
        ("Role Assignments",   "Microsoft.Authorization/roleAssignments"),
        ("Storage",            "Microsoft.Storage/storageAccounts"),
        ("Key Vault",          "Microsoft.KeyVault/vaults"),
        ("SQL / PostgreSQL",   "Microsoft.Sql"),
        ("App Gateway / WAF",  "Microsoft.Network/applicationGateways"),
        ("Policy",             "Microsoft.Authorization/policyAssignments"),
        ("Network (other)",    "Microsoft.Network"),
        ("Compute (other)",    "Microsoft.Compute"),
    ]
    try:
        result = []
        used_ids: set = set()
        for label, prefix in _CATEGORIES:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM activity_log WHERE resource_type LIKE ? OR operation_name LIKE ?",
                (f"{prefix}%", f"{prefix}%"),
            ).fetchone()
            cnt = row["cnt"] if row else 0
            if cnt > 0:
                result.append({"category": label, "count": cnt})
                used_ids.add(prefix)
        result.sort(key=lambda x: x["count"], reverse=True)
        return result
    except Exception:
        return []
    finally:
        conn.close()


def get_activity_daily_trend(days: int = 7) -> list[dict]:
    """Return per-day event counts (total vs failed) for the last N days."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT substr(event_timestamp, 1, 10) AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('Failed','Failure') THEN 1 ELSE 0 END) AS failed
            FROM activity_log
            WHERE event_timestamp >= datetime('now', ?)
            GROUP BY day
            ORDER BY day
            """,
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
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


# ── VM list with IPs ─────────────────────────────────────────────────────────

def get_vms_with_ips(resource_group: str = None, tag_filter: str = None, power_state: str = None) -> list[dict]:
    """VMs joined with their primary NIC's private + public IP."""
    conn = get_db()
    sql = """
        SELECT v.*,
               n.private_ip,
               p.ip_address AS public_ip
        FROM vms v
        LEFT JOIN nics n ON LOWER(n.vm_id) = LOWER(v.vm_id)
        LEFT JOIN public_ips p ON LOWER(p.nic_id) = LOWER(n.nic_id)
        WHERE 1=1
    """
    params: list = []
    if resource_group:
        sql += " AND v.resource_group = ?"
        params.append(resource_group)
    if tag_filter:
        sql += " AND v.tags LIKE ?"
        params.append(f"%{tag_filter}%")
    if power_state:
        sql += " AND v.power_state = ?"
        params.append(power_state)
    sql += " ORDER BY v.name"
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


# ── NSG list ──────────────────────────────────────────────────────────────────

def get_nsgs_with_rule_counts() -> list[dict]:
    """NSGs with their rule counts and open-inbound count."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT n.nsg_id, n.name, n.resource_group, n.location, n.synced_at,
                   COUNT(r.rule_id) AS rule_count,
                   SUM(CASE WHEN r.direction='Inbound' AND r.access='Allow' THEN 1 ELSE 0 END) AS inbound_allow,
                   SUM(CASE WHEN r.direction='Inbound' AND r.access='Allow'
                            AND r.source_prefix IN ('*','Any','Internet','0.0.0.0/0') THEN 1 ELSE 0 END) AS open_inbound
            FROM nsgs n
            LEFT JOIN nsg_rules r ON LOWER(r.nsg_id) = LOWER(n.nsg_id)
            GROUP BY n.nsg_id
            ORDER BY open_inbound DESC, n.name
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_nsg_by_name(resource_group: str, name: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM nsgs WHERE LOWER(resource_group)=LOWER(?) AND LOWER(name)=LOWER(?)",
            (resource_group, name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Cost currency ─────────────────────────────────────────────────────────────

def get_cost_currency() -> str:
    """Most-used currency string in cost_daily, defaults to EUR."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT currency FROM cost_daily WHERE currency IS NOT NULL "
            "GROUP BY currency ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        return row["currency"] if row else "EUR"
    except Exception:
        return "EUR"
    finally:
        conn.close()


# ── VM networking ─────────────────────────────────────────────────────────────

def get_nics_for_vm(vm_id: str) -> list[dict]:
    """Get NICs attached to a VM with associated public IPs."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT n.nic_id, n.name, n.private_ip, n.subnet_id, n.public_ip_id,
                      p.ip_address AS public_ip, p.name AS pip_name, p.allocation_method
               FROM nics n
               LEFT JOIN public_ips p ON LOWER(p.nic_id) = LOWER(n.nic_id)
               WHERE LOWER(n.vm_id) = LOWER(?)""",
            (vm_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_nsg_rules_for_vm(vm_id: str) -> list[dict]:
    """Get NSG rules that apply to a VM's NICs (via subnet or NIC-level NSG)."""
    conn = get_db()
    try:
        # Get subnet IDs and NSG IDs for this VM's NICs
        nic_rows = conn.execute(
            "SELECT subnet_id FROM nics WHERE LOWER(vm_id) = LOWER(?)", (vm_id,)
        ).fetchall()
        subnet_ids = [r["subnet_id"] for r in nic_rows if r["subnet_id"]]

        rules = []
        if subnet_ids:
            # Get NSG IDs attached to these subnets
            for subnet_id in subnet_ids:
                nsg_row = conn.execute(
                    "SELECT nsg_id FROM subnets WHERE LOWER(subnet_id) = LOWER(?)", (subnet_id,)
                ).fetchone()
                if nsg_row and nsg_row["nsg_id"]:
                    nsg_rules = conn.execute(
                        "SELECT * FROM nsg_rules WHERE LOWER(nsg_id) = LOWER(?) ORDER BY direction, priority",
                        (nsg_row["nsg_id"],),
                    ).fetchall()
                    rules.extend([dict(r) for r in nsg_rules])
        return rules
    except Exception:
        return []
    finally:
        conn.close()


# ── CMDB ──────────────────────────────────────────────────────────────────────

def get_cmdb_resources(
    type_filter: str = None,
    rg_filter: str = None,
    search_q: str = None,
    limit: int = 2000,
) -> list[dict]:
    """UNION of all major resource tables for unified CMDB inventory view."""
    conn = get_db()
    try:
        union_parts = [
            "SELECT name,'Virtual Machine' AS type,resource_group,location,power_state AS state,subscription_id,tags,synced_at FROM vms",
            "SELECT name,'SQL Server' AS type,resource_group,location,state,subscription_id,tags,synced_at FROM sql_servers",
            "SELECT name,'SQL Database' AS type,resource_group,location,status AS state,subscription_id,tags,synced_at FROM sql_databases",
            "SELECT name,'Elastic Pool' AS type,resource_group,location,state,subscription_id,tags,synced_at FROM elastic_pools",
            "SELECT name,'PostgreSQL Server' AS type,resource_group,location,state,subscription_id,tags,synced_at FROM postgresql_servers",
            "SELECT name,'Virtual Network' AS type,resource_group,NULL AS location,NULL AS state,subscription_id,NULL AS tags,synced_at FROM vnets",
            "SELECT name,'NSG' AS type,resource_group,location,NULL AS state,subscription_id,NULL AS tags,synced_at FROM nsgs",
            "SELECT name,'Public IP' AS type,resource_group,NULL AS location,ip_address AS state,subscription_id,NULL AS tags,synced_at FROM public_ips",
            "SELECT name,'App Gateway' AS type,resource_group,location,operational_state AS state,subscription_id,tags,synced_at FROM app_gateways",
        ]
        sql = "SELECT * FROM (" + " UNION ALL ".join(union_parts) + ") t WHERE 1=1"
        params: list = []

        if type_filter:
            sql += " AND type = ?"
            params.append(type_filter)
        if rg_filter:
            sql += " AND LOWER(resource_group) = LOWER(?)"
            params.append(rg_filter)
        if search_q:
            like = f"%{search_q}%"
            sql += " AND (name LIKE ? OR resource_group LIKE ? OR state LIKE ? OR tags LIKE ?)"
            params.extend([like, like, like, like])

        sql += " ORDER BY type, name LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def get_cmdb_resource_types() -> list[str]:
    return [
        "Virtual Machine", "SQL Server", "SQL Database", "Elastic Pool",
        "PostgreSQL Server", "Virtual Network", "NSG", "Public IP", "App Gateway",
    ]


# ── Security score & high-risk ports ──────────────────────────────────────────

def get_high_risk_ports() -> list[dict]:
    """NSG inbound-allow rules exposing known dangerous ports from Any/Internet."""
    HIGH_RISK = {
        "22": ("SSH", "critical"), "3389": ("RDP", "critical"),
        "445": ("SMB", "critical"), "23": ("Telnet", "critical"),
        "21": ("FTP", "high"), "1433": ("MSSQL", "high"),
        "3306": ("MySQL", "high"), "5432": ("PostgreSQL", "high"),
        "5985": ("WinRM", "high"), "5986": ("WinRM-SSL", "high"),
        "27017": ("MongoDB", "high"), "6379": ("Redis", "high"),
        "9200": ("Elasticsearch", "high"),
        "*": ("All Ports", "critical"),
    }
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM nsg_rules WHERE direction='Inbound' AND access='Allow' "
            "AND source_prefix IN ('*','Any','Internet','0.0.0.0/0') "
            "ORDER BY nsg_name, priority"
        ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            port = str(r.get("dest_port") or "")
            if port in HIGH_RISK:
                svc, lvl = HIGH_RISK[port]
                r["risk_service"] = svc
                r["risk_level"] = lvl
                result.append(r)
        return result
    except Exception:
        return []
    finally:
        conn.close()


def calculate_security_score() -> dict:
    """Return a 0-100 security score with risk-factor breakdown."""
    conn = get_db()
    try:
        open_inbound = conn.execute(
            "SELECT COUNT(*) AS cnt FROM nsg_rules WHERE direction='Inbound' AND access='Allow' "
            "AND source_prefix IN ('*','Any','Internet','0.0.0.0/0')"
        ).fetchone()["cnt"]
        advisor_high_sec = conn.execute(
            "SELECT COUNT(*) AS cnt FROM advisor_recs WHERE category='Security' AND impact='High'"
        ).fetchone()["cnt"]
        pip_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM public_ips WHERE ip_address IS NOT NULL"
        ).fetchone()["cnt"]
        gateways_no_waf = conn.execute(
            "SELECT COUNT(*) AS cnt FROM app_gateways WHERE waf_enabled=0 OR waf_enabled IS NULL"
        ).fetchone()["cnt"]

        penalties = {
            "open_inbound": min(open_inbound * 3, 30),
            "advisor_security": min(advisor_high_sec * 5, 25),
            "public_ips": min(pip_count * 2, 20),
            "gateways_no_waf": min(gateways_no_waf * 5, 15),
        }
        score = max(0, 100 - sum(penalties.values()))
        return {
            "score": score,
            "grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F",
            "color": "#16a34a" if score >= 75 else "#d97706" if score >= 50 else "#dc2626",
            "open_inbound": open_inbound,
            "advisor_high_sec": advisor_high_sec,
            "public_ips": pip_count,
            "gateways_no_waf": gateways_no_waf,
            "penalties": penalties,
        }
    except Exception:
        return {"score": 0, "grade": "F", "color": "#dc2626", "open_inbound": 0,
                "advisor_high_sec": 0, "public_ips": 0, "gateways_no_waf": 0, "penalties": {}}
    finally:
        conn.close()


# ── Change tracking / snapshots ───────────────────────────────────────────────

def get_resource_snapshots(limit: int = 14) -> list[dict]:
    """Resource count snapshots ordered newest-first, grouped by date."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT snapshot_date, resource_type, total_count FROM resource_snapshots "
            "ORDER BY snapshot_date DESC, resource_type"
        ).fetchall()
        grouped: dict = {}
        for row in rows:
            d = row["snapshot_date"]
            if d not in grouped:
                grouped[d] = {"date": d, "types": {}}
            grouped[d]["types"][row["resource_type"]] = row["total_count"]
        return list(grouped.values())[:limit]
    except Exception:
        return []
    finally:
        conn.close()


def get_snapshot_diff() -> dict:
    """Compare latest vs previous snapshot for change-tracking view."""
    conn = get_db()
    try:
        dates = [r["snapshot_date"] for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM resource_snapshots ORDER BY snapshot_date DESC LIMIT 2"
        ).fetchall()]
        if not dates:
            return {"has_diff": False, "latest": None, "previous": None, "changes": []}
        if len(dates) == 1:
            latest_rows = {r["resource_type"]: r["total_count"] for r in conn.execute(
                "SELECT resource_type, total_count FROM resource_snapshots WHERE snapshot_date=?",
                (dates[0],),
            ).fetchall()}
            changes = [{"type": k, "current": v, "previous": 0, "delta": v, "change_type": "added"}
                       for k, v in sorted(latest_rows.items())]
            return {"has_diff": False, "latest": dates[0], "previous": None, "changes": changes}

        latest_date, prev_date = dates[0], dates[1]
        latest_rows = {r["resource_type"]: r["total_count"] for r in conn.execute(
            "SELECT resource_type, total_count FROM resource_snapshots WHERE snapshot_date=?",
            (latest_date,),
        ).fetchall()}
        prev_rows = {r["resource_type"]: r["total_count"] for r in conn.execute(
            "SELECT resource_type, total_count FROM resource_snapshots WHERE snapshot_date=?",
            (prev_date,),
        ).fetchall()}

        changes = []
        for rt in sorted(set(latest_rows) | set(prev_rows)):
            curr = latest_rows.get(rt, 0)
            prev = prev_rows.get(rt, 0)
            delta = curr - prev
            changes.append({
                "type": rt, "current": curr, "previous": prev, "delta": delta,
                "change_type": "added" if delta > 0 else "removed" if delta < 0 else "unchanged",
            })
        return {"has_diff": True, "latest": latest_date, "previous": prev_date, "changes": changes}
    except Exception:
        return {"has_diff": False, "latest": None, "previous": None, "changes": []}
    finally:
        conn.close()


def get_top_security_risks(limit: int = 10) -> list[dict]:
    """Consolidated top security risks for dashboard panel."""
    risks = []
    conn = get_db()
    try:
        HIGH_RISK_PORTS = {"22": "SSH", "3389": "RDP", "445": "SMB", "23": "Telnet",
                           "21": "FTP", "1433": "MSSQL", "3306": "MySQL", "5432": "PostgreSQL",
                           "*": "All Ports"}
        rows = conn.execute(
            "SELECT nsg_name, resource_group, dest_port, name "
            "FROM nsg_rules WHERE direction='Inbound' AND access='Allow' "
            "AND source_prefix IN ('*','Any','Internet','0.0.0.0/0') "
            "ORDER BY nsg_name LIMIT 20"
        ).fetchall()
        for r in rows:
            port = str(r["dest_port"] or "")
            svc = HIGH_RISK_PORTS.get(port, f"Port {port}")
            risks.append({
                "severity": "critical" if port in ("22", "3389", "445", "*") else "high",
                "category": "Open Port",
                "title": f"{svc} open from Internet",
                "detail": f"NSG {r['nsg_name']} rule '{r['name']}'",
                "url": f"/nsgs/{r['resource_group']}/{r['nsg_name']}",
            })

        # Public IPs attached to VMs
        pip_rows = conn.execute(
            "SELECT p.name, p.ip_address, v.name AS vm_name, v.resource_group "
            "FROM public_ips p LEFT JOIN nics n ON LOWER(n.nic_id)=LOWER(p.nic_id) "
            "LEFT JOIN vms v ON LOWER(v.vm_id)=LOWER(n.vm_id) "
            "WHERE p.ip_address IS NOT NULL AND v.name IS NOT NULL"
        ).fetchall()
        for r in pip_rows:
            risks.append({
                "severity": "high",
                "category": "Public Exposure",
                "title": f"VM {r['vm_name']} has public IP",
                "detail": f"{r['ip_address']} via {r['name']}",
                "url": f"/vms/{r['resource_group']}/{r['vm_name']}",
            })

        # App Gateways without WAF
        gw_rows = conn.execute(
            "SELECT name, resource_group FROM app_gateways WHERE waf_enabled=0 OR waf_enabled IS NULL"
        ).fetchall()
        for r in gw_rows:
            risks.append({
                "severity": "high",
                "category": "WAF",
                "title": f"App Gateway without WAF",
                "detail": f"{r['name']} ({r['resource_group']})",
                "url": f"/waf/{r['resource_group']}/{r['name']}",
            })

        # High-impact Security advisor recs
        adv_rows = conn.execute(
            "SELECT short_description, resource_id, category FROM advisor_recs "
            "WHERE category='Security' AND impact='High' LIMIT 5"
        ).fetchall()
        for r in adv_rows:
            risks.append({
                "severity": "high",
                "category": "Advisor",
                "title": (r["short_description"] or "Security recommendation")[:70],
                "detail": "Azure Advisor — Security",
                "url": "/advisor?category=Security",
            })

    except Exception:
        pass
    finally:
        conn.close()

    order = {"critical": 0, "high": 1, "medium": 2}
    risks.sort(key=lambda x: order.get(x["severity"], 9))
    return risks[:limit]


def get_recent_changes_summary() -> dict:
    """Snapshot diff summary for dashboard recent-changes panel."""
    diff = get_snapshot_diff()
    significant = [c for c in diff.get("changes", []) if c["delta"] != 0]
    return {
        "has_changes": bool(significant),
        "latest": diff.get("latest"),
        "previous": diff.get("previous"),
        "added": sum(1 for c in significant if c["delta"] > 0),
        "removed": sum(1 for c in significant if c["delta"] < 0),
        "changes": significant[:8],
    }


# ── Security history / trend (from snapshots) ─────────────────────────────────

def get_security_history(days: int = 14) -> list[dict]:
    """Return daily security posture snapshots for trend chart."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT snapshot_date, resource_type, total_count FROM resource_snapshots "
            "ORDER BY snapshot_date DESC"
        ).fetchall()
        by_date: dict = {}
        for r in rows:
            d = r["snapshot_date"]
            if d not in by_date:
                by_date[d] = {}
            by_date[d][r["resource_type"]] = r["total_count"]

        result = []
        for date in sorted(by_date.keys())[-days:]:
            data = by_date[date]
            open_ports = data.get("NSG Rules", 0)
            pub_ips    = data.get("Public IPs", 0)
            score = max(0, 100 - open_ports * 3 - pub_ips * 2)
            result.append({"date": date, "score": score, "open_ports": open_ports, "public_ips": pub_ips})
        return result
    except Exception:
        return []
    finally:
        conn.close()


def get_public_ip_exposure() -> list[dict]:
    """Public IPs with associated NIC and VM details."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT p.name, p.ip_address, p.allocation_method,
                   p.resource_group,
                   n.name AS nic_name,
                   v.name AS vm_name,
                   v.resource_group AS vm_rg,
                   v.power_state
            FROM public_ips p
            LEFT JOIN nics n ON LOWER(n.nic_id) = LOWER(p.nic_id)
            LEFT JOIN vms v ON LOWER(v.vm_id) = LOWER(n.vm_id)
            WHERE p.ip_address IS NOT NULL
            ORDER BY p.name
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Elastic Pool detail ───────────────────────────────────────────────────────

def get_elastic_pool_by_name(resource_group: str, server_name: str, pool_name: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM elastic_pools WHERE LOWER(resource_group)=LOWER(?) AND LOWER(server_name)=LOWER(?) AND LOWER(name)=LOWER(?)",
            (resource_group, server_name, pool_name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_databases_in_pool(pool_id: str) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM sql_databases WHERE elastic_pool_id LIKE ? ORDER BY name",
            (f"%{pool_id.split('/')[-1]}%",),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Cost savings (Advisor cost recommendations) ───────────────────────────────

def get_cost_advisor_recs() -> list[dict]:
    """Advisor recommendations of category=Cost for the cost savings panel."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM advisor_recs WHERE category='Cost' ORDER BY impact DESC, last_updated DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Azure DevOps ───────────────────────────────────────────────────────────────

def get_devops_projects() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM devops_projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_devops_pipelines(project_id: str = None) -> list[dict]:
    conn = get_db()
    try:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM devops_pipelines WHERE project_id=? ORDER BY project_name, folder, name",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM devops_pipelines ORDER BY project_name, folder, name"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_devops_builds(project_id: str = None, limit: int = 100) -> list[dict]:
    conn = get_db()
    try:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM devops_builds WHERE project_id=? ORDER BY start_time DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM devops_builds ORDER BY start_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_devops_repos(project_id: str = None) -> list[dict]:
    conn = get_db()
    try:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM devops_repos WHERE project_id=? ORDER BY name",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM devops_repos ORDER BY project_name, name"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_devops_summary() -> dict:
    conn = get_db()
    try:
        r = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM devops_projects WHERE state='wellFormed') AS active_projects,
                (SELECT COUNT(*) FROM devops_pipelines) AS total_pipelines,
                (SELECT COUNT(*) FROM devops_pipelines WHERE queue_status='enabled') AS enabled_pipelines,
                (SELECT COUNT(*) FROM devops_pipelines WHERE last_build_result='failed') AS failing_pipelines,
                (SELECT COUNT(*) FROM devops_builds WHERE result='succeeded'
                    AND start_time >= datetime('now','-1 day')) AS builds_ok_24h,
                (SELECT COUNT(*) FROM devops_builds WHERE result='failed'
                    AND start_time >= datetime('now','-1 day')) AS builds_failed_24h,
                (SELECT COUNT(*) FROM devops_repos) AS total_repos
        """).fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}
    finally:
        conn.close()


def get_devops_build_trend(days: int = 7) -> list[dict]:
    """Returns daily succeeded/failed build counts."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                substr(start_time, 1, 10) AS day,
                SUM(CASE WHEN result='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                SUM(CASE WHEN result='failed' THEN 1 ELSE 0 END) AS failed,
                COUNT(*) AS total
            FROM devops_builds
            WHERE start_time >= datetime('now', ? || ' days')
            GROUP BY day
            ORDER BY day ASC
        """, (f"-{days}",)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Security score history ─────────────────────────────────────────────────────

def get_security_score_trend(days: int = 30) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT snapshot_date, score FROM security_score_history "
            "ORDER BY snapshot_date DESC LIMIT ?",
            (days,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []
    finally:
        conn.close()


# ── Reservations ───────────────────────────────────────────────────────────────

def get_reservations(state_filter: str = None) -> list[dict]:
    conn = get_db()
    try:
        sql = "SELECT * FROM reservations"
        params: list = []
        if state_filter:
            sql += " WHERE state = ?"
            params.append(state_filter)
        sql += " ORDER BY state ASC, expiry_date ASC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_reservation_summary() -> dict:
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN state='Active' THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN state='Expired' THEN 1 ELSE 0 END) AS expired,
                SUM(CASE WHEN state='Cancelled' THEN 1 ELSE 0 END) AS cancelled,
                SUM(CASE WHEN state='Active' AND expiry_date <= date('now','+30 days') THEN 1 ELSE 0 END) AS expiring_soon,
                AVG(CASE WHEN state='Active' THEN utilization_pct END) AS avg_utilization,
                SUM(CASE WHEN state='Active' THEN quantity ELSE 0 END) AS total_quantity
               FROM reservations"""
        ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}
    finally:
        conn.close()


# ── Reporting ──────────────────────────────────────────────────────────────────

def get_report_data() -> dict:
    """Aggregate data for the executive reporting page."""
    conn = get_db()
    try:
        sec = calculate_security_score()
        mtd = get_mtd_total()

        vm_rows = conn.execute(
            "SELECT name, resource_group, location, vm_size, os_type, power_state, tags FROM vms ORDER BY name"
        ).fetchall()

        open_ports = conn.execute(
            "SELECT r.nsg_name, n.resource_group, r.name AS rule_name, r.dest_port, r.source_prefix, r.priority "
            "FROM nsg_rules r LEFT JOIN nsgs n ON r.nsg_name = n.name "
            "WHERE r.direction='Inbound' AND r.access='Allow' "
            "AND r.source_prefix IN ('*','Any','Internet','0.0.0.0/0') ORDER BY r.nsg_name, r.priority"
        ).fetchall()

        advisor_high = conn.execute(
            "SELECT category, impact, short_description, solution, resource_id "
            "FROM advisor_recs WHERE impact IN ('High','Medium') ORDER BY impact, category LIMIT 100"
        ).fetchall()

        month_start = datetime.now(timezone.utc).strftime("%Y%m01")
        cost_by_rg = conn.execute(
            "SELECT resource_group, SUM(cost) AS total FROM cost_daily "
            "WHERE date >= ? GROUP BY resource_group ORDER BY total DESC",
            (month_start,),
        ).fetchall()

        pg_servers = conn.execute(
            "SELECT name, resource_group, location, version, state, sku_name, storage_gb FROM postgresql_servers"
        ).fetchall()

        sql_servers = conn.execute(
            "SELECT name, resource_group, location, state, fqdn FROM sql_servers"
        ).fetchall()

        backup_issues = conn.execute(
            "SELECT vm_name, vault_name, last_backup_status, last_backup_time, resource_group "
            "FROM backup_status WHERE last_backup_status NOT IN ('Completed','IRPending') ORDER BY last_backup_time"
        ).fetchall()

        try:
            nsg_summary = conn.execute(
                """SELECT
                    COUNT(DISTINCT n.nsg_id) AS total,
                    COUNT(DISTINCT CASE WHEN r.direction='Inbound' AND r.access='Allow'
                        AND r.source_prefix IN ('*','Any','Internet','0.0.0.0/0')
                        THEN n.nsg_id END) AS nsgs_with_open,
                    SUM(CASE WHEN r.direction='Inbound' AND r.access='Allow' THEN 1 ELSE 0 END) AS total_inbound_allow,
                    SUM(CASE WHEN r.direction='Inbound' AND r.access='Allow'
                        AND r.source_prefix IN ('*','Any','Internet','0.0.0.0/0') THEN 1 ELSE 0 END) AS total_open_inbound
                FROM nsgs n LEFT JOIN nsg_rules r ON r.nsg_id = n.nsg_id"""
            ).fetchone()
            nsg_summary = dict(nsg_summary) if nsg_summary else {}
        except Exception:
            nsg_summary = {}

        try:
            reservations_expiring = conn.execute(
                "SELECT name, quantity, sku_name, scope_type, expiry_date, utilization_pct, term "
                "FROM reservations WHERE state='Active' AND expiry_date <= date('now','+30 days') "
                "ORDER BY expiry_date ASC"
            ).fetchall()
            reservations_expiring = [dict(r) for r in reservations_expiring]
        except Exception:
            reservations_expiring = []

        try:
            network_summary = conn.execute(
                "SELECT (SELECT COUNT(*) FROM vnets) AS vnet_count, "
                "(SELECT COUNT(*) FROM network_interfaces WHERE public_ip IS NOT NULL AND public_ip != '') AS nic_with_public_ip"
            ).fetchone()
            network_summary = dict(network_summary) if network_summary else {}
        except Exception:
            network_summary = {}

        last_sync = last_sync_info()

        return {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "last_sync": last_sync,
            "security_score": sec,
            "mtd_cost": mtd,
            "vms": [dict(r) for r in vm_rows],
            "open_ports": [dict(r) for r in open_ports],
            "advisor_high": [dict(r) for r in advisor_high],
            "cost_by_rg": [dict(r) for r in cost_by_rg],
            "pg_servers": [dict(r) for r in pg_servers],
            "sql_servers": [dict(r) for r in sql_servers],
            "backup_issues": [dict(r) for r in backup_issues],
            "nsg_summary": nsg_summary,
            "reservations_expiring": reservations_expiring,
            "network_summary": network_summary,
        }
    except Exception:
        return {}
    finally:
        conn.close()
