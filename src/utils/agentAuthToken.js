// Deprecated: use getAgentAccessToken() from src/getAccessToken.js
import { getAgentAccessToken } from '../getAccessToken.js';

export async function getAzureAccessToken() {
  console.warn('[deprecation] src/utils/agentAuthToken.js is deprecated. Use getAgentAccessToken from src/getAccessToken.js');
  return getAgentAccessToken();
}
