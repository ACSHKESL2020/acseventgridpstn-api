"""
Access Token Management for Azure AI Foundry Agent Service
Handles the generation and retrieval of agent access tokens for Voice Live API.

Based on official Azure documentation, agent mode uses direct Azure credential tokens
with scope 'https://ai.azure.com/.default' - no special token generation API needed.
"""

import os
from azure.identity.aio import DefaultAzureCredential
from loguru import logger as sync_logger

# Setup simple logger interface
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

# Configuration constants
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # Optional: will be auto-generated if not provided
AZURE_AGENT_ENDPOINT = os.getenv("AZURE_AGENT_ENDPOINT")  # Used for imports in voice_live_handler
AGENT_PROJECT_NAME = os.getenv("AGENT_PROJECT_NAME")      # Used for imports in voice_live_handler  
AGENT_ID = os.getenv("AGENT_ID")                          # Used for imports in voice_live_handler


async def get_agent_access_token() -> str:
    """Get or generate the Agent Access Token for Voice Live agent mode.
    
    Based on official Azure documentation:
    For agent mode, the agent_access_token is simply the Azure credential token
    with scope 'https://ai.azure.com/.default'
    
    Order of precedence:
    1) If ACCESS_TOKEN env is provided, use it (useful for local dev).
    2) Generate Azure credential token directly (this is the correct approach for agent mode).
    """
    if ACCESS_TOKEN:
        return ACCESS_TOKEN

    # Agent mode uses direct Azure credential token - no special API needed!
    # Source: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-agents-quickstart
    credential = DefaultAzureCredential()
    try:
        aad_token = await credential.get_token("https://ai.azure.com/.default")
        return aad_token.token
    finally:
        await credential.close()
