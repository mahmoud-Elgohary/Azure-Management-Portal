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
    resource_graph,
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


def run_sync():
    init_db()
    ts = _now()
    print(f"[sync] Starting at {ts}")
    errors = []

    conn = get_db()

    # VMs
    print("[sync] Fetching VMs …")
    try:
        vm_rows = compute.fetch_all_vms()
        _upsert_vms(conn, vm_rows, ts)
        print(f"  → {len([r for r in vm_rows if '_error' not in r])} VMs cached")

        # VM metrics
        print("[sync] Fetching VM metrics …")
        vm_ids = [r["vm_id"] for r in vm_rows if "_error" not in r]
        for vm_id in vm_ids:
            metric_rows = monitor.fetch_vm_metrics(vm_id, hours=2)
            _upsert_metrics(conn, metric_rows)
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

    status = "error" if len(errors) == 6 else ("partial" if errors else "ok")
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
