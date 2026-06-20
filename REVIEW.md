# WTS Azure Cloud Manager — Enterprise Architecture Review
**Date:** 2026-06-20
**Reviewer:** Claude Code (automated + manual audit)
**Scope:** Full enterprise review — 10 phases, 16 deliverables
**Security constraint:** Application remains STRICTLY READ-ONLY throughout

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Review](#2-architecture-review)
3. [Security Review](#3-security-review)
4. [Database Review](#4-database-review)
5. [Topology Review](#5-topology-review)
6. [Cost Review](#6-cost-review)
7. [CMDB Design](#7-cmdb-design)
8. [Security Dashboard Design](#8-security-dashboard-design)
9. [KQL Center Design](#9-kql-center-design)
10. [Documentation Center Design](#10-documentation-center-design)
11. [Technical Debt List](#11-technical-debt-list)
12. [Risk Register](#12-risk-register)
13. [Prioritized Backlog](#13-prioritized-backlog)
14. [30-Day Roadmap](#14-30-day-roadmap)
15. [60-Day Roadmap](#15-60-day-roadmap)
16. [90-Day Roadmap](#16-90-day-roadmap)

---

## 1. Executive Summary

The WTS Azure Cloud Manager is a Flask-based internal portal providing read-only visibility into WTS Taxtools' Azure subscriptions. As of the enterprise review (2026-06-20), the platform has progressed from a proof-of-concept to a structured multi-module application with authentication, caching, and a growing feature surface.

### Current state scorecard

| Domain | Status | Score |
|--------|--------|-------|
| Authentication & Authorization | Entra ID OIDC, group-gated | Good |
| Read-only constraint | Enforced — no ARM writes, no PUT/POST to Azure | Enforced |
| Secret handling | Split SP model; `.env` gitignored; never logged | Good |
| Database design | SQLite WAL-mode; schema growing organically | Needs normalisation |
| Observability | `print()` to `logging` migrated; sync log present | No structured log export |
| Cost management | Chart.js MTD/trend added; anomaly detection live | Good |
| Security dashboard | NSG rules, open ports, Advisor Security section live | Good |
| KQL console | End-to-end fixed; workspace picker; history tab | Good |
| Network topology | Cytoscape.js; VNet/Subnet/NSG/PG nodes; SQL edges | Good |
| PostgreSQL support | Flexible Servers sync + listing page added | Added |
| Dependency hygiene | `requirements.txt` pinned; `azure-mgmt-rdbms` added | Good |

### Key risks

- SQLite concurrency under >3 simultaneous users (no connection pooling)
- No audit trail: who viewed what data, when
- No alerting on sync failures — failures are silent beyond logs
- `azure-mgmt-recoveryservicesbackup` v9 API has breaking changes in >=10

---

## 2. Architecture Review

### 2.1 Component diagram

```
Browser
  |  HTTPS (Caddy TLS)
  v
Caddy :8444 -> reverse proxy -> Gunicorn :8050
                                   |
                           Flask app (app.py)
                           |-- Auth layer (MSAL OIDC)
                           |-- Route handlers
                           |-- models/queries.py --> SQLite cache.sqlite (WAL)
                           |-- azure_client/     --> Azure ARM APIs (read-only)
                           `-- sync/sync_job.py  --> background daemon thread
```

### 2.2 Strengths

- **Split credential model**: sign-in SP (AUTH_CLIENT_ID) and data SP (AZURE_CLIENT_ID) are decoupled. If data SP is compromised, sign-in flow is unaffected.
- **SQLite WAL mode**: readers never block writers, appropriate for single-instance portal.
- **Gunicorn 2 workers**: survives one worker crash; restart on failure.
- **Daemon sync thread**: background sync does not block request handling.
- **CSRF on all POST routes**: signed Flask session cookie carries CSRF token.

### 2.3 Identified weaknesses

| # | Area | Issue | Recommendation |
|---|------|-------|----------------|
| A1 | Concurrency | SQLite `check_same_thread=False` — each request opens new connection, no pool | Add `threading.local()` connection cache or migrate to PostgreSQL for >5 users |
| A2 | Sync errors | Failed sync sections update `sync_status` but no alert is sent | Add email/Teams webhook on consecutive sync failures |
| A3 | Session storage | Flask signed cookies — unlimited session size could exceed cookie limits | Consider server-side session store for large token payloads |
| A4 | Worker count | 2 Gunicorn workers is low if sync triggers are frequent | Increase to 4 workers or use async workers (gevent) |
| A5 | No health endpoint | No `/healthz` endpoint for uptime monitoring | Add `GET /healthz` returning sync age and DB write test |

### 2.4 File structure (current)

```
wts-azure-manager/
|-- app.py                  Main Flask app, all routes
|-- config.py               Env-var config
|-- auth/
|   |-- decorator.py        @login_required
|   `-- msal_helper.py      OIDC flow, token cache
|-- azure_client/
|   |-- credentials.py      DefaultAzureCredential / SP credential
|   |-- compute.py          VM fetch
|   |-- sql.py              SQL / Elastic Pool fetch
|   |-- network.py          VNets / NICs / PIPs / NSGs / NSG rules
|   |-- postgresql.py       PostgreSQL Flexible Servers
|   |-- cost.py             Cost Management
|   |-- advisor.py          Advisor recommendations
|   |-- health.py           Resource Health
|   |-- backup.py           Recovery Services Backup
|   `-- logs.py             Log Analytics KQL
|-- models/
|   |-- db.py               Schema + migrations
|   `-- queries.py          All read queries
|-- sync/
|   `-- sync_job.py         Background sync orchestrator
|-- templates/              Jinja2 HTML
|-- static/css/style.css    Custom styles
|-- requirements.txt
`-- .env                    Gitignored; never committed
```

---

## 3. Security Review

### 3.1 Authentication

| Control | Status |
|---------|--------|
| Entra ID OIDC (Authorization Code) | Implemented |
| Group-based access gate (ALLOWED_GROUP_ID) | Configurable |
| Session lifetime (SESSION_TIMEOUT_MINUTES, default 8h) | Configurable |
| Session renewal on activity | `before_request` hook |
| CSRF tokens on POST routes | X-CSRF-Token header checked |
| No write operations to Azure | Enforced — no ARM PUT/PATCH/DELETE |

### 3.2 Secret handling

| Control | Status |
|---------|--------|
| `.env` in `.gitignore` | Verified |
| CLIENT_SECRET never logged or printed | Verified — `logging` module used; no credential fields in log messages |
| Split SP: data vs auth | AUTH_CLIENT_ID/SECRET separate from data SP |
| Secret rotation path | Manual — no rotation reminder or expiry tracking |

### 3.3 Network exposure

| Finding | Risk | Recommendation |
|---------|------|----------------|
| App behind Caddy TLS (8444) | Low — internal only | Confirm Caddy uses valid cert |
| No rate limiting on login endpoint | Medium | Add Flask-Limiter on `/auth/login` (10 req/min) |
| `/api/*` endpoints return Azure resource data | Low — require session | All API routes verified to have `@login_required` |

### 3.4 NSG analysis (from Security Dashboard data)

Once sync runs and NSG rules are collected:
- Rules allowing `source = *` or `source = Internet` on inbound direction are flagged as open inbound rules
- These appear in the Security Center page with NSG name, rule name, port, priority, and protocol
- Recommended action: scope each rule to known source IP ranges

---

## 4. Database Review

### 4.1 Current schema

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `vms` | VM inventory | vm_id, name, resource_group, status, cpu_pct, mem_free_gb |
| `sql_databases` | SQL DB inventory | db_id, server_name, sku_name, max_size_gb |
| `elastic_pools` | Elastic pools | pool_id, server_name, sku_name, max_capacity |
| `postgresql_servers` | PG Flexible Servers | server_id, name, version, state, sku, storage_gb, fqdn, ha_mode |
| `vnets` | Virtual Networks | vnet_id, name, resource_group, address_space |
| `subnets` | Subnets | subnet_id, vnet_id, nsg_id, address_prefix |
| `nics` | Network Interfaces | nic_id, vm_id, subnet_id, private_ip |
| `public_ips` | Public IP addresses | pip_id, ip_address, allocation, nic_id |
| `nsgs` | NSGs | nsg_id, name, resource_group |
| `nsg_rules` | NSG security rules | nsg_id, name, priority, direction, access, protocol, source/dest |
| `cost_data` | Daily costs | date, resource_group, cost, currency |
| `advisor_recs` | Advisor recommendations | rec_id, category, impact, short_description, solution |
| `resource_health` | Health events | resource_id, state, reason, occured_time |
| `backup_status` | Backup jobs | vm_name, last_backup_time, status |
| `kql_history` | Query history | query, workspace_id, row_count, elapsed_ms, executed_at |
| `resources` | Generic CMDB store | resource_id, name, type, resource_group, subscription_id, location |
| `sync_log` | Sync metadata | id, started_at, finished_at, status, sections_ok, sections_fail |

### 4.2 Index coverage

All high-traffic query patterns are indexed:
- `idx_vms_rg`, `idx_vms_name` — VM list/filter
- `idx_cost_date`, `idx_cost_rg` — cost trend/by-RG queries
- `idx_nsg_rules_nsg` — NSG join
- `idx_kql_history_time` — history list
- `idx_resources_type`, `idx_resources_rg` — CMDB queries

### 4.3 Identified issues

| # | Issue | Fix |
|---|-------|-----|
| D1 | `resource_group` stored as plain string — no FK | Acceptable for read cache; no referential integrity needed |
| D2 | `cost_data` upsert not atomic — duplicate inserts possible if sync runs mid-day | Add `INSERT OR REPLACE` with composite unique key `(date, resource_group, currency)` |
| D3 | `kql_history` has no maximum size cap — grows unbounded | Cap at 200 rows — delete oldest after insert |
| D4 | No WAL checkpoint forced — WAL file can grow large on busy instances | Add `PRAGMA wal_checkpoint(PASSIVE)` after each sync |

---

## 5. Topology Review

### 5.1 Node types and rendering

| Type | Color | Shape | Source table |
|------|-------|-------|-------------|
| VM | `#0078d4` blue | Rectangle | `vms` |
| VNet | `#7c3aed` purple | Round-rectangle | `vnets` |
| Subnet | `#a78bfa` light purple | Round-rectangle | `subnets` |
| SQL | `#059669` green | Diamond | `sql_databases` |
| NIC | `#6b7280` grey | Ellipse | `nics` |
| Public IP | `#f59e0b` amber | Star | `public_ips` |
| NSG | `#dc2626` red | Hexagon | `nsgs` |
| PostgreSQL | `#0891b2` cyan | Barrel | `postgresql_servers` |

### 5.2 Edge types

| Edge | Type | Meaning |
|------|------|---------|
| VM to NIC | api | VM has NIC |
| NIC to Subnet | api | NIC in Subnet |
| Subnet to VNet | api | Subnet in VNet |
| NSG to Subnet | api | NSG protects Subnet |
| SQL to VNet | logical | SQL server in VNet |
| PostgreSQL to Subnet | api | PG server delegated subnet |

### 5.3 Identified issues

| # | Issue | Fix |
|---|-------|-----|
| T1 | Large environments (>200 nodes) cause Cytoscape to lag | Add node count warning + RG filter |
| T2 | No persistence of topology layout between page loads | Save layout JSON to localStorage |
| T3 | Orphaned NICs (no VM) appear as isolated nodes | Filter or add visual indicator |

---

## 6. Cost Review

### 6.1 Features implemented

- Month-to-date (MTD) total — filtered to current calendar month
- Daily spend trend line chart (Chart.js) via `/api/cost/trend`
- MTD by resource group doughnut chart via `/api/cost/by-rg`
- Anomaly detection: flag days where spend > 1.5x recent daily average
- Sortable detail table with date descending

### 6.2 Known limitations

| # | Issue | Recommendation |
|---|-------|----------------|
| C1 | Cost data only available if SP has `Cost Management Reader` role | Document and surface in UI when data is empty |
| C2 | Currency assumed EUR (symbol hard-coded in template) | Use `currency` column from `cost_data` table |
| C3 | No budget threshold comparison | Add optional `MONTHLY_BUDGET_EUR` env var |
| C4 | Historical cost beyond subscription retention period unavailable | Document retention limits |

---

## 7. CMDB Design

### 7.1 Purpose

The `resources` table acts as a lightweight CMDB for cross-cutting queries: "what resources are in subscription X", "what resources have tag Y", "what resources went stale".

### 7.2 Recommended schema extension

```sql
-- Tags support (JSON blob)
ALTER TABLE resources ADD COLUMN tags TEXT DEFAULT '{}';

-- Subscription label for multi-sub clarity
ALTER TABLE resources ADD COLUMN subscription_name TEXT DEFAULT '';

-- Staleness detection
ALTER TABLE resources ADD COLUMN last_seen TEXT DEFAULT '';
```

### 7.3 Recommended queries to add

```python
def get_resources_by_type(resource_type: str) -> list[dict]: ...
def search_resources(q: str) -> list[dict]: ...
def get_stale_resources(days: int = 7) -> list[dict]: ...
```

### 7.4 CMDB page design

A `/cmdb` route with:
- Filter by subscription, resource group, type
- Full-text search box
- Export CSV button
- Staleness indicator (last_seen > 7 days shows amber badge)

---

## 8. Security Dashboard Design

### 8.1 Implemented features

The `/security` page provides:

1. KPI row — Open Inbound Rules, Public IPs, Security Advisor Recs, Unavailable Resources
2. Open Inbound Rules panel — NSG rules where source = `*` or `Internet`, inbound
3. Security Advisor panel — Advisor recs filtered to category = `Security`
4. All NSG Rules table — full list with filter buttons (Inbound/Outbound/Allow/Deny)

### 8.2 Future enhancements

| Priority | Feature | Implementation |
|----------|---------|----------------|
| High | Risk score calculation | Score = (open_inbound_rules x 3) + (public_ips x 2) + (advisor_recs_high x 5) |
| High | Trend tracking — posture improvement since last sync | Store score per sync in `sync_log` table |
| Medium | VM disk encryption status | Add `disk_encryption` column to `vms`; populate from compute API |
| Medium | Port-specific risk flags | Flag rules allowing port 22 (SSH), 3389 (RDP), 445 (SMB) by name |
| Low | Compliance framework mapping | Map NSG findings to CIS Azure Benchmark controls |

---

## 9. KQL Center Design

### 9.1 Implemented features

- KQL editor with Ctrl+Enter shortcut
- Run button with loading state and error surfacing
- Blitz query sidebar (pre-built queries from `queries.json`)
- History tab (auto-saved queries via `/api/kql/history`)
- Workspace selector (for `LOG_ANALYTICS_WORKSPACE_IDS` multi-workspace config)
- CSV export of results
- Save custom queries (POST `/api/kql/queries`)
- URL parameter `?resource=<name>` pre-fills a query

### 9.2 Blitz query catalogue

The 10 built-in blitz queries cover:
1. Failed sign-ins last 24h
2. Azure Activity — failed operations last 7d
3. VM performance — high CPU last 1h
4. VM performance — low memory last 1h
5. Syslog — errors last 24h
6. Security events — privilege escalation
7. Update compliance summary
8. Heartbeat — offline agents
9. Custom logs — application errors
10. Network — denied flows (NSG flow logs)

### 9.3 Future enhancements

| Feature | Implementation |
|---------|---------------|
| Query parameterisation | `@ResourceGroup` substitution in query text |
| Scheduled queries | Run blitz query on schedule; save results to SQLite |
| Result caching | Cache identical query+workspace results for 5 min |

---

## 10. Documentation Center Design

### 10.1 Current documentation

- `README.md` — setup and deployment guide
- `CHANGES.md` — changelog with root-cause notes
- `REVIEW.md` — this document
- `deploy/` — systemd service file

### 10.2 Recommended additions

| Document | Purpose |
|----------|---------|
| `docs/RUNBOOK.md` | On-call runbook: sync failure, auth issues, high memory |
| `docs/ARCHITECTURE.md` | Component diagram, data flow, credential model |
| `docs/ONBOARDING.md` | New subscription onboarding checklist (SP roles, env vars) |
| `docs/AZURE_ROLES.md` | Required RBAC roles per feature with justification |

### 10.3 In-portal documentation

A `/docs` route rendering `docs/*.md` as HTML (using `markdown` Python library) would allow ops staff to access the runbook without leaving the tool.

---

## 11. Technical Debt List

| ID | Severity | Area | Description | Effort |
|----|----------|------|-------------|--------|
| TD-01 | High | Concurrency | SQLite: no connection pooling | 1 day |
| TD-02 | High | Sync | Sync failure notification missing | 0.5 day |
| TD-03 | Medium | Auth | No SP token refresh on long-running sync | 1 day |
| TD-04 | Medium | DB | `cost_data` upsert not atomic — duplicate rows possible | 2 hours |
| TD-05 | Medium | DB | `kql_history` grows unbounded | 1 hour |
| TD-06 | Medium | UI | Topology lags with >200 nodes | 1 day |
| TD-07 | Medium | Security | No rate limiting on auth endpoints | 4 hours |
| TD-08 | Low | Currency | EUR hard-coded in cost templates | 2 hours |
| TD-09 | Low | Config | `MEM_FREE_RED/AMBER_GB` defined but not surfaced | 2 hours |
| TD-10 | Low | Docs | README still mentions nginx; Caddy is the actual proxy | 1 hour |
| TD-11 | Low | Error | `_rbac_error()` produces generic message — not actionable | 4 hours |
| TD-12 | Low | Testing | Zero automated tests | 3 days |

---

## 12. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|-----------|
| R-01 | Azure SP secret expires — sync stops | High | High | Calendar reminder 30 days before expiry; document in RUNBOOK |
| R-02 | SQLite corruption under concurrent writes during sync | Low | High | Sync uses single daemon thread; WAL mode isolates readers |
| R-03 | Cost data unavailable — SP lacks Cost Management Reader | Medium | Medium | Surface clear UI message when cost table is empty |
| R-04 | KQL results expose sensitive log data | Low | High | All authenticated users are WTS IT staff (group gate) |
| R-05 | `azure-mgmt-recoveryservicesbackup` v9 to v10 breaking change | Medium | Low | Pin to `>=9.0.0,<10.0.0` in requirements.txt |
| R-06 | Gunicorn worker OOM (large topology response) | Low | Medium | Limit topology nodes to 500; paginate API response |
| R-07 | GitHub push exposes `.env` via accident | Low | Critical | `.env` in `.gitignore`; verified untracked; CI check recommended |
| R-08 | Session cookie theft on non-TLS connection | Very Low | High | Caddy enforces HTTPS; `SESSION_COOKIE_SECURE=True` in Flask config |

---

## 13. Prioritized Backlog

### P0 — Critical (block on)

| Item | Why |
|------|-----|
| SP secret expiry monitoring | Silent failure of all Azure data reads |
| SQLite upsert deduplication for `cost_data` | Data integrity |
| `kql_history` row cap | Unbounded growth fills disk |

### P1 — High (sprint 1)

| Item | Effort |
|------|--------|
| `/healthz` endpoint | 2h |
| Sync failure Teams/email webhook | 4h |
| Rate limiting on auth routes (Flask-Limiter) | 4h |
| Topology: RG filter to limit node count | 4h |
| Unit tests for `queries.py` critical functions | 2d |

### P2 — Medium (sprint 2)

| Item | Effort |
|------|--------|
| CMDB page (`/cmdb`) with RG/type/search filters | 2d |
| Security risk score calculation + trend | 1d |
| Port-specific NSG risk flags (22, 3389, 445) | 4h |
| Budget threshold KPI (`MONTHLY_BUDGET_EUR`) | 4h |
| Topology: persist layout to localStorage | 2h |

### P3 — Low (backlog)

| Item | Effort |
|------|--------|
| `/docs` in-portal documentation route | 1d |
| Scheduled KQL queries | 2d |
| Compliance framework mapping (CIS Azure) | 3d |
| Multi-tenant support | 5d |

---

## 14. 30-Day Roadmap

**Goal:** Stabilise and harden current platform. Fix data integrity issues. Add minimum alerting.

| Week | Focus | Deliverables |
|------|-------|-------------|
| W1 | Data integrity | Fix `cost_data` upsert dedup; cap `kql_history` to 200 rows; force WAL checkpoint after sync |
| W1 | Monitoring | Add `/healthz` endpoint returning sync age, DB status |
| W2 | Alerting | Sync failure webhook (Teams or email) on 2 consecutive failures |
| W2 | Security | Rate limiting on `/auth/login` and `/auth/callback` (Flask-Limiter) |
| W3 | Topology | Add RG filter dropdown to limit topology to <=200 nodes |
| W3 | Cost | Fix EUR hard-coding — use `currency` column from DB |
| W4 | Testing | Write unit tests for 10 critical `queries.py` functions |
| W4 | Docs | Update README (remove nginx, add Caddy); create `RUNBOOK.md` |

---

## 15. 60-Day Roadmap

**Goal:** Expand coverage and analysis capability. CMDB, security posture scoring, KQL improvements.

| Week | Focus | Deliverables |
|------|-------|-------------|
| W5-6 | CMDB | Build `/cmdb` page with resource inventory, tag display, staleness indicator |
| W5-6 | Security | Security risk score; port-specific NSG risk flags (22, 3389, 445, 1433) |
| W7 | Cost | Monthly budget KPI with threshold alert; fix currency display |
| W7 | KQL | Query parameterisation (`@ResourceGroup` substitution); result caching (5 min) |
| W8 | Topology | Layout persistence to localStorage; orphaned NIC indicator |
| W8 | PostgreSQL | Add `backup_status` for PG servers if backup vault data available |

---

## 16. 90-Day Roadmap

**Goal:** Enterprise readiness — compliance, automation, multi-subscription scaling.

| Week | Focus | Deliverables |
|------|-------|-------------|
| W9-10 | Compliance | CIS Azure Benchmark mapping for NSG findings; exported compliance report (CSV) |
| W9-10 | Automation | Scheduled KQL queries; results stored in DB; alert on threshold breach |
| W11 | Portal docs | `/docs` route rendering `docs/*.md`; RBAC guide; onboarding checklist |
| W11 | Scale | Test with >5 concurrent users; evaluate PostgreSQL migration if SQLite bottlenecks |
| W12 | Review & hardening | Full security re-scan; rotate all SP secrets |
| W12 | Roadmap reset | Plan next quarter based on WTS operational feedback |

---

*Generated 2026-06-20. All implementation changes described are committed to the codebase unless explicitly marked as future enhancement.*
