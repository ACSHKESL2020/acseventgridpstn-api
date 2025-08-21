import fs from 'fs';
import { startRecording, writePcm, stopRecording, cleanupSessionTemp } from '../src/services/recorderService.js';

(async () => {
  try {
    console.log('Starting smoke-record test');
    const sessionName = 'smoke-test-session-' + Date.now();
    const { sessionId, outPath } = startRecording(sessionName);
    console.log('Recorder started:', sessionId, outPath);

    // generate 0.6s mono 24kHz 16-bit PCM sine wave at 440Hz
    const sampleRate = 24000;
    const durationSec = 0.6;
    const samples = Math.floor(sampleRate * durationSec);
    const buf = Buffer.alloc(samples * 2);
    for (let i = 0; i < samples; i++) {
      const t = i / sampleRate;
      const s = Math.round(Math.sin(2 * Math.PI * 440 * t) * 15000); // amplitude
      buf.writeInt16LE(s, i * 2);
    }

    // write in a couple of chunks
    const chunkSize = 4096;
    for (let offset = 0; offset < buf.length; offset += chunkSize) {
      const chunk = buf.slice(offset, Math.min(offset + chunkSize, buf.length));
      writePcm(sessionId, chunk);
      // small pause to allow processing
      await new Promise(r => setTimeout(r, 10));
    }

    // wait briefly then stop
    await new Promise(r => setTimeout(r, 300));
    console.log('Stopping recording...');
    const rec = await stopRecording(sessionId);
    if (!rec || !rec.outPath) {
      console.error('No recording produced (file too small or missing)');
      cleanupSessionTemp(sessionId);
      process.exit(2);
    }

    // check file
    let stat;
    try {
      stat = fs.statSync(rec.outPath);
    } catch (e) {
      console.error('Recorded file missing:', e);
      cleanupSessionTemp(sessionId);
      process.exit(2);
    }

    console.log('Recorded file:', rec.outPath);
    console.log('Size bytes:', stat.size);
    if (stat.size < 1024) {
      console.error('Recorded file too small');
      cleanupSessionTemp(sessionId);
      process.exit(2);
    }

    // cleanup temp dir
    try { cleanupSessionTemp(sessionId); } catch (e) {}
    console.log('Smoke-record test succeeded');
    process.exit(0);
  } catch (err) {
    console.error('Smoke test error:', err);
    process.exit(3);
  }
})();
