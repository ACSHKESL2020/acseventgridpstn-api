import { DefaultAzureCredential } from '@azure/identity';
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

export async function getAgentAccessToken() {
  if (ACCESS_TOKEN) return ACCESS_TOKEN;
  const credential = new DefaultAzureCredential();
  try {
    const tokenResponse = await credential.getToken('https://ai.azure.com/.default');
    return tokenResponse.token;
  } finally {
    // No close API in Node DefaultAzureCredential
  }
}
