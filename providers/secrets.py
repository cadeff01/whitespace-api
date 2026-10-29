import os
import re
from functools import lru_cache
from urllib.parse import urlsplit

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient

from providers.errors import APIError


@lru_cache(maxsize=4)
def secret_client(vault_url: str) -> SecretClient:
    credential = (
        ManagedIdentityCredential()
        if os.environ.get('WEBSITE_SITE_NAME')
        else DefaultAzureCredential(exclude_interactive_browser_credential=True)
    )
    return SecretClient(
        vault_url=vault_url, credential=credential,
        retry_total=1, connection_timeout=5, read_timeout=10,
    )


def sam_api_key(vault_url: str, secret_name: str) -> str:
    try:
        parsed = urlsplit(vault_url)
    except ValueError:
        raise APIError(503, 'configuration_error', 'Key Vault configuration is invalid.') from None
    if (parsed.scheme != 'https' or not parsed.hostname
            or not parsed.hostname.endswith('.vault.azure.net')
            or parsed.username or parsed.password or ':' in parsed.netloc
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or not re.fullmatch(r'[A-Za-z0-9-]{1,127}', secret_name)):
        raise APIError(503, 'configuration_error', 'Key Vault configuration is invalid.')
    try:
        value = secret_client(vault_url).get_secret(secret_name).value
    except AzureError:
        # SDK exceptions can contain request details; never expose or log their text.
        raise APIError(503, 'secret_unavailable', 'The SAM.gov credential is unavailable.') from None
    if not value or not value.strip():
        raise APIError(503, 'secret_unavailable', 'The SAM.gov credential is unavailable.')
    return value
