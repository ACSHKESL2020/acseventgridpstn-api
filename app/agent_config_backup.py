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
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI function format"""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }


class AgentConfig:
    """Base configuration class for agents"""
    
    def __init__(
        self,
        name: str,
        voice: str = "alloy",
        instructions: str = "",
        functions: Optional[List[AgentFunction]] = None,
        vad_settings: Optional[Dict[str, Any]] = None
    ):
        self.name = name
        self.voice = voice
        self.instructions = instructions
        self.functions = functions or []
        self.vad_settings = vad_settings or {
            "threshold": 0.2,
            "silence_duration_ms": 700,
            "prefix_padding_ms": 500,
            "type": "server_vad"
        }
    
    def get_openai_functions(self) -> List[Dict[str, Any]]:
        """Get functions in OpenAI format"""
        return [func.to_openai_format() for func in self.functions]
    
    def get_session_config(self) -> Dict[str, Any]:
        """Get complete session configuration for OpenAI"""
        return {
            "voice": self.voice,
            "instructions": self.instructions,
            "input_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": self.vad_settings,
            "tools": self.get_openai_functions(),
        }
    
    async def handle_function_call(self, function_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle function calls and return response"""
        for func in self.functions:
            if func.name == function_name:
                try:
                    result = await func.handler(args)
                    return {
                        "success": True,
                        "output": result.get("output", "Function executed successfully"),
                        "follow_up": result.get("follow_up", None)
                    }
                except Exception as e:
                    logger.error(f"Error executing function {function_name}: {e}")
                    return {
                        "success": False,
                        "output": f"Sorry, I encountered an error while executing {function_name}.",
                        "follow_up": None
                    }
        
        return {
            "success": False,
            "output": f"Unknown function: {function_name}",
            "follow_up": None
        }


class RecipeAssistantConfig(AgentConfig):
    """Configuration for Recipe Assistant Agent"""
    
    def __init__(self):
        # Recipe search function handler
        async def get_recipe_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            ingredients = args.get("ingredients", [])
            cuisine = args.get("cuisine", "")
            
            recipe_finder = RecipeFinder()
            recipes = await recipe_finder.find_recipe(cuisine, ingredients)
            
            first_recipe = next(iter(recipes), None)
            if not first_recipe:
                return {
                    "output": "I couldn't find a recipe for you.",
                    "follow_up": None
                }
            
            recipe_name = first_recipe["name"]
            recipe_url = first_recipe["url"]
            
            return {
                "output": f"Here is a recipe for you: {recipe_name}",
                "follow_up": {
                    "instructions": f"Respond to the user that you found a recipe named {recipe_name}. Be concise and friendly.",
                    "recipe_url": recipe_url
                }
            }
        
        # SMS function handler (commented out)
        async def send_recipe_handler(args: Dict[str, Any]) -> Dict[str, Any]:
            url = args.get("url", "")
            # SMS functionality would go here when phone numbers are configured
            return {
                "output": f"I found a great recipe for you! Here's the link: {url}",
                "follow_up": None
            }
        
        # Define functions
        get_recipe_function = AgentFunction(
            name="get_recipe",
            description="Get a recipe based on the cuisine and provided list of ingredients.",
            parameters={
                "type": "object",
                "properties": {
                    "cuisine": {
                        "type": "string",
                        "description": "The type of cuisine the user is interested in.",
                    },
                    "ingredients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "A list of ingredients the user has available.",
                    },
                },
                "required": ["cuisine", "ingredients"],
            },
            handler=get_recipe_handler
        )
        
        # SMS function (commented out for now)
        # send_recipe_function = AgentFunction(
        #     name="send_recipe",
        #     description="Send a link to the recipe.",
        #     parameters={
        #         "type": "object",
        #         "properties": {"url": {"type": "string"}},
        #         "required": ["url"],
        #     },
        #     handler=send_recipe_handler
        # )
        
        instructions = """
        You are Chef's Assistant, an AI expert in finding recipes from SeriousEats.com. Your role is to:

        - Help users discover recipes based on their available ingredients
        - Guide the conversation in a warm, friendly, and concise manner
        - Recognize when users are ending the conversation and respond appropriately to farewells
        - Ask focused questions about:
          * Type of cuisine they're interested in
          * Key ingredients they have available
          * Any dietary preferences or restrictions
        
        Conversation Flow:
        1. Start with a brief, welcoming greeting
        2. Ask about cuisine preferences and available ingredients
        3. If needed, ask about additional common pantry ingredients they might have
        4. Once you find a suitable recipe, offer to share the link
        5. When users say goodbye (bye, goodbye, see you later, etc.), respond with a friendly farewell

        Guidelines:
        - Keep responses brief and focused
        - Suggest recipes only from SeriousEats.com
        - If a recipe requires additional ingredients, mention them upfront
        - When sharing a recipe, highlight its key features in 1-2 sentences
        - When users are leaving, say goodbye politely instead of continuing to offer recipe help
        - Recognize farewell phrases like "bye", "goodbye", "see you", "talk to you later", etc.
        """
        
        super().__init__(
            name="Recipe Assistant",
            voice="alloy",
            instructions=instructions.strip(),
            functions=[get_recipe_function],  # add send_recipe_function when SMS is enabled
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
        # Set the devTunnel base URL - will be updated when testing
        self.base_url = "https://YOUR_DEVTUNNEL_URL_HERE"  # TODO: Update with actual devTunnel URL
        
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
                        "description": "Employee ID provided by caller (format: EMP followed by numbers, e.g., 'EMP001234')"
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


# Agent factory function
def get_agent_config(agent_type: str = "it_helpdesk") -> AgentConfig:
    """Factory function to get agent configuration by type"""
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
}
