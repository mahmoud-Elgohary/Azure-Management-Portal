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


def fetch_reservations() -> list[dict]:
    """Fetch Azure reservation details via the reservationresources table."""
    kql = """
    reservationresources
    | where type == "microsoft.capacity/reservationorders/reservations"
    | extend props = properties
    | project
        reservation_id  = id,
        name,
        sku_name        = tostring(sku.name),
        location,
        quantity        = toint(props.quantity),
        term            = tostring(props.term),
        scope_type      = tostring(props.appliedScopeType),
        scope           = tostring(coalesce(props.appliedScopes[0], '')),
        state           = tostring(props.provisioningState),
        expiry_date     = tostring(props.expiryDate),
        purchase_date   = tostring(props.purchaseDate),
        utilization_pct = todouble(props.utilization.aggregates[0].value),
        order_id        = tostring(split(id, '/')[8]),
        subscription_id = subscriptionId
    | order by state asc, expiry_date asc
    """
    try:
        return _query(kql)
    except Exception:
        return []


def fetch_generic_resources() -> list[dict]:
    """Fetch Storage Accounts, Key Vaults, VPN Gateways, Bastions, Log Analytics, and Container Registries."""
    kql = """
    Resources
    | where type in (
        'microsoft.storage/storageaccounts',
        'microsoft.keyvault/vaults',
        'microsoft.network/virtualnetworkgateways',
        'microsoft.network/bastionhosts',
        'microsoft.operationalinsights/workspaces',
        'microsoft.containerregistry/registries',
        'microsoft.web/sites',
        'microsoft.containerservice/managedclusters'
      )
    | project
        resource_id   = id,
        name,
        type,
        resource_group = resourceGroup,
        subscription_id = subscriptionId,
        location,
        tags          = tostring(tags),
        kind,
        sku           = tostring(sku)
    | order by type asc, name asc
    """
    return _query(kql)
