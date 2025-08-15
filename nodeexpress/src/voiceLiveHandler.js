import WebSocket from 'ws';
import crypto from 'crypto';
import { getAgentAccessToken, AZURE_AGENT_ENDPOINT, AGENT_PROJECT_NAME, AGENT_ID } from './getAccessToken.js';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

export class VoiceLiveCommunicationHandler {
  constructor(ws) {
    this.voiceLiveWs = null;
    this.activeWebsocket = ws;
    this.conversationCallId = cryptoRandomUuid();
    this.isConnected = false;
    this.isAgentMode = true;
    this._voiceOverrideSent = false;
    this._voiceOverrideFallbackDone = false;
    this._currentResponseId = null;
    this._isStreamingAudio = false;
    this._responseItems = [];
    this._interruptionTimestamp = null;
    this._interruptionCooldown = 2.0;
  }

  async startConversationAsync() {
    if (!(AZURE_AGENT_ENDPOINT && AGENT_PROJECT_NAME && AGENT_ID)) {
      throw new Error('Agent mode required: set AZURE_AGENT_ENDPOINT, AGENT_PROJECT_NAME, and AGENT_ID in the environment.');
    }

    const baseWs = `${AZURE_AGENT_ENDPOINT.replace('https://', 'wss://').replace(/\/+$/,'')}/voice-live/realtime`;
    const agentAccessToken = await getAgentAccessToken();
    const wsUrl = `${baseWs}?api-version=2025-05-01-preview&agent-project-name=${AGENT_PROJECT_NAME}&agent-id=${AGENT_ID}&agent-access-token=${agentAccessToken}`;

    const headers = {
      'x-ms-client-request-id': cryptoRandomUuid(),
      Authorization: `Bearer ${agentAccessToken}`
    };

  return new Promise((resolve, reject) => {
      this.voiceLiveWs = new WebSocket(wsUrl, { headers });
      this.voiceLiveWs.on('open', async () => {
        this.isConnected = true;
        this._callStartTime = Date.now();
        await this._configureSession();
        this.receiveMessagesAsync();
        resolve();
      });
      this.voiceLiveWs.on('error', (err) => {
        reject(err);
      });
    });
  }

  async _configureSession() {
    const sessionBody = {
      turn_detection: { type: 'server_vad', threshold: 0.9, prefix_padding_ms: 1000, silence_duration_ms: 1000, create_response: true, interrupt_response: true },
      input_audio_noise_reduction: { type: 'azure_deep_noise_suppression' },
      input_audio_echo_cancellation: { type: 'server_echo_cancellation' },
      voice: { name: process.env.SESSION_VOICE_NAME || 'en-US-Davis:DragonHDLatestNeural', type: 'azure-standard', temperature: parseFloat(process.env.SESSION_VOICE_TEMPERATURE || '0.8') },
      modalities: ['text','audio']
    };

    const msg = { type: 'session.update', session: sessionBody, event_id: '' };
    this.voiceLiveWs.send(JSON.stringify(msg));

    const greeting = { type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [ { type: 'input_text', text: 'Hello' } ] } };
    this.voiceLiveWs.send(JSON.stringify(greeting));

    const responseReq = { type: 'response.create', response: { modalities: ['text','audio'] } };
    this.voiceLiveWs.send(JSON.stringify(responseReq));
  }

  receiveMessagesAsync() {
    this.voiceLiveWs.on('message', async (raw) => {
      try {
        const message = JSON.parse(raw.toString());
  await this._handleVoiceLiveMessage(message);
      } catch (e) {
  // avoid flooding on parse issues
      }
    });

    this.voiceLiveWs.on('close', () => {
      this.isConnected = false;
    });
  }

  async _handleVoiceLiveMessage(message) {
    // Minimal mapping: log types and mimic main behaviors
    const messageType = message.type;
    const eventId = message.event_id || '';
    const responseId = (message.response && message.response.id) || message.response_id || '';

    // Focus on critical events only
    if (messageType === 'session.created' || messageType === 'response.created' || messageType === 'response.done' || messageType === 'input_audio_buffer.speech_started' || messageType === 'input_audio_buffer.speech_stopped') {
      console.info(`${messageType} | event_id: ${eventId} | response_id: ${responseId}`);
    }

    switch (messageType) {
      case 'session.created':
        console.info('Session created');
        break;
      case 'response.created':
        this._currentResponseId = responseId;
        this._isStreamingAudio = true;
        this._responseItems = [];
        console.info('AI response created', responseId);
        break;
      case 'response.done':
        this._currentResponseId = null;
        this._isStreamingAudio = false;
        this._responseItems = [];
        console.info('AI response completed', responseId);
        break;
      case 'response.audio.delta':
        if (message.delta) await this.receiveAudio(message.delta);
        break;
      default:
        // ignore
        break;
    }
  }

  async receiveAudio(audioData) {
    // Forward audio back to ACS websocket
    try {
      if (this.activeWebsocket && this.activeWebsocket.readyState === WebSocket.OPEN) {
  // Match Python framing expected by ACS media streaming bridge
  const payload = { Kind: 'AudioData', AudioData: { Data: audioData }, StopAudio: null };
  await this.activeWebsocket.send(JSON.stringify(payload));
      }
    } catch (e) {
      console.error('Failed to forward audio to ACS websocket', e);
    }
  }

  async commitInputAudio() {
    if (!this.voiceLiveWs || !this.isConnected) return;
    const commitMsg = { type: 'input_audio_buffer.commit', event_id: `commit_${cryptoRandomUuid()}` };
    try {
      this.voiceLiveWs.send(JSON.stringify(commitMsg));
    } catch {}
  }

  async _handleUserInterruption(newItemId) {
    console.info('Interruption initiated', newItemId);
    this._isStreamingAudio = false;
    if (this.voiceLiveWs && this._currentResponseId) {
      const cancelMsg = { type: 'response.cancel', event_id: `cancel_${cryptoRandomUuid()}` };
      this.voiceLiveWs.send(JSON.stringify(cancelMsg));
    }
    if (this.voiceLiveWs) {
      const clearMsg = { type: 'input_audio_buffer.clear', event_id: `clear_input_${cryptoRandomUuid()}` };
      this.voiceLiveWs.send(JSON.stringify(clearMsg));
    }
    this._currentResponseId = null;
    this._interruptionTimestamp = Date.now();
  }

  // Python parity alias for app.js usage
  async send_audio_async(audioData) {
    return this.sendAudioAsync(audioData);
  }

  async sendAudioAsync(audioData) {
    if (!this.voiceLiveWs || !this.isConnected) return;
    const msg = { type: 'input_audio_buffer.append', audio: audioData, event_id: '' };
    try {
      this.voiceLiveWs.send(JSON.stringify(msg));
    } catch (e) {
      // keep quiet to avoid flooding
    }
  }
}

function cryptoRandomUuid() {
  return crypto.randomUUID();
}
