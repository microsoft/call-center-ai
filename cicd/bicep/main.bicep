param cognitiveCommunicationLocation string
param embeddingDeploymentType string = 'Standard' // Pay-as-you-go in a single region
param embeddingDimensions int = 3072
param embeddingModel string = 'text-embedding-3-large'
param embeddingQuota int = 50
param embeddingVersion string = '1'
param imageVersion string = 'main'
param instance string
param llmRealtimeContext int = 128000
param llmRealtimeDeploymentType string = 'GlobalStandard' // Pay-as-you-go in all regions
param llmRealtimeModel string = 'gpt-4o-realtime-preview'
param llmRealtimeQuota int = 1
param llmRealtimeVersion string = '2024-10-01'
param llmSequentialContext int = 128000
param llmSequentialDeploymentType string = 'GlobalStandard' // Pay-as-you-go in all regions
param llmSequentialModel string = 'gpt-4o'
param llmSequentialQuota int = 300
param llmSequentialVersion string = '2024-08-06'
param location string = deployment().location
param openaiLocation string
param promptContentFilter bool = true // Should be set to false but requires a custom approval from Microsoft
param searchLocation string

targetScope = 'subscription'

output appUrl string = app.outputs.appUrl
output blobStoragePublicName string = app.outputs.blobStoragePublicName
output containerAppName string = app.outputs.containerAppName
output logAnalyticsCustomerId string = app.outputs.logAnalyticsCustomerId

var tags = {
  application: 'call-center-ai'
  instance: instance
  managed_by: 'Bicep'
  sources: 'https://github.com/clemlesne/call-center-ai'
  version: imageVersion
}

resource sub 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  location: location
  name: instance
  tags: tags
}

module app 'app.bicep' = {
  name: instance
  scope: sub
  params: {
    cognitiveCommunicationLocation: cognitiveCommunicationLocation
    embeddingDeploymentType: embeddingDeploymentType
    embeddingDimensions: embeddingDimensions
    embeddingModel: embeddingModel
    embeddingQuota: embeddingQuota
    embeddingVersion: embeddingVersion
    imageVersion: imageVersion
    llmRealtimeContext: llmRealtimeContext
    llmRealtimeDeploymentType: llmRealtimeDeploymentType
    llmRealtimeModel: llmRealtimeModel
    llmRealtimeQuota: llmRealtimeQuota
    llmRealtimeVersion: llmRealtimeVersion
    llmSequentialContext: llmSequentialContext
    llmSequentialDeploymentType: llmSequentialDeploymentType
    llmSequentialModel: llmSequentialModel
    llmSequentialQuota: llmSequentialQuota
    llmSequentialVersion: llmSequentialVersion
    location: location
    openaiLocation: openaiLocation
    promptContentFilter: promptContentFilter
    searchLocation: searchLocation
    tags: tags
  }
}
