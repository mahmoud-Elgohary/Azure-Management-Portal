# WTS Azure Cloud Manager — Security & Quality Review
**Date:** 2026-06-18  
**Reviewer:** Claude Code (automated + manual audit)  
**App version:** as-built, last sync 2026-06-16

---

## Summary Table

| # | Severity | Area | Finding |
|---|----------|------|---------|
| 1 | **Critical** | Security | No portal authentication — app open to anyone with loopback/tunnel access |
| 2 | **High** | Deployment | Running as Flask dev server (`python app.py`), not gunicorn |
| 3 | **High** | Deployment | No reverse proxy active — nginx not installed, Caddy ignores port 8050 |
| 4 | **High** | Security | `FLASK_SECRET_KEY` falls back to hardcoded insecure default if env var missing |
| 5 | **Medium** | Deployment | No background sync scheduler — `SYNC_INTERVAL_MINUTES` is never acted on |
| 6 | **Medium** | Security | No CSRF protection on `POST /sync` |
| 7 | **Medium** | Data | `resource_graph` module imported but never called — dead code, and its queries have no pagination |
| 8 | **Medium** | Data | Cost query has no pagination guard — silently truncates large result sets |
| 9 | **Medium** | Code | `vm_power_summary()` called twice in dashboard route |
| 10 | **Low** | Code | Memory thresholds (`MEM_FREE_RED/AMBER_GB`) defined in config but used nowhere |
| 11 | **Low** | Azure | N+1 API calls for VM power state (one `instance_view` per VM) |
| 12 | **Low** | Docs | README describes nginx setup that isn't installed; doesn't mention Caddy |
| 13 | **Low** | Config | `AZURE_CLIENT_ID` in `.env` differs from WTS-Azure-Reports ID in prior audit |
| 14 | **Info** | Completeness | `alerts`, `retirements`, `postgres`, `identity` modules not present |

---

## 1. CRITICAL — No Portal Authentication

**File:** `app.py` (all routes)  
**What's wrong:** Every Flask route (`/`, `/vms`, `/sql`, `/cost`, `/advisor`, `/health`, `/backup`, `/sync`) requires zero authentication. No login, no session check, no IP allowlist.

**Current exposure:** The app binds to `127.0.0.1:8050` (loopback only — confirmed via `ss`). That means it's **not** directly reachable over the LAN right now, because no reverse proxy is forwarding to it (nginx isn't installed; Caddy only covers port 5000). So today's blast radius is limited to SSH access to the VM.

**Why it still matters:** The moment a reverse proxy is wired up to serve this externally (the intended goal), every page becomes public. That means Azure resource inventory, cost figures, SQL server names, backup status, and Advisor recommendations — all of it visible without any credential.

**Options (your decision, not auto-applied):**

| Option | Effort | Trade-off |
|--------|--------|-----------|
| **nginx basic-auth** | ~15 min | Easiest; credentials in a `.htpasswd` file; no app changes; nginx would need to be installed first |
| **Caddy basic-auth directive** | ~5 min | Caddy is already running; just add `basicauth` block in `Caddyfile` with a bcrypt-hashed password; WTS path becomes `/wts/` or similar |
| **Flask session/login** | ~2 hours | Better UX, but adds a dependency (Flask-Login) and a user store |
| **Entra ID SSO** | ~4 hours | Strongest; same pattern as the Evolvice M365 portal; requires an app registration |

**Recommendation:** Caddy basic-auth is the fastest safe interim (already running, no new installs). Let me know and I'll prep the one-liner.

---

## 2. HIGH — Running as Flask Dev Server

**File:** `deploy/wts-azure-manager.service`, process list  
**What's wrong:** The systemd service has never been installed. The app is running as:
```
.venv/bin/python app.py
```
Flask's built-in dev server is single-threaded, not designed for production, and will die on VM restart.

**Fix (requires your go-ahead):**
```bash
sudo cp deploy/wts-azure-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wts-azure-manager
```
Then kill the current `python app.py` process. The service is already correct: `User=claude-code`, `Restart=on-failure`, 2 workers, loopback bind.

---

## 3. HIGH — No Reverse Proxy Active

**File:** `deploy/nginx-wts-azure-manager.conf`  
**What's wrong:** nginx is not installed on this VM (`which nginx` → nothing). The deploy config is present but has never been applied. The Caddy instance (confirmed running, serving `192.168.1.115:8443`) only proxies to port 5000 (the Evolvice M365 app). Port 8050 has no TLS termination, no external exposure.

**Result:** The WTS app is currently only accessible via SSH tunnel to the VM (`ssh -L 8050:127.0.0.1:8050`). That means:
- No one can reach it externally yet — net-positive for now
- TLS cert listed in nginx config (`/etc/ssl/certs/wts-azure-manager.crt`) doesn't exist

**Fix options:** (a) Install nginx and apply the cert, or (b) add a `route` block to the existing Caddyfile to proxy `/wts/*` → `127.0.0.1:8050` with `basicauth`. Option (b) is simpler given Caddy is already running.

---

## 4. HIGH — Insecure FLASK_SECRET_KEY Fallback

**File:** `config.py:17`  
**What's wrong:**
```python
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-key-change-me")
```
If the env var is ever missing (e.g., after an accidental `.env` edit like the Defender credential bug from prior session), Flask silently uses the hardcoded string. Anyone who knows the key can forge session cookies.

**The .env currently has a good 64-char random key**, so this isn't actively exploitable today. But the silent fallback is the danger.

**Fix applied (safe — raises loud error instead of silent fallback):**
```python
FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]   # KeyError on startup if missing
```
(Applied — see fixes section below.)

---

## 5. MEDIUM — No Background Sync Scheduler

**File:** `config.py:18`, `app.py`  
**What's wrong:** `SYNC_INTERVAL_MINUTES = 60` is read from env but never acted on. There's no `APScheduler`, `threading.Timer`, or cron job wiring in `app.py`. The only sync path is the manual `POST /sync` button. Last sync was 46 hours ago (2026-06-16 11:09 UTC).

**Fix (requires your decision):** Add a background thread in `app.py` that calls `run_sync()` every `SYNC_INTERVAL_MINUTES`. Or set up a Linux cron job:
```cron
0 * * * * /home/claude-code/wts-azure-manager/.venv/bin/python -m sync.sync_job >> /home/claude-code/wts-azure-manager/sync.log 2>&1
```

---

## 6. MEDIUM — No CSRF Protection on POST /sync

**File:** `app.py:194`, `templates/base.html:28`  
**What's wrong:** The "Sync Now" form is a bare POST with no CSRF token:
```html
<form action="/sync" method="post">
  <button type="submit">Sync Now</button>
</form>
```
A malicious page could silently trigger a sync (e.g., via an `<img src="...">` or fetch) if the victim's browser can reach port 8050. Low impact today (sync is read-only), but the surface grows once a proxy is added.

**Fix:** Add Flask-WTF CSRF protection, or generate and verify a session-tied nonce in the sync form. Not applied — requires your decision on whether Flask-WTF is acceptable.

---

## 7. MEDIUM — resource_graph Module: Imported, Unused, No Pagination

**File:** `sync/sync_job.py:22`, `azure_client/resource_graph.py:16-25`  

**Part A — Dead import:** `resource_graph` is imported in `sync_job.py` line 22 but none of its functions are ever called in the sync loop or anywhere else. The module has three useful functions (`fetch_inventory`, `fetch_vms_basic`, `fetch_public_ips`) that would give cross-subscription inventory views — but they're wired to nothing.

**Part B — No pagination:** The `_query()` helper in `resource_graph.py` doesn't handle `$skipToken` pagination. Azure Resource Graph returns a maximum of 1000 rows per call; if results exceed that, `result.data` is silently truncated and `result.skip_token` is set. For WTS with 10 VMs this isn't a current problem, but it's a correctness gap.

**Fix for Part B (not auto-applied — needs the module to be used first):**
```python
def _query(kql: str, subs: list[str] | None = None) -> list[dict]:
    subs = subs or subscription_ids()
    client = _client()
    all_rows = []
    skip_token = None
    while True:
        req = QueryRequest(
            subscriptions=subs,
            query=kql,
            options=QueryRequestOptions(result_format="objectArray", skip_token=skip_token),
        )
        result = client.resources(req)
        all_rows.extend(result.data or [])
        if not result.skip_token:
            break
        skip_token = result.skip_token
    return all_rows
```

---

## 8. MEDIUM — Cost Query: No Pagination

**File:** `azure_client/cost.py:50-63`  
**What's wrong:** `client.query.usage()` can return a `next_link` for large result sets. The current code reads only `result.rows` and stops, potentially missing rows. For WTS (1 subscription, 10 VMs, 34 DBs) this is unlikely to hit the limit, but it's a correctness gap for any larger tenant.

---

## 9. MEDIUM — vm_power_summary() Called Twice

**File:** `app.py:41-42`  
**What's wrong:**
```python
"vm_power": queries.vm_power_summary(),
"vm_total": sum(queries.vm_power_summary().values()),
```
Two separate SQLite queries when one suffices. 

**Fix applied:**
```python
vm_power = queries.vm_power_summary()
ctx = {
    "vm_power": vm_power,
    "vm_total": sum(vm_power.values()),
    ...
}
```
(Applied — see fixes section.)

---

## 10. LOW — Memory Thresholds Defined, Never Used

**File:** `config.py:23-24`  
**What's wrong:**
```python
MEM_FREE_RED_GB = 0.5
MEM_FREE_AMBER_GB = 1.0
```
These thresholds exist in config, but no function in `models/queries.py` or any template uses them. `monitor.py` does fetch `Available Memory Bytes` metric data (stored in `vm_metrics`), but there's no `mem_status()` function analogous to `cpu_status()`.

**Fix (not auto-applied):** Add a `mem_status(bytes_free)` function to `queries.py` and surface memory status alongside CPU on the VMs page.

---

## 11. LOW — N+1 API Calls for VM Power State

**File:** `azure_client/compute.py:20-29`  
**What's wrong:** For each VM returned by `list_all()`, the code makes a separate `instance_view()` call to get power state. With 10 VMs, that's 10 extra ARM calls every sync. This works but is slow and could hit throttling on larger subscriptions.

**Better approach:** Use a Resource Graph query that includes `instanceDetails` to get power state in one batched call. The existing `resource_graph.py` is already set up for this.

---

## 12. LOW — README Inaccurate

**File:** `README.md`  
**What's wrong:**
- README says "serve via nginx" with a config that doesn't apply because nginx isn't installed
- README doesn't mention Caddy (the actual reverse proxy in use)
- README Step 4 says "Access at `http://<vm-ip>:8050`" — should be localhost only, not the VM IP  
- README doesn't mention the self-signed cert acceptance step needed for any browser access

---

## 13. LOW — AZURE_CLIENT_ID Discrepancy

**File:** `.env`  
**What's wrong:** The Client ID in `.env` (`22737d02-0889-4c9f-8c51-77e589823c0b`) is different from the WTS-Azure-Reports app registration recorded in memory (`169bc0d4-343e-4501-8495-d5c315d32151`). This is likely a new dedicated app registration created for the Cloud Manager (which is correct practice). Confirm in Entra that this app registration exists, has Reader + Cost Management Reader roles, and the secret isn't expired.

---

## 14. INFO — Missing Modules from Original Spec

The audit prompt mentioned `alerts`, `retirements`, `postgres`, and `identity` modules. None exist. The running app covers: `compute`, `sql`, `advisor`, `backup`, `health`, `cost`, `monitor`, `resource_graph`. Pages for alerts and retirements are also absent. These were likely not in scope for this build — just flagging for completeness.

---

## Security Snapshot (what's clean)

| Check | Result |
|-------|--------|
| App binds to loopback only (127.0.0.1:8050) | ✅ Confirmed |
| `debug=False` in app.py | ✅ Confirmed |
| 404 page has no debug/traceback output | ✅ Confirmed |
| `.env` excluded from `.gitignore` | ✅ Confirmed |
| No git repo → no git history secrets | ✅ No git history |
| `AZURE_CLIENT_SECRET` only in `.env`, not in code/templates | ✅ Confirmed |
| `FLASK_SECRET_KEY` is 64-char random hex (post-fix: required, no fallback) | ✅ Good |
| `ProxyFix(x_for=1, x_proto=1, x_host=1)` — trusts exactly 1 hop | ✅ Correct |
| All SQLite queries use `?` parameterization (no string interpolation) | ✅ Confirmed |
| Resource Graph KQL is hardcoded, not built from user input | ✅ Confirmed |
| `tag_filter` uses LIKE with `?` placeholder (safe) | ✅ Confirmed |
| Routes never call Azure SDK directly | ✅ Confirmed |
| `ClientSecretCredential` created once and reused (module-level singleton) | ✅ Confirmed |
| Sync job: per-module error isolation (one failure doesn't abort all) | ✅ Confirmed |
| `RBAC error` messages are actionable (name the role + scope) | ✅ Confirmed |
| `pip-audit` — no known CVEs in dependencies | ✅ Clean |

---

## Fixes Applied in This Review

### Fix A — `config.py`: Remove insecure FLASK_SECRET_KEY fallback
**Before:** `FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-key-change-me")`  
**After:** `FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]` — raises `KeyError` on startup if missing, forcing explicit resolution rather than silent degradation.

### Fix B — `app.py`: Eliminate double vm_power_summary() call  
**Before:** Two separate calls to `queries.vm_power_summary()` in the dashboard context dict.  
**After:** Computed once, passed as two dict keys (`vm_power`, `vm_total`).

---

## Decisions Needed from You

| Decision | Options | My recommendation |
|----------|---------|-------------------|
| **Portal auth** | Caddy basicauth / nginx basicauth / Flask-Login / Entra SSO | Caddy basicauth (fastest; 5 min; already running) |
| **Production deployment** | Install systemd service + kill dev server | Yes — do this as soon as auth is in place |
| **Reverse proxy** | Wire Caddy to expose WTS at `/wts/` on port 8443 | Yes, alongside Caddy basicauth |
| **Auto-sync** | Add cron job or APScheduler thread | Cron job is simpler; APScheduler is tidier |
| **CSRF on /sync** | Add Flask-WTF, or live with it given auth will protect the route | Add Flask-WTF once auth is in |
