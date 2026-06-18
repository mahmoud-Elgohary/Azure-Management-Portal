"""
Read-only VM data from azure-mgmt-compute.
Returns plain dicts ready for DB insertion.
"""

from azure.mgmt.compute import ComputeManagementClient

from azure_client.auth import get_credential, subscription_ids


def _client(sub_id: str) -> ComputeManagementClient:
    return ComputeManagementClient(get_credential(), sub_id)


def fetch_vms(sub_id: str) -> list[dict]:
    client = _client(sub_id)
    result = []
    for vm in client.virtual_machines.list_all():
        # power state requires instance_view
        try:
            iv = client.virtual_machines.instance_view(
                vm.id.split("/")[4],  # resource group
                vm.name,
            )
            statuses = iv.statuses or []
            power_state = next(
                (s.code.replace("PowerState/", "") for s in statuses if s.code and s.code.startswith("PowerState/")),
                "unknown",
            )
        except Exception:
            power_state = "unknown"

        result.append(
            {
                "vm_id": vm.id,
                "name": vm.name,
                "resource_group": vm.id.split("/")[4],
                "subscription_id": sub_id,
                "location": vm.location,
                "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
                "os_type": (
                    vm.storage_profile.os_disk.os_type
                    if vm.storage_profile and vm.storage_profile.os_disk
                    else None
                ),
                "power_state": power_state,
                "tags": str(vm.tags or {}),
            }
        )
    return result


def fetch_all_vms() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        try:
            rows.extend(fetch_vms(sub))
        except Exception as exc:
            rows.append({"_error": str(exc), "subscription_id": sub})
    return rows
