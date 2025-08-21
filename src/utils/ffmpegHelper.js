import { spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

// Minimal ffmpeg helper that spawns ffmpeg to encode PCM to opus OGG file.
// Expects PCM signed 16-bit little-endian, sample rate provided.
export function startFfmpegEncode({ outPath, sampleRate = 24000, bitrate = '16k' }) {
	// Ensure out dir exists
	fs.mkdirSync(path.dirname(outPath), { recursive: true });

	// ffmpeg args: read raw PCM from stdin and write OGG/Opus
	const args = [
		'-f', 's16le',
		'-ar', String(sampleRate),
		'-ac', '1',
		'-i', 'pipe:0',
		'-c:a', 'libopus',
		'-b:a', bitrate,
	'-vbr', 'on',
	'-y', // overwrite output if exists
	'-vn',
	outPath,
	];

	const ff = spawn('ffmpeg', args, { stdio: ['pipe', 'ignore', 'pipe'] });

	// Silence ffmpeg stderr in normal operation to avoid noisy logs.
	ff.stderr.on('data', () => {});
	ff.on('error', () => {});

	return ff; // expose stdin to write PCM
}

export function stopFfmpeg(ff) {
	return new Promise((resolve) => {
		if (!ff) return resolve();
		try {
			ff.stdin.end();
		} catch (e) {
			// ignore
		}
		let settled = false;
		const onDone = () => {
			if (settled) return;
			settled = true;
			try { ff.removeAllListeners(); } catch (e) {}
			resolve();
		};
		ff.on('close', onDone);
		ff.on('exit', onDone);
		// safety timeout: resolve after 5s if ffmpeg doesn't exit
		setTimeout(() => {
			onDone();
		}, 5000);
	});
}
