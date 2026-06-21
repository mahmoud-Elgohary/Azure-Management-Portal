"""
Pulls data from Azure and writes to the local SQLite cache.
Run directly (python -m sync.sync_job) or called from the Flask "Sync now" route.
Never called from page load — routes always read the cache.
"""

import sys
import os
import json
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

from models.db import get_db, init_db
from azure_client import (
    compute,
    sql as sql_client,
    advisor,
    backup,
    health,
    cost,
    monitor,
    network,
    alerts as alerts_client,
    postgresql as pg_client,
    appgateway as appgw_client,
    activity as activity_client,
    resource_graph,
)

log = logging.getLogger("wts.sync")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_vms(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            log.warning("VM fetch error (sub=%s): %s", r.get("subscription_id"), r["_error"])
            continue
        conn.execute(
            """INSERT INTO vms
               (vm_id,name,resource_group,subscription_id,location,vm_size,os_type,power_state,tags,synced_at)
               VALUES (:vm_id,:name,:resource_group,:subscription_id,:location,:vm_size,:os_type,:power_state,:tags,:synced_at)
               ON CONFLICT(vm_id) DO UPDATE SET
                 name=excluded.name, resource_group=excluded.resource_group,
                 subscription_id=excluded.subscription_id, location=excluded.location,
                 vm_size=excluded.vm_size, os_type=excluded.os_type,
                 power_state=excluded.power_state, tags=excluded.tags, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_sql_servers(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            log.warning("SQL server fetch error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT INTO sql_servers
               (server_id,name,resource_group,subscription_id,location,admin_login,state,fqdn,tags,synced_at)
               VALUES (:server_id,:name,:resource_group,:subscription_id,:location,:admin_login,:state,:fqdn,:tags,:synced_at)
               ON CONFLICT(server_id) DO UPDATE SET
                 name=excluded.name, resource_group=excluded.resource_group,
                 state=excluded.state, fqdn=excluded.fqdn, tags=excluded.tags, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_databases(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO sql_databases
               (db_id,name,server_name,resource_group,subscription_id,location,status,elastic_pool_id,edition,tags,synced_at)
               VALUES (:db_id,:name,:server_name,:resource_group,:subscription_id,:location,:status,:elastic_pool_id,:edition,:tags,:synced_at)
               ON CONFLICT(db_id) DO UPDATE SET
                 status=excluded.status, elastic_pool_id=excluded.elastic_pool_id,
                 edition=excluded.edition, tags=excluded.tags, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_elastic_pools(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO elastic_pools
               (pool_id,name,server_name,resource_group,subscription_id,location,state,edition,capacity,sku_name,tags,synced_at)
               VALUES (:pool_id,:name,:server_name,:resource_group,:subscription_id,:location,:state,:edition,:capacity,:sku_name,:tags,:synced_at)
               ON CONFLICT(pool_id) DO UPDATE SET
                 state=excluded.state, capacity=excluded.capacity,
                 sku_name=excluded.sku_name, tags=excluded.tags, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_advisor(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM advisor_recs")
    for r in rows:
        if "_error" in r:
            log.warning("Advisor error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO advisor_recs
               (rec_id,subscription_id,category,impact,resource_id,short_description,solution,last_updated,synced_at)
               VALUES (:rec_id,:subscription_id,:category,:impact,:resource_id,:short_description,:solution,:last_updated,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _upsert_backup(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM backup_status")
    for r in rows:
        if "_error" in r:
            log.warning("Backup error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO backup_status
               (item_id,subscription_id,vault_name,resource_group,vm_name,protection_state,last_backup_status,last_backup_time,synced_at)
               VALUES (:item_id,:subscription_id,:vault_name,:resource_group,:vm_name,:protection_state,:last_backup_status,:last_backup_time,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _upsert_health(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM resource_health")
    for r in rows:
        if "_error" in r:
            log.warning("Health error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO resource_health
               (resource_id,subscription_id,location,availability_state,summary,reason_type,occured_time,synced_at)
               VALUES (:resource_id,:subscription_id,:location,:availability_state,:summary,:reason_type,:occured_time,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _upsert_costs(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            log.warning("Cost error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT INTO cost_daily
               (subscription_id,date,resource_group,cost,currency,synced_at)
               VALUES (:subscription_id,:date,:resource_group,:cost,:currency,:synced_at)
               ON CONFLICT(subscription_id,date,resource_group) DO UPDATE SET
                 cost=excluded.cost, currency=excluded.currency, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_metrics(conn, rows: list[dict]):
    for r in rows:
        if "_error" in r:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO vm_metrics (vm_id,metric,timestamp,value)
               VALUES (:vm_id,:metric,:timestamp,:value)""",
            r,
        )


def _upsert_nsg_rules(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM nsg_rules")
    for r in rows:
        if "_error" in r:
            log.warning("NSG rules error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO nsg_rules
               (rule_id,nsg_id,nsg_name,name,priority,direction,access,protocol,
                source_prefix,source_port,dest_prefix,dest_port,synced_at)
               VALUES (:rule_id,:nsg_id,:nsg_name,:name,:priority,:direction,:access,:protocol,
                :source_prefix,:source_port,:dest_prefix,:dest_port,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _upsert_postgresql(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            log.warning("PostgreSQL error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT INTO postgresql_servers
               (server_id,name,resource_group,subscription_id,location,version,state,
                admin_login,storage_gb,sku_name,tags,synced_at)
               VALUES (:server_id,:name,:resource_group,:subscription_id,:location,:version,:state,
                :admin_login,:storage_gb,:sku_name,:tags,:synced_at)
               ON CONFLICT(server_id) DO UPDATE SET
                 state=excluded.state, version=excluded.version,
                 storage_gb=excluded.storage_gb, sku_name=excluded.sku_name,
                 tags=excluded.tags, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_network(conn, data: dict, ts: str):
    for r in data.get("vnets", []):
        if "_error" in r:
            print(f"  [warn] VNet error: {r['_error']}")
            continue
        conn.execute(
            """INSERT INTO vnets (vnet_id,name,resource_group,subscription_id,location,address_space,synced_at)
               VALUES (:vnet_id,:name,:resource_group,:subscription_id,:location,:address_space,:synced_at)
               ON CONFLICT(vnet_id) DO UPDATE SET
                 name=excluded.name, resource_group=excluded.resource_group,
                 address_space=excluded.address_space, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )
    for r in data.get("subnets", []):
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO subnets (subnet_id,name,vnet_id,resource_group,address_prefix,nsg_id,synced_at)
               VALUES (:subnet_id,:name,:vnet_id,:resource_group,:address_prefix,:nsg_id,:synced_at)
               ON CONFLICT(subnet_id) DO UPDATE SET
                 name=excluded.name, address_prefix=excluded.address_prefix, nsg_id=excluded.nsg_id, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )
    for r in data.get("nics", []):
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO nics (nic_id,name,resource_group,subscription_id,vm_id,subnet_id,private_ip,public_ip_id,synced_at)
               VALUES (:nic_id,:name,:resource_group,:subscription_id,:vm_id,:subnet_id,:private_ip,:public_ip_id,:synced_at)
               ON CONFLICT(nic_id) DO UPDATE SET
                 vm_id=excluded.vm_id, subnet_id=excluded.subnet_id,
                 private_ip=excluded.private_ip, public_ip_id=excluded.public_ip_id, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )
    for r in data.get("public_ips", []):
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO public_ips (pip_id,name,resource_group,subscription_id,ip_address,allocation_method,nic_id,synced_at)
               VALUES (:pip_id,:name,:resource_group,:subscription_id,:ip_address,:allocation_method,:nic_id,:synced_at)
               ON CONFLICT(pip_id) DO UPDATE SET
                 ip_address=excluded.ip_address, nic_id=excluded.nic_id, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )
    for r in data.get("nsgs", []):
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO nsgs (nsg_id,name,resource_group,subscription_id,location,synced_at)
               VALUES (:nsg_id,:name,:resource_group,:subscription_id,:location,:synced_at)
               ON CONFLICT(nsg_id) DO UPDATE SET name=excluded.name, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )
    for r in data.get("peerings", []):
        if "_error" in r:
            continue
        conn.execute(
            """INSERT INTO vnet_peerings (peering_id,src_vnet_id,dst_vnet_id,name,state,synced_at)
               VALUES (:peering_id,:src_vnet_id,:dst_vnet_id,:name,:state,:synced_at)
               ON CONFLICT(peering_id) DO UPDATE SET state=excluded.state, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def _upsert_alerts(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM alerts")
    for r in rows:
        if "_error" in r:
            log.warning("Alerts error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO alerts
               (alert_id,subscription_id,severity,alert_rule,target_resource,target_resource_name,
                monitor_condition,description,fired_time,resolved_time,synced_at)
               VALUES (:alert_id,:subscription_id,:severity,:alert_rule,:target_resource,:target_resource_name,
                :monitor_condition,:description,:fired_time,:resolved_time,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _upsert_app_gateways(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM app_gateways")
    for r in rows:
        if "_error" in r:
            log.warning("App Gateway error: %s", r["_error"])
            continue
        conn.execute(
            """INSERT OR REPLACE INTO app_gateways
               (gw_id,name,resource_group,subscription_id,location,sku_name,sku_tier,
                operational_state,waf_enabled,waf_mode,owasp_version,frontend_ips,capacity,tags,synced_at)
               VALUES (:gw_id,:name,:resource_group,:subscription_id,:location,:sku_name,:sku_tier,
                :operational_state,:waf_enabled,:waf_mode,:owasp_version,:frontend_ips,:capacity,:tags,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _upsert_waf_rules(conn, rows: list[dict], ts: str):
    conn.execute("DELETE FROM waf_rules")
    for r in rows:
        if "_error" in r:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO waf_rules
               (rule_id,gw_id,gw_name,rule_set_type,rule_set_version,rule_group,rule_rule_id,state,action,synced_at)
               VALUES (:rule_id,:gw_id,:gw_name,:rule_set_type,:rule_set_version,:rule_group,:rule_rule_id,:state,:action,:synced_at)""",
            {**r, "synced_at": ts},
        )


def _take_resource_snapshot(conn, ts: str):
    """Record current resource counts as a daily snapshot for change tracking."""
    snapshot_date = ts[:10]  # YYYY-MM-DD
    tables = {
        "Virtual Machines":     "SELECT COUNT(*) FROM vms",
        "SQL Servers":          "SELECT COUNT(*) FROM sql_servers",
        "SQL Databases":        "SELECT COUNT(*) FROM sql_databases",
        "Elastic Pools":        "SELECT COUNT(*) FROM elastic_pools",
        "PostgreSQL Servers":   "SELECT COUNT(*) FROM postgresql_servers",
        "Virtual Networks":     "SELECT COUNT(*) FROM vnets",
        "NSG Rules":            "SELECT COUNT(*) FROM nsg_rules",
        "Public IPs":           "SELECT COUNT(*) FROM public_ips WHERE ip_address IS NOT NULL",
        "App Gateways":         "SELECT COUNT(*) FROM app_gateways",
    }
    conn.execute("DELETE FROM resource_snapshots WHERE snapshot_date=?", (snapshot_date,))
    for rtype, sql in tables.items():
        count = conn.execute(sql).fetchone()[0]
        conn.execute(
            "INSERT INTO resource_snapshots (snapshot_date,resource_type,total_count,synced_at) VALUES (?,?,?,?)",
            (snapshot_date, rtype, count, ts),
        )


def _upsert_activity_log(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            log.warning("Activity log error: %s", r["_error"])
            continue
        event_id = r.get("event_id") or ""
        if not event_id:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO activity_log
               (event_id,caller,operation_name,resource_type,resource_group,resource_id,
                status,sub_status,event_timestamp,description,subscription_id,synced_at)
               VALUES (:event_id,:caller,:operation_name,:resource_type,:resource_group,:resource_id,
                :status,:sub_status,:event_timestamp,:description,:subscription_id,:synced_at)""",
            {**r, "synced_at": ts},
        )
    # Keep only last 2000 entries
    conn.execute(
        "DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 2000)"
    )


def _upsert_generic_resources(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r or not r.get("resource_id"):
            continue
        conn.execute(
            """INSERT INTO resources (resource_id,name,type,resource_group,subscription_id,location,tags,kind,sku,synced_at)
               VALUES (:resource_id,:name,:type,:resource_group,:subscription_id,:location,:tags,:kind,:sku,:synced_at)
               ON CONFLICT(resource_id) DO UPDATE SET
                 name=excluded.name, type=excluded.type, resource_group=excluded.resource_group,
                 location=excluded.location, tags=excluded.tags, kind=excluded.kind,
                 sku=excluded.sku, synced_at=excluded.synced_at""",
            {**r, "synced_at": ts},
        )


def run_sync():
    init_db()
    ts = _now()
    log.info("Sync starting at %s", ts)
    errors = []
    total_sections = 12  # VMs, SQL, PostgreSQL, Advisor, Backup, Health, Cost, Network, NSG rules, Alerts, AppGateway, Activity

    conn = get_db()

    # VMs
    log.info("Fetching VMs …")
    try:
        vm_rows = compute.fetch_all_vms()
        _upsert_vms(conn, vm_rows, ts)
        vm_ok = len([r for r in vm_rows if "_error" not in r])
        log.info("  → %d VMs cached", vm_ok)

        log.info("Fetching VM metrics (last 2 h) …")
        vm_ids = [r["vm_id"] for r in vm_rows if "_error" not in r]
        for vm_id in vm_ids:
            metric_rows = monitor.fetch_vm_metrics(vm_id, hours=2)
            _upsert_metrics(conn, metric_rows)
        log.info("  → metrics fetched for %d VMs", len(vm_ids))
    except Exception as exc:
        err = f"VMs: {exc}"
        log.error(err)
        errors.append(err)

    # SQL
    log.info("Fetching SQL resources …")
    try:
        sql_data = sql_client.fetch_all_sql()
        _upsert_sql_servers(conn, sql_data["servers"], ts)
        _upsert_databases(conn, sql_data["databases"], ts)
        _upsert_elastic_pools(conn, sql_data["elastic_pools"], ts)
        log.info("  → %d servers, %d pools, %d DBs",
                 len(sql_data["servers"]), len(sql_data["elastic_pools"]), len(sql_data["databases"]))
    except Exception as exc:
        err = f"SQL: {exc}"
        log.error(err)
        errors.append(err)

    # PostgreSQL Flexible Servers
    log.info("Fetching PostgreSQL Flexible Servers …")
    try:
        pg_rows = pg_client.fetch_all_postgresql()
        _upsert_postgresql(conn, pg_rows, ts)
        pg_ok = len([r for r in pg_rows if "_error" not in r])
        log.info("  → %d PostgreSQL servers cached", pg_ok)
    except Exception as exc:
        err = f"PostgreSQL: {exc}"
        log.error(err)
        errors.append(err)

    # Advisor
    log.info("Fetching Advisor recommendations …")
    try:
        adv_rows = advisor.fetch_all_recommendations()
        _upsert_advisor(conn, adv_rows, ts)
        log.info("  → %d recommendations", len([r for r in adv_rows if "_error" not in r]))
    except Exception as exc:
        err = f"Advisor: {exc}"
        log.error(err)
        errors.append(err)

    # Backup
    log.info("Fetching backup status …")
    try:
        bk_rows = backup.fetch_all_backup_status()
        _upsert_backup(conn, bk_rows, ts)
        log.info("  → %d backup items", len([r for r in bk_rows if "_error" not in r]))
    except Exception as exc:
        err = f"Backup: {exc}"
        log.error(err)
        errors.append(err)

    # Resource Health
    log.info("Fetching resource health …")
    try:
        h_rows = health.fetch_all_resource_health()
        _upsert_health(conn, h_rows, ts)
        log.info("  → %d health records", len([r for r in h_rows if "_error" not in r]))
    except Exception as exc:
        err = f"Health: {exc}"
        log.error(err)
        errors.append(err)

    # Cost
    log.info("Fetching cost data …")
    try:
        cost_rows = cost.fetch_all_costs()
        _upsert_costs(conn, cost_rows, ts)
        log.info("  → %d daily cost rows", len([r for r in cost_rows if "_error" not in r]))
    except Exception as exc:
        err = f"Cost: {exc}"
        log.error(err)
        errors.append(err)

    # Network topology
    log.info("Fetching network topology (VNets, subnets, NICs, public IPs, NSGs) …")
    try:
        net_data = network.fetch_all_network()
        _upsert_network(conn, net_data, ts)
        ok_vnets = len([r for r in net_data["vnets"] if "_error" not in r])
        ok_nics  = len([r for r in net_data["nics"]  if "_error" not in r])
        ok_pips  = len([r for r in net_data["public_ips"] if "_error" not in r])
        log.info("  → %d VNets, %d NICs, %d public IPs", ok_vnets, ok_nics, ok_pips)
    except Exception as exc:
        err = f"Network: {exc}"
        log.error(err)
        errors.append(err)

    # NSG security rules
    log.info("Fetching NSG security rules …")
    try:
        nsg_rule_rows = net_data.get("nsg_rules", []) if "net_data" in dir() else network.fetch_nsg_rules()
        _upsert_nsg_rules(conn, nsg_rule_rows, ts)
        ok_rules = len([r for r in nsg_rule_rows if "_error" not in r])
        log.info("  → %d NSG rules cached", ok_rules)
    except Exception as exc:
        err = f"NSG rules: {exc}"
        log.error(err)
        errors.append(err)

    # Azure Monitor Alerts
    log.info("Fetching Azure Monitor alerts …")
    try:
        alert_rows = alerts_client.fetch_all_alerts()
        _upsert_alerts(conn, alert_rows, ts)
        log.info("  → %d alert instances", len([r for r in alert_rows if "_error" not in r]))
    except Exception as exc:
        err = f"Alerts: {exc}"
        log.error(err)
        errors.append(err)

    # Application Gateways / WAF
    log.info("Fetching Application Gateways & WAF …")
    try:
        appgw_data = appgw_client.fetch_all_appgateways()
        _upsert_app_gateways(conn, appgw_data["gateways"], ts)
        _upsert_waf_rules(conn, appgw_data["waf_rules"], ts)
        ok_gws = len([r for r in appgw_data["gateways"] if "_error" not in r])
        log.info("  → %d Application Gateways cached", ok_gws)
    except Exception as exc:
        err = f"AppGateway: {exc}"
        log.error(err)
        errors.append(err)

    # Azure Activity Log (via KQL — only if workspace configured)
    log.info("Fetching Activity Log (via KQL) …")
    try:
        activity_rows = activity_client.fetch_activity_log(days=7)
        if activity_rows and "_error" not in activity_rows[0]:
            _upsert_activity_log(conn, activity_rows, ts)
            ok_events = len([r for r in activity_rows if "_error" not in r])
            log.info("  → %d activity events synced", ok_events)
        else:
            log.info("  → Activity log skipped (no workspace configured or error)")
    except Exception as exc:
        err = f"ActivityLog: {exc}"
        log.error(err)
        errors.append(err)

    # Generic resources via Resource Graph (Storage, Key Vault, VPN, Bastion, etc.)
    log.info("Fetching generic resources via Resource Graph …")
    try:
        generic_rows = resource_graph.fetch_generic_resources()
        _upsert_generic_resources(conn, generic_rows, ts)
        log.info("  → %d generic resources (Storage/KV/VPN/Bastion/etc.) cached", len(generic_rows))
    except Exception as exc:
        log.warning("GenericResources: %s", exc)
        # non-fatal

    # Reservations via Resource Graph
    log.info("Fetching Azure Reservations …")
    try:
        res_rows = resource_graph.fetch_reservations()
        for r in res_rows:
            if not r.get("reservation_id"):
                continue
            conn.execute(
                """INSERT INTO reservations
                   (reservation_id,order_id,name,sku_name,quantity,term,scope_type,scope,
                    state,expiry_date,purchase_date,location,utilization_pct,subscription_id,synced_at)
                   VALUES (:reservation_id,:order_id,:name,:sku_name,:quantity,:term,:scope_type,:scope,
                    :state,:expiry_date,:purchase_date,:location,:utilization_pct,:subscription_id,:synced_at)
                   ON CONFLICT(reservation_id) DO UPDATE SET
                    state=excluded.state, utilization_pct=excluded.utilization_pct,
                    expiry_date=excluded.expiry_date, synced_at=excluded.synced_at""",
                {**r, "synced_at": ts},
            )
        log.info("  → %d reservations cached", len(res_rows))
    except Exception as exc:
        log.warning("Reservations: %s", exc)
        # non-fatal — reservations require special permissions

    # Security score snapshot (always runs regardless of errors)
    try:
        from models.queries import calculate_security_score
        sec = calculate_security_score()
        today = ts[:10]
        p = sec.get("penalties", {})
        conn.execute(
            """INSERT INTO security_score_history
               (snapshot_date, score, open_inbound, advisor_high_sec, public_ips, gateways_no_waf, synced_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(snapshot_date) DO UPDATE SET
                score=excluded.score, open_inbound=excluded.open_inbound,
                advisor_high_sec=excluded.advisor_high_sec, public_ips=excluded.public_ips,
                gateways_no_waf=excluded.gateways_no_waf, synced_at=excluded.synced_at""",
            (today, sec.get("score", 0), p.get("open_inbound", 0),
             p.get("advisor_security", 0), p.get("public_ips", 0),
             p.get("gateways_no_waf", 0), ts),
        )
        log.info("  → security score snapshot recorded: %d/100", sec.get("score", 0))
    except Exception as exc:
        log.warning("Security score snapshot failed: %s", exc)

    # Resource change snapshot (always runs regardless of errors)
    try:
        _take_resource_snapshot(conn, ts)
        log.info("  → resource snapshot recorded for %s", ts[:10])
    except Exception as exc:
        log.warning("Snapshot failed: %s", exc)

    status = "error" if len(errors) == total_sections else ("partial" if errors else "ok")
    conn.execute(
        "INSERT INTO sync_log (synced_at,status,detail) VALUES (?,?,?)",
        (ts, status, "; ".join(errors) if errors else None),
    )
    conn.commit()
    conn.close()
    log.info("Sync done — status=%s", status)
    return status


if __name__ == "__main__":
    run_sync()
