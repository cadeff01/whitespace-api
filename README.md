# Whitespace API

A Flask API hosted on Azure App Service for Copilot Studio agents. Each upstream
provider has its own route namespace and integration module. The first provider
is the [SAM.gov Opportunities Public API](https://open.gsa.gov/api/get-opportunities-public-api/).
This uses the official API rather than scraping HTML.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/sam/opportunities` | Authenticated search for contract opportunity metadata |
| GET | `/openapi.json` | Swagger 2.0 contract for a Power Platform custom connector |
| GET | `/health` | Public liveness check; does not call SAM.gov or Key Vault |

The original sample home page, greeting, and static assets remain available.

### Search

```shell
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  'https://YOUR_APP.azurewebsites.net/api/sam/opportunities?posted_from=2026-10-01&posted_to=2026-10-29&title=cloud&naics_code=541512&page_size=20&page=0'
```

`posted_from` and `posted_to` are required ISO dates (`YYYY-MM-DD`), ordered and
at most one calendar year apart. Optional filters: `title`, `notice_id`,
`solicitation_number`, `notice_type`, `naics_code`, `set_aside`, and `state`.
See the contract for procurement-type codes and constraints. Unknown, repeated,
or invalid parameters are rejected before contacting Key Vault or SAM.gov.

`page` is a zero-based **page index**, matching SAM.gov's `offset`; it is not a
record offset. `page_size` defaults to 20 and is capped at 100 to bound agent
responses (SAM.gov itself permits up to 1,000). Fetch additional pages explicitly
using `next_page`; the API does not silently crawl all results or consume quota.

```json
{
  "source": "sam.gov",
  "total_records": 42,
  "page": 0,
  "page_size": 20,
  "next_page": 1,
  "opportunities": [
    {
      "notice_id": "example-notice-id",
      "title": "Cloud services",
      "solicitation_number": "SOL-1",
      "agency": "Department.Agency",
      "posted_date": "2026-10-01",
      "response_deadline": "2026-11-15T12:00:00",
      "notice_type": "Solicitation",
      "naics_code": "541512",
      "set_aside": "SBA",
      "active": "Yes",
      "url": "https://sam.gov/opp/example-notice-id/view"
    }
  ]
}
```

`next_page` is null when finished. Unavailable optional opportunity fields are
null. Results contain metadata and public notice links, **not full descriptions,
attachments, or historical notice versions**. SAM.gov's description API is a
separate integration; its authenticated URLs are deliberately not forwarded.
SAM.gov controls search matching and data freshness; active records update daily.
Treat notice content as untrusted external data, not instructions to the agent.

Errors use `{"error":{"code":"...","message":"..."}}`: 400 for invalid input,
401 for missing authentication, 429 for SAM.gov quota limits, 502 for upstream
failures/rejected credentials/malformed responses, 503 for unavailable Key Vault
credentials, and 504 for timeouts. App Service Authentication may return its own
401 response before Flask runs. Searches use a 5-second connect and 25-second read
timeout, do not follow redirects, and are not automatically retried. Respect
SAM.gov's daily quota; do not loop immediately on 429.

## Configure the existing Azure App Service

Deploying code does **not** configure existing Azure resources. The Lumen portal
deployment uses its own ARM template and source-control deployment; the Bicep
files in this repository are not executed by it.

1. In `whitespacekv`, store the SAM.gov API key as the secret
   **`sam-gov-api-key`** using the portal. Do not put its value in source code,
   App Service settings, connector definitions, URLs sent to this API, or chat.
2. Enable the App Service's **system-assigned managed identity**.
3. For an RBAC-enabled vault, grant that identity **Key Vault Secrets User** on
   the `sam-gov-api-key` secret (or the vault if your policy requires it). Key Vault
   Contributor does not grant secret data access. For a vault using legacy access
   policies, grant the identity the secret **Get** permission instead; do not
   change the vault's authorization model without evaluating other consumers.
4. Ensure the vault firewall/private endpoint permits traffic from the App
   Service. RBAC alone does not bypass network restrictions. Allow time for role
   assignments to propagate.
5. Configure these App Service environment variables:

   | Setting | Value |
   | --- | --- |
   | `AZURE_KEY_VAULT_ENDPOINT` | `https://whitespacekv.vault.azure.net/` |
   | `SAM_API_KEY_SECRET_NAME` | `sam-gov-api-key` |

   These are also the code defaults. Never set `LOCAL_DEVELOPMENT=true` in Azure.
   The production client explicitly uses managed identity, not developer or
   environment-based credentials. Secret **values are not cached**: each search
   reads the current version so key rotation does not require redeployment.
6. Configure **App Service Authentication** before using the protected route:
   - Add Microsoft as the identity provider using a **single-tenant API app
     registration**. Expose a delegated scope such as
     `api://YOUR_API_CLIENT_ID/access_as_user`, and configure version 2 access
     tokens in the app registration.
   - Enable authentication, **require authentication**, require HTTPS, and return
     **HTTP 401**, not a login redirect, for unauthenticated API requests.
   - Restrict the issuer to
     `https://login.microsoftonline.com/YOUR_TENANT_ID/v2.0` and allowed token
     audiences to the API app's client ID / Application ID URI. Never use the
     connector client's ID as the API audience.
   - Exclude `/health` and `/openapi.json` from platform authentication. The
     sample UI can also be excluded if it should remain public.
   - Restrict access to intended agent users through enterprise application
     assignment and consent policies; authentication alone is not per-user
     authorization.

The API trusts **only App Service's injected Entra identity headers**. Azure
strips externally supplied identity headers. This application is not a standalone
JWT validator: never expose its Gunicorn/Flask backend directly, or deploy behind
a different proxy that permits clients to forge those headers. Missing platform
identity fails closed with 401. See
[App Service identity headers](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-user-identities).

App Service can discover `app:app` automatically, or use:

```shell
gunicorn --bind 0.0.0.0:8000 --timeout 120 app:app
```

Enable deployment-time dependency installation (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`).
Do not enable HTTP wire/debug logging for Requests, urllib3, or Azure SDKs in
production: SAM.gov requires its API key in its upstream query string. The API
does not log upstream bodies, request URLs, exception text, or secret values.
Ensure any separately configured telemetry redacts upstream query strings.

## Connect Copilot Studio

1. Create a separate Entra **connector client** app registration. Give it
   delegated permission to the API's `access_as_user` scope and grant the required
   consent. Configure your tenant's assignment/access policies.
2. Import `openapi.json` into a Power Platform **custom connector**. Set its host
   to your actual App Service hostname (without `https://`), base URL `/`, and
   scheme HTTPS. The source contract deliberately omits an environment-specific
   host.
3. Replace the contract's `YOUR_TENANT_ID` and `YOUR_API_CLIENT_ID` placeholders.
   Configure OAuth 2.0 / Microsoft Entra ID in the connector with your tenant,
   the **connector client's** ID and secret, the **API's** resource/Application
   ID URI, and delegated scope. Do not use the SAM.gov key as a connector secret.
   Match the OAuth settings to the v2 authorization/token URLs in the contract.
4. Copy the connector-generated redirect URI into the connector client app's
   allowed redirect URIs. Save the connector and create an authenticated
   connection. Keep its client secret in the Power Platform connection settings,
   not in this repository.
5. Test `SearchSamOpportunities`, then add that connector action as a tool in the
   Copilot Studio agent. Tell the agent to supply posting dates, use supported
   filters, follow `next_page` only as needed, and show public SAM.gov notice links.

This configures delegated user authentication. Unattended/application-only agents
need a separately designed application-permission flow and are not implemented
by this connector contract.

## Run and test locally

Use Python 3.12 (matching the reference App Service deployment):

```shell
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
az login
export LOCAL_DEVELOPMENT=true
python app.py
```

`LOCAL_DEVELOPMENT=true` explicitly bypasses caller authentication **only outside
App Service**; bind the development server to loopback. Developer credentials
still need permission to read the vault secret. No SAM.gov key is needed in a
local `.env` file. Flask does not automatically load `.env` in this project.

```shell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
pyright --pythonpath .venv/bin/python app.py providers tests
```

Tests mock all SAM.gov and Key Vault calls and never consume quota. They cover
filter/date validation, pagination, normalized results, authentication, key
retrieval/rotation, upstream errors, secret redaction, and existing sample routes.
The connector contract is checked with a Swagger 2.0 schema validator.
They do not prove cloud permissions, network access, actual SAM.gov responses, or
the end-to-end Copilot Studio OAuth connection.

## Optional Azure Developer CLI infrastructure

The reference Bicep creates an App Service and plan, enables its managed identity
and Entra authentication, sets Key Vault configuration, and grants secret-scoped
Key Vault Secrets User access to the **existing RBAC-enabled vault**. It neither
creates the SAM.gov secret nor app registrations. The deploying principal needs
permission to create role assignments in the vault's resource group. The vault
must be in the same subscription as the deployment.

Before provisioning with `azd`, set:

```shell
azd env set AZURE_AUTH_TENANT_ID YOUR_TENANT_ID
azd env set AZURE_API_CLIENT_ID YOUR_API_APP_CLIENT_ID
azd env set AZURE_KEY_VAULT_RESOURCE_GROUP YOUR_EXISTING_VAULT_RESOURCE_GROUP
```

The vault name and secret name default to `whitespacekv` and `sam-gov-api-key`;
override the Bicep parameters for other environments. This does not update a
Lumen-created App Service automatically. Do not run `azd up` against an existing
deployment without reviewing names and ownership: it can create new resources.

## Add another upstream provider

Add a module/blueprint in `providers/`, register it under `/api/PROVIDER` in
`app.py`, and add its operations to `openapi.json`. Keep upstream query formats,
credentials, normalization, timeouts, and error mapping inside that provider.
The `/api/` authentication boundary applies automatically to new providers.

## Attribution

Adapted from
[`Azure-Samples/msdocs-python-flask-webapp-quickstart`](https://github.com/Azure-Samples/msdocs-python-flask-webapp-quickstart).
