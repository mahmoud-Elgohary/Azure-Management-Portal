"""
Pulls data from Azure and writes to the local SQLite cache.
Run directly (python -m sync.sync_job) or called from the Flask "Sync now" route.
Never called from page load — routes always read the cache.
"""

import sys
import os
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
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert_vms(conn, rows: list[dict], ts: str):
    for r in rows:
        if "_error" in r:
            print(f"  [warn] VM fetch error (sub={r.get('subscription_id')}): {r['_error']}")
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
            print(f"  [warn] SQL server fetch error: {r['_error']}")
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
            print(f"  [warn] Advisor error: {r['_error']}")
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
            print(f"  [warn] Backup error: {r['_error']}")
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
            print(f"  [warn] Health error: {r['_error']}")
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
            print(f"  [warn] Cost error: {r['_error']}")
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
            print(f"  [warn] Alerts error: {r['_error']}")
            continue
        conn.execute(
            """INSERT OR REPLACE INTO alerts
               (alert_id,subscription_id,severity,alert_rule,target_resource,target_resource_name,
                monitor_condition,description,fired_time,resolved_time,synced_at)
               VALUES (:alert_id,:subscription_id,:severity,:alert_rule,:target_resource,:target_resource_name,
                :monitor_condition,:description,:fired_time,:resolved_time,:synced_at)""",
            {**r, "synced_at": ts},
        )


def run_sync():
    init_db()
    ts = _now()
    print(f"[sync] Starting at {ts}")
    errors = []
    total_sections = 8  # VMs, SQL, Advisor, Backup, Health, Cost, Network, Alerts

    conn = get_db()

    # VMs
    print("[sync] Fetching VMs …")
    try:
        vm_rows = compute.fetch_all_vms()
        _upsert_vms(conn, vm_rows, ts)
        vm_ok = len([r for r in vm_rows if "_error" not in r])
        print(f"  → {vm_ok} VMs cached")

        # VM metrics (last 2 hours per sync; lazy-load per-resource for longer windows)
        print("[sync] Fetching VM metrics (last 2 h) …")
        vm_ids = [r["vm_id"] for r in vm_rows if "_error" not in r]
        for vm_id in vm_ids:
            metric_rows = monitor.fetch_vm_metrics(vm_id, hours=2)
            _upsert_metrics(conn, metric_rows)
        print(f"  → metrics fetched for {len(vm_ids)} VMs")
    except Exception as exc:
        err = f"VMs: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # SQL
    print("[sync] Fetching SQL resources …")
    try:
        sql_data = sql_client.fetch_all_sql()
        _upsert_sql_servers(conn, sql_data["servers"], ts)
        _upsert_databases(conn, sql_data["databases"], ts)
        _upsert_elastic_pools(conn, sql_data["elastic_pools"], ts)
        print(f"  → {len(sql_data['servers'])} servers, {len(sql_data['elastic_pools'])} pools, {len(sql_data['databases'])} DBs")
    except Exception as exc:
        err = f"SQL: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # Advisor
    print("[sync] Fetching Advisor recommendations …")
    try:
        adv_rows = advisor.fetch_all_recommendations()
        _upsert_advisor(conn, adv_rows, ts)
        print(f"  → {len([r for r in adv_rows if '_error' not in r])} recommendations")
    except Exception as exc:
        err = f"Advisor: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # Backup
    print("[sync] Fetching backup status …")
    try:
        bk_rows = backup.fetch_all_backup_status()
        _upsert_backup(conn, bk_rows, ts)
        print(f"  → {len([r for r in bk_rows if '_error' not in r])} backup items")
    except Exception as exc:
        err = f"Backup: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # Resource Health
    print("[sync] Fetching resource health …")
    try:
        h_rows = health.fetch_all_resource_health()
        _upsert_health(conn, h_rows, ts)
        print(f"  → {len([r for r in h_rows if '_error' not in r])} health records")
    except Exception as exc:
        err = f"Health: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # Cost
    print("[sync] Fetching cost data …")
    try:
        cost_rows = cost.fetch_all_costs()
        _upsert_costs(conn, cost_rows, ts)
        print(f"  → {len([r for r in cost_rows if '_error' not in r])} daily cost rows")
    except Exception as exc:
        err = f"Cost: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # Network topology
    print("[sync] Fetching network topology (VNets, subnets, NICs, public IPs, NSGs) …")
    try:
        net_data = network.fetch_all_network()
        _upsert_network(conn, net_data, ts)
        ok_vnets = len([r for r in net_data["vnets"] if "_error" not in r])
        ok_nics  = len([r for r in net_data["nics"]  if "_error" not in r])
        ok_pips  = len([r for r in net_data["public_ips"] if "_error" not in r])
        print(f"  → {ok_vnets} VNets, {ok_nics} NICs, {ok_pips} public IPs")
    except Exception as exc:
        err = f"Network: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    # Azure Monitor Alerts
    print("[sync] Fetching Azure Monitor alerts …")
    try:
        alert_rows = alerts_client.fetch_all_alerts()
        _upsert_alerts(conn, alert_rows, ts)
        ok_alerts = len([r for r in alert_rows if "_error" not in r])
        print(f"  → {ok_alerts} alert instances")
    except Exception as exc:
        err = f"Alerts: {exc}"
        print(f"  [error] {err}")
        errors.append(err)

    status = "error" if len(errors) == total_sections else ("partial" if errors else "ok")
    conn.execute(
        "INSERT INTO sync_log (synced_at,status,detail) VALUES (?,?,?)",
        (ts, status, "; ".join(errors) if errors else None),
    )
    conn.commit()
    conn.close()
    print(f"[sync] Done — status={status}")
    return status


if __name__ == "__main__":
    run_sync()
