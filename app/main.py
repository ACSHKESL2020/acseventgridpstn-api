import json
import os
# import logging
import uuid
from urllib.parse import urlencode, urlparse, urlunparse
from dotenv import load_dotenv
# from fastapi.logger import logger
from loguru import logger
from fastapi import (
    FastAPI,
    WebSocket,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.communication.callautomation import (
    MediaStreamingOptions,
    AudioFormat,
    MediaStreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    ServerCallLocator,
    RecordingChannel,
    RecordingFormat,
    AzureBlobContainerRecordingStorage,
    CallAutomationClient,
)
from azure.core.exceptions import HttpResponseError
from azure.storage.blob import BlobServiceClient, generate_container_sas, ContainerSasPermissions
from datetime import datetime, timedelta
import asyncio
import re

from app.voice_live_handler import VoiceLiveCommunicationHandler

# Voice Live mode is the only supported mode



load_dotenv()

app = FastAPI(debug=True)

# Initialize ACS client lazily to avoid startup failures
ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")

def _validate_acs_connection_string(raw: str) -> bool:
    if not raw:
        return False
    # Expect pattern endpoint=...;accesskey=...
    parts = dict(
        p.split("=", 1) for p in [seg for seg in raw.split(";") if seg]
        if "=" in p
    )
    key = parts.get("accesskey")
    if not key:
        logger.error("ACS connection string missing accesskey component")
        return False
    import re
    # Real ACS access keys are long base64 strings (length multiple of 4, chars A-Za-z0-9+/=)
    if not re.fullmatch(r"[A-Za-z0-9+/=]{20,}", key) or (len(key) % 4) != 0:
        logger.error("ACS access key appears malformed (length/mod4/base64 check failed)")
        return False
    return True

if ACS_CONNECTION_STRING and not _validate_acs_connection_string(ACS_CONNECTION_STRING):
    logger.warning("Invalid ACS_CONNECTION_STRING detected; calls will fail until corrected.")
acs_ca_client = None

# Storage settings for call recording
AZURE_STORAGE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "eikenappacskyoto")
AZURE_STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "eikenappaudioblob")
AZURE_STORAGE_FOLDER = os.getenv("AZURE_STORAGE_FOLDER", "pstnrecordings")
AZURE_STORAGE_SAS_TOKEN = os.getenv("AZURE_STORAGE_SAS_TOKEN")

# Recording settings (simplified to use direct string values)
RECORDING_FORMAT = "wav" if os.getenv("RECORDING_FORMAT", "wav").lower() == "wav" else "mp3"
RECORDING_CHANNEL = "unmixed" if os.getenv("RECORDING_CHANNEL_TYPE", "mixed").lower() == "unmixed" else "mixed"
RECORDING_ENABLE_TONE = os.getenv("RECORDING_ENABLE_TONE", "true").lower() in ("true", "1", "yes", "y")
RECORDING_USE_BYOS = os.getenv("RECORDING_USE_BYOS", "true").lower() in ("true", "1", "yes", "y")  # toggle to isolate BYOS vs service-managed

# Active recordings map: contextId -> recordingId
active_recordings = {}
# Recording state map: recordingId -> last state received
recording_states = {}
# Correlation IDs per context for easier escalation (contextId -> correlationId)
context_correlations = {}
# Server call id per context (contextId -> serverCallId)
context_server_call_ids = {}

# Optional suppression of noisy 8522 warnings (treat as info)
SUPPRESS_8522_WARNING = os.getenv("RECORDING_SUPPRESS_8522_WARN", "true").lower() in ("1", "true", "yes", "y")

def get_acs_client():
    global acs_ca_client
    if acs_ca_client is None and ACS_CONNECTION_STRING:
        try:
            acs_ca_client = CallAutomationClient.from_connection_string(ACS_CONNECTION_STRING)
        except Exception as e:
            logger.error(f"Failed to initialize ACS client: {e}")
    return acs_ca_client

def get_recording_container_url() -> str:
    """Get the storage container URL with SAS token for BYOS recording storage."""
    if AZURE_STORAGE_SAS_TOKEN:
        # If SAS token provided, use it directly
        return f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net/{AZURE_STORAGE_CONTAINER}?{AZURE_STORAGE_SAS_TOKEN}"
    
    # Generate a short-lived SAS token (2 hours) with create/write/add permissions
    try:
        blob_service = BlobServiceClient.from_connection_string(os.getenv("AZURE_STORAGE_CONNECTION_STRING"))
        container_client = blob_service.get_container_client(AZURE_STORAGE_CONTAINER)
        
        # Generate SAS token with required permissions
        sas_token = generate_container_sas(
            account_name=AZURE_STORAGE_ACCOUNT,
            container_name=AZURE_STORAGE_CONTAINER,
            account_key=os.getenv("AZURE_STORAGE_ACCOUNT_KEY"),
            permission=ContainerSasPermissions(write=True, create=True, add=True),
            expiry=datetime.utcnow() + timedelta(hours=2)
        )
        return f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net/{AZURE_STORAGE_CONTAINER}?{sas_token}"
    except Exception as e:
        logger.error(f"Failed to generate storage SAS token: {e}")
        return None

async def start_call_recording(server_call_id: str, context_id: str) -> str:
    """Start recording a call.

    Modes:
      - BYOS (RECORDING_USE_BYOS=true) : Use customer storage via RBAC (no SAS query allowed).
      - Service-managed (RECORDING_USE_BYOS=false) : Let ACS place recording in Microsoft-managed storage (control test).
    """
    try:
        if not server_call_id:
            logger.error("Cannot start recording: missing serverCallId")
            return None

        client = get_acs_client()
        if not client:
            logger.error("Cannot start recording: ACS client not initialized")
            return None

        # Helper to wrap SDK call
        def _do_start(include_callback: bool, byos: bool):
            kwargs = {
                "server_call_id": server_call_id,
                "recording_channel_type": (RecordingChannel.MIXED if RECORDING_CHANNEL == "mixed" else RecordingChannel.UNMIXED),
                "recording_format_type": (RecordingFormat.WAV if RECORDING_FORMAT == "wav" else RecordingFormat.MP3),
                "pause_on_start": False,
            }
            if include_callback:
                kwargs["recording_state_callback_url"] = CALLBACK_EVENTS_URI
            if byos:
                container_url = get_recording_container_url()
                if not container_url:
                    logger.error("Cannot start recording: storage configuration error")
                    return None
                try:
                    parsed = urlparse(container_url)
                    container_url_no_query = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
                except Exception:
                    container_url_no_query = container_url.split('?')[0]
                recording_storage = AzureBlobContainerRecordingStorage(container_url=container_url_no_query)
                kwargs["recording_storage"] = recording_storage
                logger.info(f"Starting recording (BYOS) to container: {container_url_no_query}{' with callback' if include_callback else ''}")
            else:
                logger.info(f"Starting recording (service-managed storage){' with callback' if include_callback else ''}")
            return client.start_recording(**kwargs)

        recording_response = _do_start(include_callback=True, byos=RECORDING_USE_BYOS)

        if recording_response and getattr(recording_response, "recording_id", None):
            recording_id = recording_response.recording_id
            # Attempt to decode base64 JSON recording id for debugging (non-fatal)
            try:
                import base64, json
                padded = recording_id + ("=" * (-len(recording_id) % 4))
                decoded_json = base64.b64decode(padded).decode('utf-8')
                logger.debug(f"Decoded recording_id JSON: {decoded_json}")
            except Exception as dec_err:
                logger.debug(f"Could not decode recording_id: {dec_err}")
            # Capture raw response headers for escalation diagnostics
            try:
                raw_headers = {}
                raw = getattr(recording_response, "_response", None)
                if raw and getattr(raw, "headers", None):
                    for k, v in raw.headers.items():
                        if k.lower() in ("x-ms-client-request-id", "x-azure-ref", "x-microsoft-skype-chain-id", "date"):
                            raw_headers[k] = v
                if raw_headers:
                    logger.info(f"Recording start diagnostic headers: {raw_headers}")
            except Exception as hdr_err:
                logger.debug(f"Could not extract start recording headers: {hdr_err}")
            active_recordings[context_id] = recording_id
            recording_states[recording_id] = "started"
            logger.info(f"Started recording for call {context_id} -> recording {recording_id}")
            return recording_id

    except Exception as e:
        logger.error(f"Error starting call recording: {e}")

    return None

def _get_blob_service_client():
    try:
        conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not conn:
            return None
        return BlobServiceClient.from_connection_string(conn)
    except Exception as e:
        logger.debug(f"BlobService init failed: {e}")
        return None

async def discover_byos_recording_assets(server_call_id: str):
    """Best-effort listing of blobs for a given serverCallId in BYOS mode.

    Folder pattern observed (date yyyymmdd / serverCallId / recordingFolder / chunk files):
        20250812/<serverCallId>/<recordingFolder>/<index>-audio{format}.wav
        20250812/<serverCallId>/<recordingFolder>/<index>-acsmetadata.json

    Since we may not know the date folder (UTC) precisely, we scan recent date prefixes (today & yesterday) and
    look for blobs containing the serverCallId path segment. This is lightweight for low-volume containers.
    """
    if not RECORDING_USE_BYOS:
        return
    svc = _get_blob_service_client()
    if not svc:
        return
    container = svc.get_container_client(AZURE_STORAGE_CONTAINER)
    utc_now = datetime.utcnow()
    candidate_dates = {utc_now.strftime('%Y%m%d'), (utc_now - timedelta(days=1)).strftime('%Y%m%d')}
    found = []
    try:
        # Narrow listing by each date prefix; if container huge, consider adding paging limit
        for date_prefix in candidate_dates:
            prefix = f"{date_prefix}/{server_call_id}/"
            try:
                for blob in container.list_blobs(name_starts_with=prefix):
                    found.append(blob)
            except Exception:
                continue
        if not found:
            logger.info(f"[BYOS] No blobs found yet for serverCallId={server_call_id} (dates searched={candidate_dates})")
            return
        # Group by metadata vs audio
        meta_blobs = [b for b in found if b.name.endswith('-acsmetadata.json') or b.name.endswith('metadata.json')]
        audio_blobs = [b for b in found if re.search(r"-audio(\w+)\.wav$", b.name)]
        logger.info(f"[BYOS] Discovered {len(audio_blobs)} audio chunk(s) and {len(meta_blobs)} metadata file(s) for call {server_call_id}")
        for b in sorted(audio_blobs, key=lambda x: x.name):
            logger.info(f"[BYOS] Audio: {b.name} size={b.size} modified={b.last_modified}")
        for b in sorted(meta_blobs, key=lambda x: x.name):
            logger.info(f"[BYOS] Meta:  {b.name} size={b.size} modified={b.last_modified}")
    except Exception as e:
        logger.debug(f"[BYOS] Listing error for serverCallId={server_call_id}: {e}")

async def stop_call_recording(context_id: str) -> bool:
    """Stop recording a call."""
    try:
        recording_id = active_recordings.get(context_id)
        if not recording_id:
            logger.warning(f"No active recording found for call {context_id}")
            return False

        client = get_acs_client()
        if not client:
            logger.error("Cannot stop recording: ACS client not initialized")
            return False

        # Stop the recording using the SDK with recording_id (idempotent handling)
        try:
            client.stop_recording(recording_id)
        except HttpResponseError as hre:
            # If recording already gone (8522), treat as benign idempotent success
            msg = str(hre)
            if "8522" in msg or "Recording not found" in msg:
                if SUPPRESS_8522_WARNING:
                    logger.info(f"Recording {recording_id} already absent (treated as stopped - 8522)")
                else:
                    logger.warning(f"Recording {recording_id} already absent (treating as stopped): {msg}")
            else:
                raise

        # Clean up our tracking
        active_recordings.pop(context_id, None)
        logger.info(f"Stopped recording {recording_id} for call {context_id}")
        return True

    except Exception as e:
        logger.error(f"Error stopping call recording: {e}")
        return False

# Callback events URI to handle callback events.
CALLBACK_URI_HOST = os.getenv("CALLBACK_URI_HOST", "https://voice-agent-app.blacksmoke-a5c97abd.eastus.azurecontainerapps.io")
CALLBACK_EVENTS_URI = CALLBACK_URI_HOST + "/api/callbacks"



@app.get("/")
async def root():
    return JSONResponse({"message": "Hello World!"})


@app.get("/health")
async def health_check():
    """Health check endpoint for container health monitoring"""
    return JSONResponse({
        "status": "healthy",
        "service": "voice-agent-api",
        "timestamp": str(uuid.uuid4()),
        "version": "1.0.0"
    })


@app.post("/api/incomingCall")
async def incoming_call_handler(request: Request):
    logger.info("incoming event data")
    for event_dict in await request.json():
        event = EventGridEvent.from_dict(event_dict)
        # logger.info("incoming event data --> %s", event.data)
        if (
            event.event_type
            == SystemEventNames.EventGridSubscriptionValidationEventName
        ):
            logger.info("Validating subscription")
            validation_code = event.data["validationCode"]
            validation_response = {"validationResponse": validation_code}
            logger.info(validation_response)
            return JSONResponse(
                content=validation_response, status_code=status.HTTP_200_OK
            )
        elif event.event_type == "Microsoft.Communication.IncomingCall":
            if event.data["from"]["kind"] == "phoneNumber":
                caller_id = event.data["from"]["phoneNumber"]["value"]
            else:
                caller_id = event.data["from"]["rawId"]

            incoming_call_context = event.data["incomingCallContext"]
            guid = uuid.uuid4()

            query_parameters = urlencode({"callerId": caller_id})
            callback_uri = f"{CALLBACK_EVENTS_URI}/{guid}?{query_parameters}"

            parsed_url = urlparse(CALLBACK_EVENTS_URI)
            websocket_url = urlunparse(("wss", parsed_url.netloc, "/ws", "", "", ""))

            logger.info(f"callback url: {callback_uri}")
            logger.info(f"websocket url: {websocket_url}")

            try:
                # Answer the incoming call

                media_streaming_options = MediaStreamingOptions(
                    transport_url=websocket_url,
                    transport_type=MediaStreamingTransportType.WEBSOCKET,
                    content_type=MediaStreamingContentType.AUDIO,
                    audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                    start_media_streaming=True,
                    enable_bidirectional=True,
                    audio_format=AudioFormat.PCM24_K_MONO,
                )

                answer_call_result = get_acs_client().answer_call(
                    incoming_call_context=incoming_call_context,
                    operation_context="incomingCall",
                    callback_url=callback_uri,
                    media_streaming=media_streaming_options,
                )

            except Exception as e:
                raise e

            logger.info(
                f"Answered call for connection id: {answer_call_result.call_connection_id}"
            )


@app.post("/api/callbacks/{contextId}")
async def handle_callback_with_context(contextId: str, request: Request):
    try:
        events = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse callback JSON: {e}")
        return JSONResponse(status_code=200, content={})

    for event in events:
        try:
            # Parsing callback events safely
            event_type = event.get("type")
            event_data = event.get("data", {})
            call_connection_id = event_data.get("callConnectionId")
            corr_id = event_data.get("correlationId")
            logger.info(
                f"Received Event:-> {event_type}, Correlation Id:-> {corr_id}, CallConnectionId:-> {call_connection_id}"
            )

            if event_type == "Microsoft.Communication.CallConnected":
                try:
                    # Get call properties including serverCallId
                    props = get_acs_client().get_call_connection(call_connection_id).get_call_properties()
                    media_streaming_subscription = props.media_streaming_subscription
                    logger.info(f"MediaStreamingSubscription:--> {media_streaming_subscription}")
                    logger.info(f"Received CallConnected event for connection id: {call_connection_id}")
                    logger.info(f"CORRELATION ID:--> {corr_id}")
                    logger.info(f"CALL CONNECTION ID:--> {call_connection_id}")
                    if props.server_call_id:
                        context_server_call_ids[contextId] = props.server_call_id

                    # Start recording using serverCallId
                    if props.server_call_id:
                        recording_id = await start_call_recording(props.server_call_id, contextId)
                        if recording_id:
                            logger.info(f"Started recording {recording_id} for call {contextId}")
                        else:
                            logger.error(f"Failed to start recording for call {contextId}")
                except Exception as e:
                    logger.warning(f"CallConnected handling failed (non-fatal): {e}")

            elif event_type == "Microsoft.Communication.MediaStreamingStarted":
                msu = event_data.get("mediaStreamingUpdate", {})
                logger.info(f"Media streaming content type:--> {msu.get('contentType')}")
                logger.info(f"Media streaming status:--> {msu.get('mediaStreamingStatus')}")
                logger.info(f"Media streaming status details:--> {msu.get('mediaStreamingStatusDetails')}")

            elif event_type == "Microsoft.Communication.MediaStreamingStopped":
                msu = event_data.get("mediaStreamingUpdate", {})
                logger.info(f"Media streaming content type:--> {msu.get('contentType')}")
                logger.info(f"Media streaming status:--> {msu.get('mediaStreamingStatus')}")
                logger.info(f"Media streaming status details:--> {msu.get('mediaStreamingStatusDetails')}")

            elif event_type == "Microsoft.Communication.MediaStreamingFailed":
                ri = event_data.get("resultInformation", {})
                logger.info(f"Code:->{ri.get('code')}, Subcode:-> {ri.get('subCode')}")
                logger.info(f"Message:->{ri.get('message')}")

            elif event_type == "Microsoft.Communication.CallDisconnected":
                logger.info(f"Call disconnected for {contextId}")
                # Stop recording if active
                if await stop_call_recording(contextId):
                    logger.info(f"Stopped recording for disconnected call {contextId}")
                else:
                    logger.warning(f"No active recording found to stop for call {contextId}")
                # Attempt BYOS discovery (best-effort) using last known serverCallId from earlier props if available
                try:
                    if RECORDING_USE_BYOS:
                        server_call_id = context_server_call_ids.get(contextId)
                        if server_call_id:
                            asyncio.create_task(discover_byos_recording_assets(server_call_id))
                        else:
                            logger.debug("BYOS discovery skipped: no stored serverCallId for context")
                except Exception as _e:
                    logger.debug(f"BYOS discovery scheduling failed: {_e}")

            elif event_type == "Microsoft.Communication.RecordingFileStatusUpdated":
                try:
                    # Handle recording ready notification
                    recording_data = event_data.get("recordingStorageInfo", {}).get("recordingChunks", [])
                    if recording_data:
                        chunk = recording_data[0]  # First chunk
                        logger.info(f"Recording ready for call {contextId}:")
                        logger.info(f"- Document ID: {chunk.get('documentId')}")
                        logger.info(f"- Content URL: {chunk.get('contentLocation')}")
                        logger.info(f"- Duration: {event_data.get('recordingDurationMs')}ms")
                except Exception as e:
                    logger.error(f"Error processing recording status: {e}")

            else:
                logger.debug(f"Unhandled callback event type: {event_type}")
        except Exception as e:
            # Never let callback processing crash the server; log and continue
            logger.error(f"Error handling callback event: {e}")

    return JSONResponse(status_code=200, content={})


@app.post("/api/callbacks")
async def handle_root_callbacks(request: Request):
    """Root callback endpoint used specifically for recording state / file status events.

    start_call_recording() registers this root path (without a contextId) as recording_state_callback_url.
    Previously this route was missing which resulted in 404s for POST /api/callbacks and no processing of
    RecordingStateChanged / RecordingFileStatusUpdated events. This handler is intentionally narrow and
    non-intrusive: it only logs and processes recording related events and always returns 200 to avoid
    retries from the platform. It will NOT interfere with the existing context-specific callback handler.
    """
    try:
        events = await request.json()
    except Exception as e:
        logger.error(f"[RecordingCallback] Failed to parse JSON payload: {e}")
        return JSONResponse(status_code=200, content={})

    for event in events if isinstance(events, list) else [events]:
        try:
            event_type = event.get("type")
            event_data = event.get("data", {})
            recording_id = event_data.get("recordingId") or event_data.get("recordingId".lower())
            corr_id = event_data.get("correlationId")

            if event_type == "Microsoft.Communication.RecordingStateChanged":
                state = event_data.get("state") or event_data.get("status")
                logger.info(f"[RecordingCallback] StateChanged: state={state} recordingId={recording_id} corr={corr_id}")
                if recording_id and state:
                    recording_states[recording_id] = state
                    if corr_id:
                        # Attempt to reverse map correlation id to any known context (best-effort)
                        for ctx, rid in active_recordings.items():
                            if rid == recording_id:
                                context_correlations[ctx] = corr_id
                # If a recording transitions to 'Stopped' but we still track it, drop from active map
                if state and state.lower() in ("stopped", "stopping", "failed"):
                    # Attempt best-effort removal (we only stored by contextId, so keep map untouched here)
                    pass
            elif event_type == "Microsoft.Communication.RecordingFileStatusUpdated":
                try:
                    recording_data = event_data.get("recordingStorageInfo", {}).get("recordingChunks", [])
                    if recording_data:
                        chunk = recording_data[0]
                        logger.info("[RecordingCallback] FileReady: "
                                    f"docId={chunk.get('documentId')} url={chunk.get('contentLocation')} "
                                    f"durMs={event_data.get('recordingDurationMs')}")
                        if recording_id:
                            recording_states[recording_id] = "file_ready"
                    else:
                        logger.info("[RecordingCallback] FileStatusUpdated with no chunks present yet")
                except Exception as inner:
                    logger.error(f"[RecordingCallback] Error processing file status: {inner}")
            else:
                # Log at debug to avoid noise for unrelated events accidentally posted here
                logger.debug(f"[RecordingCallback] Unhandled event type: {event_type}")
        except Exception as e:
            logger.error(f"[RecordingCallback] Error handling event: {e}")

    return JSONResponse(status_code=200, content={})


# WebSocket
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    # Always use Azure Voice Live API
    logger.info("Using Azure Voice Live API with enhanced audio processing (agent mode only)")
    service = VoiceLiveCommunicationHandler(websocket)
    # Establish upstream connection; if it fails, close gracefully to avoid churn
    try:
        await service.start_conversation_async()
    except Exception as e:
        logger.error(f"Upstream connect failed, closing WS: {e}")
        try:
            await websocket.close(code=1011)
        finally:
            return
    
    # Handle both JSON and binary frames defensively to avoid crashes during calls
    while True:
        try:
            message = await websocket.receive()
        except Exception as e:
            logger.warning(f"WebSocket receive error, closing: {e}")
            break

        # Client disconnected
        if message.get("type") == "websocket.disconnect":
            logger.info("Client disconnected from /ws")
            break

        # Text frame: expected JSON envelope from ACS
        if message.get("text") is not None:
            text = message["text"]
            try:
                data = json.loads(text)
            except Exception:
                logger.debug("Ignoring non-JSON text frame from ACS")
                continue

            kind = data.get("kind") or data.get("Kind")
            if kind == "AudioData":
                audio_data = (
                    data.get("audioData", {}).get("data")
                    or data.get("AudioData", {}).get("Data")
                )
                if audio_data:
                    await service.send_audio_async(audio_data)
            elif kind == "StopAudio":
                await service.stop_audio_async()
            else:
                # Other control messages can be ignored
                logger.debug(f"Unhandled ACS message kind: {kind}")
                continue

        # Binary frame: unexpected for our flow; ignore to avoid crashes
        elif message.get("bytes") is not None:
            logger.debug("Ignoring unexpected binary frame from ACS")
            continue
