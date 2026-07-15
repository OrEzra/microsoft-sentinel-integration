# PerceptionPoint Sentinel Connector

Pulls scan and audit events from the PerceptionPoint API and ingests them into
Microsoft Sentinel (Log Analytics) on a 5-minute timer, using an Azure
Function.

Ingestion uses the Azure Monitor **Logs Ingestion API** via a Data Collection
Rule (DCR). The Function App authenticates with its own system-assigned
managed identity — there are no shared keys or app registrations to manage.

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

Keeping the payload as a single `dynamic` column means the pipeline doesn't
need to be updated if PerceptionPoint adds, removes, or renames fields.
Query into it with dot notation, e.g. `RawData.verdict`.

## Prerequisites

- An Azure subscription, with a Microsoft Sentinel / Log Analytics workspace already deployed
- A PerceptionPoint API auth token and organization ID
- Permission to deploy resources **and create role assignments** in the resource group that contains your Log Analytics workspace (e.g. `Owner`, or `Contributor` + `Role Based Access Control Administrator`) — the deployment grants the Function App's managed identity access to the DCR
- To publish the function code after provisioning (required regardless of which option below you use): [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local) (`func`)

## Deploy

### 1. Provision the Azure resources

Everything — the Function App (with a system-assigned managed identity), the
Data Collection Endpoint, the Data Collection Rule, the two custom tables,
and the role assignment that lets the Function App ingest data — is defined
in [`infra/main.bicep`](infra/main.bicep). Pick whichever deployment option fits; they all deploy the
same template. **Deploy into the same resource group as your Log Analytics workspace.**

#### Option A — Deploy to Azure button (no CLI, no local tools)

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FOrEzra%2Fmicrosoft-sentinel-integration%2Fmain%2Finfra%2Fmain.json)

Click the button, sign in, pick the resource group that contains your
Sentinel workspace, and fill in the form (see the parameter table below).
The portal masks the `ppAuthToken` field automatically since it's a secure
parameter. When the deployment finishes, expand the **Outputs** tab on the
deployment's overview page to get the `functionAppName` you'll need in step 2.

#### Option B — VS Code (no CLI typing)

1. Install the [Bicep extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-bicep) for VS Code (the [Azure Account](https://marketplace.visualstudio.com/items?itemName=ms-vscode.azure-account) extension is required alongside it for sign-in).
2. Open `infra/main.bicep` in VS Code.
3. Right-click in the editor and choose **Deploy Bicep File...**.
4. Sign in when prompted, then pick your subscription, the resource group containing your workspace, and a parameters file (or enter values interactively).

#### Option C — Azure CLI

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

The deployment takes a few minutes. When it finishes, note the
`functionAppName` output — you'll need it in the next step.

#### Parameters (all options)

| Parameter | Required | Notes |
|---|---|---|
| `namePrefix` | recommended | Used to derive resource names, including a globally-unique storage account name. Keep it short (≤ 16 chars) and alphanumeric, e.g. `pp-acme01`. Defaults to `pp-sentinel-connector`, which will collide if left as-is across multiple customers/tenants. |
| `logAnalyticsWorkspaceName` | yes | Name of your existing Sentinel/Log Analytics workspace. |
| `ppAuthToken` | yes | Your PerceptionPoint API token. Passed as a secure parameter — with the CLI, consider sourcing it from Key Vault (`--parameters ppAuthToken=@keyvault-ref`) instead of the command line in shared shell history. |
| `ppOrgId` | yes | Your PerceptionPoint organization ID. |
| `ppBaseApi` | no | Defaults to `https://api.perception-point.io`. |
| `location` | no | Defaults to the resource group's location. |
| `retentionInDays` | no | Retention for the two custom tables. Defaults to `90`. |

> `infra/main.json` is the compiled ARM template used by the Deploy to Azure
> button. It's generated automatically from `infra/main.bicep` by a GitHub
> Actions workflow on every push to `main`, so `infra/main.bicep` is always
> the source of truth — you never need to edit `main.json` by hand.

### 2. Publish the function code

The Function App already has all the app settings it needs (`DCE_ENDPOINT`,
`DCR_IMMUTABLE_ID`, PerceptionPoint credentials, remote build enabled) from
step 1 — whichever option below you use, there's nothing else to configure.

#### Option A — Azure Portal Deployment Center (no CLI, no local tools, auto-deploys on every push)

If you're working from your own fork of this repo, this is the lowest-maintenance
option: it wires up continuous deployment so future `git push`es deploy automatically.

1. Fork this repo to your GitHub account (if you haven't already).
2. In the [Azure Portal](https://portal.azure.com), open the Function App from step 1 → **Deployment Center**.
3. Set **Source** to `GitHub`, authorize access, and select your fork, the branch, and this app's folder as the build path.
4. Save. Azure commits a GitHub Actions workflow to your fork and triggers the first deployment automatically.

#### Option B — VS Code (no CLI)

1. Install the [Azure Functions extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-azurefunctions) for VS Code.
2. Open this repository's folder in VS Code.
3. Right-click the folder in the Explorer and choose **Deploy to Function App...**.
4. Sign in when prompted, pick your subscription, then select the `functionAppName` from step 1. Confirm the overwrite prompt.

#### Option C — Azure CLI

From the repository root:

```bash
func azure functionapp publish <functionAppName>
```

### 3. Verify data is flowing

The timer trigger runs every 5 minutes. Role assignment propagation can take
a few minutes after deployment, so allow ~10 minutes before checking. In your
Log Analytics workspace, run:

```kql
PerceptionPoint_Scans_CL
| take 10
```

```kql
PerceptionPoint_Audits_CL
| take 10
```

If rows aren't appearing, check the Function App's logs (Application
Insights, `Live Metrics` or `Logs`) for warnings from the connector.

## Querying the data

Since the payload lives in the `RawData` dynamic column, expand the fields
you need in your KQL queries, for example:

```kql
PerceptionPoint_Scans_CL
| extend Verdict = tostring(RawData.verdict), Sender = tostring(RawData.sender)
| where Verdict == "malicious"
```

## Updating

Re-run whichever deploy option you used any time you want to change a
parameter (e.g. table retention) — the template is idempotent. Re-run
`func azure functionapp publish` to ship code changes.
