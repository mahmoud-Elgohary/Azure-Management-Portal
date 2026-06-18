"""
SQL servers, databases, and elastic pools — read-only.
"""

from azure.mgmt.sql import SqlManagementClient

from azure_client.auth import get_credential, subscription_ids


def _client(sub_id: str) -> SqlManagementClient:
    return SqlManagementClient(get_credential(), sub_id)


def fetch_sql_servers(sub_id: str) -> list[dict]:
    client = _client(sub_id)
    result = []
    for server in client.servers.list():
        rg = server.id.split("/")[4]
        result.append(
            {
                "server_id": server.id,
                "name": server.name,
                "resource_group": rg,
                "subscription_id": sub_id,
                "location": server.location,
                "admin_login": server.administrator_login,
                "state": server.state,
                "fqdn": server.fully_qualified_domain_name,
                "tags": str(server.tags or {}),
            }
        )
    return result


def fetch_databases(sub_id: str, server_name: str, resource_group: str) -> list[dict]:
    client = _client(sub_id)
    result = []
    for db in client.databases.list_by_server(resource_group, server_name):
        result.append(
            {
                "db_id": db.id,
                "name": db.name,
                "server_name": server_name,
                "resource_group": resource_group,
                "subscription_id": sub_id,
                "location": db.location,
                "status": db.status,
                "elastic_pool_id": db.elastic_pool_id,
                "edition": db.edition if hasattr(db, "edition") else None,
                "tags": str(db.tags or {}),
            }
        )
    return result


def fetch_elastic_pools(sub_id: str, server_name: str, resource_group: str) -> list[dict]:
    client = _client(sub_id)
    result = []
    for pool in client.elastic_pools.list_by_server(resource_group, server_name):
        result.append(
            {
                "pool_id": pool.id,
                "name": pool.name,
                "server_name": server_name,
                "resource_group": resource_group,
                "subscription_id": sub_id,
                "location": pool.location,
                "state": pool.state,
                "edition": pool.edition if hasattr(pool, "edition") else None,
                "capacity": pool.sku.capacity if pool.sku else None,
                "sku_name": pool.sku.name if pool.sku else None,
                "tags": str(pool.tags or {}),
            }
        )
    return result


def fetch_all_sql() -> dict:
    """Return {'servers': [...], 'databases': [...], 'elastic_pools': [...]}."""
    servers, databases, pools = [], [], []
    for sub in subscription_ids():
        try:
            sub_servers = fetch_sql_servers(sub)
            servers.extend(sub_servers)
            for s in sub_servers:
                if "_error" not in s:
                    databases.extend(
                        fetch_databases(sub, s["name"], s["resource_group"])
                    )
                    pools.extend(
                        fetch_elastic_pools(sub, s["name"], s["resource_group"])
                    )
        except Exception as exc:
            servers.append({"_error": str(exc), "subscription_id": sub})
    return {"servers": servers, "databases": databases, "elastic_pools": pools}
