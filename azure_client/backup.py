"""
Recovery Services backup status for VMs.
"""

from azure.mgmt.recoveryservicesbackup import RecoveryServicesBackupClient

from azure_client.auth import get_credential, subscription_ids


def _client(sub_id: str) -> RecoveryServicesBackupClient:
    return RecoveryServicesBackupClient(get_credential(), sub_id)


def fetch_backup_status(sub_id: str) -> list[dict]:
    """
    Iterates Recovery Services vaults in the subscription and returns
    backup status for each protected VM item.
    """
    from azure.mgmt.recoveryservices import RecoveryServicesClient

    rs_client = RecoveryServicesClient(get_credential(), sub_id)
    backup_client = _client(sub_id)
    result = []

    try:
        vaults = list(rs_client.vaults.list_by_subscription_id())
    except Exception as exc:
        return [{"_error": str(exc), "subscription_id": sub_id}]

    for vault in vaults:
        rg = vault.id.split("/")[4]
        try:
            items = backup_client.backup_protected_items.list(
                vault.name,
                rg,
                filter="backupManagementType eq 'AzureIaasVM'",
            )
            for item in items:
                props = item.properties
                result.append(
                    {
                        "item_id": item.id,
                        "subscription_id": sub_id,
                        "vault_name": vault.name,
                        "resource_group": rg,
                        "vm_name": props.friendly_name if props else None,
                        "protection_state": props.protection_state if props else None,
                        "last_backup_status": props.last_backup_status if props else None,
                        "last_backup_time": (
                            props.last_backup_time.isoformat()
                            if props and props.last_backup_time
                            else None
                        ),
                    }
                )
        except Exception as exc:
            result.append(
                {"_error": str(exc), "vault_name": vault.name, "subscription_id": sub_id}
            )

    return result


def fetch_all_backup_status() -> list[dict]:
    rows = []
    for sub in subscription_ids():
        rows.extend(fetch_backup_status(sub))
    return rows
