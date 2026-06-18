"""
Azure Resource Graph queries for cross-subscription inventory.
All functions return plain dicts; no DB writes happen here.
"""

from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

from azure_client.auth import get_credential, subscription_ids


def _client() -> ResourceGraphClient:
    return ResourceGraphClient(get_credential())


def _query(kql: str, subs: list[str] | None = None) -> list[dict]:
    subs = subs or subscription_ids()
    client = _client()
    req = QueryRequest(
        subscriptions=subs,
        query=kql,
        options=QueryRequestOptions(result_format="objectArray"),
    )
    result = client.resources(req)
    return result.data or []


def fetch_inventory() -> list[dict]:
    """Return all VMs, disks, SQL servers, NICs, and public IPs across subscriptions."""
    kql = """
    Resources
    | where type in (
        'microsoft.compute/virtualmachines',
        'microsoft.compute/disks',
        'microsoft.sql/servers',
        'microsoft.network/networkinterfaces',
        'microsoft.network/publicipaddresses'
      )
    | project
        id,
        name,
        type,
        resourceGroup,
        location,
        subscriptionId,
        tags
    | order by type asc, name asc
    """
    return _query(kql)


def fetch_vms_basic() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.compute/virtualmachines'
    | project
        id,
        name,
        resourceGroup,
        location,
        subscriptionId,
        tags,
        vmSize = properties.hardwareProfile.vmSize,
        osType  = properties.storageProfile.osDisk.osType
    | order by name asc
    """
    return _query(kql)


def fetch_public_ips() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/publicipaddresses'
    | project
        id,
        name,
        resourceGroup,
        subscriptionId,
        ipAddress   = properties.ipAddress,
        allocationMethod = properties.publicIPAllocationMethod
    """
    return _query(kql)
