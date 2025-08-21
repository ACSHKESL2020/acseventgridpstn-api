import { DefaultAzureCredential, ClientSecretCredential } from '@azure/identity';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

export const ACCESS_TOKEN = process.env.ACCESS_TOKEN || null;
export const AZURE_AGENT_ENDPOINT = process.env.AZURE_AGENT_ENDPOINT || null;
export const AGENT_PROJECT_NAME = process.env.AGENT_PROJECT_NAME || null;
export const AGENT_ID = process.env.AGENT_ID || null;

const credential = new ClientSecretCredential(
  process.env.AZURE_TENANT_ID,
  process.env.AZURE_CLIENT_ID,
  process.env.AZURE_CLIENT_SECRET
);

export async function getAgentAccessToken() {
  console.log('F [getAgentAccessToken] Starting access token acquisition...');
  console.log('F [getAgentAccessToken] Using ClientSecretCredential for Azure AI Foundry');
  try {
    // Azure AI Foundry requires the ai.azure.com audience specifically
    const token = await credential.getToken('https://ai.azure.com/.default');
    console.log('F [getAgentAccessToken] Successfully acquired token using ClientSecretCredential for ai.azure.com');
    return token.token;
  } catch (e) {
    const msg = e?.message || String(e);
    console.error(`F [getAgentAccessToken] ClientSecretCredential failed: ${msg}`);
    throw new Error(`Failed to acquire agent access token with client credentials: ${msg}`);
  }
}
