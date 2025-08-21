import express from 'express';
import dotenv from 'dotenv';
import mongoose from 'mongoose';
import path from 'path';
import { fileURLToPath } from 'url';
import crypto from 'crypto';
import { getAcsClient } from './acsClient.js';
// import { BlobServiceClient } from '@azure/storage-blob';

dotenv.config({ path: path.resolve(process.cwd(), '.env') });

// Connect to MongoDB if MONGO_URL present
const mongoUrl = process.env.MONGO_URL || process.env.MONGO_URI || null;
if (mongoUrl) {
  mongoose.connect(mongoUrl, { autoIndex: false }).then(() => {
    console.info('MongoDB connected');
  }).catch((err) => {
    console.error('MongoDB connection error:', err);
  });
} else {
  console.warn('MONGO_URL not set - Sessions writes will fail until configured');
}

// const __filename = fileURLToPath(import.meta.url);
// const __dirname = path.dirname(__filename);

function cryptoRandomUuid() {
  return crypto.randomUUID();
}

const app = express();
app.use(express.json({ limit: '10mb' }));
// Optionally trust proxy when running behind ACA ingress
if ((process.env.TRUST_PROXY || '').toLowerCase() === 'true') {
  app.set('trust proxy', true);
}

app.get('/', (req, res) => res.json({ message: 'Hello World!' }));
app.get('/health', (req, res) => res.json({ status: 'healthy', service: 'voice-agent-api', version: '1.0.0' }));

// Placeholder for incoming call EventGrid events
const pendingServerCallIdsByContext = new Map(); // contextId -> serverCallId from initial EventGrid
const pendingCallerIdsByContext = new Map(); // contextId -> callerId for session creation
let currentCallerId = 'unknown'; // Most recent caller ID for WebSocket connections
app.post('/api/incomingCall', (req, res) => {
  //console.info('incoming event data');
  const events = Array.isArray(req.body) ? req.body : [req.body];

  // Handle EventGrid subscription validation synchronously and return immediately.
  // This avoids the async IIFE also trying to send a response later (double-send).
  for (const ev of events) {
    try {
      const et = ev.eventType || ev.event_type || ev.type;
      if (et === 'Microsoft.EventGrid.SubscriptionValidationEvent') {
        const validationCode = ev?.data?.validationCode;
        if (validationCode) {
          //console.info('Responding to EventGrid subscription validation');
          return res.status(200).json({ validationResponse: validationCode });
        }
      }
    } catch (e) {
      // Fall through to normal processing on any unexpected shape
    }
  }

  (async () => {
    function extractIncomingCallContext(data) {
      if (!data) return null;
      let ctx = data.incomingCallContext ?? data.incoming_call_context ?? data.callContext ?? null;
      if (!ctx) return null;
      // If already a string, return as-is
      if (typeof ctx === 'string') return ctx;
      // If common wrappers exist, prefer string fields
      if (typeof ctx.value === 'string') return ctx.value;
      if (typeof ctx.rawId === 'string') return ctx.rawId;
      if (typeof ctx.incomingCallContext === 'string') return ctx.incomingCallContext;
      if (typeof ctx.id === 'string') return ctx.id;
      // If it's an object, try to stringify to JSON
      try {
        const s = JSON.stringify(ctx);
        // avoid returning "[object Object]"
        if (s && s !== '{}') return s;
      } catch (e) {}
      try {
        const s2 = String(ctx);
        if (s2 && s2 !== '[object Object]') return s2;
      } catch (e) {}
      return null;
    }

    for (const eventDict of events) {
      try {
        console.log(`📞 [DEBUG] Processing event:`, eventDict);
        try { console.info('Incoming raw event:', JSON.stringify(eventDict).slice(0,1000)); } catch {};
        const eventType = eventDict['eventType'] || eventDict['event_type'] || eventDict.type;
        if (eventType === 'Microsoft.EventGrid.SubscriptionValidationEvent') {
          const validationCode = eventDict.data && eventDict.data.validationCode;
          if (validationCode) return res.status(200).json({ validationResponse: validationCode });
        } else if (eventType === 'Microsoft.Communication.IncomingCall') {
          const data = eventDict.data || {};
          const incomingCallContext = extractIncomingCallContext(data);
          try { console.info('normalized incomingCallContext (typeof):', typeof incomingCallContext, ' value:', (incomingCallContext || '').slice ? incomingCallContext.slice(0,200) : String(incomingCallContext)); } catch {}

          if (!incomingCallContext) {
            console.error('Cannot extract a valid incomingCallContext string from event; skipping answerCall to avoid SDK serialization error. Event data keys:', Object.keys(data));
            continue;
          }

          const from = data.from || {};
          console.log(`📞 [DEBUG] FROM object:`, JSON.stringify(from, null, 2));
          const callerId = from.phoneNumber ? from.phoneNumber.value : (from.rawId || 'unknown');
          // console.log(`📞 [DEBUG] Extracted callerId: ${callerId}`);

          // Prefer explicit env; otherwise derive from request headers (works behind ACA ingress if trust proxy enabled)
          let callbackUriHost = process.env.CALLBACK_URI_HOST;
          if (!callbackUriHost) {
            const proto = req.get('x-forwarded-proto') || (req.secure ? 'https' : 'http');
            const host = req.get('x-forwarded-host') || req.get('host');
            callbackUriHost = host ? `${proto}://${host}` : 'http://localhost:8080';
          }
          const guid = cryptoRandomUuid();
          const callbackUri = `${callbackUriHost}/api/callbacks/${guid}?callerId=${encodeURIComponent(callerId)}`;
          const parsed = new URL(callbackUri);
          const websocketUrl = `wss://${parsed.host}/ws`;

          // Store caller ID for this context to use when WebSocket connects
          pendingCallerIdsByContext.set(guid, callerId);

          // Capture serverCallId from EventGrid to use as a fallback later
          try {
            const serverCallId = data.serverCallId;
            if (serverCallId) pendingServerCallIdsByContext.set(guid, serverCallId);
          } catch {}

          try {
            const client = getAcsClient();
            if (!client) {
              console.error('ACS client not initialized');
              continue;
            }
            // Build media streaming options with exact casing expected by ACS service
            const mediaStreaming = {
              transportType: 'websocket',
              transportUrl: websocketUrl,
              contentType: 'audio',
              audioChannelType: 'mixed',
              audioFormat: 'pcm24KMono',
              startMediaStreaming: true,
              enableBidirectional: true,
            };

            const requestBody = { incomingCallContext, callbackUri, mediaStreamingOptions: mediaStreaming, operationContext: 'incomingCall' };
            try { console.info('About to call answerCall with request body:', JSON.stringify(requestBody).slice(0,1000)); } catch {}

            // Call SDK with expected signature: (incomingCallContext, callbackUri, options)
            await client.answerCall(incomingCallContext, callbackUri, { operationContext: 'incomingCall', mediaStreamingOptions: mediaStreaming });
              console.info('Answered call. Media streaming requested.');
          } catch (e) {
            console.error('Failed to answer call:', e);
          }
        }
      } catch (e) {
        console.error('Error processing event:', e);
      }
    }
  })();
  return res.status(200).json({});
});

// Recording features removed — keep minimal mapping for serverCallId fallback
const contextServerCallIds = new Map(); // contextId -> serverCallId
let currentVoiceLiveHandler = null; // Currently active VoiceLiveCommunicationHandler
let handlerFinalizationTimeout = null; // Timeout to finalize recording if no ACS events arrive
const CALLBACK_URI_HOST = process.env.CALLBACK_URI_HOST || 'http://localhost:8080';

// Callback handlers (context-specific)
app.post('/api/callbacks/:contextId', async (req, res) => {
  const contextId = req.params.contextId;
  const callerId = req.query.callerId || 'unknown';
  // console.log(`📞 [DEBUG] Received callback for contextId: ${contextId}, callerId: ${callerId}`);
  // console.log(`📞 [DEBUG] Request body:`, JSON.stringify(req.body, null, 2));
  
  // Store callerId for this context for later use in session creation
  pendingCallerIdsByContext.set(contextId, callerId);
  currentCallerId = callerId; // Store most recent for WebSocket connections
  //console.log(`📞 [DEBUG] Updated currentCallerId to: ${currentCallerId}`);
  
  let events = req.body;
  if (!Array.isArray(events)) events = [events];
  // console.log(`📞 [DEBUG] Processing ${events.length} events`);
  for (const event of events) {
    try {
      const eventType = event?.type;
      const data = event?.data || {};
      const callConnectionId = data.callConnectionId;
      const corrId = data.correlationId;
      // console.info(`Received Event:-> ${eventType}, Correlation Id:-> ${corrId}, CallConnectionId:-> ${callConnectionId}`);

      if (eventType === 'Microsoft.Communication.CallConnected') {
        try {
          const client = getAcsClient();
          const callConn = client.getCallConnection(callConnectionId);
          let props = null;
          // Try known method variants across SDK versions
          try {
            if (typeof callConn.getCallConnectionProperties === 'function') {
              props = await callConn.getCallConnectionProperties();
            } else if (typeof callConn.getCallProperties === 'function') {
              props = await callConn.getCallProperties();
            } else if (typeof client.getCallConnectionProperties === 'function') {
              props = await client.getCallConnectionProperties(callConnectionId);
            }
          } catch (inner) {
            console.warn('Failed to retrieve call properties via primary method:', inner?.message || inner);
          }

          let serverCallId = props?.serverCallId;
          if (!serverCallId) {
            // Fallback to the value captured during IncomingCall for this context
            const fallbackServerCallId = pendingServerCallIdsByContext.get(contextId);
            if (fallbackServerCallId) {
              serverCallId = fallbackServerCallId;
              console.info('Using fallback serverCallId from IncomingCall payload.');
            }
          }

          if (!props && !serverCallId) {
            console.warn('CallConnected handling failed (non-fatal): unable to obtain call properties (serverCallId).');
          } else {
            const msu = props.mediaStreamingSubscription;
            if (msu !== undefined) {
              const msuLog = typeof msu === 'object' ? JSON.stringify(msu) : String(msu);
              console.info(`MediaStreamingSubscription:--> ${msuLog}`);
            }
            if (serverCallId) contextServerCallIds.set(contextId, serverCallId);
            if (serverCallId) {
              console.info('Recording has been removed from this app; serverCallId captured for manual processing if needed.');
              // Clear pending fallback once used
              pendingServerCallIdsByContext.delete(contextId);
            } else {
              console.warn('Call properties retrieved but missing serverCallId; recording disabled.');
            }
          }
        } catch (e) {
          console.warn(`CallConnected handling failed (non-fatal): ${e}`);
        }
      } else if (eventType === 'Microsoft.Communication.MediaStreamingStarted') {
        const msu = data.mediaStreamingUpdate || {};
        // console.info(`Media streaming content type:--> ${msu.contentType}`);
        // console.info(`Media streaming status:--> ${msu.mediaStreamingStatus}`);
        // console.info(`Media streaming status details:--> ${msu.mediaStreamingStatusDetails}`);
      } else if (eventType === 'Microsoft.Communication.MediaStreamingStopped') {
        const msu = data.mediaStreamingUpdate || {};
        // console.info(`Media streaming content type:--> ${msu.contentType}`);
        // console.info(`Media streaming status:--> ${msu.mediaStreamingStatus}`);
        // console.info(`Media streaming status details:--> ${msu.mediaStreamingStatusDetails}`);
      } else if (eventType === 'Microsoft.Communication.MediaStreamingFailed') {
        const ri = data.resultInformation || {};
        // console.info(`Code:->${ri.code}, Subcode:-> ${ri.subCode}`);
        // console.info(`Message:->${ri.message}`);
      } else if (eventType === 'Microsoft.Communication.CallDisconnected') {
        // console.info(`🎬 [DEBUG] Call disconnected for ${contextId}`);
        // console.info(`🎬 [DEBUG] currentVoiceLiveHandler exists: ${currentVoiceLiveHandler ? 'YES' : 'NO'}`);
        
        // Cancel the timeout since we got the ACS event
        if (handlerFinalizationTimeout) {
          clearTimeout(handlerFinalizationTimeout);
          handlerFinalizationTimeout = null;
          console.info('🎬 [DEBUG] Cancelled WebSocket close timeout - using ACS event instead');
        }
        
        // 🎬 [RECORDING] Finalize recording on call disconnect
        if (currentVoiceLiveHandler) {
          //console.info(`🎬 [RECORDING] Finalizing recording for disconnected call ${contextId}`);
          try {
            await currentVoiceLiveHandler.finalizeRecording();
            // console.info(`🎬 [RECORDING] Recording finalized successfully for ${contextId}`);
            currentVoiceLiveHandler = null; // Clear the handler after finalizing
          } catch (e) {
            console.error(`🎬 [RECORDING] Error finalizing recording for ${contextId}:`, e);
          }
        } else {
          // console.info(`🎬 [DEBUG] Recording disabled — no active handler for disconnected call ${contextId}`);
        }
        
        try {
          const serverCallId = contextServerCallIds.get(contextId);
          if (serverCallId) console.info(`[BYOS-REMOVED] serverCallId available for manual retrieval: ${serverCallId}`);
        } catch {}
      } else if (eventType === 'Microsoft.Communication.RecordingFileStatusUpdated') {
        try {
          const chunks = data?.recordingStorageInfo?.recordingChunks || [];
          if (chunks.length) {
            const c = chunks[0];
          //   console.info(`Recording ready for call ${contextId}:`);
          //   console.info(`- Document ID: ${c.documentId}`);
          //   console.info(`- Content URL: ${c.contentLocation}`);
          //   console.info(`- Duration: ${data.recordingDurationMs}ms`);
          }
        } catch (e) {
          console.error('Error processing recording status:', e);
        }
      } else {
        // ignore others
      }
    } catch (e) {
      console.error('Error processing callback event:', e);
    }
  }
  res.status(200).json({});
});

// Root callback handlers (recording state)
app.post('/api/callbacks', async (req, res) => {
  let events = req.body;
  if (!Array.isArray(events)) events = [events];
  for (const event of events) {
    try {
      const eventType = event?.type;
      const data = event?.data || {};
      const recordingId = data.recordingId;
      const corrId = data.correlationId;
      if (eventType === 'Microsoft.Communication.RecordingStateChanged') {
        const state = data.state || data.status;
        console.info(`[RecordingCallback] StateChanged: state=${state} recordingId=${recordingId} corr=${corrId}`);
        if (recordingId && state) recordingStates.set(recordingId, state);
      } else if (eventType === 'Microsoft.Communication.RecordingFileStatusUpdated') {
        try {
          const chunks = data?.recordingStorageInfo?.recordingChunks || [];
          if (chunks.length) {
            const c = chunks[0];
            console.info(`[RecordingCallback] FileReady: docId=${c.documentId} url=${c.contentLocation} durMs=${data.recordingDurationMs}`);
            if (recordingId) recordingStates.set(recordingId, 'file_ready');
          } else {
            console.info('[RecordingCallback] FileStatusUpdated with no chunks present yet');
          }
        } catch (e) {
          console.error('[RecordingCallback] Error processing file status:', e);
        }
      } else {
        // ignore unrelated events
      }
    } catch (e) {
      console.error('[RecordingCallback] Error handling event:', e);
    }
  }
  res.status(200).json({});
});

// Websocket endpoint for media streaming (using ws library)
import { WebSocketServer } from 'ws';
import { VoiceLiveCommunicationHandler } from './voiceLiveHandler.js';

const wss = new WebSocketServer({ noServer: true });

wss.on('connection', async (ws, request) => {
  // console.info('WS client connected');
  
  let callerId = currentCallerId || 'unknown';
  // console.log(`📞 [DEBUG] WebSocket connected with callerId: ${callerId}`);
  
  const service = new VoiceLiveCommunicationHandler(ws, callerId);
  currentVoiceLiveHandler = service; // Store the active handler globally
  try {
    await service.startConversationAsync();
  } catch (e) {
    // console.error('Upstream connect failed, closing WS:', e);
    try { ws.close(1011); } catch {};
    currentVoiceLiveHandler = null; // Clear on connection failure
    return;
  }

  ws.on('message', async (message) => {
    try {
      const data = JSON.parse(message.toString());
      const kindLower = ((data.kind || data.Kind || '') + '').toLowerCase();
      if (kindLower === 'audiodata') {
        const audio = data.audioData?.data || data.AudioData?.Data;
        if (audio) {
          // Forward caller audio FROM ACS -> Voice Live upstream
          await service.send_audio_async(audio);
        }
      } else if (kindLower === 'stopaudio') {
        // Commit input to nudge Voice Live to finalize turn
        await service.commitInputAudio();
      }
    } catch (e) {
      // silently ignore non-JSON frames to avoid console flooding
    }
  });

  ws.on('close', () => {
    console.info('WS client disconnected');
    if (currentVoiceLiveHandler === service) {
      // Don't clear immediately - wait for potential ACS CallDisconnected event
      // console.info('🎬 [DEBUG] Scheduling delayed handler cleanup (waiting for ACS events)');
      
      if (handlerFinalizationTimeout) {
        clearTimeout(handlerFinalizationTimeout);
      }
      
      handlerFinalizationTimeout = setTimeout(async () => {
        // console.info('🎬 [DEBUG] Timeout reached - finalizing recording due to WebSocket close');
        if (currentVoiceLiveHandler === service) {
          try {
            await service.finalizeRecording();
            // console.info('🎬 [RECORDING] Recording finalized successfully (WebSocket close timeout)');
          } catch (e) {
            console.error('🎬 [RECORDING] Error finalizing recording (WebSocket close timeout):', e);
          }
          currentVoiceLiveHandler = null;
        }
      }, 5000); // Wait 5 seconds for ACS events
    }
  });
});

const server = app.listen(process.env.PORT || 8080, () => {
  console.info(`Server listening on port ${server.address().port}`);
  if (CALLBACK_URI_HOST.startsWith('http://localhost')) {
    console.warn('[config] CALLBACK_URI_HOST is using a localhost default. Set CALLBACK_URI_HOST to your public ACA base URL before deploying.');
  }
});

server.on('upgrade', (request, socket, head) => {
  const { url } = request;
  if (url === '/ws') {
    wss.handleUpgrade(request, socket, head, (ws) => {
      wss.emit('connection', ws, request);
    });
  } else {
    socket.destroy();
  }
});

export default app;

// // Graceful shutdown for Azure Container Apps (SIGTERM on scale down/rollout)
// function shutdown(signal) {
//   console.info(`Received ${signal}. Shutting down gracefully...`);
//   try { wss.close(); } catch {}
//   try {
//     server.close((err) => {
//       if (err) {
//         console.error('Error during HTTP server close:', err);
//       }
//       process.exit(0);
//     });
//     // Force exit if not closed within timeout
//     setTimeout(() => process.exit(0), 10000).unref();
//   } catch {
//     process.exit(0);
//   }
// }
// process.on('SIGTERM', () => shutdown('SIGTERM'));
// process.on('SIGINT', () => shutdown('SIGINT'));
// process.on('unhandledRejection', (reason) => {
//   console.error('Unhandled promise rejection:', reason);
// });
// process.on('uncaughtException', (err) => {
//   console.error('Uncaught exception:', err);
// });
