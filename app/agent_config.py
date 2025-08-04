"""
Agent Configuration Module

This module defines agent configurations including instructions, voice settings,
functions, and tools. It separates business logic from communication handling.
"""

from typing import List, Dict, Any, Optional
from app.recipe_finder import RecipeFinder
from app.it_helpdesk_tools import get_it_helpdesk_tools, get_it_helpdesk_system_message
import aiohttp
import json

# Logger setup
from loguru import logger


class AgentFunction:
    """Represents a function that an agent can call"""
    
    def __init__(self, name: str, description: str, parameters: Dict[str, Any], handler):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for OpenAI API"""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }


class AgentConfig:
    """Base configuration for agents"""
    
    def __init__(self, name: str, voice: str, instructions: str, functions: List[AgentFunction], vad_settings: Dict[str, Any]):
        self.name = name
        self.voice = voice
        self.instructions = instructions
        self.functions = functions
        self.vad_settings = vad_settings
    
    def get_functions_dict(self) -> List[Dict[str, Any]]:
        """Get functions in OpenAI format"""
        return [func.to_dict() for func in self.functions]
    
    def get_function_handler(self, function_name: str):
        """Get handler for a specific function"""
        for func in self.functions:
            if func.name == function_name:
                return func.handler
        return None
    
    def get_initial_greeting(self, user_name: str = "there") -> str:
        """Get initial greeting message for the agent"""
        return f"Hello {user_name}, how can I help you today?"
    
    def get_session_config(self) -> Dict[str, Any]:
        """Get session configuration for OpenAI Realtime API"""
        return {
            "modalities": ["text", "audio"],
            "instructions": self.instructions,
            "voice": self.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": self.vad_settings,
            "tools": self.get_functions_dict(),
            "temperature": 0.7,
            "max_response_output_tokens": 4096
        }


class RecipeAssistantConfig(AgentConfig):
    """Configuration for Recipe Assistant Agent"""
    
    def __init__(self):
        async def find_recipe_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            cuisine = args.get("cuisine", "")
            ingredients = args.get("ingredients", [])
            
            recipe_finder = RecipeFinder()
            recipes = await recipe_finder.find_recipe(cuisine, ingredients)
            
            return {
                "output": recipes,
                "follow_up": "Would you like more details about any of these recipes or search for something else?"
            }
        
        find_recipe_function = AgentFunction(
            name="find_recipe",
            description="Find recipes based on cuisine type and available ingredients",
            parameters={
                "type": "object",
                "properties": {
                    "cuisine": {
                        "type": "string",
                        "description": "Type of cuisine (e.g., Italian, Mexican, Asian)"
                    },
                    "ingredients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of available ingredients"
                    }
                },
                "required": ["cuisine", "ingredients"]
            },
            handler=find_recipe_handler
        )
        
        super().__init__(
            name="Recipe Assistant",
            voice="alloy",
            instructions="""You are a helpful recipe assistant. Help users find recipes based on their preferred cuisine and available ingredients. 

When users ask about recipes:
1. Ask them what type of cuisine they prefer
2. Ask what ingredients they have available
3. Use the find_recipe function to search for recipes
4. Present the results in a friendly, conversational way
5. Offer to provide more details or search for alternatives

Be enthusiastic about cooking and food! Keep responses conversational and helpful.""",
            functions=[find_recipe_function],
            vad_settings={
                "threshold": 0.6,
                "silence_duration_ms": 300,
                "prefix_padding_ms": 200,
                "type": "server_vad"
            }
        )
    
    def get_initial_greeting(self, user_name: str = "there") -> str:
        """Get initial greeting message for the agent"""
        return f"You are having a conversation with a returning user named {user_name}. Greet the user with a quick cheery message asking how you can help them find a recipe."


class ITHelpdeskConfig(AgentConfig):
    """Configuration for IT Helpdesk Agent (Richard)"""
    
    def __init__(self):
        # Use localhost for testing since both servers are on same machine
        # TODO: Switch back to DevTunnel when it's working properly
        self.base_url = "http://localhost:8081"  # Local Node.js server
        
        # Employee lookup function handler
        async def lookup_employee_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            employee_id = args.get("employeeId", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/employees/{employee_id}"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            return {
                                "output": json.dumps(data),
                                "follow_up": None
                            }
                        else:
                            return {
                                "output": json.dumps({"success": False, "error": "Employee not found"}),
                                "follow_up": None
                            }
            except Exception as e:
                logger.error(f"Error calling lookup_employee API: {e}")
                return {
                    "output": json.dumps({"success": False, "error": "API call failed"}),
                    "follow_up": None
                }
        
        # Security verification function handler  
        async def verify_security_answer_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            employee_id = args.get("employee_id", "")
            security_answer = args.get("security_answer", "")
            question_type = args.get("question_type", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/verify_security_answer"
                    payload = {
                        "employee_id": employee_id,
                        "security_answer": security_answer,
                        "question_type": question_type
                    }
                    async with session.post(url, json=payload) as response:
                        data = await response.json()
                        return {
                            "output": json.dumps(data),
                            "follow_up": None
                        }
            except Exception as e:
                logger.error(f"Error calling verify_security_answer API: {e}")
                return {
                    "output": json.dumps({"success": False, "error": "API call failed"}),
                    "follow_up": None
                }
        
        # Account recovery function handler
        async def account_recovery_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            employee_id = args.get("employee_id", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/account_recovery"
                    payload = {"employee_id": employee_id}
                    async with session.post(url, json=payload) as response:
                        data = await response.json()
                        return {
                            "output": json.dumps(data),
                            "follow_up": None
                        }
            except Exception as e:
                logger.error(f"Error calling account_recovery API: {e}")
                return {
                    "output": json.dumps({"success": False, "error": "API call failed"}),
                    "follow_up": None
                }
        
        # Define IT helpdesk functions
        lookup_employee_function = AgentFunction(
            name="lookup_employee",
            description="Look up employee information by employee ID from HR SharePoint database. Used for identity verification during IT support requests like password resets.",
            parameters={
                "type": "object",
                "properties": {
                    "employeeId": {
                        "type": "string",
                        "description": "Employee ID provided by caller (format: EMP followed by 4 digits, e.g., 'EMP1029')"
                    }
                },
                "required": ["employeeId"]
            },
            handler=lookup_employee_handler
        )
        
        verify_security_function = AgentFunction(
            name="verify_security_answer",
            description="Verify caller's security question answer against HR database record. Only call this after successful employee lookup to confirm identity.",
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {
                        "type": "string", 
                        "description": "Employee ID from previous successful lookup"
                    },
                    "security_answer": {
                        "type": "string",
                        "description": "Caller's answer to the security question that was asked"
                    },
                    "question_type": {
                        "type": "string",
                        "enum": ["manager_name", "department", "office_location", "start_year"],
                        "description": "Type of security question being verified - must match the question that was asked"
                    }
                },
                "required": ["employee_id", "security_answer", "question_type"]
            },
            handler=verify_security_answer_handler
        )
        
        account_recovery_function = AgentFunction(
            name="account_recovery",
            description="Starts the account recovery process for the given employee ID. This can include triggering a password reset and sending email confirmation.",
            parameters={
                "type": "object",
                "properties": {
                    "employee_id": {
                        "type": "string",
                        "description": "Employee's unique ID"
                    }
                },
                "required": ["employee_id"]
            },
            handler=account_recovery_handler
        )
        
        super().__init__(
            name="IT Helpdesk Agent",
            voice="alloy",
            instructions=get_it_helpdesk_system_message(),
            functions=[lookup_employee_function, verify_security_function, account_recovery_function],
            vad_settings={
                "threshold": 0.6,
                "silence_duration_ms": 300,
                "prefix_padding_ms": 200,
                "type": "server_vad"
            }
        )
    
    def get_initial_greeting(self, user_name: str = "there") -> str:
        """Get initial greeting message for Richard"""
        return "Hello there, My name is Richard, how can I help you today?"
    
    def update_base_url(self, new_url: str):
        """Update the base URL for API calls (for devTunnel)"""
        self.base_url = new_url


def get_agent_config(agent_type: str = "it_helpdesk") -> AgentConfig:
    """Factory function to get agent configuration based on type"""
    if agent_type == "recipe_assistant":
        return RecipeAssistantConfig()
    elif agent_type == "it_helpdesk":
        return ITHelpdeskConfig()
    else:
        raise ValueError(f"Unknown agent type: {agent_type}")


# Available agent types
AVAILABLE_AGENTS = {
    "recipe_assistant": "Recipe Assistant - Helps find recipes based on ingredients and cuisine preferences",
    "it_helpdesk": "IT Helpdesk Agent (Richard) - Helps with password resets and IT support"
}
