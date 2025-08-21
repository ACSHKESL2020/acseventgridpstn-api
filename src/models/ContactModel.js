import mongoose from 'mongoose';

const { Schema } = mongoose;

// SessionSchema stores a single websocket session (events, transcripts, audio blob links)
const SessionSchema = new mongoose.Schema(
	{
		sessionId: { type: String, required: true, index: true },
		channel: { type: String, default: 'websocket' },
		seq: Number,
		ts: Date,
		origin: String, // 'user'|'assistant'|'system'
		callId: String, //pstn call ID
		callerId: String, // caller phone number (E164 format)
		transcript: String,
		confidence: Number,
		startedAt: Date,
		endedAt: Date,
		durationMs: Number,
		status: { type: String, default: 'active' },
		finalRecordingUrl: String, // blob URL for final recording (store single merged file)
		transcript: String, // aggregated transcript
		transcriptSegments: [
			{
				text: String,
				startTs: Date,
				endTs: Date,
				speaker: String,
				confidence: Number,
					seq: Number,
			},
		],
			// Audio metadata for final uploaded recording
			audio: {
				recordingBlobName: String,
				// finalRecordingUrl: String, // kept at top-level for backward compatibility
				codec: String,
				sampleRate: Number,
				sha256: String,
				etag: String,
				sizeBytes: Number,
			},
			// Helper counters
			messagesCount: { type: Number, default: 0 },
			// next sequence number allocation helper (internal)
			seqNextAllocation: { type: Number, default: 1 },
	},
	{ timestamps: true }
);

export default mongoose.model('Sessions', SessionSchema);
