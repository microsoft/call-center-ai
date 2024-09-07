param cognitiveCommunicationLocation string
param embeddingDeploymentType string
param embeddingModel string
param embeddingQuota int
param embeddingVersion string
param functionappLocation string
param llmFastContext int
param llmFastDeploymentType string
param llmFastModel string
param llmFastQuota int
param llmFastVersion string
param llmSlowContext int
param llmSlowDeploymentType string
param llmSlowModel string
param llmSlowQuota int
param llmSlowVersion string
param location string
param openaiLocation string
param promptContentFilter bool
param searchLocation string
param tags object
param version string

var appName = 'call-center-ai'
var prefix = deployment().name
var functionAppName = '${prefix}-${appName}'
var appUrl = 'https://${functionAppName}.azurewebsites.net'
var llmFastModelFullName = toLower('${llmFastModel}-${llmFastVersion}')
var llmSlowModelFullName = toLower('${llmSlowModel}-${llmSlowVersion}')
var embeddingModelFullName = toLower('${embeddingModel}-${embeddingVersion}')
var cosmosContainerName = 'calls-v3' // Third schema version
var localConfig = loadYamlContent('../config.yaml')
var phonenumberSanitized = replace(localConfig.communication_services.phone_number, '+', '')
var config = {
  public_domain: appUrl
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
  conversation: {
    initiate: {
      agent_phone_number: localConfig.conversation.initiate.agent_phone_number
      bot_company: localConfig.conversation.initiate.bot_company
      bot_name: localConfig.conversation.initiate.bot_name
      lang: localConfig.conversation.initiate.lang
    }
  }
  communication_services: {
    access_key: communicationServices.listKeys().primaryKey
    call_queue_name: callQueue.name
    endpoint: communicationServices.properties.hostName
    phone_number: localConfig.communication_services.phone_number
    post_queue_name: postQueue.name
    resource_id: communicationServices.properties.immutableResourceId
    sms_queue_name: smsQueue.name
    trainings_queue_name: trainingsQueue.name
  }
  sms: localConfig.sms
  cognitive_service: {
    endpoint: cognitiveCommunication.properties.endpoint
  }
  llm: {
    fast: {
      mode: 'azure_openai'
      azure_openai: {
        context: llmFastContext
        deployment: llmFast.name
        endpoint: cognitiveOpenai.properties.endpoint
        model: llmFastModel
        streaming: true
      }
    }
    slow: {
      mode: 'azure_openai'
      azure_openai: {
        context: llmSlowContext
        deployment: llmSlow.name
        endpoint: cognitiveOpenai.properties.endpoint
        model: llmSlowModel
        streaming: true
      }
    }
  }
  ai_search: {
    access_key: search.listAdminKeys().primaryKey
    endpoint: 'https://${search.name}.search.windows.net'
    index: 'trainings'
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
output functionAppName string = functionAppName
output logAnalyticsCustomerId string = logAnalytics.properties.customerId

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: prefix
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018' // Pay-as-you-go
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
        // See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#azurewebjobsstorage
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        // See: https://learn.microsoft.com/en-us/azure/azure-functions/functions-app-settings#applicationinsights_connection_string
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: applicationInsights.properties.ConnectionString
        }
        // See: https://learn.microsoft.com/en-us/azure/azure-monitor/app/opentelemetry-configuration?tabs=python#enable-sampling
        {
          name: 'OTEL_TRACES_SAMPLER_ARG'
          value: '0.5'
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
            storageAccountConnectionStringName: 'AzureWebJobsStorage'
            type: 'StorageAccountConnectionString'
          }
        }
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 2048 // Default and recommended
        maximumInstanceCount: 100 // TODO: Avoid billing surprises, delete when sure
        alwaysReady: [
          {
            instanceCount: 2
            name: 'http' // Handle "conversation" events
          }
        ]
        triggers: {
          http: {
            perInstanceConcurrency: 4
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: replace(toLower(prefix), '-', '')
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

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource callQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'call-${phonenumberSanitized}'
}

resource smsQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'sms-${phonenumberSanitized}'
}

resource postQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'post-${phonenumberSanitized}'
}

resource trainingsQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'trainings-${phonenumberSanitized}'
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource publicBlob 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: '$web'
}

resource functionAppBlob 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: toLower(functionAppName)
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

resource eventgridTopic 'Microsoft.EventGrid/systemTopics@2024-06-01-preview' = {
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
          queueMessageTimeToLiveInSeconds: 30 // Short lived messages, only new call events
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
          queueMessageTimeToLiveInSeconds: -1 // Infinite persistence, SMS is async
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

resource cognitiveCommunication 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: '${prefix}-${cognitiveCommunicationLocation}-communication'
  location: cognitiveCommunicationLocation
  tags: tags
  sku: {
    name: 'S0' // Only one available
  }
  kind: 'CognitiveServices'
  properties: {
    customSubDomainName: '${prefix}-${cognitiveCommunicationLocation}-communication'
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

resource cognitiveOpenai 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: '${prefix}-${openaiLocation}-openai'
  location: openaiLocation
  tags: tags
  sku: {
    name: 'S0' // Pay-as-you-go
  }
  kind: 'OpenAI'
  properties: {
    customSubDomainName: '${prefix}-${openaiLocation}-openai'
  }
}

resource contentfilter 'Microsoft.CognitiveServices/accounts/raiPolicies@2024-04-01-preview' = {
  parent: cognitiveOpenai
  name: 'disabled'
  tags: tags
  properties: {
    basePolicyName: 'Microsoft.Default'
    mode: 'Deferred' // Async moderation
    contentFilters: [
      // Indirect attacks
      {
        allowedContentLevel: 'Medium'
        blocking: true
        enabled: true
        name: 'indirect_attack'
        source: 'Prompt'
      }
      // Jailbreak
      {
        allowedContentLevel: 'Medium'
        blocking: true
        enabled: true
        name: 'jailbreak'
        source: 'Prompt'
      }
      // Prompt
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'hate'
        source: 'Prompt'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'sexual'
        source: 'Prompt'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'selfharm'
        source: 'Prompt'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'violence'
        source: 'Prompt'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'profanity'
        source: 'Prompt'
      }
      // Completion
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'hate'
        source: 'Completion'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'sexual'
        source: 'Completion'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'selfharm'
        source: 'Completion'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'violence'
        source: 'Completion'
      }
      {
        allowedContentLevel: 'Low'
        blocking: !promptContentFilter
        enabled: !promptContentFilter
        name: 'profanity'
        source: 'Completion'
      }
    ]
  }
}

resource llmSlow 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: cognitiveOpenai
  name: llmSlowModelFullName
  tags: tags
  sku: {
    capacity: llmSlowQuota
    name: llmSlowDeploymentType
  }
  properties: {
    raiPolicyName: contentfilter.name
    versionUpgradeOption: 'NoAutoUpgrade'
    model: {
      format: 'OpenAI'
      name: llmSlowModel
      version: llmSlowVersion
    }
  }
}

resource llmFast 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: cognitiveOpenai
  name: llmFastModelFullName
  tags: tags
  sku: {
    capacity: llmFastQuota
    name: llmFastDeploymentType
  }
  properties: {
    raiPolicyName: contentfilter.name
    versionUpgradeOption: 'NoAutoUpgrade'
    model: {
      format: 'OpenAI'
      name: llmFastModel
      version: llmFastVersion
    }
  }
  dependsOn: [
    llmSlow
  ]
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: cognitiveOpenai
  name: embeddingModelFullName
  tags: tags
  sku: {
    capacity: embeddingQuota
    name: embeddingDeploymentType
  }
  properties: {
    raiPolicyName: contentfilter.name
    versionUpgradeOption: 'NoAutoUpgrade'
    model: {
      format: 'OpenAI'
      name: embeddingModel
      version: embeddingVersion
    }
  }
  dependsOn: [
    llmFast
  ]
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: prefix
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    consistencyPolicy: {
      defaultConsistencyLevel: 'BoundedStaleness' // ACID in a single-region, lag in the others
      maxIntervalInSeconds: 600 // 5 mins lags at maximum
      maxStalenessPrefix: 1000 // 1000 requests lags at max
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

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: appName
  properties: {
    resource: {
      id: appName
    }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
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

resource search 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: prefix
  location: searchLocation
  tags: tags
  sku: {
    name: 'basic' // Smallest with semantic search
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    semanticSearch: 'standard'
  }
}

resource translate 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: '${prefix}-${location}-translate'
  location: location
  tags: tags
  sku: {
    name: 'S1' // Pay-as-you-go
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
      capacity: 0 // 250 MB of data
      family: 'C'
      name: 'Standard' // First tier with SLA
    }
    minimumTlsVersion: '1.2'
    redisVersion: '6' // v6.x
  }
}
