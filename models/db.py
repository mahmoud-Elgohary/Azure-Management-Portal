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

-- ── NSG security rules (Phase 5 — security dashboard) ───────────────────
CREATE TABLE IF NOT EXISTS nsg_rules (
    rule_id         TEXT PRIMARY KEY,   -- nsg_id||'/'||name
    nsg_id          TEXT NOT NULL,
    nsg_name        TEXT,
    name            TEXT,
    priority        INTEGER,
    direction       TEXT,               -- Inbound | Outbound
    access          TEXT,               -- Allow | Deny
    protocol        TEXT,
    source_prefix   TEXT,
    source_port     TEXT,
    dest_prefix     TEXT,
    dest_port       TEXT,
    synced_at       TEXT
);

-- ── PostgreSQL Flexible Servers ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS postgresql_servers (
    server_id       TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    version         TEXT,
    state           TEXT,
    admin_login     TEXT,
    storage_gb      INTEGER,
    sku_name        TEXT,
    tags            TEXT,
    synced_at       TEXT
);

-- ── KQL query history ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kql_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT    NOT NULL,
    workspace_id    TEXT,
    executed_at     TEXT    NOT NULL,
    row_count       INTEGER,
    elapsed_ms      INTEGER,
    had_error       INTEGER DEFAULT 0
);

-- ── Universal resource inventory (CMDB foundation) ────────────────────────
CREATE TABLE IF NOT EXISTS resources (
    resource_id     TEXT PRIMARY KEY,
    name            TEXT,
    type            TEXT,               -- microsoft.compute/virtualmachines etc.
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    tags            TEXT,               -- JSON
    kind            TEXT,
    sku             TEXT,
    synced_at       TEXT
);

-- ── Application Gateway & WAF ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_gateways (
    gw_id           TEXT PRIMARY KEY,
    name            TEXT,
    resource_group  TEXT,
    subscription_id TEXT,
    location        TEXT,
    sku_name        TEXT,
    sku_tier        TEXT,
    operational_state TEXT,
    waf_enabled     INTEGER DEFAULT 0,
    waf_mode        TEXT,
    owasp_version   TEXT,
    frontend_ips    TEXT,   -- JSON
    capacity        INTEGER,
    tags            TEXT,
    synced_at       TEXT
);

CREATE TABLE IF NOT EXISTS waf_rules (
    rule_id         TEXT PRIMARY KEY,
    gw_id           TEXT NOT NULL,
    gw_name         TEXT,
    rule_set_type   TEXT,
    rule_set_version TEXT,
    rule_group      TEXT,
    rule_rule_id    TEXT,
    state           TEXT,
    action          TEXT,
    synced_at       TEXT
);

-- ── Azure Activity Log (synced via KQL from Log Analytics) ────────────────
CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT UNIQUE,
    caller          TEXT,
    operation_name  TEXT,
    resource_type   TEXT,
    resource_group  TEXT,
    resource_id     TEXT,
    status          TEXT,
    sub_status      TEXT,
    event_timestamp TEXT,
    description     TEXT,
    subscription_id TEXT,
    synced_at       TEXT
);

-- ── Resource change snapshots (for change tracking) ───────────────────────
CREATE TABLE IF NOT EXISTS resource_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    total_count     INTEGER,
    detail_json     TEXT,
    synced_at       TEXT
);

-- ── Security score history ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS security_score_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL,      -- YYYY-MM-DD
    score           INTEGER NOT NULL,
    open_inbound    INTEGER,
    advisor_high_sec INTEGER,
    public_ips      INTEGER,
    gateways_no_waf INTEGER,
    synced_at       TEXT,
    UNIQUE(snapshot_date)
);

-- ── Azure Reservations ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id      TEXT PRIMARY KEY,   -- full resource ID
    order_id            TEXT,               -- parent reservationOrder ID
    name                TEXT,
    type                TEXT,               -- microsoft.compute/virtualmachines etc.
    sku_name            TEXT,               -- Standard_D4s_v3 etc.
    quantity            INTEGER,
    term                TEXT,               -- P1Y | P3Y
    scope_type          TEXT,               -- Shared | Single | ManagementGroup
    scope               TEXT,               -- subscription or resource group
    state               TEXT,               -- Active | Expired | Cancelled | PaymentPending
    expiry_date         TEXT,
    purchase_date       TEXT,
    location            TEXT,
    utilization_pct     REAL,
    subscription_id     TEXT,
    synced_at           TEXT
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_vm_metrics_vm_metric ON vm_metrics(vm_id, metric);
CREATE INDEX IF NOT EXISTS idx_advisor_resource    ON advisor_recs(resource_id);
CREATE INDEX IF NOT EXISTS idx_advisor_category    ON advisor_recs(category);
CREATE INDEX IF NOT EXISTS idx_alerts_severity     ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_fired        ON alerts(fired_time);
CREATE INDEX IF NOT EXISTS idx_vms_rg              ON vms(resource_group);
CREATE INDEX IF NOT EXISTS idx_vms_name            ON vms(name);
CREATE INDEX IF NOT EXISTS idx_backup_vm_name      ON backup_status(vm_name);
CREATE INDEX IF NOT EXISTS idx_cost_date           ON cost_daily(date);
CREATE INDEX IF NOT EXISTS idx_cost_rg             ON cost_daily(resource_group);
CREATE INDEX IF NOT EXISTS idx_health_state        ON resource_health(availability_state);
CREATE INDEX IF NOT EXISTS idx_nsg_rules_nsg       ON nsg_rules(nsg_id);
CREATE INDEX IF NOT EXISTS idx_resources_type      ON resources(type);
CREATE INDEX IF NOT EXISTS idx_resources_rg        ON resources(resource_group);
CREATE INDEX IF NOT EXISTS idx_kql_history_time    ON kql_history(executed_at);
CREATE INDEX IF NOT EXISTS idx_activity_time       ON activity_log(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_rg         ON activity_log(resource_group);
CREATE INDEX IF NOT EXISTS idx_activity_caller     ON activity_log(caller);
CREATE INDEX IF NOT EXISTS idx_activity_status     ON activity_log(status);
CREATE INDEX IF NOT EXISTS idx_snapshots_date      ON resource_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_reservations_state  ON reservations(state);
CREATE INDEX IF NOT EXISTS idx_reservations_expiry ON reservations(expiry_date);
CREATE INDEX IF NOT EXISTS idx_sec_score_date      ON security_score_history(snapshot_date);
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
