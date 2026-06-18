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

APP_PORT = int(os.environ.get("APP_PORT", 8050))
SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", 60))
FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]

DB_PATH = os.path.join(os.path.dirname(__file__), "cache.sqlite")

# ── Status-light thresholds (all configurable here) ──────────────────────────
CPU_RED_PCT = 85
CPU_AMBER_PCT = 70
MEM_FREE_RED_GB = 0.5
MEM_FREE_AMBER_GB = 1.0

COST_ANOMALY_MULTIPLIER = 1.5   # flag day if spend > N × recent daily average

BACKUP_STALE_HOURS = 26         # flag VM if last successful backup older than this
