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
    CallAutomationClient,
)


from app.voice_live_handler import VoiceLiveCommunicationHandler

# Voice Live mode is the only supported mode



load_dotenv()

app = FastAPI(debug=True)

# Initialize ACS client lazily to avoid startup failures
ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
acs_ca_client = None

def get_acs_client():
    global acs_ca_client
    if acs_ca_client is None and ACS_CONNECTION_STRING:
        try:
            acs_ca_client = CallAutomationClient.from_connection_string(ACS_CONNECTION_STRING)
        except Exception as e:
            logger.error(f"Failed to initialize ACS client: {e}")
    return acs_ca_client

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
                    props = get_acs_client().get_call_connection(call_connection_id).get_call_properties()
                    media_streaming_subscription = props.media_streaming_subscription
                    logger.info(f"MediaStreamingSubscription:--> {media_streaming_subscription}")
                    logger.info(f"Received CallConnected event for connection id: {call_connection_id}")
                    logger.info(f"CORRELATION ID:--> {corr_id}")
                    logger.info(f"CALL CONNECTION ID:--> {call_connection_id}")
                except Exception as e:
                    logger.warning(f"CallConnected property fetch failed (non-fatal): {e}")

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
                logger.info("Call disconnected")

            else:
                logger.debug(f"Unhandled callback event type: {event_type}")
        except Exception as e:
            # Never let callback processing crash the server; log and continue
            logger.error(f"Error handling callback event: {e}")

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
