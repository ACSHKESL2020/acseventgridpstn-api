import json
import os
import uuid
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from openai import AzureOpenAI
import base64
import asyncio
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential
from azure.communication.sms import SmsClient, SmsSendResult
from rtclient import (
    FunctionCallOutputItem,
    InputAudioBufferAppendMessage,
    InputAudioTranscription,
    InputTextContentPart,
    ItemCreateMessage,
    RTLowLevelClient,
    ResponseCreateMessage,
    ResponseCreateParams,
    ServerMessageType,
    ServerVAD,
    SessionUpdateMessage,
    SessionUpdateParams,
    UserMessageItem,
)
from rtclient.low_level_client import RTLowLevelClient as BaseRTLowLevelClient
from rtclient.models import ServerMessageType, UserMessageType, create_message_from_dict
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError
from rtclient.util.user_agent import get_user_agent
from typing import Optional, AsyncIterator
from collections.abc import AsyncIterator

import logging
from aiologger import Logger

logging.basicConfig(level=logging.INFO)
logger = Logger.with_default_handlers()

load_dotenv()


class RTLowLevelClientAgent(BaseRTLowLevelClient):
    """
    Custom RTLowLevelClient that supports Azure AI Agent Mode with agent_id parameter.
    Based on your requirements: mode=agent, agent_id, accessToken, endpoint, model='gpt-4o-realtime'
    """
    
    def __init__(
        self,
        url: str,
        key_credential: AzureKeyCredential,
        agent_id: str,
        access_token: str,
        model: str = "gpt-4o-realtime",
    ):
        # Initialize with agent mode parameters
        self._url = url
        self._key_credential = key_credential
        self._agent_id = agent_id
        self._access_token = access_token  # Store access token
        self._model = model
        self._session = ClientSession(base_url=self._url)
        self.request_id: Optional[uuid.UUID] = None
        self._is_azure_openai = True  # Agent mode is always Azure
    
    async def connect(self):
        """Connect with agent mode parameters"""
        try:
            self.request_id = uuid.uuid4()
            
            print(f"🤖 AGENT MODE: Connecting with agent_id={self._agent_id}")
            print(f"🤖 AGENT MODE: Endpoint={self._url}")
            print(f"🤖 AGENT MODE: Model={self._model}")
            
            # Agent mode connection parameters
            headers = {
                "x-ms-client-request-id": str(self.request_id),
                "User-Agent": get_user_agent(),
                "Authorization": f"Bearer {self._access_token}",  # Use your ACCESS_TOKEN
            }
            
            # Agent mode WebSocket connection with specific parameters
            params = {
                "mode": "agent",  # This is the key parameter for agent mode
                "agent_id": self._agent_id,
                "model": self._model,
                "api-version": "2024-10-01-preview",
            }
            
            print(f"🤖 AGENT MODE: Connection params={params}")
            
            # Connect to the agent endpoint with agent mode parameters
            agent_ws_url = f"{self._endpoint}/agents/realtime"  # Back to your original endpoint path
            print(f"🤖 AGENT MODE: Connecting to URL: {agent_ws_url}")
            print(f"🤖 AGENT MODE: With params: {params}")
            
            self.ws = await self._session.ws_connect(
                agent_ws_url,  # Use your Azure AI Agent endpoint path
                headers=headers,
                params=params,
            )
            
            print("🤖 AGENT MODE: Successfully connected to Azure AI Agent Service!")
            
        except WSServerHandshakeError as e:
            await self._session.close()
            error_message = f"Received status code {e.status} from the agent service"
            print(f"❌ AGENT MODE ERROR: {error_message}")
            raise ConnectionError(error_message, e.headers) from e

# Azure AI Agent Mode Configuration
AGENT_ID = os.getenv("AGENT_ID")
AZURE_AGENT_ENDPOINT = os.getenv("AZURE_AGENT_ENDPOINT")
AZURE_AGENT_DEPLOYMENT = os.getenv("AZURE_AGENT_DEPLOYMENT")
AGENT_PROJECT_NAME = os.getenv("AGENT_PROJECT_NAME")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
AZURE_OPENAI_REALTIME_SERVICE_KEY = os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")

# ACS Configuration
ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
ACS_SMS_CONNECTION_STRING = os.getenv("ACS_SMS_CONNECTION_STRING")


class CommunicationHandlerAgent:
    """
    Communication handler that uses Azure AI Agent Mode with pre-configured assistant
    instead of direct real-time API with custom agent configuration.
    """
    
    def __init__(self, websocket: WebSocket) -> None:
        self.rt_client = None
        self.active_websocket = websocket
        self.agent_id = AGENT_ID
        return

    async def start_conversation_async(self) -> None:
        """Initialize connection using Azure AI Agent Mode"""
        
        # Create a custom RTLowLevelClient that connects to Azure AI Agent Service
        # with the agent mode parameters you specified
        self.rt_client = RTLowLevelClientAgent(
            url=AZURE_AGENT_ENDPOINT,
            key_credential=AzureKeyCredential(ACCESS_TOKEN),  # Use ACCESS_TOKEN for agent mode
            agent_id=AGENT_ID,
            access_token=ACCESS_TOKEN,  # Pass the access token separately
            model="gpt-4o-realtime",  # Specify the model as you mentioned
        )
        
        try:
            await self.rt_client.connect()
        except Exception as e:
            print(f"Failed to connect to Azure AI Agent Service: {e}")
            raise e

        # Agent mode session configuration - uses pre-configured assistant
        session_update_message = {
            "type": "session.update",
            "session": {
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "threshold": 0.6,
                    "silence_duration_ms": 300,
                    "prefix_padding_ms": 200,
                    "type": "server_vad",
                },
                # In agent mode, the assistant is already configured
                # No need to specify instructions or tools
            },
        }

        session_update_message_payload = SessionUpdateMessage(**session_update_message)
        await self.rt_client.send(session_update_message_payload)

        # Generate initial call_id
        self.conversation_call_id = str(uuid.uuid4())

        # Let the pre-configured agent handle the initial greeting
        content_part = InputTextContentPart(
            text=f"You are having a conversation with a returning user named Asihan. Use your pre-configured personality and tools to help them."
        )
        initial_conversation_item = ItemCreateMessage(
            item=UserMessageItem(content=[content_part]),
            call_id=self.conversation_call_id
        )

        await self.rt_client.send(message=initial_conversation_item)
        await self.rt_client.send(ResponseCreateMessage())

        asyncio.create_task(self.receive_messages_async())
        return

    async def send_message_async(self, message: str) -> None:
        try:
            if self.active_websocket.client_state == WebSocketState.CONNECTED:
                await self.active_websocket.send_text(message)
        except Exception as e:
            logger.error(f"Send Message - Failed to send message: {e}")
            raise e

    async def receive_messages_async(self) -> None:
        try:
            while not self.rt_client.closed:
                message: ServerMessageType = await self.rt_client.recv()

                if message is None or self.rt_client.ws.closed:
                    continue
                
                match message.type:
                    case "session.created":
                        print("Agent Mode Session Created")
                        print(f"Session Id: {message.session.id}")
                        print(f"Agent Id: {self.agent_id}")
                        pass
                    case "error":
                        print(f"Agent Error: {message.error}")
                        pass
                    case "input_audio_buffer.cleared":
                        print("Input Audio Buffer Cleared Message")
                        pass
                    case "input_audio_buffer.speech_started":
                        print(
                            f"Voice activity detection started at {message.audio_start_ms} [ms]"
                        )
                        await self.stop_audio_async()
                        pass
                    case "input_audio_buffer.speech_stopped":
                        pass
                    case "conversation.item.input_audio_transcription.completed":
                        print(f"User:-- {message.transcript}")
                    case "conversation.item.input_audio_transcription.failed":
                        print(f"Error: {message.error}")
                    case "response.done":
                        print("Agent Response Done")
                        print(f"  Response Id: {message.response.id}")

                        if message.response.status_details:
                            print(
                                f"Status Details: {message.response.status_details.model_dump_json()}"
                            )
                    case "response.audio_transcript.done":
                        print(f"Agent:-- {message.transcript}")
                    case "response.audio.delta":
                        await self.receive_audio(message.delta)
                        pass
                    case "function_call":
                        print(f"Agent Function Call: {message}")
                        call_id = message.call_id
                        pass
                    case "response.function_call_arguments.done":
                        print(f"Agent Function Call Complete: {message}")
                        function_name = message.name
                        args = json.loads(message.arguments)
                        call_id = message.call_id

                        print(f"Agent Function args: {message.arguments}")

                        # In Agent Mode, the pre-configured agent handles function calls
                        # We just need to acknowledge and let the agent continue
                        try:
                            # The agent's pre-configured tools will handle the function execution
                            # We acknowledge the function call but let the agent manage it
                            await self.rt_client.ws.send_json(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "output": f"Function {function_name} executed by pre-configured agent.",
                                        "call_id": call_id
                                    }
                                }
                            )

                        except Exception as e:
                            logger.error(f"Error in agent function call {function_name}: {e}")
                            await self.rt_client.ws.send_json(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "output": f"Sorry, I encountered an error while processing your request.",
                                        "call_id": call_id
                                    }
                                }
                            )

                        logger.info(f"Agent Function Call Arguments: {message.arguments}")
                        print(f"Agent Function Call Arguments: {message.arguments}")
                        pass
                    case _:
                        pass
        except Exception as e:
            logger.error(f"Error in receive_messages_async: {e}")
            if not isinstance(e, asyncio.CancelledError):
                raise e

    async def receive_audio(self, data_payload) -> None:
        try:
            data_payload = {
                "Kind": "AudioData",
                "AudioData": {"Data": data_payload},
                "StopAudio": None,
            }

            # Serialize the server streaming data
            serialized_data = json.dumps(data_payload)
            await self.send_message_async(serialized_data)

        except Exception as e:
            print(e)

    async def send_audio_async(self, audio_data: str) -> None:
        await self.rt_client.send(
            message=InputAudioBufferAppendMessage(
                type="input_audio_buffer.append", audio=audio_data, _is_azure=True
            )
        )

    async def stop_audio_async(self) -> None:
        try:
            stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
            json_data = json.dumps(stop_audio_data)
            await self.send_message_async(json_data)
        except Exception as e:
            logger.error(f"Stop Audio - Failed to send message: {e}")
            raise e
        return

    async def send_sms(self, message: str) -> None:
        try:
            # Check if SMS configuration is available
            if not ACS_SMS_CONNECTION_STRING:
                raise ValueError("SMS connection string not configured")
            
            if not self.target_phone_number:
                raise ValueError("Target phone number not configured")
                
            sms_client = SmsClient.from_connection_string(ACS_SMS_CONNECTION_STRING)
            sms_response_list: list[SmsSendResult] = sms_client.send(
                from_=os.getenv("ACS_SMS_FROM_PHONE_NUMBER"),
                to=[self.target_phone_number],
                message=f"Hello from your AI Agent! Here's what you requested:\n\n{message}",
            )

            for sms_response in sms_response_list:
                if sms_response.successful is True:
                    logger.info(f"SMS sent: {sms_response}")
                else:
                    logger.error(f"Failed to send SMS: {sms_response}")
                    raise Exception(f"SMS sending failed: {sms_response}")

        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")
            raise e
