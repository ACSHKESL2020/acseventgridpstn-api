export default function createMicroBatcher({ flushMs = 300, maxSize = 25, onFlush }) {
	let buffer = [];
	let timer = null;

	function schedule() {
		if (timer) return;
		timer = setTimeout(() => flush(), flushMs);
	}

	async function flush() {
		if (timer) {
			clearTimeout(timer);
			timer = null;
		}
		if (buffer.length === 0) return;
		const toFlush = buffer;
		buffer = [];
		try {
			await onFlush(toFlush);
		} catch (err) {
			// onFlush should handle retries; in case of error, requeue
			buffer = toFlush.concat(buffer);
		}
	}

	return {
		push(item) {
			buffer.push(item);
			if (buffer.length >= maxSize) {
				flush();
			} else {
				schedule();
			}
		},
		async flush() {
			await flush();
		},
	};
}
