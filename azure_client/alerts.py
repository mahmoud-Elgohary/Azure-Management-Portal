"""
Azure Monitor alerts — fired alert instances.
Requires 'Monitoring Reader' role on subscription.
"""

import requests
from azure_client.auth import get_credential, subscription_ids


def _get_token() -> str:
    cred = get_credential()
    token = cred.get_token("https://management.azure.com/.default")
    return token.token


def fetch_alerts_for_sub(sub_id: str) -> list[dict]:
    """
    Fetch fired alert instances from Azure Monitor Alerts Management REST API.
    Falls back gracefully if the subscription lacks Monitoring Reader role.
    """
    url = f"https://management.azure.com/subscriptions/{sub_id}/providers/Microsoft.AlertsManagement/alerts"
    params = {
        "api-version": "2019-03-01",
        "timeRange": "7d",
        "pageCount": 250,
        # no alertState filter → returns all (New, Acknowledged, Closed)
    }
    headers = {"Authorization": f"Bearer {_get_token()}"}
    results = []

    while url:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            params = {}  # only first request uses query params; next_link has them embedded
            if r.status_code == 403:
                return [{"_error": f"Permission denied (403) on {sub_id} — grant Monitoring Reader role"}]
            if r.status_code != 200:
                return [{"_error": f"HTTP {r.status_code}: {r.text[:200]}"}]
            data = r.json()
            for item in data.get("value", []):
                props = item.get("properties", {})
                essentials = props.get("essentials", {})
                results.append({
                    "alert_id": item.get("id", ""),
                    "subscription_id": sub_id,
                    "severity": essentials.get("severity", ""),
                    "alert_rule": essentials.get("alertRule", ""),
                    "target_resource": essentials.get("targetResource", ""),
                    "target_resource_name": essentials.get("targetResourceName", ""),
                    "monitor_condition": essentials.get("monitorCondition", ""),
                    "description": props.get("context", {}).get("description", "")[:500],
                    "fired_time": essentials.get("startDateTime", ""),
                    "resolved_time": essentials.get("lastModifiedDateTime", ""),
                })
            url = data.get("nextLink")
        except Exception as exc:
            return [{"_error": str(exc)}]

    return results


def fetch_all_alerts() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        rows.extend(fetch_alerts_for_sub(sub))
    return rows
