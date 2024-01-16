param config string
param imageVersion string
param instance string = deployment().name
param location string = resourceGroup().location
param openaiLocation string
param tags object

var prefix = instance
var appUrl = 'https://claim-ai.${acaEnv.properties.defaultDomain}'

output appUrl string = appUrl
output communicationId string = communication.id

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource acaEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        // Consumption workload profile name must be 'Consumption'
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'claim-ai'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
      }
    }
    environmentId: acaEnv.id
    template: {
      scale: {
        maxReplicas: 1
      }
      containers: [
        {
          image: 'ghcr.io/clemlesne/claim-ai-phone-bot:${imageVersion}'
          name: 'claim-ai'
          env: [
            {
              name: 'CONFIG_JSON'
              value: config
            }
            {
              name: 'EVENTS_DOMAIN'
              value: appUrl
            }
            {
              name: 'SQLITE_PATH'
              value: '/app/data/.default.sqlite'
            }
          ]
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/liveness'
                port: 8080
              }
            }
          ]
        }
      ]
    }
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: replace(prefix, '-', '')
  location: location
  tags: tags
  sku: {
    name: 'Standard_ZRS'
  }
  kind: 'StorageV2'
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: '$web'
}

resource roleCommunicationContributor 'Microsoft.Authorization/roleDefinitions@2018-01-01-preview' existing = {
  name: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
}

resource appContributeCommunication 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, deployment().name, 'appContributeCommunication')
  scope: communication
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleCommunicationContributor.id
  }
}

resource communication 'Microsoft.Communication/CommunicationServices@2023-06-01-preview' existing = {
  name: prefix
}

resource eventgridTopic 'Microsoft.EventGrid/systemTopics@2023-12-15-preview' = {
  name: prefix
  location: 'global'
  tags: tags
  properties: {
    source: communication.id
    topicType: 'Microsoft.Communication.CommunicationServices'
  }
}

resource roleOpenaiContributor 'Microsoft.Authorization/roleDefinitions@2018-01-01-preview' existing = {
  name: 'a001fd3d-188f-4b5d-821b-7da978bf7442'
}

resource appAccessOpenai 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, deployment().name, 'appAccessOpenai')
  scope: cognitiveOpenai
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleOpenaiContributor.id
  }
}

resource cognitiveOpenai 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-openai'
  location: openaiLocation
  tags: tags
  sku: {
    name: 'S0'
  }
  kind: 'OpenAI'
  properties: {
    customSubDomainName: '${prefix}-openai'
  }
}

resource gpt 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: cognitiveOpenai
  name: 'gpt'
  sku: {
    capacity: 50
    name: 'Standard'
  }
  properties: {
    raiPolicyName: contentfilter.name
    model: {
      format: 'OpenAI'
      name: 'gpt-4'
      version: '1106-Preview'
    }
  }
}

resource contentfilter 'Microsoft.CognitiveServices/accounts/raiPolicies@2023-06-01-preview' = {
  parent: cognitiveOpenai
  name: 'gpt'
  properties: {
    basePolicyName: 'Microsoft.Default'
    mode: 'Default'
    contentFilters: [
      {
        blocking: false
        enabled: false
        name: 'hate'
        source: 'Prompt'
      }
      {
        blocking: false
        enabled: false
        name: 'sexual'
        source: 'Prompt'
      }
      {
        blocking: false
        enabled: false
        name: 'selfharm'
        source: 'Prompt'
      }
      {
        blocking: false
        enabled: false
        name: 'violence'
        source: 'Prompt'
      }
      {
        blocking: false
        enabled: false
        name: 'hate'
        source: 'Completion'
      }
      {
        blocking: false
        enabled: false
        name: 'sexual'
        source: 'Completion'
      }
      {
        blocking: false
        enabled: false
        name: 'selfharm'
        source: 'Completion'
      }
      {
        blocking: false
        enabled: false
        name: 'violence'
        source: 'Completion'
      }
    ]
  }
}
