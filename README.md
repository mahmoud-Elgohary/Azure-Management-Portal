# WTS Azure Cloud Manager

Read-only monitoring portal for the WTS tenant — VMs, SQL/Elastic Pools, Cost, Advisor, Backup, Resource Health.

> **Port**: 8050 (default). The Evolvice O365 app uses port 5000. Verify no conflict:
> ```bash
> ss -tlnp | grep 8050
> ```

## Setup

### 1. Create virtual environment and install dependencies

```bash
cd /home/claude-code/wts-azure-manager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials (interactive wizard)

```bash
python setup_env.py
```

The wizard prompts for:
- `AZURE_TENANT_ID` — WTS Directory/tenant ID (GUID)
- `AZURE_CLIENT_ID` — WTS-Azure-Manager app registration client ID (GUID)
- `AZURE_CLIENT_SECRET` — client secret (hidden input)
- `AZURE_SUBSCRIPTION_IDS` — comma-separated WTS subscription GUIDs
- `APP_PORT` — default 8050
- `SYNC_INTERVAL_MINUTES` — default 60

It test-connects to Azure Resource Graph before writing `.env`. If auth fails, it reports
whether the issue is a bad secret (AADSTS error) or missing RBAC (AuthorizationFailed).

**Required RBAC roles on each WTS subscription:**
- Reader
- Cost Management Reader

RBAC assignments can take a few minutes to propagate after creation.

### 3. First sync

```bash
python -m sync.sync_job
```

This populates the SQLite cache. All dashboard pages read from the cache; Azure is never
queried on page load.

### 4. Start (development)

```bash
python app.py
```

Access at `http://<vm-ip>:8050`

### 5. Start (production — systemd)

```bash
sudo cp deploy/wts-azure-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wts-azure-manager
sudo systemctl status wts-azure-manager
```

### Optional: nginx reverse proxy

To serve both apps on one hostname via different paths:

```nginx
server {
    listen 443 ssl;
    server_name your-vm-hostname;

    location /wts/ {
        proxy_pass http://127.0.0.1:8050/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /o365/ {
        proxy_pass http://127.0.0.1:5000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Architecture notes

- Routes **never** call Azure SDKs directly — only `models/queries.py` (cache reads).
- Azure SDKs are called exclusively from `azure_client/` modules and the sync job.
- `config.py` holds all threshold constants (CPU red/amber, cost anomaly multiplier,
  backup staleness hours) — tune without touching application logic.
- The "Sync Now" button runs the sync in a background thread to avoid blocking the request.
- Data freshness is displayed on every page ("last synced X min ago").

## File layout

```
wts-azure-manager/
├── app.py                  Flask entry point (binds to APP_PORT)
├── config.py               Env loader + threshold constants
├── setup_env.py            Interactive .env wizard with live Azure validation
├── requirements.txt
├── .env.example
├── .gitignore
├── azure_client/
│   ├── auth.py             ClientSecretCredential factory
│   ├── resource_graph.py   KQL inventory queries
│   ├── compute.py          VM list + power state
│   ├── sql.py              SQL servers, databases, elastic pools
│   ├── monitor.py          Azure Monitor VM metrics
│   ├── advisor.py          Advisor recommendations
│   ├── backup.py           Recovery Services backup status
│   ├── health.py           Resource Health
│   └── cost.py             Cost Management + anomaly detection
├── sync/
│   └── sync_job.py         Azure → SQLite sync
├── models/
│   ├── db.py               SQLite connection + schema
│   └── queries.py          Read-only data-access layer
├── templates/              Jinja2 HTML (base + one per page)
├── static/css/style.css
└── deploy/
    └── wts-azure-manager.service   systemd unit
```
