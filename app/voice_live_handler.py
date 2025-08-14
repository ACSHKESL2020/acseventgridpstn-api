"""
Voice Live Communication Handler
Modern replacement for the rtclient-based CommunicationHandler using direct WebSocket connection.
Maintains full compatibility with existing agent configuration and function calling.
"""

import json
import os
import uuid
import base64
import asyncio
import logging
import time
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
import websockets
from azure.identity.aio import DefaultAzureCredential
from loguru import logger as sync_logger
import aiohttp

from app.agent_config import get_agent_config, AgentConfig, ACKNOWLEDGMENT_MESSAGES

# Setup logging (use loguru for simplicity and reliability in async contexts)
logging.basicConfig(level=logging.INFO)
# Expose a simple logger interface compatible with previous calls
class _LoggerWrapper:
    def info(self, *a, **k):
        sync_logger.info(*a, **k)
    def warning(self, *a, **k):
        sync_logger.warning(*a, **k)
    def error(self, *a, **k):
        sync_logger.error(*a, **k)
    def debug(self, *a, **k):
        sync_logger.debug(*a, **k)

logger = _LoggerWrapper()

load_dotenv()

# Voice Live API Configuration (Direct API mode - not used in Agent mode)
# AZURE_VOICE_LIVE_ENDPOINT = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")  # Not used in agent mode
# AZURE_VOICE_LIVE_API_KEY = os.getenv("AZURE_VOICE_LIVE_API_KEY")     # Not used in agent mode
# VOICE_LIVE_MODEL = os.getenv("VOICE_LIVE_MODEL", "gpt-4o")           # Not used in agent mode
AZURE_VOICE_LIVE_API_VERSION = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")  # Used for agent mode API versioning

# Agent mode (Azure AI Foundry Agent Service) configuration
AGENT_ID = os.getenv("AGENT_ID")
AGENT_PROJECT_NAME = os.getenv("AGENT_PROJECT_NAME")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # Optional: will be auto-generated if not provided
AZURE_AGENT_ENDPOINT = os.getenv("AZURE_AGENT_ENDPOINT")
AGENT_TOKEN_URL = os.getenv("AGENT_TOKEN_URL")  # Optional override for token generation endpoint

class VoiceLiveCommunicationHandler:
    """
    Voice Live API handler that maintains compatibility with existing agent configuration
    while using the new Voice Live WebSocket protocol.
    """
    
    def __init__(self, websocket: WebSocket, agent_type: str = "it_helpdesk") -> None:
        self.voice_live_ws = None
        self.active_websocket = websocket
        self.agent_config: AgentConfig = get_agent_config(agent_type)
        self.conversation_call_id = str(uuid.uuid4())
        self.is_connected = False
        # Agent mode is the only supported mode
        self.is_agent_mode = True
        # Track voice override behavior
        self._voice_override_sent: bool = False
        self._voice_override_fallback_done: bool = False
        
        # Interruption handling state
        self._current_response_id: Optional[str] = None
        self._current_response_audio_queue: list = []
        self._is_streaming_audio: bool = False
        self._response_items: list = []  # Track assistant audio items for truncation
        
        # Call startup protection - track when call begins
        self._call_start_time: Optional[float] = None

    async def start_conversation_async(self) -> None:
        """Initialize Voice Live WebSocket connection and configure the session."""
        try:
            # Create WebSocket URL for Voice Live API (Agent mode only)
            if not (AZURE_AGENT_ENDPOINT and AGENT_PROJECT_NAME and AGENT_ID):
                raise RuntimeError("Agent mode required: set AZURE_AGENT_ENDPOINT, AGENT_PROJECT_NAME, and AGENT_ID in the environment.")

            base_ws = f"{AZURE_AGENT_ENDPOINT.replace('https://', 'wss://').rstrip('/')}/voice-live/realtime"
            # Agent mode URL (Agent Service)
            # wss://.../voice-live/realtime?api-version=...&agent-project-name=...&agent-id=...&agent-access-token=...
            agent_access_token = await self._get_agent_access_token()
            ws_url = (
                f"{base_ws}?api-version={AZURE_VOICE_LIVE_API_VERSION}"
                f"&agent-project-name={AGENT_PROJECT_NAME}"
                f"&agent-id={AGENT_ID}"
                f"&agent-access-token={agent_access_token}"
            )
            
            # Setup headers for authentication
            headers = {
                "x-ms-client-request-id": str(uuid.uuid4())
            }
            
            # Authentication: Agent mode requires AAD token
            credential = DefaultAzureCredential()
            try:
                token = await credential.get_token("https://ai.azure.com/.default")
            finally:
                # Ensure the underlying transport is closed to prevent warnings/leaks
                await credential.close()
            headers["Authorization"] = f"Bearer {token.token}"
            
            # Connect to Voice Live API
            logger.info(
                f"Connecting to Voice Live API (agent mode): {ws_url}"
            )
            # Disable per-message-deflate to avoid frame issues; set ping interval
            self.voice_live_ws = await websockets.connect(
                ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                max_queue=None,
                compression=None,
            )
            self.is_connected = True
            logger.info("Successfully connected to Voice Live API")
            
            # Mark call start time for greeting protection
            self._call_start_time = time.time()
            logger.info("🕐 Call started - greeting protection active for 10 seconds")
            
            # Configure session with agent settings
            await self._configure_session()
            
            # Start message processing loop
            asyncio.create_task(self.receive_messages_async())
            
        except Exception as e:
            logger.error(f"Failed to connect to Voice Live API: {e}")
            raise e

    async def _get_agent_access_token(self) -> str:
        """Get or generate the Agent Access Token for Voice Live agent mode.
        Order of precedence:
        1) If ACCESS_TOKEN env is provided, use it (useful for local dev).
        2) If AGENT_TOKEN_URL env is provided, call it to fetch a token.
        3) Attempt default Azure Agents API pattern using AZURE_AGENT_ENDPOINT.
        """
        if ACCESS_TOKEN:
            return ACCESS_TOKEN

        # Acquire AAD token to authorize token-generation request
        credential = DefaultAzureCredential()
        try:
            aad_token = await credential.get_token("https://ai.azure.com/.default")
        finally:
            await credential.close()
        auth_header = {"Authorization": f"Bearer {aad_token.token}", "Content-Type": "application/json"}

        # Determine endpoint(s) to call
        if not (AZURE_AGENT_ENDPOINT and AGENT_PROJECT_NAME and AGENT_ID):
            raise RuntimeError("Cannot generate agent access token: missing AZURE_AGENT_ENDPOINT or identifiers.")

        base = AZURE_AGENT_ENDPOINT.rstrip("/")

        # Candidate API versions and paths (try most likely first)
        versions = [AZURE_VOICE_LIVE_API_VERSION, "2024-10-01-preview"]
        path_templates = [
            "/openai/agents/v2/projects/{project}/agents/{agent}:generateAccessToken",
            "/openai/agents/v2/projects/{project}/agents/{agent}:generateToken",
            "/openai/agents/v1/projects/{project}/agents/{agent}:generateAccessToken",
            "/openai/agents/v1/projects/{project}/agents/{agent}:generateToken",
        ]

        # If a full override URL is provided, try it first (with and without api-version)
        candidate_urls: list[str] = []
        if AGENT_TOKEN_URL:
            candidate_urls.append(AGENT_TOKEN_URL)
            for v in versions:
                candidate_urls.append(f"{AGENT_TOKEN_URL}{'&' if '?' in AGENT_TOKEN_URL else '?'}api-version={v}")

        # Build default candidates
        for tmpl in path_templates:
            for v in versions:
                candidate_urls.append(
                    f"{base}{tmpl.format(project=AGENT_PROJECT_NAME, agent=AGENT_ID)}?api-version={v}"
                )

        last_error_text = None
        try:
            async with aiohttp.ClientSession() as session:
                for url in candidate_urls:
                    try:
                        async with session.post(url, headers=auth_header, json={}) as resp:
                            if 200 <= resp.status < 300:
                                data = await resp.json()
                                token = (
                                    data.get("access_token") or
                                    data.get("agent_access_token") or
                                    data.get("agentAccessToken") or
                                    data.get("token")
                                )
                                if token:
                                    return token
                                last_error_text = f"Missing token field in response from {url}: {data}"
                            else:
                                text = await resp.text()
                                last_error_text = f"{resp.status} {text} (url={url})"
                                # Only continue to next candidate on 404; other codes are likely auth/permission
                                if resp.status != 404:
                                    raise RuntimeError(f"Token generation failed: {last_error_text}")
                    except Exception as inner:
                        # Continue trying other candidates; remember last error
                        last_error_text = str(inner)
                        continue
        except Exception:
            # Outer-level exceptions are unexpected; re-raise below
            pass

        logger.error(f"Error generating agent access token: {last_error_text or 'Unknown error'}")
        raise RuntimeError(f"Token generation failed: {last_error_text or 'Unknown error'}")

    async def _configure_session(self) -> None:
        """Configure the Voice Live session with agent-specific settings."""
        # Build session-only update (hosted agent owns instructions & tools)
        # Defaults follow Azure Agent Mode tech specs and user's tested values.
        session_body = {
            "turn_detection": {
                "type": "azure_semantic_vad",
                "threshold": 0.3,
                "prefix_padding_ms": 200,
                "silence_duration_ms": 200,
                "remove_filler_words": False,
                "end_of_utterance_detection": {
                    "model": "semantic_detection_v1",
                    "threshold": 0.01,
                    "timeout": 2,
                },
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.4,  # Lower threshold for faster speech detection (more sensitive)
                "prefix_padding_ms": "200",  # Reduce padding for faster response
                "silence_duration_ms": "600",  # Shorter silence detection for quicker responses
                "create_response": True,  # Automatically create responses when speech stops
                "interrupt_response": True  # Automatically interrupt ongoing responses when speech starts
            },
            "voice": {
                "name": os.getenv("SESSION_VOICE_NAME", "en-US-Davis:DragonHDLatestNeural"),
                "type": "azure-standard",
                "temperature": float(os.getenv("SESSION_VOICE_TEMPERATURE", "0.8")),
            },
            "modalities": ["text", "audio"],
        }

        # We intentionally always include voice in session for hosted-agent mode.
        self._voice_override_sent = True

        voice_live_session_config = {
            "type": "session.update",
            "session": session_body,
            "event_id": "",
        }
        
        # Send session configuration
        if self.voice_live_ws:
            await self.voice_live_ws.send(json.dumps(voice_live_session_config))
        logger.info("Voice Live session configured with agent settings")
        
        # Send initial greeting message to trigger Richard's proactive greeting
        initial_greeting_message = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Hello"
                    }
                ]
            }
        }
        if self.voice_live_ws:
            await self.voice_live_ws.send(json.dumps(initial_greeting_message))
        
        # Request response to trigger the greeting
        response_request = {
            "type": "response.create",
            "response": {
                "modalities": ["text", "audio"]
            }
        }
        if self.voice_live_ws:
            await self.voice_live_ws.send(json.dumps(response_request))

    async def send_message_async(self, message: str) -> None:
        """Send message to ACS WebSocket."""
        try:
            if self.active_websocket.client_state == WebSocketState.CONNECTED:
                await self.active_websocket.send_text(message)
        except Exception as e:
            logger.error(f"Send Message - Failed to send message: {e}")
            raise e

    async def receive_messages_async(self) -> None:
        """Process messages from Voice Live API - maintains compatibility with existing logic."""
        try:
            while self.is_connected and self.voice_live_ws:
                try:
                    raw_message = await self.voice_live_ws.recv()
                    message = json.loads(raw_message)
                    
                    await self._handle_voice_live_message(message)
                except websockets.exceptions.ConnectionClosed:
                    logger.info("Voice Live WebSocket connection closed")
                    self.is_connected = False
                    break
                    
        except Exception as e:
            logger.error(f"Error in receive_messages_async: {e}")
            self.is_connected = False
            if not isinstance(e, asyncio.CancelledError):
                raise e

    async def _handle_voice_live_message(self, message: Dict[str, Any]) -> None:
        """Handle Voice Live API messages - maintains compatibility with existing message handling."""
        message_type = message.get("type")
        
        # Extract all IDs from the message according to Azure Voice Live API documentation
        event_id = message.get("event_id", "")
        item_id = message.get("item_id", "") or message.get("item", {}).get("id", "")
        response_id = message.get("response_id", "") or message.get("response", {}).get("id", "")
        call_id = message.get("call_id", "")
        session_id = message.get("session", {}).get("id", "")
        
        # Log comprehensive ID information for tracking
        id_info = []
        if event_id: id_info.append(f"event_id: {event_id}")
        if item_id: id_info.append(f"item_id: {item_id}")
        if response_id: id_info.append(f"response_id: {response_id}")
        if call_id: id_info.append(f"call_id: {call_id}")
        if session_id: id_info.append(f"session_id: {session_id}")
        
        ids_str = " | ".join(id_info) if id_info else "No IDs"
        logger.info(f"📋 {message_type} | {ids_str}")
        
        match message_type:
            case "session.created":
                logger.info(f"🎯 Voice Live session created | Session ID: {session_id} | Event ID: {event_id}")
                
            case "session.updated":
                logger.info(f"🔄 Voice Live session updated | Session ID: {session_id} | Event ID: {event_id}")
                
            case "conversation.item.created":
                item = message.get("item", {})
                item_type = item.get("type", "unknown")
                role = item.get("role", "")
                logger.info(f"📝 Conversation item created | Type: {item_type} | Role: {role} | Item ID: {item_id} | Event ID: {event_id}")
                
                # Detect user interruption (new user message while AI is responding)
                if item_type == "message" and role == "user" and self._current_response_id:
                    await self._handle_user_interruption(item_id)
                
            case "conversation.item.input_audio_transcription.completed":
                transcript = message.get('transcript', '').strip()
                logger.info(f"📞 User transcription | Item ID: {item_id} | Event ID: {event_id}")
                # LOG POTENTIAL AUDIO QUALITY ISSUES
                if len(transcript) < 3 or not transcript:
                    logger.warning(f"⚠️ POOR AUDIO: Very short/empty transcript: '{transcript}' | Item ID: {item_id}")
                elif any(char in transcript.lower() for char in ['ä', 'ü', 'ö', 'ñ', 'ç']):
                    logger.warning(f"⚠️ POOR AUDIO: Foreign characters detected: '{transcript}' | Item ID: {item_id}")
                elif transcript.count(' ') > 10 and len(transcript) < 30:
                    logger.warning(f"⚠️ POOR AUDIO: Fragmented speech pattern: '{transcript}' | Item ID: {item_id}")
                else:
                    logger.info(f"📞 User (from phone): {transcript} | Item ID: {item_id} | Event ID: {event_id}")
                    
            case "response.created":
                # Track the current response for interruption handling
                self._current_response_id = response_id
                self._is_streaming_audio = True
                self._audio_stream_start_time = time.time()  # Track when streaming actually starts
                self._response_items.clear()  # Clear previous items for new response
                logger.info(f"🤖 AI response created | Response ID: {response_id} | Event ID: {event_id}")
                
            case "response.done":
                # Reset response tracking when response completes normally
                self._current_response_id = None
                self._is_streaming_audio = False
                self._audio_stream_start_time = None  # Clear timing
                self._response_items.clear()  # Clear tracked items
                logger.info(f"✅ AI response completed | Response ID: {response_id} | Event ID: {event_id}")
                
            case "response.output_item.added":
                # Track new assistant items for potential truncation
                added_item_id = message.get("item", {}).get("id", item_id)
                output_index = message.get("output_index", 0)
                if added_item_id and added_item_id not in self._response_items:
                    self._response_items.append(added_item_id)
                    logger.info(f"📋 Response item tracked for truncation | Item ID: {added_item_id} | Output: {output_index} | Total items: {len(self._response_items)}")
                
                
            case "response.audio.delta":
                # Handle audio output - process each audio chunk individually
                audio_data = message.get("delta")
                output_index = message.get("output_index", 0)
                content_index = message.get("content_index", 0)
                if audio_data:
                    logger.debug(f"🔊 Audio chunk | Response ID: {response_id} | Item ID: {item_id} | Event ID: {event_id} | Output: {output_index} | Content: {content_index} | Size: {len(audio_data)} chars")
                    # Early interruption check - drop audio immediately if streaming stopped
                    if not self._is_streaming_audio:
                        logger.info(f"🛑 Audio chunk dropped due to interruption (early check) | Response ID: {response_id} | Current: {self._current_response_id}")
                        return
                    await self.receive_audio(audio_data)
                    
            case "response.audio.done":
                output_index = message.get("output_index", 0)
                content_index = message.get("content_index", 0)
                logger.info(f"🔊 Audio generation complete | Response ID: {response_id} | Item ID: {item_id} | Event ID: {event_id} | Output: {output_index} | Content: {content_index}")
                
            case "response.audio_transcript.done":
                transcript = message.get('transcript', '')
                logger.info(f"🤖 AI (to phone): {transcript} | Response ID: {response_id} | Item ID: {item_id} | Event ID: {event_id}")
                
            case "response.function_call_arguments.done":
                # Handle function calls - maintain existing logic
                function_name = message.get("name", "")
                output_index = message.get("output_index", 0)
                logger.info(f"🔧 Function call complete | Function: {function_name} | Call ID: {call_id} | Response ID: {response_id} | Item ID: {item_id} | Event ID: {event_id} | Output: {output_index}")
                await self._handle_function_call(message)
                
            case "input_audio_buffer.speech_started":
                audio_start_ms = message.get("audio_start_ms", 0)
                logger.info(f"📞 Voice activity started | Item ID: {item_id} | Event ID: {event_id} | Start: {audio_start_ms}ms")
                
            case "input_audio_buffer.speech_stopped":
                audio_end_ms = message.get("audio_end_ms", 0)
                logger.info(f"📞 Voice activity stopped | Item ID: {item_id} | Event ID: {event_id} | End: {audio_end_ms}ms")
                
            case "input_audio_buffer.committed":
                previous_item_id = message.get("previous_item_id", "")
                logger.info(f"📞 Audio buffer committed | Item ID: {item_id} | Previous Item: {previous_item_id} | Event ID: {event_id}")
                
            case "input_audio_buffer.speech_started":
                audio_start_ms = message.get('audio_start_ms', 0)
                logger.info(f"📞 Voice activity detected from phone call at {audio_start_ms}ms | Item ID: {item_id} | Event ID: {event_id}")
                
                # CALL STARTUP PROTECTION: Don't interrupt during greeting/introduction (first 10 seconds)
                if self._call_start_time:
                    time_since_call_start = (time.time() - self._call_start_time) * 1000  # Convert to milliseconds
                    if time_since_call_start < 10000:  # First 10 seconds
                        logger.info(f"🔇 Ignoring speech during call startup period ({time_since_call_start:.0f}ms since start) - protecting greeting")
                        return
                
                # CONSERVATIVE interruption detection - only if we're actively streaming audio AND have a current response
                if self._current_response_id and self._is_streaming_audio:
                    # Reduced safety check: ensure we've been streaming for at least 200ms to avoid false triggers
                    if hasattr(self, '_audio_stream_start_time'):
                        stream_duration = (time.time() - self._audio_stream_start_time) * 1000
                        if stream_duration < 200:  # Reduced from 500ms to 200ms for faster interruption
                            logger.info(f"🔇 Ignoring very early speech detection - stream only active for {stream_duration:.0f}ms")
                            return
                    
                    logger.info(f"🛑 FAST User interruption detected - speech started during AI response | Current Response ID: {self._current_response_id}")
                    await self._handle_user_interruption(item_id or f"speech_started_{event_id}")
                else:
                    logger.info(f"🔇 Speech detected but no active AI response to interrupt | Current Response: {self._current_response_id} | Streaming: {self._is_streaming_audio}")
                
            case "input_audio_buffer.speech_stopped":
                logger.info("📞 Voice activity stopped from phone call")
                
            case "input_audio_buffer.committed":
                logger.info("📞 Audio buffer committed for processing")
                
            case "conversation.item.created":
                item = message.get("item", {})
                item_type = item.get("type", "unknown")
                logger.info(f"📝 Conversation item created: {item_type}")
                
            case "response.created":
                response = message.get("response", {})
                logger.info(f"🤖 AI response created: {response.get('id', 'unknown')}")
                
            case "conversation.item.input_audio_transcription.completed":
                transcript = message.get('transcript', '').strip()
                # LOG POTENTIAL AUDIO QUALITY ISSUES
                if len(transcript) < 3 or not transcript:
                    logger.warning(f"⚠️ POOR AUDIO: Very short/empty transcript: '{transcript}'")
                elif any(char in transcript.lower() for char in ['ä', 'ü', 'ö', 'ñ', 'ç']):
                    logger.warning(f"⚠️ POOR AUDIO: Foreign characters detected: '{transcript}'")
                elif transcript.count(' ') > 10 and len(transcript) < 30:
                    logger.warning(f"⚠️ POOR AUDIO: Fragmented speech pattern: '{transcript}'")
                else:
                    logger.info(f"📞 User (from phone): {transcript}")
                
            case "conversation.item.input_audio_transcription.failed":
                logger.error(f"❌ Phone call transcription failed: {message.get('error')}")
                
            case "response.done":
                logger.info(f"✅ AI response completed: {message.get('response', {}).get('id')}")
                
            case "response.audio_transcript.delta":
                # Handle partial transcription if needed
                delta = message.get('delta', '')
                logger.debug(f"🔤 Transcript delta | Response ID: {response_id} | Item ID: {item_id} | Event ID: {event_id} | Delta: {delta[:50]}...")
                
            case "response.audio_transcript.done":
                logger.info(f"🤖 AI (to phone): {message.get('transcript', '')}")
                
            case "response.audio.delta":
                # Handle audio output - process each audio chunk individually
                audio_data = message.get("delta")
                if audio_data:
                    await self.receive_audio(audio_data)
                
            case "response.function_call_arguments.done":
                # Handle function calls - maintain existing logic
                await self._handle_function_call(message)
                
            case "response.function_call_arguments.delta":
                # Handle function call argument streaming
                delta = message.get('delta', '')
                function_name = message.get('name', '')
                logger.debug(f"� Function args delta | Function: {function_name} | Call ID: {call_id} | Response ID: {response_id} | Event ID: {event_id}")
                
            case "conversation.item.input_audio_transcription.failed":
                error_info = message.get('error', {})
                logger.error(f"❌ Phone call transcription failed | Item ID: {item_id} | Event ID: {event_id} | Error: {error_info}")
                
            case "error":
                err = message.get("error", {})
                error_code = err.get("code", "") if isinstance(err, dict) else ""
                error_message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                error_event_id = err.get("event_id", "") if isinstance(err, dict) else ""
                logger.error(f"❌ Voice Live API error | Code: {error_code} | Message: {error_message} | Error Event ID: {error_event_id} | Event ID: {event_id}")
                
                # Graceful fallback if the service rejects voice override
                try:
                    err_text = json.dumps(err) if not isinstance(err, str) else err
                except Exception:
                    err_text = str(err)
                # Determine if this error is due to an invalid voice override in agent mode
                is_voice_error = False
                if isinstance(err, dict):
                    code = err.get("code")
                    param = err.get("param")
                    # Common variants observed: invalid_update_message, invalid_voice, param=session.voice
                    if code in ("invalid_update_message", "invalid_voice") or param == "session.voice":
                        is_voice_error = True
                if ("invalid_voice" in err_text) or ("Only Azure voice is supported" in err_text) or ("session.voice" in err_text):
                    is_voice_error = True

                if (
                    self._voice_override_sent
                    and not self._voice_override_fallback_done
                    and is_voice_error
                ):
                    # Re-send session.update without voice to keep the session healthy
                    try:
                        base_session_config = self.agent_config.get_session_config()
                        filtered = {
                            k: v
                            for k, v in base_session_config.items()
                            if k not in ("instructions", "temperature", "max_response_output_tokens", "voice", "tools")
                        }
                        fallback_body = {
                            **filtered,
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 800,
                            },
                            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
                            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
                            "input_audio_transcription": {"model": "whisper-1"},
                            "modalities": ["text", "audio"],
                        }
                        update_msg = {
                            "type": "session.update",
                            "session": fallback_body,
                            "event_id": "",
                        }
                        if self.voice_live_ws:
                            await self.voice_live_ws.send(json.dumps(update_msg))
                        self._voice_override_fallback_done = True
                        logger.warning("Voice override rejected; applied fallback (session.update without voice) and continued")
                        # Ask the agent to continue speaking after fallback just in case pipeline paused
                        try:
                            response_request = {
                                "type": "response.create",
                                "response": {"modalities": ["text", "audio"]},
                            }
                            if self.voice_live_ws:
                                await self.voice_live_ws.send(json.dumps(response_request))
                        except Exception:
                            # Non-fatal
                            pass
                    except Exception as inner_e:
                        logger.error(f"Failed to apply voice fallback: {inner_e}")
                
            case "response.cancelled":
                # Handle response cancellation confirmation from Azure
                cancelled_response_id = message.get("response", {}).get("id", response_id)
                logger.info(f"✅ Response cancelled confirmed | Response ID: {cancelled_response_id} | Event ID: {event_id}")
                # Reset state if this matches our current response
                if cancelled_response_id == self._current_response_id:
                    self._current_response_id = None
                    self._is_streaming_audio = False
                    self._audio_stream_start_time = None
                    logger.info(f"🔄 Response tracking reset after cancellation | Response ID: {cancelled_response_id}")
                
            case "conversation.item.truncated":
                # Handle item truncation confirmation - critical for precise audio control
                truncated_item_id = message.get("item_id", item_id)
                content_index = message.get("content_index", 0)
                audio_end_ms = message.get("audio_end_ms", 0)
                logger.info(f"✂️ Item truncated confirmed | Item ID: {truncated_item_id} | Audio end: {audio_end_ms}ms | Content: {content_index} | Event ID: {event_id}")
                
                # If this was part of our current response, update tracking
                if self._current_response_id and hasattr(self, '_response_items'):
                    if truncated_item_id in self._response_items:
                        logger.info(f"🔄 Current response item truncated | Response ID: {self._current_response_id}")
                
            case "input_audio_buffer.cleared":
                # Handle input buffer clearing confirmation
                logger.info(f"🧹 Input audio buffer cleared confirmed | Event ID: {event_id}")
                
            case "output_audio_buffer.cleared":
                # Handle output buffer clearing (WebRTC only, but good to support)
                buffer_response_id = message.get("response_id", response_id)
                logger.info(f"🧹 Output audio buffer cleared | Response ID: {buffer_response_id} | Event ID: {event_id}")
                
                # Reset streaming state if this matches our current response
                if buffer_response_id == self._current_response_id:
                    self._is_streaming_audio = False
                    logger.info(f"🔄 Audio streaming stopped after buffer clear | Response ID: {buffer_response_id}")
                
            case "error":
                # Handle comprehensive error reporting
                error_details = message.get("error", {})
                error_type = error_details.get("type", "unknown")
                error_message = error_details.get("message", "No message")
                error_code = error_details.get("code", "")
                causing_event_id = error_details.get("event_id", "")
                logger.error(f"❌ Voice Live API Error | Type: {error_type} | Code: {error_code} | Message: {error_message} | Caused by Event: {causing_event_id} | Event ID: {event_id}")
                
                # Handle interruption-related errors gracefully
                if "truncate" in error_message.lower() or "cancel" in error_message.lower():
                    logger.warning(f"⚠️ Interruption command failed - continuing with fallback strategy")
                
                
            case _:
                logger.debug(f"🔍 Unhandled Voice Live message | Type: {message_type} | Event ID: {event_id}")

    async def _handle_function_call(self, message: Dict[str, Any]) -> None:
        """Handle function calls - maintains existing function calling logic."""
        function_name = message.get("name")
        call_id = message.get("call_id")
        
        # Skip if we don't have essential information
        if not function_name or not call_id:
            return
        
        # Handle potential incomplete JSON from API
        try:
            args = json.loads(message.get("arguments", "{}"))
            logger.info(f"Function call: {function_name} with args: {message.get('arguments')}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for function arguments: {e}")
            logger.error(f"Raw arguments: {message.get('arguments')}")
            # Send error response back to Voice Live API
            await self.voice_live_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"success": False, "error": "Invalid function arguments received"})
                }
            }))
            
            # CRITICAL: Request response even after JSON errors to ensure Richard responds
            await self.voice_live_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"]
                }
            }))
            return

        # Use agent config to handle function calls - same as existing implementation
        try:
            handler = self.agent_config.get_function_handler(function_name)
            if handler:
                result = await handler(args)
                # Handle both string and dict results
                if isinstance(result, str):
                    output = result
                else:
                    # Convert the output to JSON string for API
                    output = json.dumps(result.get("output", result))
            else:
                output = json.dumps({"success": False, "error": f"Function {function_name} not found"})
            
            # Send function result back to Voice Live API
            await self.voice_live_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "output": output,
                    "call_id": call_id
                }
            }))
            
            # CRITICAL: Request response after function completion to ensure Richard responds
            await self.voice_live_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"]
                }
            }))
            
            logger.info(f"Function {function_name} completed successfully")
        
        except Exception as e:
            logger.error(f"Error handling function call {function_name}: {e}")
            await self.voice_live_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "output": json.dumps({"success": False, "error": f"Error processing {function_name}"}),
                    "call_id": call_id
                }
            }))
            
            # CRITICAL: Request response even after errors to ensure Richard responds
            await self.voice_live_ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"]
                }
            }))

    async def _handle_user_interruption(self, new_item_id: str) -> None:
        """
        Enhanced multi-layered user interruption handler implementing comprehensive audio control.
        Uses conversation.item.truncate for precise item-level control and response.cancel for immediate stopping.
        """
        logger.info(f"🛑 INTERRUPTION INITIATED | New Item: {new_item_id} | Active Response: {self._current_response_id}")
        
        # LAYER 1: Immediate local state changes - stop everything locally first
        old_streaming_state = self._is_streaming_audio
        self._is_streaming_audio = False
        self._audio_stream_start_time = None
        logger.info(f"🛑 Local audio streaming stopped: {old_streaming_state} → {self._is_streaming_audio}")
        
        # LAYER 2: Clear local audio queue to prevent buffered chunks from being sent
        if hasattr(self, '_current_response_audio_queue'):
            try:
                queue_size = self._current_response_audio_queue.qsize() if hasattr(self._current_response_audio_queue, 'qsize') else len(self._current_response_audio_queue)
                self._current_response_audio_queue.clear()
                logger.info(f"🧹 Local audio queue cleared: {queue_size} items removed")
            except:
                logger.debug("🧹 Audio queue clearing attempted")
        
        # LAYER 3: Cancel current response in Azure (immediate effect)
        if self._current_response_id and self.voice_live_ws:
            try:
                cancel_msg = {
                    "type": "response.cancel",
                    "event_id": f"cancel_{uuid.uuid4()}"
                }
                await self.voice_live_ws.send(json.dumps(cancel_msg))
                logger.info(f"✅ Sent response.cancel for Response ID: {self._current_response_id}")
            except Exception as e:
                logger.error(f"❌ Failed to send response.cancel: {e}")
        
        # LAYER 4: Clear input audio buffer to prevent echo/feedback
        if self.voice_live_ws:
            try:
                clear_input_msg = {
                    "type": "input_audio_buffer.clear",
                    "event_id": f"clear_input_{uuid.uuid4()}"
                }
                await self.voice_live_ws.send(json.dumps(clear_input_msg))
                logger.info(f"🧹 Input audio buffer clear requested")
            except Exception as e:
                logger.error(f"❌ Failed to clear input audio buffer: {e}")
        
        # LAYER 5: Item-specific truncation for precise audio control
        # Truncate any assistant audio items that may still be playing
        if hasattr(self, '_response_items') and getattr(self, '_response_items', []):
            current_time_ms = int(time.time() * 1000)  # Current time in milliseconds
            
            for item_id in self._response_items:
                try:
                    truncate_msg = {
                        "type": "conversation.item.truncate",
                        "item_id": item_id,
                        "content_index": 0,
                        "audio_end_ms": current_time_ms,  # Truncate at current timestamp
                        "event_id": f"truncate_{uuid.uuid4()}"
                    }
                    await self.voice_live_ws.send(json.dumps(truncate_msg))
                    logger.info(f"✂️ Sent conversation.item.truncate for Item ID: {item_id} at {current_time_ms}ms")
                except Exception as e:
                    logger.error(f"❌ Failed to truncate item {item_id}: {e}")
        
        # LAYER 6: Reset response tracking and prepare for new interaction
        old_response_id = self._current_response_id
        self._current_response_id = None
        if hasattr(self, '_response_items'):
            if not hasattr(self, '_response_items'):
                self._response_items = []
            self._response_items.clear()
        
        logger.info(f"🔄 Interruption tracking reset | Previous Response: {old_response_id} | Ready for new interaction")
        
        # LAYER 7: Optional aggressive fallback for WebRTC environments
        # Note: This only works in WebRTC mode, but included for completeness
        if self.voice_live_ws:
            try:
                clear_output_msg = {
                    "type": "output_audio_buffer.clear",
                    "event_id": f"clear_output_{uuid.uuid4()}"
                }
                await self.voice_live_ws.send(json.dumps(clear_output_msg))
                logger.debug(f"🧹 Output audio buffer clear attempted (WebRTC only)")
            except Exception as e:
                logger.debug(f"ℹ️ Output buffer clear not supported (WebSocket mode): {e}")
        
        logger.info(f"✅ MULTI-LAYERED INTERRUPTION COMPLETE | 7 layers executed for maximum effectiveness")

    async def _handle_hardware_interruption(self) -> None:
        """Handle hardware-level signal detection interruption (Rollback-Safe)"""
        try:
            # Only interrupt if there's an active AI response
            if self._current_response_id and self._is_streaming_audio:
                logger.info(f"🔥 HARDWARE INTERRUPTION triggered | Current Response: {self._current_response_id}")
                await self._handle_user_interruption("hardware_signal_detection")
            else:
                logger.debug(f"🔇 Hardware signal detected but no active response to interrupt | Response ID: {self._current_response_id} | Streaming: {self._is_streaming_audio}")
        except Exception as e:
            logger.error(f"❌ Hardware interruption error: {e}")

    async def receive_audio(self, data_payload: str) -> None:
        """Handle audio output from Voice Live API - send back to phone call via ACS."""
        try:
            # Check if we should still be streaming (interruption handling)
            if not self._is_streaming_audio:
                logger.info(f"🛑 Audio streaming stopped due to interruption - dropping audio chunk | Current Response ID: {self._current_response_id}")
                return
            
            data_payload_formatted = {
                "Kind": "AudioData",
                "AudioData": {"Data": data_payload},
                "StopAudio": None,
            }

            # Serialize the server streaming data
            serialized_data = json.dumps(data_payload_formatted)
            await self.send_message_async(serialized_data)

        except Exception as e:
            logger.error(f"Error in receive_audio: {e}")

    async def send_audio_async(self, audio_data: str) -> None:
        """Send audio data from phone call to Voice Live API."""
        if self.voice_live_ws and self.is_connected:
            audio_message = {
                "type": "input_audio_buffer.append",
                "audio": audio_data,
                "event_id": ""
            }
            try:
                await self.voice_live_ws.send(json.dumps(audio_message))
            except Exception as e:
                logger.error(f"Failed sending audio upstream: {e}")

    async def stop_audio_async(self) -> None:
        """Stop audio playback - maintains existing ACS integration."""
        try:
            stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
            json_data = json.dumps(stop_audio_data)
            await self.send_message_async(json_data)
        except Exception as e:
            logger.error(f"Stop Audio - Failed to send message: {e}")
            raise e

    async def close(self) -> None:
        """Clean up connections."""
        self.is_connected = False
        if self.voice_live_ws:
            try:
                await self.voice_live_ws.close()
            except Exception as e:
                logger.error(f"Error closing Voice Live WebSocket: {e}")
