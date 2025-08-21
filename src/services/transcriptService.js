import Sessions from '../models/ContactModel.js';

// appendBatch: allocate seq numbers atomically then push batch with seqs
export async function appendTranscriptBatch(sessionId, segments, callerId = 'unknown') {
	if (!Array.isArray(segments) || segments.length === 0) return;
	const n = segments.length;
	// atomically increment seqNextAllocation and get previous value
		const isPstn = String(sessionId || '').startsWith('pstn_');
		const callIdVal = isPstn ? sessionId.replace(/^pstn_/, '') : null;
		const updateObj = { $inc: { seqNextAllocation: n, messagesCount: n } };
		if (isPstn) {
			updateObj.$setOnInsert = { sessionId, startedAt: new Date(), status: 'active', channel: 'PSTN', callId: callIdVal, callerId: callerId };
		} else {
			// For non-PSTN prefixed sessions, assume they are PSTN calls (since we only handle PSTN now)
			updateObj.$setOnInsert = { sessionId, startedAt: new Date(), status: 'active', channel: 'PSTN', callerId: callerId };
		}
		const res = await Sessions.findOneAndUpdate(
			{ sessionId },
			updateObj,
			{ new: true, upsert: true }
		).lean();

	// compute starting seq
	const startSeq = (res && res.seqNextAllocation ? res.seqNextAllocation - n : 1);
	// attach seq to segments
	for (let i = 0; i < n; i++) {
		segments[i].seq = startSeq + i;
	}

	// push segments into transcriptSegments array
	await Sessions.updateOne({ sessionId }, { $push: { transcriptSegments: { $each: segments } } });
}
