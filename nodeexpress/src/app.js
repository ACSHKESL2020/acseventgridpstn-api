import express from 'express';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';
import crypto from 'crypto';
import { getAcsClient } from './acsClient.js';

dotenv.config({ path: path.resolve(process.cwd(), '.env') });

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function cryptoRandomUuid() {
  return crypto.randomUUID();
}

const app = express();
app.use(express.json({ limit: '10mb' }));

app.get('/', (req, res) => res.json({ message: 'Hello World!' }));
app.get('/health', (req, res) => res.json({ status: 'healthy', service: 'voice-agent-api', version: '1.0.0' }));

// Placeholder for incoming call EventGrid events
app.post('/api/incomingCall', (req, res) => {
  console.info('incoming event data');
  const events = Array.isArray(req.body) ? req.body : [req.body];
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
          const callerId = from.phoneNumber ? from.phoneNumber.value : (from.rawId || 'unknown');

          const callbackUriHost = process.env.CALLBACK_URI_HOST || 'http://localhost:8080';
          const guid = cryptoRandomUuid();
          const callbackUri = `${callbackUriHost}/api/callbacks/${guid}?callerId=${encodeURIComponent(callerId)}`;
          const parsed = new URL(callbackUri);
          const websocketUrl = `wss://${parsed.host}/ws`;

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

// Callback handlers
app.post('/api/callbacks/:contextId', (req, res) => {
  console.info('callback with context received');
  // TODO: replicate detailed handling
  res.status(200).json({});
});

app.post('/api/callbacks', (req, res) => {
  console.info('root callback received');
  // TODO: replicate recording callback handling
  res.status(200).json({});
});

// Websocket endpoint for media streaming (using ws library)
import { WebSocketServer } from 'ws';
import { VoiceLiveCommunicationHandler } from './voiceLiveHandler.js';

const wss = new WebSocketServer({ noServer: true });

wss.on('connection', async (ws, request) => {
  console.info('WS client connected');
  const service = new VoiceLiveCommunicationHandler(ws);
  try {
    await service.startConversationAsync();
  } catch (e) {
    console.error('Upstream connect failed, closing WS:', e);
    try { ws.close(1011); } catch {};
    return;
  }

  ws.on('message', async (message) => {
    try {
      const data = JSON.parse(message.toString());
    const kindLower = ((data.kind || data.Kind || '') + '').toLowerCase();
    if (kindLower === 'audiodata') {
        const audio = data.audioData?.data || data.AudioData?.Data;
        if (audio) {
          // Hardware detection and forwarding to Voice Live would be implemented here
          // Forward caller audio FROM ACS -> Voice Live upstream
          await service.send_audio_async(audio);
        }
    } else if (kindLower === 'stopaudio') {
        // handle stop audio
      }
    } catch (e) {
    // silently ignore non-JSON frames to avoid console flooding
    }
  });
});

const server = app.listen(process.env.PORT || 8080, () => {
  console.info(`Server listening on port ${server.address().port}`);
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
