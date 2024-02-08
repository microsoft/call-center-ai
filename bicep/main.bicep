param adaModel string = 'text-embedding-ada-002'
param adaVersion string = '2'
param agentPhoneNumber string
param botCompany string
param botName string
param botPhoneNumber string
param gptModel string = 'gpt-4'
param gptVersion string = '1106-Preview'
param instance string = deployment().name
param location string = deployment().location
param openaiLocation string
param searchLocation string

targetScope = 'subscription'

output appUrl string = app.outputs.appUrl
output blobStoragePublicName string = app.outputs.blobStoragePublicName
output communicationId string = app.outputs.communicationId

var tags = {
  application: 'claim-ai'
  instance: instance
  managed_by: 'Bicep'
  sources: 'https://github.com/clemlesne/claim-ai-phone-bot'
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
    adaModel: adaModel
    adaVersion: adaVersion
    agentPhoneNumber: agentPhoneNumber
    botCompany: botCompany
    botName: botName
    botPhoneNumber: botPhoneNumber
    botVoiceName: 'fr-FR-DeniseNeural'
    gptModel: gptModel
    gptVersion: gptVersion
    location: location
    moderationBlocklists: []
    openaiLocation: openaiLocation
    searchLocation: searchLocation
    tags: tags
  }
}
