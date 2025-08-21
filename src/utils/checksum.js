import crypto from 'crypto';

export function sha256Stream(stream) {
	return new Promise((resolve, reject) => {
		const hash = crypto.createHash('sha256');
		stream.on('data', (chunk) => hash.update(chunk));
		stream.on('end', () => resolve(hash.digest('hex')));
		stream.on('error', reject);
	});
}

export function sha256Buffer(buffer) {
	return crypto.createHash('sha256').update(buffer).digest('hex');
}
