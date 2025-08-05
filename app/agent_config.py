"""
Agent Configuration Module

This module defines agent configurations including instructions, voice settings,
functions, and tools. It separates business logic from communication handling.
"""

import os
from typing import List, Dict, Any, Optional
from app.recipe_finder import RecipeFinder
from app.it_helpdesk_tools import get_it_helpdesk_system_message
from app.policy_search_tools import search_it_policies_handler
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
        # Use environment variable for IT tools base URL with fallback
        self.base_url = os.getenv("IT_TOOLS_BASE_URL", "https://ec631f98f69c.ngrok-free.app")
        
        # Employee lookup function handler
        async def lookup_employee_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            employeeId = args.get("employeeId", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/employees/{employeeId}"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("success", False):
                                employee_name = data.get("employee", {}).get("name", "")
                                return {
                                    "output": json.dumps(data),
                                    "follow_up": {
                                        "instructions": f"Employee lookup successful for {employee_name}. Now you MUST proceed with security verification. Ask the user to answer their security question and then call verify_security_answer."
                                    }
                                }
                            else:
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
            employeeId = args.get("employeeId", "")
            security_answer = args.get("security_answer", "")
            question_type = args.get("question_type", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/verify_security_answer"
                    payload = {
                        "employeeId": employeeId,
                        "security_answer": security_answer,
                        "question_type": question_type
                    }
                    async with session.post(url, json=payload) as response:
                        data = await response.json()
                        if data.get("success", False) and data.get("verified", False):
                            return {
                                "output": json.dumps(data),
                                "follow_up": {
                                    "instructions": "Security verification successful. Now you MUST proceed with account recovery. Call account_recovery to complete the password reset."
                                }
                            }
                        else:
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
        
        # Policy search function handler
        async def search_policies_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            query = args.get("query", "")
            try:
                result = search_it_policies_handler(query)
                return {
                    "output": result,
                    "follow_up": None
                }
            except Exception as e:
                logger.error(f"Error searching policies: {e}")
                return {
                    "output": "I'm sorry, I couldn't search the policy database at the moment. Please try again later or contact IT support directly.",
                    "follow_up": None
                }

        # Account recovery function handler
        async def account_recovery_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            employeeId = args.get("employeeId", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/account_recovery"
                    payload = {"employeeId": employeeId}
                    async with session.post(url, json=payload) as response:
                        data = await response.json()
                        return {
                            "output": json.dumps(data),
                            "follow_up": {
                                "instructions": "Password reset completed successfully. Inform the user that their password has been reset and they should check their email for further instructions."
                            }
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
            description="STEP 1: Look up employee information by employee ID from HR SharePoint database. Used for identity verification during IT support requests like password resets. This is the FIRST and REQUIRED step in the password reset workflow.",
            parameters={
                "type": "object",
                "properties": {
                    "employeeId": {
                        "type": "string",
                        "description": "Employee ID provided by caller (format: EMP followed by numbers, e.g., 'EMP001234')"
                    }
                },
                "required": ["employeeId"]
            },
            handler=lookup_employee_handler
        )
        
        verify_security_function = AgentFunction(
            name="verify_security_answer",
            description="STEP 2: Verify caller's security question answer against HR database record. Can ONLY be called AFTER successful employee lookup to confirm identity. This step is MANDATORY before account recovery.",
            parameters={
                "type": "object",
                "properties": {
                    "employeeId": {
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
                "required": ["employeeId", "security_answer", "question_type"]
            },
            handler=verify_security_answer_handler
        )
        
        account_recovery_function = AgentFunction(
            name="account_recovery",
            description="STEP 3: Starts the account recovery process for the given employee ID. Can ONLY be called AFTER both successful employee lookup (STEP 1) AND successful security verification (STEP 2). This includes triggering a password reset and sending email confirmation.",
            parameters={
                "type": "object",
                "properties": {
                    "employeeId": {
                        "type": "string",
                        "description": "Employee ID that has been successfully looked up and verified"
                    }
                },
                "required": ["employeeId"]
            },
            handler=account_recovery_handler
        )
        
        search_policies_function = AgentFunction(
            name="search_it_policies",
            description="Search IT policy documents for information about company policies, procedures, guidelines, and rules. Use this when users ask about passwords, security, access, equipment, software, or any other IT-related policies.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The user's question or keywords to search for in IT policy documents"
                    }
                },
                "required": ["query"]
            },
            handler=search_policies_handler
        )

        super().__init__(
            name="IT Helpdesk Agent",
            voice="alloy",
            instructions=get_it_helpdesk_system_message(),
            functions=[lookup_employee_function, verify_security_function, account_recovery_function, search_policies_function],
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
