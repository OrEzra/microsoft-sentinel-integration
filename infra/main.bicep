// Deploys the PerceptionPoint Sentinel connector end to end:
// Function App (system-assigned identity) + Data Collection Endpoint + Data Collection Rule
// + two raw-JSON custom tables, wired together so no secrets/app registration are needed.
//
// Deploy this into the SAME resource group as the target Log Analytics / Sentinel workspace.

@description('Name prefix used to derive resource names (Function App, plan, storage, etc.).')
param namePrefix string = 'pp-sentinel-connector'

@description('Location for all resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Name of the existing Log Analytics workspace (Sentinel workspace) in this resource group.')
param logAnalyticsWorkspaceName string

@description('PerceptionPoint API base URL.')
param ppBaseApi string = 'https://api.perception-point.io'

@description('PerceptionPoint API auth token.')
@secure()
param ppAuthToken string

@description('PerceptionPoint organization ID.')
param ppOrgId string

@description('How long ingested data is retained in the custom tables, in days.')
param retentionInDays int = 90

var storageAccountName = toLower(replace('${namePrefix}stor', '-', ''))
var functionAppName = '${namePrefix}-func'
var hostingPlanName = '${namePrefix}-plan'
var appInsightsName = '${namePrefix}-ai'
var dceName = '${namePrefix}-dce'
var dcrName = '${namePrefix}-dcr'

var scansTableName = 'PerceptionPoint_Scans_CL'
var auditsTableName = 'PerceptionPoint_Audits_CL'
var scansStreamName = 'Custom-PerceptionPointScans'
var auditsStreamName = 'Custom-PerceptionPointAudits'

// Built-in "Monitoring Metrics Publisher" role — the only permission the
// Function App's managed identity needs in order to ingest logs via the DCR.
var monitoringMetricsPublisherRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = {
  name: logAnalyticsWorkspaceName
}

// Raw-JSON schema: each row is a timestamp plus the untouched payload from the
// PerceptionPoint API. Keeps the pipeline schema-agnostic if PerceptionPoint adds
// or renames fields; consumers parse RawData with KQL (e.g. RawData.field_name).
var rawJsonColumns = [
  {
    name: 'TimeGenerated'
    type: 'datetime'
  }
  {
    name: 'RawData'
    type: 'dynamic'
  }
]

resource scansTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: logAnalyticsWorkspace
  name: scansTableName
  properties: {
    schema: {
      name: scansTableName
      columns: rawJsonColumns
    }
    retentionInDays: retentionInDays
    totalRetentionInDays: retentionInDays
  }
}

resource auditsTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: logAnalyticsWorkspace
  name: auditsTableName
  properties: {
    schema: {
      name: auditsTableName
      columns: rawJsonColumns
    }
    retentionInDays: retentionInDays
    totalRetentionInDays: retentionInDays
  }
}

resource dce 'Microsoft.Insights/dataCollectionEndpoints@2023-03-11' = {
  name: dceName
  location: location
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

resource dcr 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: dcrName
  location: location
  properties: {
    dataCollectionEndpointId: dce.id
    streamDeclarations: {
      '${scansStreamName}': {
        columns: rawJsonColumns
      }
      '${auditsStreamName}': {
        columns: rawJsonColumns
      }
    }
    destinations: {
      logAnalytics: [
        {
          workspaceResourceId: logAnalyticsWorkspace.id
          name: 'logAnalyticsDest'
        }
      ]
    }
    dataFlows: [
      {
        streams: [scansStreamName]
        destinations: ['logAnalyticsDest']
        transformKql: 'source'
        outputStream: 'Custom-${scansTableName}'
      }
      {
        streams: [auditsStreamName]
        destinations: ['logAnalyticsDest']
        transformKql: 'source'
        outputStream: 'Custom-${auditsTableName}'
      }
    ]
  }
  dependsOn: [
    scansTable
    auditsTable
  ]
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
  }
}

resource hostingPlan 'Microsoft.Web/serverfarms@2022-09-01' = {
  name: hostingPlanName
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2022-09-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};EndpointSuffix=${environment().suffixes.storage};AccountKey=${storageAccount.listKeys().keys[0].value}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          // Ensures a remote (Oryx) build runs on deploy, installing requirements.txt server-side.
          // Needed for zip-deploy-based publishing paths (VS Code extension, Deployment Center) —
          // `func azure functionapp publish` triggers this itself regardless of the setting.
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'PP_BASE_API'
          value: ppBaseApi
        }
        {
          name: 'PP_AUTH_TOKEN'
          value: ppAuthToken
        }
        {
          name: 'PP_ORG_ID'
          value: ppOrgId
        }
        {
          name: 'DCE_ENDPOINT'
          value: dce.properties.logsIngestion.endpoint
        }
        {
          name: 'DCR_IMMUTABLE_ID'
          value: dcr.properties.immutableId
        }
      ]
    }
  }
}

// Grants the Function App's managed identity permission to ingest logs
// through this DCR. No app registration, no client secret.
resource dcrRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dcr.id, functionApp.id, monitoringMetricsPublisherRoleId)
  scope: dcr
  properties: {
    roleDefinitionId: monitoringMetricsPublisherRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output functionAppName string = functionApp.name
output dceEndpoint string = dce.properties.logsIngestion.endpoint
output dcrImmutableId string = dcr.properties.immutableId
