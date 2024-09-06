param cognitiveCommunicationLocation string
param embeddingDeploymentType string = 'Standard' // Pay-as-you-go in a single region
param embeddingModel string = 'text-embedding-ada-002'
param embeddingQuota int = 100
param embeddingVersion string = '2'
param functionappLocation string
param instance string
param llmFastContext int = 128000
param llmFastDeploymentType string = 'GlobalStandard' // Pay-as-you-go in all regions
param llmFastModel string = 'gpt-4o-mini'
param llmFastQuota int = 600
param llmFastVersion string = '2024-07-18'
param llmSlowContext int = 128000
param llmSlowDeploymentType string = 'GlobalStandard' // Pay-as-you-go in all regions
param llmSlowModel string = 'gpt-4o'
param llmSlowQuota int = 300
param llmSlowVersion string = '2024-08-06'
param location string = deployment().location
param openaiLocation string
param promptContentFilter bool = true // Should be set to false but requires a custom approval from Microsoft
param searchLocation string
param version string

targetScope = 'subscription'

output appUrl string = app.outputs.appUrl
output blobStoragePublicName string = app.outputs.blobStoragePublicName
output functionAppName string = app.outputs.functionAppName
output logAnalyticsCustomerId string = app.outputs.logAnalyticsCustomerId

var tags = {
  application: 'call-center-ai'
  instance: instance
  managed_by: 'Bicep'
  sources: 'https://github.com/clemlesne/call-center-ai'
  version: version
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
    embeddingModel: embeddingModel
    embeddingQuota: embeddingQuota
    embeddingVersion: embeddingVersion
    functionappLocation: functionappLocation
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
    version: version
  }
}
