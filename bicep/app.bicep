param adaModel string
param adaVersion string
param agentPhoneNumber string
param botCompany string
param botName string
param botPhoneNumber string
param botVoiceName string
param gptModel string
param gptVersion string
param imageVersion string
param location string
param moderationBlocklists array
param openaiLocation string
param searchLocation string
param tags object

var prefix = deployment().name
var appUrl = 'https://claim-ai.${acaEnv.properties.defaultDomain}'
var gptModelFullName = toLower('${gptModel}-${gptVersion}')
var adaModelFullName = toLower('${adaModel}-${adaVersion}')
var config = {
  api: {
    events_domain: appUrl
  }
  database: {
    mode: 'cosmos_db'
    cosmos_db: {
      access_key: cosmos.listKeys().primaryMasterKey
      container: container.name
      database: database.name
      endpoint: cosmos.properties.documentEndpoint
    }
  }
  resources: {
    public_url: storageAccount.properties.primaryEndpoints.web
  }
  workflow: {
    agent_phone_number: agentPhoneNumber
    bot_company: botCompany
    bot_name: botName
  }
  communication_service: {
    access_key: communication.listKeys().primaryKey
    endpoint: communication.properties.hostName
    phone_number: botPhoneNumber
    voice_name: botVoiceName
  }
  cognitive_service: {
    endpoint: cognitiveCommunication.properties.endpoint
  }
  openai: {
    endpoint: cognitiveOpenai.properties.endpoint
    gpt_deployment: gpt.name
    gpt_model: gptModel
  }
  ai_search: {
    access_key: search.listAdminKeys().primaryKey
    endpoint: 'https://${search.name}.search.windows.net'
    index: 'trainings'
    semantic_configuration: 'default'
  }
  content_safety: {
    access_key: cognitiveContentsafety.listKeys().key1
    blocklists: moderationBlocklists
    endpoint: cognitiveContentsafety.properties.endpoint
  }
  prompts: {
    llm: loadYamlContent('../config.yaml').prompts.llm
    tts: loadYamlContent('../config.yaml').prompts.tts
  }
}

output appUrl string = appUrl
output blobStoragePublicName string = storageAccount.name
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
      containers: [
        {
          image: 'ghcr.io/clemlesne/claim-ai-phone-bot:${imageVersion}'
          name: 'claim-ai'
          env: [
            {
              name: 'CONFIG_JSON'
              value: string(config)
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

resource roleCommunicationContributor 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
}

resource appContribCommunication 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, deployment().name, 'appContribCommunication')
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

resource cognitiveCommunication 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-communication'
  location: location
  tags: tags
  sku: {
    name: 'S0'
  }
  kind: 'CognitiveServices'
  properties: {
    customSubDomainName: '${prefix}-communication'
  }
}

resource cognitiveDocument 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-document'
  location: location
  tags: tags
  sku: {
    name: 'S0'
  }
  kind: 'FormRecognizer'
  properties: {
    customSubDomainName: '${prefix}-document'
  }
}

resource cognitiveContentsafety 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-contentsafety'
  location: location
  tags: tags
  sku: {
    name: 'S0'
  }
  kind: 'ContentSafety'
  properties: {
    customSubDomainName: '${prefix}-contentsafety'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource roleOpenaiContributor 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'a001fd3d-188f-4b5d-821b-7da978bf7442'
}

resource appContribOpenai 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, deployment().name, 'appContribOpenai')
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
  name: gptModelFullName
  sku: {
    capacity: 50
    name: 'Standard'
  }
  properties: {
    // raiPolicyName: contentfilter.name
    model: {
      format: 'OpenAI'
      name: gptModel
      version: gptVersion
    }
  }
}

// resource contentfilter 'Microsoft.CognitiveServices/accounts/raiPolicies@2023-06-01-preview' = {
//   parent: cognitiveOpenai
//   name: 'gpt'
//   properties: {
//     basePolicyName: 'Microsoft.Default'
//     mode: 'Default'
//     contentFilters: [
//       {
//         blocking: false
//         enabled: false
//         name: 'hate'
//         source: 'Prompt'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'sexual'
//         source: 'Prompt'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'selfharm'
//         source: 'Prompt'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'violence'
//         source: 'Prompt'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'hate'
//         source: 'Completion'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'sexual'
//         source: 'Completion'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'selfharm'
//         source: 'Completion'
//       }
//       {
//         blocking: false
//         enabled: false
//         name: 'violence'
//         source: 'Completion'
//       }
//     ]
//   }
// }

resource ada 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: cognitiveOpenai
  name: adaModelFullName
  sku: {
    capacity: 50
    name: 'Standard'
  }
  properties: {
    // raiPolicyName: contentfilter.name
    model: {
      format: 'OpenAI'
      name: adaModel
      version: adaVersion
    }
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2023-11-15' = {
  name: prefix
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2023-11-15' = {
  parent: cosmos
  name: 'claim-ai'
  properties: {
    resource: {
      id: 'claim-ai'
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-11-15' = {
  parent: database
  name: 'calls'
  properties: {
    resource: {
      id: 'calls'
      indexingPolicy: {
        automatic: true
        includedPaths: [
          {
            path: '/created_at/?'
            indexes: [
              {
                dataType: 'String'
                kind: 'Range'
                precision: -1
              }
            ]
          }
          {
            path: '/claim/policyholder_phone/?'
            indexes: [
              {
                dataType: 'String'
                kind: 'Hash'
                precision: -1
              }
            ]
          }
        ]
        excludedPaths: [
          {
            path: '/*'
          }
        ]
      }
      partitionKey: {
        paths: [
          '/phone_number'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: prefix
  location: searchLocation
  tags: tags
  sku: {
    name: 'basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    semanticSearch: 'standard'
  }
}
