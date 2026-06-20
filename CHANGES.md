# WTS Azure Cloud Manager — Change Log

## 2026-06-20 — KQL Console: fix silent failure (backend crash + frontend UX)

### Root causes found

1. **Backend crash (500)** — `azure_client/logs.py` used `col.name` on each column, but
   `azure-monitor-query` v1.4.1 returns `table.columns` as `List[str]` (not objects).
   Every query silently 500'd. Fixed: `columns = list(table.columns)`.

2. **Frontend silent failure** — `runQuery()` had `if (!query) return;` with no feedback.
   Users who clicked Run with an empty editor saw nothing. Fixed: shows red error panel.

3. **Blitz query click didn't run** — `loadBlitz()` populated the editor but didn't call
   `runQuery()`. Fixed: `loadBlitz` now calls `runQuery()` after setting the value.

4. **No loading state** — Run button wasn't disabled while a query was in-flight.
   Fixed: button disabled + re-enabled in `finally`, with explicit `credentials: 'same-origin'`.

5. **Silent non-JSON / redirect responses** — fetch had no guard against 302→login or
   non-JSON bodies. Fixed: checks `r.ok`, catches JSON parse errors, surfaces both visibly.

### Empirical test results (curl against prod workspace 0f3d10b0-…)

| Test | Before | After |
|---|---|---|
| `AzureActivity \| take 5` | 500 (`col.name` crash) | ✅ JSON rows |
| `notatable \| take 1` | 500 crash | ✅ `{"error": "... SEM0100 ..."}` visible in UI |
| Empty query | Silent return | ✅ Red error panel "Enter a KQL query first." |
| Blitz query click | Populates editor only | ✅ Populates + runs immediately |

## 2026-06-19 — Switched data backend to Azure-Reports-MSGraph (split-credential)

### Summary

The portal now runs a **split-credential model**:

| Role | App registration | Client ID | Purpose |
|---|---|---|---|
| DATA reads | Azure-Reports-MSGraph | `b7728d06-82f8-480c-a743-dc7b4095baf6` | All ARM / Cost / Log Analytics SDK calls |
| SIGN-IN | WTS-Azure-Manager | `03504b3d-50f1-449b-b6f0-f6a9b9c61fca` | MSAL Authorization Code browser login |

### .env structure (no duplicate keys after cleanup)

```
AZURE_CLIENT_ID=b7728d06-…          # DATA SP — Reader + Cost Mgmt Reader + LA Data Reader
AZURE_CLIENT_SECRET=<masked>
AUTH_CLIENT_ID=03504b3d-…           # SIGN-IN app — redirect URI already registered
AUTH_CLIENT_SECRET=<masked>
```

### Code wiring

| File | Variable used | Purpose |
|---|---|---|
| `azure_client/auth.py` | `config.AZURE_CLIENT_ID / SECRET` | `ClientSecretCredential` for all ARM SDK clients |
| `auth/sso.py` | `config.AUTH_CLIENT_ID / SECRET` | `msal.ConfidentialClientApplication` for OIDC login |
| `config.py` | `AUTH_CLIENT_ID` falls back to `AZURE_CLIENT_ID` if unset | backward-compat |

### Required RBAC on subscription `484960a1-…`

| Role | Maps to | Validated |
|---|---|---|
| Reader | Azure Resource Graph inventory | ✓ PASS |
| Cost Management Reader | Cost Management MTD query | ✓ PASS |
| Log Analytics Data Reader | LogsQueryClient KQL on `law-btprod` | ✓ PASS |

---

## Validation results — 2026-06-19

### A. Data-plane tests (Azure-Reports-MSGraph SP `b7728d06-…`)

| Plane | Role | Result | Detail |
|---|---|---|---|
| A. Reader → Resource Graph | Reader | **PASS** | 5 resources returned |
| B. Cost Management | Cost Management Reader | **PASS** | MTD = 2,076.88 EUR |
| C. Log Analytics KQL | Log Analytics Data Reader | **PASS** | workspace `law-btprod`, query ran (0 rows — workspace accessible, sparse AzureActivity data) |

### B. Sign-in / auth tests (WTS-Azure-Manager SP `03504b3d-…`)

| Test | Expected | Result |
|---|---|---|
| `GET /` unauthenticated | 302 → `/login` | **PASS** |
| `GET /auth/login` | 302 → `login.microsoftonline.com` with `client_id=03504b3d-…` and `redirect_uri=…%3A8444…` | **PASS** |
| `GET /vms` unauthenticated | 302 (never 200) | **PASS** |
| `GET /cost` unauthenticated | 302 (never 200) | **PASS** |
| Loopback listener | `127.0.0.1:8050` only | **PASS** |
| O365 app (port 5000/8443) | Still running, not disrupted | **PASS** |

### C. Full sync (status=ok)

| Table | Rows | Notes |
|---|---|---|
| vms | 10 (10 running) | |
| vm_metrics | 4,320 data points across 10 VMs | |
| sql_servers | 1 | |
| sql_databases | 34 | |
| elastic_pools | 1 | |
| advisor_recs | 336 (4 categories) | |
| backup_status | 10 (0 problems) | |
| resource_health | 162 (2 availability states) | |
| cost_daily | 185 rows, €2,076.88 MTD | latest: 2026-06-18 |

### D. View status

| View | Route | Status |
|---|---|---|
| Status dashboard | `/` | **POPULATED** — VM count, cost, advisor summary, health |
| VMs + utilization | `/vms` | **POPULATED** — 10 VMs, CPU metrics per VM |
| SQL / Elastic Pools / DBs | `/sql` | **POPULATED** — 1 server, 34 DBs, 1 pool |
| Cost trend + by-RG | `/cost` | **POPULATED** — 185 daily rows, anomaly detection |
| Advisor | `/advisor` | **POPULATED** — 336 recommendations |
| Resource Health | `/health` | **POPULATED** — 162 records |
| Backup | `/backup` | **POPULATED** — 10 items, 0 problems |
| Disks (full inventory) | n/a | **NOT IMPLEMENTED** — no dedicated disk route/template |
| Network (VNet/NSG/NIC) | n/a | **NOT IMPLEMENTED** |
| Key Vault / Storage | n/a | **NOT IMPLEMENTED** |
| AGW / WAF | n/a | **NOT IMPLEMENTED** |
| VPN / Bastion | n/a | **NOT IMPLEMENTED** |
| Log Analytics KQL console | n/a | **NOT IMPLEMENTED** |
| Activity log explorer | n/a | **NOT IMPLEMENTED** |
| Topology | n/a | **NOT IMPLEMENTED** |
| Security (WAF/NSG/encryption) | n/a | **NOT IMPLEMENTED** |
| Reservations | n/a | **NOT IMPLEMENTED** |
| Retirements | n/a | **NOT IMPLEMENTED** |

Not-implemented views were never part of the app — they are future work, not regressions from this credential change.

### Service restart command

```bash
systemctl --user restart wts-azure-manager
```

(User-scoped service — no `sudo` needed. System service equivalent: `sudo systemctl restart wts-azure-manager` if ever moved to system scope.)
