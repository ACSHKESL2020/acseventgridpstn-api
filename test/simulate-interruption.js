import { VoiceLiveCommunicationHandler } from '../src/voiceLiveHandler.js';

// Minimal mock websocket that implements send and readyState
const mockWs = {
  send: (m) => { console.log('[mockWs.send]', typeof m === 'string' ? m.slice(0,200) : m); },
  readyState: 1
};

async function runScenario(label, speechDurationMs) {
  console.log(`\n=== ${label} dur=${speechDurationMs}ms ===`);
  const handler = new VoiceLiveCommunicationHandler(mockWs);

  // Simulate a response being active so interruptions will attempt to cancel
  await handler._handleVoiceLiveMessage({ type: 'response.created', response: { id: 'r1' } });

  // Simulate speech started
  await handler._handleVoiceLiveMessage({ type: 'input_audio_buffer.speech_started' });
  await new Promise(r => setTimeout(r, speechDurationMs));
  // Simulate speech stopped
  await handler._handleVoiceLiveMessage({ type: 'input_audio_buffer.speech_stopped' });

  // allow any delayed confirmation handlers to run
  await new Promise(r => setTimeout(r, Math.max(300, (handler._minUserSpeechMs || 250) + 100)));
}

(async () => {
  // Very short noise: 100ms (should be ignored if MIN_USER_SPEECH_MS = 250)
  await runScenario('Short noise', 100);
  // Borderline: 250ms
  await runScenario('Borderline', 250);
  // Real phrase: 500ms
  await runScenario('Phrase', 500);
  // Long speech: 1500ms
  await runScenario('Long speech', 1500);
})();
