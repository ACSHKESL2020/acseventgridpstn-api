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

# Voice Live API Configuration
AZURE_VOICE_LIVE_ENDPOINT = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
AZURE_VOICE_LIVE_API_KEY = os.getenv("AZURE_VOICE_LIVE_API_KEY") 
VOICE_LIVE_MODEL = os.getenv("VOICE_LIVE_MODEL", "gpt-4o")
AZURE_VOICE_LIVE_API_VERSION = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2025-05-01-preview")

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
            "voice": {
                "name": os.getenv("SESSION_VOICE_NAME", "en-US-Ava:DragonHDLatestNeural"),
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
        
        match message_type:
            case "session.created":
                logger.info(f"Voice Live session created: {message.get('session', {}).get('id')}")
                
            case "session.updated":
                logger.info("Voice Live session updated")
                
            case "error":
                err = message.get("error")
                logger.error(f"Voice Live API error: {err}")
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
                        fallback_body = {
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
                
            case "input_audio_buffer.speech_started":
                logger.info(f"📞 Voice activity detected from phone call at {message.get('audio_start_ms', 0)}ms")
                
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
                pass
                
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
                # Ignore delta messages - only process when complete
                pass
                
            case _:
                logger.debug(f"Unhandled Voice Live message type: {message_type}")

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

    async def receive_audio(self, data_payload: str) -> None:
        """Handle audio output from Voice Live API - send back to phone call via ACS."""
        try:
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
