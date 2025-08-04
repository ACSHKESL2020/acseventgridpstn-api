import json
import os
import uuid  # Add uuid import
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

import logging
from aiologger import Logger

from app.agent_config import get_agent_config, AgentConfig

logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)
logger = Logger.with_default_handlers()


load_dotenv()

AZURE_OPENAI_REALTIME_ENDPOINT = os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT")
AZURE_OPENAI_REALTIME_SERVICE_KEY = os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")
AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME = os.getenv("AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME")

ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
ACS_SMS_CONNECTION_STRING = os.getenv("ACS_SMS_CONNECTION_STRING")

load_dotenv()

class CommunicationHandler:
    def __init__(self, websocket: WebSocket, agent_type: str = "it_helpdesk") -> None:
        self.rt_client = None
        self.active_websocket = websocket
        self.agent_config: AgentConfig = get_agent_config(agent_type)
        return

    async def start_conversation_async(self) -> None:
        self.rt_client = RTLowLevelClient(
            url=AZURE_OPENAI_REALTIME_ENDPOINT,
            key_credential=AzureKeyCredential(AZURE_OPENAI_REALTIME_SERVICE_KEY),
            azure_deployment=AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME,
        )
        try:
            # Add retry logic with exponential backoff for rate limiting
            max_retries = 3
            retry_delay = 1  # Start with 1 second
            
            for attempt in range(max_retries):
                try:
                    await self.rt_client.connect()
                    logger.info("Successfully connected to Azure OpenAI Realtime Service")
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < max_retries - 1:
                        logger.warning(f"Rate limit hit (attempt {attempt + 1}/{max_retries}), waiting {retry_delay}s before retry...")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        logger.error(f"Failed to connect to Azure OpenAI Realtime Service: {e}")
                        raise e
        except Exception as e:
            logger.error(f"Failed to connect to Azure OpenAI Realtime Service after all retries: {e}")
            raise e

        session_update_message = {
            "type": "session.update",
            "session": self.agent_config.get_session_config()
        }

        session_update_message_payload = SessionUpdateMessage(**session_update_message)
        await self.rt_client.send(session_update_message_payload)

        # Generate initial call_id that will be used for the entire conversation
        self.conversation_call_id = str(uuid.uuid4())

        # Don't send any initial message - let the user speak first
        # This prevents the confusion where Richard's greeting is sent as a user message
        # and OpenAI responds as if it needs help instead of being the helper
        
        # NOTE: Tell OpenAI to start the conversation and be ready to respond
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
                        print("Session Created Message")
                        print(f"Session Id: {message.session.id}")
                        pass
                    case "error":
                        print(f"Error: {message.error}")
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
                        print("Response Done Message")
                        print(f"  Response Id: {message.response.id}")

                        if message.response.status_details:
                            print(
                                f"Status Details: {message.response.status_details.model_dump_json()}"
                            )
                    case "response.audio_transcript.done":
                        print(f"AI:-- {message.transcript}")
                    case "response.audio.delta":
                        await self.receive_audio(message.delta)
                        pass
                    case "function_call":
                        print(f"Function Call Message: {message}")
                        # Store the original call_id from the function call
                        call_id = message.call_id
                        pass
                    case "response.function_call_arguments.done":
                        print(f"Message: {message}")
                        function_name = message.name
                        call_id = message.call_id
                        
                        # Handle potential incomplete JSON from OpenAI
                        try:
                            args = json.loads(message.arguments)
                            print(f"Function args: {message.arguments}")
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error for function arguments: {e}")
                            logger.error(f"Raw arguments: {message.arguments}")
                            # Send error response back to OpenAI
                            await self.rt_client.ws.send_json({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps({"success": False, "error": "Invalid function arguments received"})
                                }
                            })
                            continue

                        # Use agent config to handle function calls
                        try:
                            handler = self.agent_config.get_function_handler(function_name)
                            if handler:
                                result = await handler(args)
                            else:
                                result = {"output": json.dumps({"success": False, "error": f"Function {function_name} not found"})}
                            
                            await self.rt_client.ws.send_json(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "output": result["output"],
                                        "call_id": call_id
                                    }
                                }
                            )

                            # If there's a follow-up instruction, send response.create
                            if result.get("follow_up") and result["follow_up"].get("instructions"):
                                await self.rt_client.ws.send_json(
                                    {
                                        "type": "response.create",
                                        "response": {
                                            "modalities": ["text", "audio"],
                                            "instructions": result["follow_up"]["instructions"]
                                        }
                                    }
                                )
                        
                        except Exception as e:
                            logger.error(f"Error handling function call {function_name}: {e}")
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

                        logger.info(f"Function Call Arguments: {message.arguments}")
                        print(f"Function Call Arguments: {message.arguments}")
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
            # print(f"Stop Audio - Failed to send message: {e}")
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
                message=f"Hello from RecipeFinder! Here's the recipe you requested:\n\n{message}",
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
