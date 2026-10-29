param keyVaultName string
param secretName string
param principalId string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource secret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: secretName
}

// Key Vault Secrets User, scoped to only the SAM.gov credential.
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(secret.id, principalId, '4633458b-17de-408a-b874-0445c86b69e6')
  scope: secret
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}
