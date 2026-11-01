targetScope = 'resourceGroup'

@description('Public registry image reference for the app, pinned to a sha256 digest from the reviewed commit.')
param containerImage string

@description('Image for the purge job, pinned to a digest whose backend.app.purge writes the closure marker. Set on its own, so that rolling the app back never rolls the job back.')
param purgeJobImage string

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

@description('US dollars the beta may spend on planning calls, after spending outside the ledger.')
param planningBudgetUsd string = '8'

@description('US dollars the beta may spend on try-on images, after spending outside the ledger.')
param imageBudgetUsd string = '3'

@description('When the purge job deletes every pilot record and the beta closes, as an ISO date-time with a time zone after pilotEndsAt; empty for no job.')
param recordsPurgeAt string = ''

@description('When the purge job runs, as a five-field cron expression in UTC. The first run should fall at recordsPurgeAt; the job deletes nothing earlier, and later runs retry a failure.')
param recordsPurgeSchedule string = '0 9 9-15 11 *'

@description('Name of the scheduled job that deletes the pilot records.')
param purgeJobName string = 'fullaura-demo-purge'

@description('Days after its last change before a pilot record is deleted, as a backstop to the purge job.')
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
    publicNetworkAccess: 'Enabled'
    peerAuthentication: {
      mtls: {
        enabled: false
      }
    }
    peerTrafficConfiguration: {
      encryption: {
        enabled: false
      }
    }
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
  properties: {
    deleteRetentionPolicy: {
      enabled: false
      allowPermanentDelete: false
    }
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobService
  name: pilotContainer
  properties: {
    publicAccess: 'None'
    defaultEncryptionScope: '$account-encryption-key'
    denyEncryptionScopeOverride: false
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
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
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
                name: 'FULLAURA_RECORDS_PURGE_AT'
                value: recordsPurgeAt
              }
              {
                name: 'FULLAURA_PLANNING_BUDGET_USD'
                value: planningBudgetUsd
              }
              {
                name: 'FULLAURA_IMAGE_BUDGET_USD'
                value: imageBudgetUsd
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
        cooldownPeriod: 300
        pollingInterval: 30
      }
    }
  }
}

resource pilotAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(container.id, app.id, storageBlobDataContributor)
  scope: container
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributor
  }
}

// Deletes the pilot records once recordsPurgeAt has passed (backend/app/purge.py). Its identity
// gets Storage Blob Data Contributor on the pilot container, the narrowest built-in role that can
// delete blobs; the role also allows writing blobs and changing or deleting that container, which
// the job never does.
resource purgeJob 'Microsoft.App/jobs@2025-07-01' = if (!empty(recordsPurgeAt)) {
  name: purgeJobName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 1800
      replicaRetryLimit: 3
      scheduleTriggerConfig: {
        cronExpression: recordsPurgeSchedule
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'purge'
          image: purgeJobImage
          command: ['python', '-m', 'backend.app.purge']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'FULLAURA_STORE_URL'
              value: '${storage.properties.primaryEndpoints.blob}${pilotContainer}'
            }
            {
              name: 'FULLAURA_PILOT_ENDS_AT'
              value: pilotEndsAt
            }
            {
              name: 'FULLAURA_RECORDS_PURGE_AT'
              value: recordsPurgeAt
            }
          ]
        }
      ]
    }
  }
}

resource purgeAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(recordsPurgeAt)) {
  name: guid(container.id, purgeJob.id, storageBlobDataContributor)
  scope: container
  properties: {
    principalId: purgeJob!.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: storageBlobDataContributor
  }
}

output containerAppName string = app.name
output containerAppFqdn string = app.properties.configuration.ingress.fqdn
output storageAccountName string = storage.name
