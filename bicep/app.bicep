param adaModel string
param adaVersion string
param cognitiveCommunicationLocation string
param functionappLocation string
param gptBackupContext int
param gptBackupModel string
param gptBackupVersion string
param gptContext int
param gptModel string
param gptVersion string
param location string
param moderationBlocklists array
param openaiLocation string
param searchLocation string
param tags object
param version string

var appName = 'call-center-ai'
var prefix = deployment().name
var functionAppName = '${prefix}-${appName}'
var appUrl = 'https://${functionAppName}.azurewebsites.net'
var gptBackupModelFullName = toLower('${gptBackupModel}-${gptBackupVersion}')
var gptModelFullName = toLower('${gptModel}-${gptVersion}')
var adaModelFullName = toLower('${adaModel}-${adaVersion}')
var cosmosContainerName = 'calls-v3'  // Third schema version
var localConfig = loadYamlContent('../config.yaml')
var phonenumberSanitized = replace(localConfig.communication_services.phone_number, '+', '')
var config = {
  public_domain: appUrl
  monitoring: {
    application_insights: {
      connection_string: applicationInsights.properties.ConnectionString
    }
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
    initiate: {
      agent_phone_number: localConfig.workflow.initiate.agent_phone_number
      bot_company: localConfig.workflow.initiate.bot_company
      bot_name: localConfig.workflow.initiate.bot_name
      lang: localConfig.workflow.initiate.lang
    }
  }
  communication_services: {
    access_key: communicationServices.listKeys().primaryKey
    call_queue_name: callQueue.name
    endpoint: communicationServices.properties.hostName
    phone_number: localConfig.communication_services.phone_number
    post_queue_name: postQueue.name
    sms_queue_name: smsQueue.name
  }
  sms: localConfig.sms
  cognitive_service: {
    endpoint: cognitiveCommunication.properties.endpoint
  }
  llm: {
    backup: {
      mode: 'azure_openai'
      azure_openai: {
        context: gptBackupContext
        deployment: gptBackup.name
        endpoint: cognitiveOpenai.properties.endpoint
        model: gptBackupModel
        streaming: true
      }
    }
    primary: {
      mode: 'azure_openai'
      azure_openai: {
        context: gptContext
        deployment: gpt.name
        endpoint: cognitiveOpenai.properties.endpoint
        model: gptModel
        streaming: true
      }
    }
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
    llm: localConfig.prompts.llm
    tts: localConfig.prompts.tts
  }
  ai_translation: {
    access_key: translate.listKeys().key1
    endpoint: 'https://${translate.name}.cognitiveservices.azure.com/'
  }
  cache: {
    mode: 'redis'
    redis: {
      host: redis.properties.hostName
      password: redis.listKeys().primaryKey
      port: redis.properties.sslPort
    }
  }
}

output appUrl string = appUrl
output blobStoragePublicName string = storageAccount.name
output communicationId string = communicationServices.id
output functionAppName string = functionAppName
output logAnalyticsCustomerId string = logAnalytics.properties.customerId

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'  // Pay-as-you-go
    }
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: prefix
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource flexFuncPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: prefix
  location: functionappLocation
  tags: tags
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: functionappLocation
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: flexFuncPlan.id
    siteConfig: {
      appSettings: [
        // See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#azurewebjobsstorage__accountname
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccount.name
        }
        // See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#appinsights_instrumentationkey
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: applicationInsights.properties.InstrumentationKey
        }
        // Threadpool for async tasks
        // See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#python_threadpool_thread_count
        {
          name: 'PYTHON_THREADPOOL_THREAD_COUNT'
          value: '4'
        }
        {
          name: 'CONFIG_JSON'
          value: string(config)
        }
        {
          name: 'VERSION'
          value: version
        }
      ]
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${functionAppBlob.name}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 2048  // Default and recommended
        maximumInstanceCount: 100  // TODO: Avoid billing surprises, delete when sure
        alwaysReady: [
          {
            instanceCount: 1
            name: 'function:communicationservices_event_post'
          }
          {
            instanceCount: 1
            name: 'function:call_event'
          }
        ]
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
  }
}

// Storage Account Contributor
resource roleStorageAccountContributor 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '17d1049b-9a84-46fb-8f53-869881c3d3ab'
}

resource assignmentFunctionAppStorageAccountContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, storageAccount.name, 'assignmentFunctionAppStorageAccountContributor')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleStorageAccountContributor.id
  }
}

// Storage Blob Data Owner
resource roleBlobDataOwner 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
}

resource assignmentFunctionAppBlobDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, storageAccount.name, 'assignmentFunctionAppBlobDataOwner')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleBlobDataOwner.id
  }
}

// Storage Queue Data Contributor
resource roleQueueDataContributor 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
}

resource assignmentFunctionAppQueueDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, storageAccount.name, 'assignmentFunctionAppQueueDataContributor')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleQueueDataContributor.id
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
  properties: {
    supportsHttpsTrafficOnly: true
  }
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource callQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueService
  name: 'call-${phonenumberSanitized}'
}

resource smsQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueService
  name: 'sms-${phonenumberSanitized}'
}

resource postQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-01-01' = {
  parent: queueService
  name: 'post-${phonenumberSanitized}'
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource publicBlob 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: '$web'
}

resource functionAppBlob 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: functionAppName
}

// Contributor
resource roleContributor 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
}

resource assignmentFunctionAppContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, communicationServices.name, 'assignmentFunctionAppContributor')
  scope: communicationServices
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleContributor.id
  }
}

resource communicationServices 'Microsoft.Communication/CommunicationServices@2023-06-01-preview' existing = {
  name: prefix
}

resource eventgridTopic 'Microsoft.EventGrid/systemTopics@2023-12-15-preview' = {
  name: prefix
  location: 'global'
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    source: communicationServices.id
    topicType: 'Microsoft.Communication.CommunicationServices'
  }
}

// Storage Queue Data Message Sender
resource roleQueueSender 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
}

resource assignmentEventgridTopicQueueSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, eventgridTopic.name, 'assignmentEventgridTopicQueueSender')
  scope: storageAccount
  properties: {
    principalId: eventgridTopic.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleQueueSender.id
  }
}

resource eventgridSubscriptionCall 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2024-06-01-preview' = {
  parent: eventgridTopic
  name: '${prefix}-${phonenumberSanitized}'
  properties: {
    eventDeliverySchema: 'EventGridSchema'
    deliveryWithResourceIdentity: {
      identity: {
        type: 'SystemAssigned'
      }
      destination: {
        endpointType: 'StorageQueue'
        properties: {
          queueMessageTimeToLiveInSeconds: 60  // Short lived messages, only new call events
          queueName: callQueue.name
          resourceId: storageAccount.id
        }
      }
    }
    filter: {
      enableAdvancedFilteringOnArrays: true
      advancedFilters: [
        {
          operatorType: 'StringBeginsWith'
          key: 'data.to.PhoneNumber.Value'
          values: [
            localConfig.communication_services.phone_number
          ]
        }
      ]
      includedEventTypes: ['Microsoft.Communication.IncomingCall']
    }
  }
  dependsOn: [assignmentEventgridTopicQueueSender]
}

resource eventgridSubscriptionSms 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2024-06-01-preview' = {
  parent: eventgridTopic
  name: '${prefix}-${phonenumberSanitized}-sms'
  properties: {
    eventDeliverySchema: 'EventGridSchema'
    deliveryWithResourceIdentity: {
      identity: {
        type: 'SystemAssigned'
      }
      destination: {
        endpointType: 'StorageQueue'
        properties: {
          queueMessageTimeToLiveInSeconds: -1  // Infinite persistence, SMS is async
          queueName: smsQueue.name
          resourceId: storageAccount.id
        }
      }
    }
    filter: {
      enableAdvancedFilteringOnArrays: true
      advancedFilters: [
        {
          operatorType: 'StringBeginsWith'
          key: 'data.to'
          values: [
            localConfig.communication_services.phone_number
          ]
        }
      ]
      includedEventTypes: ['Microsoft.Communication.SMSReceived']
    }
  }
  dependsOn: [assignmentEventgridTopicQueueSender]
}

// Cognitive Services User
resource roleCognitiveUser 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'a97b65f3-24c7-4388-baec-2e87135dc908'
}

resource assignmentsCommunicationServicesCognitiveUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, cognitiveCommunication.name, 'assignmentsCommunicationServicesCognitiveUser')
  scope: cognitiveCommunication
  properties: {
    principalId: communicationServices.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleCognitiveUser.id
  }
}

resource cognitiveCommunication 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-${cognitiveCommunicationLocation}-communication'
  location: cognitiveCommunicationLocation
  tags: tags
  sku: {
    name: 'S0'  // Only one available
  }
  kind: 'CognitiveServices'
  properties: {
    customSubDomainName: '${prefix}-${cognitiveCommunicationLocation}-communication'
  }
}

resource cognitiveDocument 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-${location}-document'
  location: location
  tags: tags
  sku: {
    name: 'S0'  // Pay-as-you-go
  }
  kind: 'FormRecognizer'
  properties: {
    customSubDomainName: '${prefix}-${location}-document'
  }
}

resource cognitiveContentsafety 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-${location}-contentsafety'
  location: location
  tags: tags
  sku: {
    name: 'S0'  // Pay-as-you-go
  }
  kind: 'ContentSafety'
  properties: {
    customSubDomainName: '${prefix}-${location}-contentsafety'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Cognitive Services OpenAI Contributor
resource roleOpenaiContributor 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  name: 'a001fd3d-188f-4b5d-821b-7da978bf7442'
}

resource assignmentsFunctionAppOpenaiContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, prefix, cognitiveOpenai.name, 'assignmentsFunctionAppOpenaiContributor')
  scope: cognitiveOpenai
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleOpenaiContributor.id
  }
}

resource cognitiveOpenai 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-${openaiLocation}-openai'
  location: openaiLocation
  tags: tags
  sku: {
    name: 'S0'  // Pay-as-you-go
  }
  kind: 'OpenAI'
  properties: {
    customSubDomainName: '${prefix}-${openaiLocation}-openai'
  }
}

resource gpt 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: cognitiveOpenai
  name: gptModelFullName
  tags: tags
  sku: {
    capacity: 20  // Keep it small, will be scaled up if needed with "dynamicThrottlingEnabled"
    name: 'Standard'  // Pay-as-you-go
  }
  properties: {
    dynamicThrottlingEnabled: true  // Declared as read-only but can be set
    // raiPolicyName: contentfilter.name
    versionUpgradeOption: 'NoAutoUpgrade'
    model: {
      format: 'OpenAI'
      name: gptModel
      version: gptVersion
    }
  }
}

resource gptBackup 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: cognitiveOpenai
  name: gptBackupModelFullName
  tags: tags
  sku: {
    capacity: 20  // Keep it small, will be scaled up if needed with "dynamicThrottlingEnabled"
    name: 'Standard'  // Pay-as-you-go
  }
  properties: {
    dynamicThrottlingEnabled: true  // Declared as read-only but can be set
    // raiPolicyName: contentfilter.name
    versionUpgradeOption: 'NoAutoUpgrade'
    model: {
      format: 'OpenAI'
      name: gptBackupModel
      version: gptBackupVersion
    }
  }
}

// resource contentfilter 'Microsoft.CognitiveServices/accounts/raiPolicies@2023-06-01-preview' = {
//   parent: cognitiveOpenai
//   name: 'disabled'
//   tags: tags
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
  tags: tags
  sku: {
    capacity: 150
    name: 'Standard'  // Pay-as-you-go
  }
  properties: {
    dynamicThrottlingEnabled: true  // Declared as read-only but can be set
    // raiPolicyName: contentfilter.name
    versionUpgradeOption: 'NoAutoUpgrade'
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
    consistencyPolicy: {
      defaultConsistencyLevel: 'BoundedStaleness'  // ACID in a single-region, lag in the others
      maxIntervalInSeconds: 600  // 5 mins lags at maximum
      maxStalenessPrefix: 1000  // 1000 requests lags at max
    }
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
  name: appName
  properties: {
    resource: {
      id: appName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-11-15' = {
  parent: database
  name: cosmosContainerName
  properties: {
    resource: {
      id: cosmosContainerName
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
          '/initiate/phone_number'
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
    name: 'basic'  // Smallest with semantic search
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    semanticSearch: 'standard'
  }
}

resource translate 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${prefix}-${location}-translate'
  location: location
  tags: tags
  sku: {
    name: 'S1'  // Pay-as-you-go
  }
  kind: 'TextTranslation'
  properties: {
    customSubDomainName: '${prefix}-${location}-translate'
  }
}

resource redis 'Microsoft.Cache/redis@2023-08-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    sku: {
      capacity: 0
      family: 'C'
      name: 'Basic'
    }
  }
}
