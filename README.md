# PerceptionPoint Sentinel Connector

Pulls scan and audit events from the PerceptionPoint API and ingests them into
Microsoft Sentinel (Log Analytics) on a 5-minute timer, using an Azure Function.

Ingestion uses the Azure Monitor Logs Ingestion API. The Function App
authenticates with its own managed identity — no shared keys or app
registrations to manage.

## Architecture

```
PerceptionPoint API --> Azure Function (timer, every 5 min) --> Data Collection Endpoint
                                                                        |
                                                                        v
                                                                 Data Collection Rule
                                                                        |
                                                       -----------------+-----------------
                                                       |                                 |
                                                       v                                 v
                                          PerceptionPoint_Scans_CL          PerceptionPoint_Audits_CL
```

Each row in both tables has two columns:

| Column | Type | Description |
|---|---|---|
| `TimeGenerated` | datetime | Ingestion timestamp |
| `RawData` | dynamic | The untouched JSON record returned by the PerceptionPoint API |

The `RawData` column holds the untouched payload, so the pipeline keeps
working even if PerceptionPoint adds or renames fields. Query into it with
dot notation, e.g. `RawData.verdict`.

## Prerequisites

- An Azure subscription, with a Microsoft Sentinel / Log Analytics workspace already deployed
- A PerceptionPoint API auth token and organization ID
- Permission to deploy resources **and create role assignments** in the resource group that contains your Log Analytics workspace (e.g. `Owner`, or `Contributor` + `Role Based Access Control Administrator`)

## Deploy

One deployment provisions the Azure resources and deploys the connector —
there's no separate code-publish step. Deploy into the **same resource group
as your Log Analytics workspace**, using whichever option fits:

### Option A — Deploy to Azure button (no CLI, no local tools)

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FOrEzra%2Fmicrosoft-sentinel-integration%2Fmain%2Finfra%2Fmain.json)

Click the button, sign in, choose the resource group containing your Sentinel
workspace, and fill in the parameters below. Then **Review + create** → **Create**.

### Option B — VS Code (no CLI typing)

1. Install the [Bicep extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-bicep) (and [Azure Account](https://marketplace.visualstudio.com/items?itemName=ms-vscode.azure-account) for sign-in).
2. Open `infra/main.bicep`.
3. Right-click in the editor and choose **Deploy Bicep File...**.
4. Sign in, then pick your subscription, resource group, and parameters.

### Option C — Azure CLI

Requires the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) with the Bicep tooling (`az bicep install`).

```bash
az deployment group create \
  --resource-group <resource-group-containing-your-workspace> \
  --template-file infra/main.bicep \
  --parameters \
      namePrefix=<short-unique-prefix> \
      logAnalyticsWorkspaceName=<your-workspace-name> \
      ppAuthToken=<your-perceptionpoint-api-token> \
      ppOrgId=<your-perceptionpoint-org-id>
```

### Parameters

| Parameter | Required | Notes |
|---|---|---|
| `namePrefix` | recommended | Prefix for resource names (≤ 16 chars, alphanumeric), e.g. `pp-acme01`. Defaults to `pp-sentinel-connector` — change it to avoid colliding with other deployments. |
| `logAnalyticsWorkspaceName` | yes | Name of your existing Sentinel/Log Analytics workspace. |
| `ppAuthToken` | yes | Your PerceptionPoint API token (masked in the portal). |
| `ppOrgId` | yes | Your PerceptionPoint organization ID. |
| `ppBaseApi` | no | Defaults to `https://api.perception-point.io`. |
| `location` | no | Defaults to the resource group's location. |
| `retentionInDays` | no | Retention for the two custom tables. Defaults to `90`. |
| `functionAppPackageUri` | no | Code package the Function App runs from. Defaults to this repo's latest build — see [Running your own code](#running-your-own-code) if you've forked and modified the connector. |

### Verify data is flowing

Allow ~10 minutes after deployment for role permissions to propagate, then run
in your Log Analytics workspace:

```kql
PerceptionPoint_Scans_CL
| take 10
```

```kql
PerceptionPoint_Audits_CL
| take 10
```

If nothing shows up, check the Function App's logs in Application Insights.

## Querying the data

Since the payload lives in the `RawData` dynamic column, expand the fields
you need in your KQL queries, for example:

```kql
PerceptionPoint_Scans_CL
| extend Verdict = tostring(RawData.verdict), Sender = tostring(RawData.sender)
| where Verdict == "malicious"
```

## Running your own code

If you fork this repo and change the Python connector, the default
`WEBSITE_RUN_FROM_PACKAGE` setting keeps the Function App locked to
`functionAppPackageUri`, so a manual code deploy won't take effect on its own.
Pick one:

- **Point at your own build** — in your fork, enable **Settings → Actions → General → Workflow permissions → Read and write permissions**, then deploy with `functionAppPackageUri=https://github.com/<your-org>/<your-repo>/releases/latest/download/function-app.zip`.
- **Deploy manually instead** — redeploy with `functionAppPackageUri=` (empty), or remove `WEBSITE_RUN_FROM_PACKAGE` via **Function App → Configuration** in the portal. Then use Deployment Center, VS Code (Azure Functions extension → **Deploy to Function App...**), or `func azure functionapp publish <functionAppName>`.

## Updating

Re-run whichever deploy option you used any time you want to change a
parameter — it's idempotent. If you're running your own code, redeploy it
the same way you did originally.
