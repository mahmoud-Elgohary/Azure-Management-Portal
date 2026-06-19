"""
SQLite connection factory and schema bootstrap.
All other model modules import get_db() from here.
"""

import sqlite3
from pathlib import Path

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at   TEXT    NOT NULL,
    status      TEXT    NOT NULL,   -- 'ok' | 'partial' | 'error'
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS vms (
    vm_id           TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    vm_size         TEXT,
    os_type         TEXT,
    power_state     TEXT,
    tags            TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS sql_servers (
    server_id       TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    admin_login     TEXT,
    state           TEXT,
    fqdn            TEXT,
    tags            TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS sql_databases (
    db_id           TEXT PRIMARY KEY,
    name            TEXT,
    server_name     TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    status          TEXT,
    elastic_pool_id TEXT,
    edition         TEXT,
    tags            TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS elastic_pools (
    pool_id         TEXT PRIMARY KEY,
    name            TEXT,
    server_name     TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    state           TEXT,
    edition         TEXT,
    capacity        INTEGER,
    sku_name        TEXT,
    tags            TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS vm_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vm_id       TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    UNIQUE(vm_id, metric, timestamp)
);

CREATE TABLE IF NOT EXISTS advisor_recs (
    rec_id          TEXT PRIMARY KEY,
    subscription_id TEXT,
    category        TEXT,
    impact          TEXT,
    resource_id     TEXT,
    short_description TEXT,
    solution        TEXT,
    last_updated    TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS backup_status (
    item_id         TEXT PRIMARY KEY,
    subscription_id TEXT,
    vault_name      TEXT,
    resource_group  TEXT,
    vm_name         TEXT,
    protection_state TEXT,
    last_backup_status TEXT,
    last_backup_time TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS resource_health (
    resource_id         TEXT PRIMARY KEY,
    subscription_id     TEXT,
    location            TEXT,
    availability_state  TEXT,
    summary             TEXT,
    reason_type         TEXT,
    occured_time        TEXT,
    synced_at           TEXT
);

CREATE TABLE IF NOT EXISTS cost_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT,
    date            TEXT,
    resource_group  TEXT,
    cost            REAL,
    currency        TEXT,
    synced_at       TEXT,
    UNIQUE(subscription_id, date, resource_group)
);

-- ── Network topology tables (Phase 4) ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS vnets (
    vnet_id         TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    address_space   TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS subnets (
    subnet_id       TEXT PRIMARY KEY,
    name            TEXT,
    vnet_id         TEXT,
    resource_group  TEXT,
    address_prefix  TEXT,
    nsg_id          TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS nics (
    nic_id          TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    vm_id           TEXT,
    subnet_id       TEXT,
    private_ip      TEXT,
    public_ip_id    TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS public_ips (
    pip_id          TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    ip_address      TEXT,
    allocation_method TEXT,
    nic_id          TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS nsgs (
    nsg_id          TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS vnet_peerings (
    peering_id      TEXT PRIMARY KEY,
    src_vnet_id     TEXT,
    dst_vnet_id     TEXT,
    name            TEXT,
    state           TEXT,
    synced_at       TEXT
);

-- ── Azure Monitor alerts (Phase 5) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts (
    alert_id            TEXT PRIMARY KEY,
    subscription_id     TEXT,
    severity            TEXT,
    alert_rule          TEXT,
    target_resource     TEXT,
    target_resource_name TEXT,
    monitor_condition   TEXT,
    description         TEXT,
    fired_time          TEXT,
    resolved_time       TEXT,
    synced_at           TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_vm_metrics_vm_metric ON vm_metrics(vm_id, metric);
CREATE INDEX IF NOT EXISTS idx_advisor_resource ON advisor_recs(resource_id);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_fired ON alerts(fired_time);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
