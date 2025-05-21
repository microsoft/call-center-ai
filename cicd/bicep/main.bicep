param cognitiveCommunicationLocation string
param embeddingDeploymentType string = 'Standard' // Pay-as-you-go in a single region
param embeddingDimensions int = 3072
param embeddingModel string = 'text-embedding-3-large'
param embeddingQuota int = 50
param embeddingVersion string = '1'
param imageVersion string = 'main'
param instance string
param llmFastContext int = 128000
param llmFastDeploymentType string = 'GlobalStandard' // Pay-as-you-go in all regions
param llmFastModel string = 'gpt-4.1-nano'
param llmFastQuota int = 150
param llmFastVersion string = '2025-04-14'
param llmSlowContext int = 128000
param llmSlowDeploymentType string = 'GlobalStandard' // Pay-as-you-go in all regions
param llmSlowModel string = 'gpt-4.1'
param llmSlowQuota int = 50
param llmSlowVersion string = '2025-04-14'
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
    llmFastContext: llmFastContext
    llmFastDeploymentType: llmFastDeploymentType
    llmFastModel: llmFastModel
    llmFastQuota: llmFastQuota
    llmFastVersion: llmFastVersion
    llmSlowContext: llmSlowContext
    llmSlowDeploymentType: llmSlowDeploymentType
    llmSlowModel: llmSlowModel
    llmSlowQuota: llmSlowQuota
    llmSlowVersion: llmSlowVersion
    location: location
    openaiLocation: openaiLocation
    promptContentFilter: promptContentFilter
    searchLocation: searchLocation
    tags: tags
  }
}
