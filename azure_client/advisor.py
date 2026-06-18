"""
Azure Advisor recommendations — read-only, grouped by category.
"""

from azure.mgmt.advisor import AdvisorManagementClient

from azure_client.auth import get_credential, subscription_ids


def _client(sub_id: str) -> AdvisorManagementClient:
    return AdvisorManagementClient(get_credential(), sub_id)


def fetch_recommendations(sub_id: str) -> list[dict]:
    client = _client(sub_id)
    result = []
    try:
        for rec in client.recommendations.list():
            result.append(
                {
                    "rec_id": rec.id,
                    "subscription_id": sub_id,
                    "category": rec.category,
                    "impact": rec.impact,
                    "resource_id": rec.resource_metadata.resource_id if rec.resource_metadata else None,
                    "short_description": (
                        rec.short_description.problem if rec.short_description else None
                    ),
                    "solution": (
                        rec.short_description.solution if rec.short_description else None
                    ),
                    "last_updated": rec.last_updated.isoformat() if rec.last_updated else None,
                }
            )
    except Exception as exc:
        result.append({"_error": str(exc), "subscription_id": sub_id})
    return result


def fetch_all_recommendations() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        rows.extend(fetch_recommendations(sub))
    return rows
