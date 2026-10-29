targetScope = 'resourceGroup'

@description('Public registry image reference pinned to a sha256 digest from the reviewed commit.')
param containerImage string

@description('Azure region for the Container Apps environment and Log Analytics workspace.')
param location string = 'westeurope'

@description('Name of the Container App.')
param appName string = 'fullaura-demo'

@description('Name of the Container Apps managed environment.')
param environmentName string = 'cae-fullaura-demo'

@description('Name of the Log Analytics workspace used for Container Apps logs.')
param workspaceName string = 'law-fullaura-demo'

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

resource app 'Microsoft.App/containerApps@2025-07-01' = {
  name: appName
  location: location
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
          env: [
            {
              name: 'FULLAURA_ENV'
              value: 'production'
            }
            {
              name: 'FULLAURA_PUBLIC_DEMO_ENABLED'
              value: 'false'
            }
            {
              name: 'FULLAURA_T02_PREVIEWS_PASSED'
              value: 'false'
            }
          ]
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

output containerAppName string = app.name
output containerAppFqdn string = app.properties.configuration.ingress.fqdn
