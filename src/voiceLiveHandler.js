import WebSocket from 'ws';
import crypto from 'crypto';
import { getAgentAccessToken, AZURE_AGENT_ENDPOINT, AGENT_PROJECT_NAME, AGENT_ID } from './getAccessToken.js';
import { startRecording, writePcm, stopRecording, cleanupSessionTemp } from './services/recorderService.js';
import { uploadFile } from './services/uploaderService.js';
import createMicroBatcher from './utils/microBatcher.js';
import Sessions from './models/ContactModel.js';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(process.cwd(), '.env') });

export class VoiceLiveCommunicationHandler {
  constructor(ws, callerId = 'unknown') {
    this.voiceLiveWs = null;
    this.activeWebsocket = ws;
    this.conversationCallId = cryptoRandomUuid();
    this.callerId = callerId; // Store caller ID for session creation
    //console.log(`🎬 [DEBUG] VoiceLiveHandler created with callerId: ${callerId} for session: ${this.conversationCallId}`);
    this.isConnected = false;
    this.isAgentMode = true;
    this._voiceOverrideSent = false;
    this._voiceOverrideFallbackDone = false;
    this._currentResponseId = null;
    this._isStreamingAudio = false;
    this._responseItems = [];
    this._interruptionTimestamp = null;
  this._interruptionCooldown = parseFloat(process.env.INTERRUPTION_COOLDOWN_SEC || '1.0');
  this._ttsStopTailMs = parseInt(process.env.TTS_STOP_TAIL_MS || '0', 10);
  // Minimum user speech duration (ms) required to consider it a real interruption
  this._minUserSpeechMs = parseInt(process.env.MIN_USER_SPEECH_MS || '250', 10);
  // internal tracking for speech_started/stopped heuristic
  this._speechStartedAt = null;
  this._pendingInterruptionTimer = null;
  // prevent duplicate interruption handling for the same speech event
  this._interruptionHandled = false;
    // Recording state for this conversation (mixed user + assistant into same recorder)
    this.audioState = null; // { sessionId, outPath, startedAt }

    // Transcript batcher to reduce DB writes
    this.transcriptBatcher = createMicroBatcher({
      flushMs: parseInt(process.env.BATCH_FLUSH_MS || '300', 10),
      maxSize: parseInt(process.env.MAX_BATCH_SIZE || '25', 10),
      onFlush: async (batch) => {
        try {
          // batch elements will be annotated with seq when appended
          await Sessions.findOneAndUpdate(
            { sessionId: this.conversationCallId },
            { 
              $inc: { seqNextAllocation: batch.length, messagesCount: batch.length },
              $setOnInsert: { 
                sessionId: this.conversationCallId, 
                startedAt: new Date(), 
                status: 'active', 
                channel: 'PSTN', 
                callerId: this.callerId 
              }
            },
            { new: true, upsert: true }
          );
          // compute and attach seqs then push
          // reuse transcriptService.appendTranscriptBatch logic via direct update
          // but here we'll attach seqs based on the updated seqNextAllocation
          // Note: simpler approach: call a service function would be ideal; for now push raw segments
          await Sessions.updateOne({ sessionId: this.conversationCallId }, { $push: { transcriptSegments: { $each: batch } } });
        } catch (e) {
          console.error('Failed to flush transcript batch', e);
          throw e;
        }
      }
    });
  }

  async startConversationAsync() {
    console.log('F [VoiceLive] Starting conversation connection...');
    if (!(AZURE_AGENT_ENDPOINT && AGENT_PROJECT_NAME && AGENT_ID)) {
      console.error('F [VoiceLive] Missing required environment variables');
      throw new Error('Agent mode required: set AZURE_AGENT_ENDPOINT, AGENT_PROJECT_NAME, and AGENT_ID in the environment.');
    }

    // console.log(`F [VoiceLive] Endpoint: ${AZURE_AGENT_ENDPOINT}, Project: ${AGENT_PROJECT_NAME}, Agent: ${AGENT_ID}`);

  const baseWs = `${AZURE_AGENT_ENDPOINT.replace('https://', 'wss://').replace(/\/+$/,'')}/voice-live/realtime`;
  // console.log('F [VoiceLive] Getting access token...');
  const agentAccessToken = await getAgentAccessToken();
  // console.log('F [VoiceLive] Access token acquired successfully');
  const wsUrl = `${baseWs}?api-version=2025-05-01-preview&agent-project-name=${AGENT_PROJECT_NAME}&agent-id=${AGENT_ID}&agent-access-token=${agentAccessToken}`;
  // console.log(`F [VoiceLive] Connecting to: ${wsUrl.replace(agentAccessToken, 'REDACTED_TOKEN')}`);

    const headers = {
      'x-ms-client-request-id': cryptoRandomUuid(),
      Authorization: `Bearer ${agentAccessToken}`
    };

  return new Promise((resolve, reject) => {
  this.voiceLiveWs = new WebSocket(wsUrl, { headers });
      this.voiceLiveWs.on('open', async () => {
        console.log('F [VoiceLive] WebSocket connection opened successfully');
        this.isConnected = true;
        this._callStartTime = Date.now();
        console.log('F [VoiceLive] About to configure session...');
        await this.configureSession();
        console.log('F [VoiceLive] Session configuration sent, setting up message listeners...');
        this.receiveMessagesAsync();
        resolve();
      });
      this.voiceLiveWs.on('error', (err) => {
        console.error('F [VoiceLive] WebSocket connection error:', err);
        reject(err);
      });
    });
  }

  async configureSession() {
    console.log('F [VoiceLive] Configuring session...');
    const sessionBody = {
      turn_detection: { type: 'server_vad', threshold: 0.7, prefix_padding_ms: 300, silence_duration_ms: 200, create_response: true, interrupt_response: true },
      input_audio_noise_reduction: { type: 'azure_deep_noise_suppression' },
      input_audio_echo_cancellation: { type: 'server_echo_cancellation' },
      voice: { name: process.env.SESSION_VOICE_NAME || 'en-US-Davis:DragonHDLatestNeural', type: 'azure-standard', temperature: parseFloat(process.env.SESSION_VOICE_TEMPERATURE || '0.8') },
      modalities: ['text','audio']
    };

    // console.log(`F [VoiceLive] Session config: ${JSON.stringify(sessionBody)}`);
    const msg = { type: 'session.update', session: sessionBody, event_id: '' };
    // console.log('F [VoiceLive] Sending session.update...');
    this.voiceLiveWs.send(JSON.stringify(msg));

    // By default, do NOT send an automatic greeting / response request.
    if ((process.env.AUTO_GREETING || '').toLowerCase() === 'true') {
      // console.log('F [VoiceLive] Creating greeting conversation item...');
      const greeting = { type: 'conversation.item.create', item: { type: 'message', role: 'user', content: [ { type: 'input_text', text: 'Hello' } ] } };
      this.voiceLiveWs.send(JSON.stringify(greeting));

      // console.log('F [VoiceLive] Requesting response...');
      const responseReq = { type: 'response.create', response: { modalities: ['text','audio'] } };
      this.voiceLiveWs.send(JSON.stringify(responseReq));
      // console.log('F [VoiceLive] Session configuration completed (auto-greeting enabled)');
    } else {
      console.log('F [VoiceLive] AUTO_GREETING not enabled — skipping automatic greeting/response to avoid unintended TTS of errors');
    }
  }

  receiveMessagesAsync() {
    console.log('F [VoiceLive] Setting up message listeners...');
    this.voiceLiveWs.on('message', async (raw) => {
      try {
        const message = JSON.parse(raw.toString());
        console.log(`F [VoiceLive] Received message: ${message.type} | event_id: ${message.event_id || 'none'}`);
        await this._handleVoiceLiveMessage(message);
      } catch (e) {
        console.error(`F [VoiceLive] Message parse error: ${e.message}`);
        // avoid flooding on parse issues
      }
    });

      this.voiceLiveWs.on('close', () => {
        console.log('F [VoiceLive] WebSocket connection closed');
        this.isConnected = false;
        // finalize recording: stop ffmpeg, upload and persist to Sessions
        this.finalizeRecording();
      });

    this.voiceLiveWs.on('error', (err) => {
      console.error('F [VoiceLive] WebSocket error:', err);
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
  // new AI response -> allow interruptions again for the upcoming speech events
  this._interruptionHandled = false;
        console.info('AI response created', responseId);
        break;
      case 'input_audio_buffer.speech_started': {
        // User started speaking – don't interrupt immediately. Use a short confirmation
        // window to avoid reacting to very short noises or artifacts.
        try {
          // reset any previous timer
          if (this._pendingInterruptionTimer) {
            clearTimeout(this._pendingInterruptionTimer);
            this._pendingInterruptionTimer = null;
          }
          this._speechStartedAt = Date.now();
          // new speech attempt -> reset the per-speech interruption gate
          this._interruptionHandled = false;
          const confirmMs = Math.max(0, this._minUserSpeechMs | 0);
          // schedule confirmation: if we don't see a quick speech_stopped event
          // that is shorter than the minimum, we'll treat this as a valid interruption
          this._pendingInterruptionTimer = setTimeout(async () => {
            const now = Date.now();
            if (!this._interruptionTimestamp || (now - this._interruptionTimestamp) / 1000 > this._interruptionCooldown) {
              await this._handleUserInterruption('speech_started_confirmed');
            }
            this._pendingInterruptionTimer = null;
          }, confirmMs);
        } catch (e) {
          // fall back to immediate interruption on unexpected errors
          const now = Date.now();
          if (!this._interruptionTimestamp || (now - this._interruptionTimestamp) / 1000 > this._interruptionCooldown) {
            await this._handleUserInterruption('speech_started');
          }
        }
        break;
      }

      case 'input_audio_buffer.speech_stopped': {
        // User stopped speaking: determine how long they spoke and only interrupt
        // if the speech duration meets the minimum threshold. This avoids
        // treating very short noises as real user input.
        try {
          const stoppedAt = Date.now();
          const startedAt = this._speechStartedAt || stoppedAt;
          const durationMs = Math.max(0, stoppedAt - startedAt);
          if (this._pendingInterruptionTimer) {
            clearTimeout(this._pendingInterruptionTimer);
            this._pendingInterruptionTimer = null;
          }
          this._speechStartedAt = null;

          if (durationMs >= (this._minUserSpeechMs | 0)) {
            const now = Date.now();
            if (!this._interruptionTimestamp || (now - this._interruptionTimestamp) / 1000 > this._interruptionCooldown) {
              await this._handleUserInterruption('speech_stopped');
            }
          } else {
            // too short -> ignore as noise
            console.info(`Ignored short user speech (${durationMs}ms) as noise`);
          }
        } catch (e) {
          // on error, be conservative and do nothing
        }
        break;
      }
      case 'conversation.item.input_audio_transcription.completed': {
        const t = (message.transcript || '').trim();
        if (t) console.info(`User: ${t}`);
            try {
              const seg = {
                text: t,
                startTs: message?.startTime ? new Date(message.startTime) : new Date(),
                endTs: message?.endTime ? new Date(message.endTime) : new Date(),
                speaker: 'user',
                confidence: message?.confidence ?? null,
              };
              this.transcriptBatcher.push(seg);
            } catch (e) {
              console.error('Failed to queue user transcript segment', e);
            }
            break;
      }
      case 'conversation.item.input_audio_transcription.failed': {
        const err = message.error || message.reason || 'transcription failed';
        console.error(`User transcription error: ${typeof err === 'string' ? err : JSON.stringify(err)}`);
        break;
      }
      case 'response.audio_transcript.done': {
        const t = (message.transcript || '').trim();
        if (t) console.info(`AI: ${t}`);
        try {
          const seg = {
            text: t,
            startTs: message?.startTime ? new Date(message.startTime) : new Date(),
            endTs: message?.endTime ? new Date(message.endTime) : new Date(),
            speaker: 'assistant',
            confidence: message?.confidence ?? null,
          };
          this.transcriptBatcher.push(seg);
        } catch (e) {
          console.error('Failed to queue assistant transcript segment', e);
        }
        break;
      }
      case 'response.done':
        this._currentResponseId = null;
        this._isStreamingAudio = false;
        this._responseItems = [];
        // console.info('AI response completed', responseId);
        break;
      case 'response.audio.delta':
        // Gate by current active response; drop stale audio frames
        if (this._isStreamingAudio === false) break;
        if (message.response_id && this._currentResponseId && message.response_id !== this._currentResponseId) break;
        if (message.delta) {
          try {
            // Start recording on first audio chunk (like js_server)
            if (!this.audioState) {
              try {
                // console.log('🎬 [RECORDING] Starting recording for first assistant audio chunk');
                const { sessionId, outPath } = startRecording(this.conversationCallId);
                this.audioState = { sessionId, outPath, startedAt: new Date() };
                // console.log('🎬 [RECORDING] Recording started:', { sessionId, outPath });
                try {
                  console.log(`🎬 [DB] Attempting to create session: ${this.conversationCallId} for caller: ${this.callerId}`);
                  const result = await Sessions.updateOne({ sessionId: this.conversationCallId }, { $setOnInsert: { sessionId: this.conversationCallId, startedAt: new Date(), status: 'active', channel: 'PSTN', callerId: this.callerId } }, { upsert: true });
                  console.log(`🎬 [DB] Session creation result:`, result);
                } catch (e) {
                  console.error('🎬 [DB] Session creation failed:', e.message);
                }
              } catch (e) {
                console.error('Failed to start recording for session', this.conversationCallId, e);
              }
            }
            
            const abuf = (typeof message.delta === 'string') ? Buffer.from(message.delta, 'base64') : Buffer.from(message.delta);
            // console.log(`🎬 [RECORDING] Writing assistant audio: ${abuf.length} bytes`);
            try { 
              writePcm(this.conversationCallId, abuf); 
            } catch (e) {
              // Only log if we expect recording to be active (audioState exists)
              if (this.audioState) {
                console.error('🎬 [RECORDING] Error writing assistant audio:', e.message);
              }
              // ignore write errors; do not affect WS
            }
          } catch (e) {}
          await this.receiveAudio(message.delta);
        }
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
    // Ensure we only handle one interruption per speech event
    if (this._interruptionHandled) {
      // console.info('Interruption already handled for current speech, skipping', newItemId);
      return;
    }
    this._interruptionHandled = true;
    console.info('Interruption initiated', newItemId);
    this._isStreamingAudio = false;
    // Stop audio downstream to ACS immediately
    await this._sendStopAudioToAcs();

    if (this.voiceLiveWs && this._currentResponseId) {
      const cancelMsg = { type: 'response.cancel', event_id: `cancel_${cryptoRandomUuid()}` };
      this.voiceLiveWs.send(JSON.stringify(cancelMsg));
    }
    if (this.voiceLiveWs) {
      const clearMsg = { type: 'input_audio_buffer.clear', event_id: `clear_input_${cryptoRandomUuid()}` };
      this.voiceLiveWs.send(JSON.stringify(clearMsg));
    }
    // Optionally leave a tiny tail before committing input audio to improve VAD cut
    const tail = Math.max(0, this._ttsStopTailMs | 0);
    if (tail > 0) {
      await new Promise(r => setTimeout(r, tail));
    }
    await this.commitInputAudio();
    this._currentResponseId = null;
    this._interruptionTimestamp = Date.now();
  }

  // Python parity alias for app.js usage
  async send_audio_async(audioData) {
    return this.sendAudioAsync(audioData);
  }

  async sendAudioAsync(audioData) {
    if (!this.voiceLiveWs || !this.isConnected) return;
    
    // Start recording on first user audio and write PCM into recorder
    try {
      if (!this.audioState) {
        try {
          const { sessionId, outPath } = startRecording(this.conversationCallId);
          this.audioState = { sessionId, outPath, startedAt: new Date() };
          console.log('🎬 [RECORDING] Recording started:', { sessionId, outPath });
          try {
            await Sessions.updateOne({ sessionId: this.conversationCallId }, { $setOnInsert: { sessionId: this.conversationCallId, startedAt: new Date(), status: 'active', channel: 'PSTN', callerId: this.callerId } }, { upsert: true });
          } catch (e) {
            // ignore db errors
          }
        } catch (e) {
          console.error('Failed to start recording for session', this.conversationCallId, e);
        }
      }
      
      let buf;
      if (typeof audioData === 'string') buf = Buffer.from(audioData, 'base64');
      else if (Buffer.isBuffer(audioData)) buf = audioData;
      else buf = Buffer.from(audioData);
      
      try { 
        writePcm(this.conversationCallId, buf); 
      } catch (e) {
        // Only log if we expect recording to be active (audioState exists)
        if (this.audioState) {
          console.error('🎬 [RECORDING] Error writing user audio:', e.message);
        }
        // ignore write errors; do not affect WS
      }
    } catch (e) {}

    const msg = { type: 'input_audio_buffer.append', audio: audioData, event_id: '' };
    try { this.voiceLiveWs.send(JSON.stringify(msg)); } catch (e) {}
  }

  async _sendStopAudioToAcs() {
    try {
      if (this.activeWebsocket && this.activeWebsocket.readyState === WebSocket.OPEN) {
        const payload = { Kind: 'StopAudio', StopAudio: {} };
        this.activeWebsocket.send(JSON.stringify(payload));
      }
    } catch (e) {
      // best-effort
    }
  }

  async finalizeRecording() {
    if (this.audioState) {
      console.log('🎬 [RECORDING] Finalizing recording');
      try {
        const rec = await stopRecording(this.conversationCallId);
        // console.log('🎬 [RECORDING] Stop recording result:', rec ? `File created: ${rec.outPath}` : 'No file (too small)');
        if (rec && rec.outPath) {
          try {
            const date = new Date();
            const y = date.getUTCFullYear();
            const m = String(date.getUTCMonth() + 1).padStart(2, '0');
            const d = String(date.getUTCDate()).padStart(2, '0');
            const blobName = `recordings/${y}/${m}/${d}/${this.conversationCallId}.ogg`;
            // console.log('🎬 [RECORDING] Uploading to blob:', blobName);
            const meta = await uploadFile(rec.outPath, blobName);
            // console.log('🎬 [RECORDING] Upload successful:', meta.url);
            try {
              await Sessions.updateOne({ sessionId: this.conversationCallId }, { $set: { finalRecordingUrl: meta.url, 'audio.recordingBlobName': meta.blobName, 'audio.codec': 'opus', 'audio.sampleRate': 24000, 'audio.sha256': meta.sha256, 'audio.etag': meta.etag, 'audio.sizeBytes': meta.sizeBytes, endedAt: new Date(), status: 'completed' } });
              console.log('🎬 [RECORDING] Session status updated to completed');
            } catch (e) {
              console.error('Failed to update session record with audio metadata', e);
            }
          } catch (e) {
            console.error('Failed to upload recording for session', this.conversationCallId, e);
            // Still update session status even if upload failed
            try {
              await Sessions.updateOne({ sessionId: this.conversationCallId }, { $set: { endedAt: new Date(), status: 'completed' } });
              console.log('🎬 [RECORDING] Session status updated to completed (no upload)');
            } catch (e2) {
              console.error('Failed to update session status', e2);
            }
          }
        } else {
          // console.log('🎬 [RECORDING] No recording file to upload (too small), updating session status');
          try {
            await Sessions.updateOne({ sessionId: this.conversationCallId }, { $set: { endedAt: new Date(), status: 'completed' } });
            // console.log('🎬 [RECORDING] Session status updated to completed (no recording)');
          } catch (e) {
            console.error('Failed to update session status', e);
          }
        }
      } catch (e) {
        console.error('Error finalizing recording for session', this.conversationCallId, e);
        // Still try to update session status
        try {
          await Sessions.updateOne({ sessionId: this.conversationCallId }, { $set: { endedAt: new Date(), status: 'error' } });
          // console.log('🎬 [RECORDING] Session status updated to error');
        } catch (e2) {
          console.error('Failed to update session status to error', e2);
        }
      } finally {
        try { cleanupSessionTemp(this.conversationCallId); } catch (e) {}
        this.audioState = null;
      }
    } else {
      // console.log('🎬 [RECORDING] No audio state found, updating session status anyway');
      try {
        await Sessions.updateOne({ sessionId: this.conversationCallId }, { $set: { endedAt: new Date(), status: 'completed' } });
        // console.log('🎬 [RECORDING] Session status updated to completed (no audio state)');
      } catch (e) {
        console.error('Failed to update session status', e);
      }
    }
    // flush any remaining transcript segments
    try { await this.transcriptBatcher.flush(); } catch (e) {}
  }
}

function cryptoRandomUuid() {
  return crypto.randomUUID();
}
