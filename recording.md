# Call Recording Setup & Operations Report

_Last updated: 2025-08-13_

## 1. Overview
The system records PSTN ↔ AI assistant calls using Azure Communication Services (ACS) Call Automation. Recording can target:
- Service-managed storage (Microsoft-managed, ephemeral URL surfaced via callbacks)
- BYOS (Bring Your Own Storage) Azure Blob container (current mode)

Current status: BYOS recording functioning; single-chunk WAV files produced for short calls with high audio quality.

## 2. High-Level Flow (User Perspective)
1. Caller dials provisioned ACS phone number.
2. System answers, establishes media streaming + Voice Live AI session.
3. Recording auto-starts immediately after `CallConnected` event.
4. User converses with AI assistant (Richard).
5. Call ends (hangup or disconnect) → system stops recording (idempotent).
6. Recording chunk(s) appear in storage container; user can download WAV.

## 3. Technical Architecture (Recording Path)
Components:
- FastAPI app (`app/main.py`)
- ACS CallAutomationClient
- Recording callbacks: `/api/callbacks` (root for recording state/file) & `/api/callbacks/{contextId}` (call lifecycle)
- Azure Blob Storage (BYOS)

Sequence:
1. Event Grid delivers `Microsoft.Communication.IncomingCall` → app answers call.
2. On `CallConnected` callback: fetch call properties (serverCallId), invoke `start_recording` with:
   - `server_call_id`
   - `recording_channel_type` (mixed or unmixed)
   - `recording_format_type` (wav/mp3)
   - `recording_state_callback_url` (root callbacks endpoint)
   - Optional `recording_storage` (BYOS AzureBlobContainerRecordingStorage) when BYOS enabled.
3. Root callback receives `RecordingStateChanged` (state=active).
4. (When service-managed) later would receive `RecordingFileStatusUpdated`; for BYOS we rely on direct blob appearance.
5. On `CallDisconnected` → `stop_recording` (404/8522 treated as benign) → schedule BYOS discovery listing.

## 4. Environment Variables (Recording Relevant)
| Variable | Purpose | Example / Notes |
|----------|---------|-----------------|
| `RECORDING_USE_BYOS` | Toggle BYOS vs service-managed | `true` (current) |
| `AZURE_STORAGE_ACCOUNT` | Storage account name | eikenappacskyoto |
| `AZURE_STORAGE_CONTAINER` | Container holding recordings | eikenappaudioblob |
| `AZURE_STORAGE_CONNECTION_STRING` | Enables SDK blob listing & SAS generation | Required for BYOS tooling |
| `AZURE_STORAGE_ACCOUNT_KEY` | Used for SAS (fallback) | Keep secret |
| `RECORDING_FORMAT` | wav/mp3 (default wav) | 24-bit PCM mono currently |
| `RECORDING_CHANNEL_TYPE` | mixed/unmixed | mixed (mono) |
| `CALLBACK_URI_HOST` | Public base URL (ngrok / container apps) | Must be HTTPS |
| `RECORDING_SUPPRESS_8522_WARN` | Suppress noisy stop warnings | true |

## 5. Storage Layout (BYOS)
Blob path pattern:
```
<YYYYMMDD>/<serverCallId>/<recordingId>/<chunkIndex>-audiowav.wav
<YYYYMMDD>/<serverCallId>/<recordingId>/<chunkIndex>-acsmetadata.json
```
Notes:
- `chunkIndex` starts at 0; short calls may only have chunk 0.
- Metadata JSON includes: `chunkDuration`, `chunkStartTime`, audio config, participants.
- WAV chunk (~16 kHz mono 256 kbps) size proportional to duration; rotation boundary not yet observed > ~100s in tests (may vary by service internals).

## 6. Callback Endpoints
| Endpoint | Purpose |
|----------|---------|
| `/api/callbacks/{contextId}` | Call lifecycle, media streaming events, triggers start/stop recording |
| `/api/callbacks` | Recording state & (when service-managed) file status events |

Important: Missing root endpoint previously caused lost recording callbacks; now restored.

## 7. Internal Tracking Maps
| Map | Key → Value | Usage |
|-----|-------------|-------|
| `active_recordings` | contextId → recordingId | Stop & correlation |
| `recording_states` | recordingId → lastState | Diagnostics |
| `context_correlations` | contextId → correlationId | Support escalation |
| `context_server_call_ids` | contextId → serverCallId | BYOS listing after disconnect |

## 8. Diagnostics & Logging
Key log lines:
- `Starting recording (BYOS) ...`
- `[RecordingCallback] StateChanged: state=active ...`
- `Recording start diagnostic headers: {...}` (for support)
- `[BYOS] Discovered X audio chunk(s)...` (post-disconnect listing)
- `Recording <id> already absent (treated as stopped - 8522)` (normal if 404 on stop)

Collected identifiers for support:
- `recording_id` (base64 JSON: PlatformEndpointId + ResourceSpecificId)
- `correlationId`
- `x-ms-client-request-id`
- Timestamps (UTC) for start/stop

## 9. Retrieval (BYOS)
To list latest blobs (Python snippet):
```python
from azure.storage.blob import BlobServiceClient
import os, datetime
svc = BlobServiceClient.from_connection_string(os.getenv('AZURE_STORAGE_CONNECTION_STRING'))
cc = svc.get_container_client(os.getenv('AZURE_STORAGE_CONTAINER'))
for b in sorted(cc.list_blobs(), key=lambda x: x.last_modified, reverse=True)[:20]:
    print(b.name, b.last_modified, b.size)
```

## 10. Merging Chunks (If Multiple)
If multiple WAV chunks appear:
1. Keep RIFF/WAV header from chunk 0.
2. Concatenate data sections (`fmt` stays same) from chunks 1..N.
Simpler: use `ffmpeg`:
```
ffmpeg -i "concat:0-audiowav.wav|1-audiowav.wav|2-audiowav.wav" -c copy full_call.wav
```
(Works because identical audio format.)

## 11. Known Behaviors / Issues
| Issue | Impact | Mitigation |
|-------|--------|-----------|
| 404 (8522) on stop | Benign; recording already removed | Treat as success (implemented) |
| Missing `RecordingFileStatusUpdated` events (service-managed) | No direct download URL | Use BYOS for guaranteed artifacts |
| Single-chunk only for >90s calls (observed) | Potential early termination | Monitor future longer calls to confirm rotation |
| Callback endpoint misnaming | No state events | Correct parameter: `recording_state_callback_url` |

## 12. Troubleshooting Checklist
1. No recording blob: confirm `RECORDING_USE_BYOS=true` and storage creds valid.
2. No callbacks: verify `/api/callbacks` reachable (200) publicly (ngrok/ingress).
3. 401/403 on start: check ACS connection string validity.
4. Long gaps or truncation: compare last `chunkStartTime + chunkDuration` vs call end.
5. Support escalation: provide resource name, region, recordingId, correlationId, client-request-id, UTC times.

## 13. Security & Compliance
- Access keys and connection strings stored in `.env` (rotate regularly).
- Recordings stored unencrypted at rest via Azure Storage (server-side encryption enabled by platform). Consider enabling customer-managed keys if required.
- Limit exposure: do not share blob URLs publicly; enforce SAS or RBAC for downstream consumers.

## 14. Operational Recommendations
| Area | Recommendation |
|------|----------------|
| Reliability | Keep BYOS enabled until service-managed file status events stabilize |
| Observability | Add metric counters for number of active recordings & chunk counts |
| Retention | Configure lifecycle policy to move >30d recordings to cool/archive if needed |
| Scaling | Monitor blob listing time; if container grows, introduce prefix listing by date/serverCallId |
| Region | Consider second ACS resource in alternate region for A/B recording stability tests |

## 15. Future Enhancements (Backlog)
- Automatic completeness checker (compare call duration vs sum of chunk durations).
- Merge service endpoint to deliver unified WAV on demand.
- Add transcription alignment with chunk timing metadata.
- Alert if no BYOS audio blob appears within X minutes after active state.

## 16. Quick Verification Procedure (Runbook)
1. Place test call (>45s).
2. Confirm logs: start + active state.
3. After hangup (wait ≤2 min): check `[BYOS] Discovered` log or list blobs.
4. Download newest `0-audiowav.wav` and play.
5. If missing late dialog: escalate (collect diagnostics).

---
Prepared for internal documentation & escalation readiness. Update this file as architecture evolves.
