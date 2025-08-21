import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';
import { CallAutomationClient } from '@azure/communication-call-automation';
import { DefaultAzureCredential } from '@azure/identity';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

let _client = null;

export function getAcsClient() {
  if (_client) return _client;
  const conn = process.env.ACS_CONNECTION_STRING;
  const endpoint = process.env.ACS_ENDPOINT; // e.g., https://<resource>.communication.azure.com
  try {
    if (conn) {
      _client = new CallAutomationClient(conn);
    } else if (endpoint) {
      // Use managed identity in ACA
      const credential = new DefaultAzureCredential();
      _client = new CallAutomationClient(endpoint, credential);
    } else {
      console.error('ACS client config missing. Provide ACS_CONNECTION_STRING or ACS_ENDPOINT for managed identity.');
      return null;
    }
    return _client;
  } catch (e) {
    console.error('Failed to initialize ACS client:', e);
    return null;
  }
}
