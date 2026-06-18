"""
Azure Resource Health — availability status per resource.
"""

from azure.mgmt.resourcehealth import ResourceHealthMgmtClient

from azure_client.auth import get_credential, subscription_ids


def _client(sub_id: str) -> ResourceHealthMgmtClient:
    return ResourceHealthMgmtClient(get_credential(), sub_id)


def fetch_resource_health(sub_id: str) -> list[dict]:
    client = _client(sub_id)
    result = []
    try:
        for item in client.availability_statuses.list_by_subscription_id():
            props = item.properties
            result.append(
                {
                    "resource_id": item.id,
                    "subscription_id": sub_id,
                    "location": item.location,
                    "availability_state": props.availability_state if props else None,
                    "summary": props.summary if props else None,
                    "reason_type": props.reason_type if props else None,
                    "occured_time": (
                        props.occured_time.isoformat()
                        if props and props.occured_time
                        else None
                    ),
                }
            )
    except Exception as exc:
        result.append({"_error": str(exc), "subscription_id": sub_id})
    return result


def fetch_all_resource_health() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        rows.extend(fetch_resource_health(sub))
    return rows
