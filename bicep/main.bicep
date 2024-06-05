param cognitiveCommunicationLocation string
param embeddingModel string = 'text-embedding-ada-002'
param embeddingQuota int = 40  // Keep it small, will be scaled up if regional quota remains
param embeddingVersion string = '2'
param functionappLocation string
param instance string = deployment().name
param llmFastContext int = 16385
param llmFastModel string = 'gpt-35-turbo'
param llmFastQuota int = 40  // Keep it small, will be scaled up if regional quota remains
param llmFastVersion string = '0125'
param llmSlowContext int = 128000
param llmSlowModel string = 'gpt-4o'
param llmSlowQuota int = 40  // Keep it small, will be scaled up if regional quota remains
param llmSlowVersion string = '2024-05-13'
param location string = deployment().location
param openaiLocation string
param searchLocation string
param version string

targetScope = 'subscription'

output appUrl string = app.outputs.appUrl
output blobStoragePublicName string = app.outputs.blobStoragePublicName
output communicationId string = app.outputs.communicationId
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
    embeddingModel: embeddingModel
    embeddingQuota: embeddingQuota
    embeddingVersion: embeddingVersion
    functionappLocation: functionappLocation
    llmFastContext: llmFastContext
    llmFastModel: llmFastModel
    llmFastQuota: llmFastQuota
    llmFastVersion: llmFastVersion
    llmSlowContext: llmSlowContext
    llmSlowModel: llmSlowModel
    llmSlowQuota: llmSlowQuota
    llmSlowVersion: llmSlowVersion
    location: location
    moderationBlocklists: []
    openaiLocation: openaiLocation
    searchLocation: searchLocation
    tags: tags
    version: version
  }
}
