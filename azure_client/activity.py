"""
Azure Activity Log via Log Analytics KQL.
Requires LOG_ANALYTICS_WORKSPACE_ID to be configured.
Falls back to empty list gracefully if not configured.
"""

import config
from azure_client.logs import run_kql


def fetch_activity_log(days: int = 7) -> list[dict]:
    """
    Fetch recent Azure activity log from Log Analytics.
    Returns list of event dicts ready for DB insertion.
    """
    workspace_id = getattr(config, "LOG_ANALYTICS_WORKSPACE_ID", "")
    if not workspace_id:
        workspaces = getattr(config, "LOG_ANALYTICS_WORKSPACES", [])
        if workspaces:
            workspace_id = workspaces[0]["id"]
    if not workspace_id:
        return []

    kql = f"""
AzureActivity
| where TimeGenerated >= ago({days}d)
| where ActivityStatusValue in ('Failed', 'Failure', 'Success', 'Start', 'Accept')
| project
    event_id        = CorrelationId,
    caller          = Caller,
    operation_name  = OperationNameValue,
    resource_type   = ResourceProviderValue,
    resource_group  = ResourceGroup,
    resource_id     = ResourceId,
    status          = ActivityStatusValue,
    sub_status      = ActivitySubstatusValue,
    event_timestamp = TimeGenerated,
    description     = Properties_d,
    subscription_id = SubscriptionId
| order by event_timestamp desc
| limit 500
"""

    try:
        result = run_kql(workspace_id, kql, timeout_seconds=30, max_rows=500)
        if "error" in result or not result.get("rows"):
            return []

        columns = result["columns"]
        rows = result["rows"]
        out = []
        for row in rows:
            d = dict(zip(columns, row))
            out.append({
                "event_id": str(d.get("event_id") or "")[:200],
                "caller": str(d.get("caller") or "")[:200],
                "operation_name": str(d.get("operation_name") or "")[:300],
                "resource_type": str(d.get("resource_type") or "")[:200],
                "resource_group": str(d.get("resource_group") or "")[:200],
                "resource_id": str(d.get("resource_id") or "")[:500],
                "status": str(d.get("status") or "")[:50],
                "sub_status": str(d.get("sub_status") or "")[:100],
                "event_timestamp": str(d.get("event_timestamp") or "")[:50],
                "description": str(d.get("description") or "")[:1000],
                "subscription_id": str(d.get("subscription_id") or "")[:100],
            })
        return out
    except Exception as exc:
        return [{"_error": str(exc)}]
