import fs from 'fs';
import path from 'path';
import { startFfmpegEncode, stopFfmpeg } from '../utils/ffmpegHelper.js';
import os from 'os';
import { v4 as uuidv4 } from 'uuid';

const BASE_TMP = process.env.SESSION_TEMP_DIR || path.join(os.tmpdir(), 'voice-sessions');

const sessions = new Map(); // sessionId -> { outPath, ff, dir, queue, processing }

export function startRecording(sessionId) {
	const id = sessionId || uuidv4();
	const dir = path.join(BASE_TMP, id);
	fs.mkdirSync(dir, { recursive: true });
	const outPath = path.join(dir, `${id}.ogg`);
	// Use 24000 Hz by default to match Azure session sample rates and avoid resampling
	const ff = startFfmpegEncode({ outPath, sampleRate: 24000, bitrate: '16k' });
	sessions.set(id, { outPath, ff, dir, queue: [], processing: false, totalBytes: 0 });
	console.log(`🎬 [RECORDER] Started recording for session ${id} -> ${outPath}`);
	return { sessionId: id, outPath };
}

export function writePcm(sessionId, buffer) {
	const s = sessions.get(sessionId);
	if (!s) throw new Error('session not recording');
	// Push to FIFO queue with a timestamp to preserve chronological order
	s.queue.push({ buffer, ts: Date.now() });
	s.totalBytes = (s.totalBytes || 0) + buffer.length;
	console.log(`🎬 [RECORDER] Queued ${buffer.length} bytes for session ${sessionId} (total: ${s.totalBytes} bytes, queue: ${s.queue.length})`);
	// Kick off processing if not already running
	if (!s.processing) {
		processQueue(sessionId).catch((e) => {
			// swallow errors to avoid crashing the server; logging done below
			try { console.error('Error in recording queue processor for', sessionId, e); } catch (e) {}
		});
	}
}

async function processQueue(sessionId) {
	const s = sessions.get(sessionId);
	if (!s) return;
	if (s.processing) return;
	s.processing = true;
	try {
		while (s.queue.length > 0) {
			const item = s.queue.shift();
			if (!item || !item.buffer) continue;
			try {
				const ok = s.ff.stdin.write(item.buffer);
				if (!ok) {
					// backpressure - wait for drain
					await new Promise((resolve) => s.ff.stdin.once('drain', resolve));
				}
			} catch (e) {
				// log and continue
				try { console.error('Error writing PCM to ffmpeg for session', sessionId, e); } catch (e) {}
			}
		}
	} finally {
		s.processing = false;
	}
}

export async function stopRecording(sessionId) {
	const s = sessions.get(sessionId);
	if (!s) return null;
	console.log(`🎬 [RECORDER] Stopping recording for session ${sessionId}, total bytes written: ${s.totalBytes || 0}`);
	await stopFfmpeg(s.ff);
	// ensure file exists and has a minimum size to indicate real audio frames
	let stat = null;
	try {
		stat = fs.statSync(s.outPath);
	} catch (e) {
		// missing file
		console.log(`🎬 [RECORDER] Output file not found: ${s.outPath}`);
	}
	sessions.delete(sessionId);
	const minBytes = parseInt(process.env.MIN_RECORDING_BYTES || '1024', 10); // default 1KB
	if (!stat || stat.size < minBytes) {
		console.log(`🎬 [RECORDER] Recording too small for session ${sessionId}: ${stat ? stat.size : 0} bytes < ${minBytes} bytes (threshold)`);
		// cleanup small or empty file and return null (no upload)
		try { fs.rmSync(s.outPath, { force: true }); } catch (e) {}
		try { fs.rmSync(s.dir, { recursive: true, force: true }); } catch (e) {}
		return null;
	}
	console.log(`🎬 [RECORDER] Recording completed for session ${sessionId}: ${stat.size} bytes saved to ${s.outPath}`);
	return { outPath: s.outPath, dir: s.dir };
}

export function cleanupSessionTemp(sessionId) {
	const dir = path.join(BASE_TMP, sessionId);
	try {
		fs.rmSync(dir, { recursive: true, force: true });
	} catch (e) {
		// ignore
	}
}
