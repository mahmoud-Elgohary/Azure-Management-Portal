"""
Network topology data — VNets, subnets, NICs, public IPs, NSGs.
Uses Resource Graph for batch cross-subscription queries.
"""

import time
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

from azure_client.auth import get_credential, subscription_ids


def _rg_client() -> ResourceGraphClient:
    return ResourceGraphClient(get_credential())


def _query(kql: str, subs: list[str] | None = None) -> list[dict]:
    subs = subs or subscription_ids()
    client = _rg_client()
    all_rows: list[dict] = []
    skip_token = None
    while True:
        req = QueryRequest(
            subscriptions=subs,
            query=kql,
            options=QueryRequestOptions(result_format="objectArray", skip_token=skip_token),
        )
        try:
            result = client.resources(req)
        except Exception as exc:
            if "429" in str(exc) or "TooManyRequests" in str(exc):
                time.sleep(10)
                result = client.resources(req)
            else:
                raise
        all_rows.extend(result.data or [])
        if not result.skip_token:
            break
        skip_token = result.skip_token
    return all_rows


def fetch_vnets() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/virtualnetworks'
    | project
        vnet_id        = id,
        name,
        resource_group = resourceGroup,
        subscription_id = subscriptionId,
        location,
        address_space  = tostring(properties.addressSpace.addressPrefixes)
    | order by name asc
    """
    rows = _query(kql)
    return [{
        "vnet_id": r.get("vnet_id", ""),
        "name": r.get("name", ""),
        "resource_group": r.get("resource_group", ""),
        "subscription_id": r.get("subscription_id", ""),
        "location": r.get("location", ""),
        "address_space": str(r.get("address_space", "")),
    } for r in rows]


def fetch_subnets(vnet_rows: list[dict]) -> list[dict]:
    """
    Subnets are embedded in VNet properties.
    We query VNets with subnet detail rather than a separate resource type.
    """
    kql = """
    Resources
    | where type == 'microsoft.network/virtualnetworks'
    | mv-expand subnet = properties.subnets
    | project
        subnet_id      = tostring(subnet.id),
        name           = tostring(subnet.name),
        vnet_id        = id,
        resource_group = resourceGroup,
        address_prefix = tostring(subnet.properties.addressPrefix),
        nsg_id         = tostring(subnet.properties.networkSecurityGroup.id)
    """
    rows = _query(kql)
    return [{
        "subnet_id": r.get("subnet_id", ""),
        "name": r.get("name", ""),
        "vnet_id": r.get("vnet_id", ""),
        "resource_group": r.get("resource_group", ""),
        "address_prefix": r.get("address_prefix", ""),
        "nsg_id": r.get("nsg_id") or None,
    } for r in rows if r.get("subnet_id")]


def fetch_nics() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/networkinterfaces'
    | project
        nic_id          = id,
        name,
        resource_group  = resourceGroup,
        subscription_id = subscriptionId,
        vm_id           = tostring(properties.virtualMachine.id),
        subnet_id       = tostring(properties.ipConfigurations[0].properties.subnet.id),
        private_ip      = tostring(properties.ipConfigurations[0].properties.privateIPAddress),
        public_ip_id    = tostring(properties.ipConfigurations[0].properties.publicIPAddress.id)
    | order by name asc
    """
    rows = _query(kql)
    return [{
        "nic_id": r.get("nic_id", ""),
        "name": r.get("name", ""),
        "resource_group": r.get("resource_group", ""),
        "subscription_id": r.get("subscription_id", ""),
        "vm_id": r.get("vm_id") or None,
        "subnet_id": r.get("subnet_id") or None,
        "private_ip": r.get("private_ip") or None,
        "public_ip_id": r.get("public_ip_id") or None,
    } for r in rows if r.get("nic_id")]


def fetch_public_ips() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/publicipaddresses'
    | project
        pip_id              = id,
        name,
        resource_group      = resourceGroup,
        subscription_id     = subscriptionId,
        ip_address          = tostring(properties.ipAddress),
        allocation_method   = tostring(properties.publicIPAllocationMethod),
        nic_id              = tostring(properties.ipConfiguration.id)
    | order by name asc
    """
    rows = _query(kql)
    result = []
    for r in rows:
        if not r.get("pip_id"):
            continue
        # nic_id from public IP is the IP-config ID, not the NIC ID itself
        # strip /ipConfigurations/... suffix to get NIC resource ID
        raw_nic = r.get("nic_id") or ""
        nic_id = raw_nic.split("/ipConfigurations/")[0] if "/ipConfigurations/" in raw_nic else None
        result.append({
            "pip_id": r.get("pip_id", ""),
            "name": r.get("name", ""),
            "resource_group": r.get("resource_group", ""),
            "subscription_id": r.get("subscription_id", ""),
            "ip_address": r.get("ip_address") or None,
            "allocation_method": r.get("allocation_method") or None,
            "nic_id": nic_id or None,
        })
    return result


def fetch_nsgs() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/networksecuritygroups'
    | project
        nsg_id          = id,
        name,
        resource_group  = resourceGroup,
        subscription_id = subscriptionId,
        location
    | order by name asc
    """
    rows = _query(kql)
    return [{
        "nsg_id": r.get("nsg_id", ""),
        "name": r.get("name", ""),
        "resource_group": r.get("resource_group", ""),
        "subscription_id": r.get("subscription_id", ""),
        "location": r.get("location", ""),
    } for r in rows if r.get("nsg_id")]


def fetch_vnet_peerings() -> list[dict]:
    kql = """
    Resources
    | where type == 'microsoft.network/virtualnetworks'
    | mv-expand peering = properties.virtualNetworkPeerings
    | project
        peering_id  = tostring(peering.id),
        src_vnet_id = id,
        dst_vnet_id = tostring(peering.properties.remoteVirtualNetwork.id),
        name        = tostring(peering.name),
        state       = tostring(peering.properties.peeringState)
    | where isnotempty(peering_id)
    """
    rows = _query(kql)
    return [{
        "peering_id": r.get("peering_id", ""),
        "src_vnet_id": r.get("src_vnet_id", ""),
        "dst_vnet_id": r.get("dst_vnet_id", ""),
        "name": r.get("name", ""),
        "state": r.get("state", ""),
    } for r in rows if r.get("peering_id")]


def fetch_all_network() -> dict:
    try:
        vnets = fetch_vnets()
    except Exception as exc:
        vnets = [{"_error": str(exc)}]
    try:
        subnets = fetch_subnets(vnets)
    except Exception as exc:
        subnets = [{"_error": str(exc)}]
    try:
        nics = fetch_nics()
    except Exception as exc:
        nics = [{"_error": str(exc)}]
    try:
        public_ips = fetch_public_ips()
    except Exception as exc:
        public_ips = [{"_error": str(exc)}]
    try:
        nsgs = fetch_nsgs()
    except Exception as exc:
        nsgs = [{"_error": str(exc)}]
    try:
        peerings = fetch_vnet_peerings()
    except Exception as exc:
        peerings = [{"_error": str(exc)}]

    return {
        "vnets": vnets,
        "subnets": subnets,
        "nics": nics,
        "public_ips": public_ips,
        "nsgs": nsgs,
        "peerings": peerings,
    }
