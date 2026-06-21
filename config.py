import os
from dotenv import load_dotenv

load_dotenv()

AZURE_TENANT_ID = os.environ["AZURE_TENANT_ID"]
AZURE_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
AZURE_CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
AZURE_SUBSCRIPTION_IDS = [
    s.strip()
    for s in os.environ["AZURE_SUBSCRIPTION_IDS"].split(",")
    if s.strip()
]

# Sign-in (MSAL OAuth) — may differ from the data SP above.
# Auth_CLIENT_ID/SECRET use the WTS-Azure-Manager app registration (which already
# has the redirect URI); AZURE_CLIENT_ID/SECRET are for ARM/Cost/Logs reads only.
AUTH_CLIENT_ID     = os.environ.get("AUTH_CLIENT_ID",     os.environ["AZURE_CLIENT_ID"])
AUTH_CLIENT_SECRET = os.environ.get("AUTH_CLIENT_SECRET", os.environ["AZURE_CLIENT_SECRET"])

APP_PORT = int(os.environ.get("APP_PORT", 8050))
SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", 60))
FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]

# ── Entra ID sign-in ──────────────────────────────────────────────────────────
AUTHORITY = os.environ.get(
    "AUTHORITY",
    f"https://login.microsoftonline.com/{os.environ.get('AZURE_TENANT_ID', '')}",
)
AUTH_REDIRECT_PATH = os.environ.get("AUTH_REDIRECT_PATH", "/auth/callback")
ALLOWED_GROUP_ID = os.environ.get("ALLOWED_GROUP_ID", "")

DB_PATH = os.path.join(os.path.dirname(__file__), "cache.sqlite")

# ── Status-light thresholds (all configurable here) ──────────────────────────
CPU_RED_PCT = 85
CPU_AMBER_PCT = 70
MEM_FREE_RED_GB = 0.5
MEM_FREE_AMBER_GB = 1.0

COST_ANOMALY_MULTIPLIER = 1.5   # flag day if spend > N × recent daily average

BACKUP_STALE_HOURS = 26         # flag VM if last successful backup older than this

# ── Session ──────────────────────────────────────────────────────────────────
SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", 480))  # 8 hours

# ── Log Analytics KQL console ─────────────────────────────────────────────────
LOG_ANALYTICS_WORKSPACE_ID = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID", "")

# Multi-workspace: "Display Name:workspace-id,Name2:id2"
# If set, overrides the single LOG_ANALYTICS_WORKSPACE_ID for the workspace picker.
_ws_raw = os.environ.get("LOG_ANALYTICS_WORKSPACE_IDS", "")
LOG_ANALYTICS_WORKSPACES: list[dict] = []
if _ws_raw:
    for _entry in _ws_raw.split(","):
        _entry = _entry.strip()
        if ":" in _entry:
            _name, _wid = _entry.split(":", 1)
            LOG_ANALYTICS_WORKSPACES.append({"name": _name.strip(), "id": _wid.strip()})
if not LOG_ANALYTICS_WORKSPACES and LOG_ANALYTICS_WORKSPACE_ID:
    LOG_ANALYTICS_WORKSPACES = [{"name": "Default", "id": LOG_ANALYTICS_WORKSPACE_ID}]

# ── Sync rate limit ───────────────────────────────────────────────────────────
MIN_SYNC_INTERVAL_MINUTES = int(os.environ.get("MIN_SYNC_INTERVAL_MINUTES", 5))

# ── Cost budget ───────────────────────────────────────────────────────────────
# Set MONTHLY_BUDGET_EUR=5000 in .env to enable the budget KPI on the Cost page.
MONTHLY_BUDGET = float(os.environ.get("MONTHLY_BUDGET_EUR", 0))

# ── Azure DevOps ──────────────────────────────────────────────────────────────
# Set AZURE_DEVOPS_ORG=myorg and AZURE_DEVOPS_PAT=<personal-access-token> in .env
# to enable the DevOps Center. The PAT needs: Build (Read), Release (Read),
# Code (Read), Project and Team (Read).
AZURE_DEVOPS_ORG = os.environ.get("AZURE_DEVOPS_ORG", "")
AZURE_DEVOPS_PAT = os.environ.get("AZURE_DEVOPS_PAT", "")
