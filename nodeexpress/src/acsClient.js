import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';
import { CallAutomationClient } from '@azure/communication-call-automation';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

let _client = null;

export function getAcsClient() {
  if (_client) return _client;
  const conn = process.env.ACS_CONNECTION_STRING;
  if (!conn) return null;
  try {
    _client = new CallAutomationClient(conn);
    return _client;
  } catch (e) {
    console.error('Failed to initialize ACS client:', e);
    return null;
  }
}
