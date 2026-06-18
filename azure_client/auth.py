from azure.identity import ClientSecretCredential
import config

_credential: ClientSecretCredential | None = None


def get_credential() -> ClientSecretCredential:
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=config.AZURE_TENANT_ID,
            client_id=config.AZURE_CLIENT_ID,
            client_secret=config.AZURE_CLIENT_SECRET,
        )
    return _credential


def subscription_ids() -> list[str]:
    return config.AZURE_SUBSCRIPTION_IDS
