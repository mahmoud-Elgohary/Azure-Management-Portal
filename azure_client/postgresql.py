"""
PostgreSQL Flexible Servers — read-only.
Requires azure-mgmt-postgresqlflexibleservers.
Falls back gracefully if the SDK is not installed.
"""

import json
from azure_client.auth import get_credential, subscription_ids


def fetch_postgresql_servers(sub_id: str) -> list[dict]:
    try:
        from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient
    except ImportError:
        return [{"_error": "azure-mgmt-rdbms not installed — run: pip install azure-mgmt-rdbms"}]

    client = PostgreSQLManagementClient(get_credential(), sub_id)
    result = []
    try:
        for server in client.servers.list():
            rg = server.id.split("/")[4] if server.id else ""
            result.append({
                "server_id": server.id,
                "name": server.name,
                "resource_group": rg,
                "subscription_id": sub_id,
                "location": server.location,
                "version": str(server.version) if server.version else None,
                "state": str(server.state) if server.state else None,
                "admin_login": server.administrator_login,
                "storage_gb": server.storage.storage_size_gb if server.storage else None,
                "sku_name": server.sku.name if server.sku else None,
                "tags": json.dumps(server.tags or {}),
            })
    except Exception as exc:
        result.append({"_error": str(exc), "subscription_id": sub_id})
    return result


def fetch_all_postgresql() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        rows.extend(fetch_postgresql_servers(sub))
    return rows
