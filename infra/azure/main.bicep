targetScope = 'resourceGroup'

@description('Public registry image reference pinned to a sha256 digest from the reviewed commit.')
param containerImage string

@description('Azure region for every resource.')
param location string = 'francecentral'

@description('Name of the Container App.')
param appName string = 'fullaura-demo'

@description('Name of the Container Apps managed environment.')
param environmentName string = 'cae-fullaura-demo-frc'

@description('Name of the Log Analytics workspace used for Container Apps logs.')
param workspaceName string = 'law-fullaura-demo-frc'

@description('Private storage account for pilot quotas, spending and feedback; never photos or plans.')
param storageAccountName string = 'stfullaura${take(uniqueString(resourceGroup().id), 10)}'

@description('Open the closed beta to invited sessions.')
param betaEnabled bool = false

@description('When the pilot closes model calls, as an ISO date-time with a time zone; empty for none.')
param pilotEndsAt string = ''

@description('Days after its last change before a pilot record is deleted.')
param pilotDataRetentionDays int = 30

@secure()
@description('Baseten API key for Muse Spark planning.')
param basetenApiKey string

@secure()
@description('Meta API key for Muse Image try-ons, in Standard mode.')
param modelApiKey string = ''

@secure()
@description('Secret that signs session cookies, at least 32 characters.')
param sessionSecret string = ''

@secure()
@description('Comma-separated SHA-256 digests of the invite codes.')
param inviteHashes string = ''

var storageBlobDataContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)
var pilotContainer = 'pilot'

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logWorkspace.properties.customerId
        sharedKey: logWorkspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: pilotContainer
  properties: {
    publicAccess: 'None'
  }
}

resource retention 'Microsoft.Storage/storageAccounts/managementPolicies@2024-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'delete-pilot-records'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${pilotContainer}/']
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: pilotDataRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
}

resource app 'Microsoft.App/containerApps@2025-07-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8000
        transport: 'auto'
      }
      secrets: concat(
        [
          {
            name: 'baseten-api-key'
            value: basetenApiKey
          }
        ],
        empty(modelApiKey) ? [] : [{ name: 'model-api-key', value: modelApiKey }],
        empty(sessionSecret) ? [] : [{ name: 'session-secret', value: sessionSecret }],
        empty(inviteHashes) ? [] : [{ name: 'invite-hashes', value: inviteHashes }]
      )
    }
    template: {
      containers: [
        {
          name: 'fullaura'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(
            [
              {
                name: 'FULLAURA_ENV'
                value: 'production'
              }
              {
                name: 'FULLAURA_BETA_ENABLED'
                value: string(betaEnabled)
              }
              {
                name: 'FULLAURA_STORE_URL'
                value: '${storage.properties.primaryEndpoints.blob}${pilotContainer}'
              }
              {
                name: 'FULLAURA_PILOT_ENDS_AT'
                value: pilotEndsAt
              }
              {
                name: 'BASETEN_API_KEY'
                secretRef: 'baseten-api-key'
              }
              {
                name: 'BASETEN_BASE_URL'
                value: 'https://inference.baseten.co/v1'
              }
              {
                name: 'BASETEN_MODEL'
                value: 'meta-models/muse-spark-open-1026'
              }
            ],
            empty(modelApiKey) ? [] : [{ name: 'MODEL_API_KEY', secretRef: 'model-api-key' }],
            empty(sessionSecret) ? [] : [{ name: 'FULLAURA_SESSION_SECRET', secretRef: 'session-secret' }],
            empty(inviteHashes) ? [] : [{ name: 'FULLAURA_INVITE_HASHES', secretRef: 'invite-hashes' }]
          )
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/ready'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

resource pilotAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, app.id, storageBlobDataContributor)
  scope: storage
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributor
  }
}

output containerAppName string = app.name
output containerAppFqdn string = app.properties.configuration.ingress.fqdn
output storageAccountName string = storage.name
