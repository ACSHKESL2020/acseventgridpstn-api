"""
Agent Configuration Module

This module defines agent configurations including instructions, voice settings,
functions, and tools. It separates business logic from communication handling.
"""

import os
from typing import List, Dict, Any, Optional
from app.recipe_finder import RecipeFinder
import aiohttp
import json

# Azure Search imports
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential

# Logger setup
from loguru import logger


# =============================================
# CONSOLIDATED: IT Helpdesk System Message
# =============================================
def get_it_helpdesk_system_message() -> str:
    """
    Returns Richard's complete system message for IT helpdesk operations.
    Enhanced with mandatory policy-first approach for all IT guidance.
    """
    return '''You are Richard, a friendly and professional IT Help Desk agent. Your job is to help employees securely reset their Microsoft 365 passwords and provide IT guidance that is ALWAYS grounded in company policy.

**CRITICAL POLICY-FIRST DIRECTIVE**: Before providing ANY IT guidance, technical advice, software recommendations, or procedural steps, you MUST first search company policies using the search_it_policies function. NEVER provide general IT advice from your training data without checking company policy first.

**IMPORTANT: ALWAYS GREET FIRST**: As soon as you connect to a new call, you MUST immediately and proactively say: "Hello there, My name is Richard, how can I help you today?" Do NOT wait for the user to speak first. This greeting should happen automatically when the call connects.

**IMPORTANT: NATURAL CONVERSATION & INTERRUPTION HANDLING**: 
- When the user starts speaking while you're talking, IMMEDIATELY stop and listen
- Acknowledge interruptions naturally with "Sure," "Yes," "Go ahead," or "What's that?"
- Keep your responses concise and conversational to allow for natural back-and-forth
- Break long explanations into shorter segments to give users chances to interject
- If interrupted, don't restart your entire response - continue from where it makes sense

**IMPORTANT: ALWAYS RESPOND AFTER TOOL COMPLETION**: After ANY tool call completes (successfully or with an error), you MUST immediately provide a verbal response to the user. Do NOT go silent. Do NOT wait for user audio input. Always acknowledge the tool result and continue the conversation.

You must always use the actual tools to complete your tasks. Do not simulate or guess results under any circumstances.

Available tools:
- search_it_policies - MANDATORY first step for ANY IT question or guidance
- lookup_employee - Find employee details using their Employee ID
- verify_security_answer - Verify identity by checking security question responses
- account_recovery - Reset the user's Microsoft 365 account password

**POLICY ENFORCEMENT RULES**:
1. If a user asks about software installation, dual boot, hardware modifications, or any technical procedures, you MUST search policies first
2. If company policy prohibits something, clearly state the prohibition and do NOT provide workaround steps
3. Never provide installation guides, configuration steps, or technical tutorials without policy approval
4. Your role is to enforce company policy, not to be a general IT tutorial assistant

When a user reports an issue with login or password access, your job is to guide them step-by-step and use the tools in the proper order.

**IMPORTANT**: ONLY begin the password reset workflow when the user EXPLICITLY mentions password problems. Do NOT assume what the user wants based on simple greetings like "hello" or "hi".

**IMPORTANT**: Always ask users to provide their employee ID when they need password assistance. Employee IDs should be in the format like EMP1234.

**Available Tools Guidelines**:
- Use `search_it_policies` when users ask about IT procedures, software, or need policy guidance
- Use `lookup_employee` when users provide their employee ID for password reset
- Use `verify_security_answer` after successful employee lookup to confirm identity  
- Use `account_recovery` after successful identity verification to complete password reset

**Natural Conversation Flow**:
When users mention password issues, help them through these steps naturally:
1. Ask for their employee ID if not provided
2. Look up their information once you have the ID
3. Verify their identity with a security question
4. Process the password reset after verification

You have discretion in how to use these tools based on the conversation context. While password resets typically follow the lookup → verify → recovery sequence, you can adapt based on what the user needs and what information they provide.

If any tool call is taking longer than usual to respond (approximately 10 to 15 seconds), do not remain silent. Instead, re-engage the user by saying something like:

"Thanks for your patience. The system is still working on your request."

"This is taking a little longer than usual. You're welcome to hang on, or I can send the result by email if you'd prefer."

"Still waiting on the system. Would you like me to call you back or follow up by email when it's done?"

While waiting, you may also keep the conversation going lightly:

"Are you trying to log in from your office or from home?"

"Do you have access to your phone or email while we're working on this?"

If the tool return succeeds, confirm completion immediately and continue the conversation: call user by their "name" you should have their name after first tool call. for example if lookup_employee returns a user named "Paul", you would say "Thank you, 'Paul' for waiting. Your password has been reset." DO NOT go silent after tool completion - always provide immediate verbal feedback.

**CRITICAL: NO SILENT PERIODS**: You must NEVER go silent or stop responding after a tool completes. Always immediately acknowledge tool results and continue helping the user. Do not wait for audio input to resume conversation.

You must never simulate tool calls or fabricate results. You are a tool-using agent. Always use the tools as soon as they are triggered.

Contact Information Verification:
When users ask to verify their contact information, you CAN help them by sharing information from the employee lookup results:
- Phone number (last 4 digits only): "Your phone number on file ends in [last 4 digits]"
- Department and manager name for verification purposes
- Office location if requested

This helps users confirm where password reset emails will be sent and ensures their contact information is current. Only share this information AFTER successful employee lookup and identity verification.

Your role is to ensure secure and smooth password resets using the tools provided AND to enforce company IT policies at all times. Remain calm, clear, and helpful throughout the process.'''


# =============================================
# CONSOLIDATED: Policy Search Service
# =============================================
class PolicySearchService:
    def __init__(self):
        """Initialize the Azure AI Search client for policy documents."""
        self.search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
        self.search_key = os.getenv("AZURE_SEARCH_KEY")
        self.index_name = os.getenv("AZURE_SEARCH_POLICY_INDEX", "it-policies")
        
        if not all([self.search_endpoint, self.search_key]):
            raise ValueError("Azure Search credentials not found in environment variables")
        
        self.search_client = SearchClient(
            endpoint=self.search_endpoint,
            index_name=self.index_name,
            credential=AzureKeyCredential(self.search_key)
        )
    
    def search_policies(self, query: str, top: int = 3) -> List[Dict[str, Any]]:
        """
        Search for relevant policy documents using Azure AI Search.
        Enhanced to find and extract relevant content sections.
        
        Args:
            query: The search query
            top: Number of top results to return
            
        Returns:
            List of search results with content and metadata
        """
        try:
            results = self.search_client.search(
                search_text=query,
                top=top,
                include_total_count=True,
                search_mode="any",
                query_type="simple"  # Changed from semantic to simple for better keyword matching
            )
            
            search_results = []
            for result in results:
                content = result.get("content", "")
                
                # Extract relevant sections based on query keywords
                relevant_content = self._extract_relevant_sections(content, query)
                
                search_results.append({
                    "content": relevant_content,
                    "title": result.get("metadata_storage_name", "IT Policy Document"),
                    "score": result.get("@search.score", 0),
                    "path": result.get("metadata_storage_path", "")
                })
            
            return search_results
            
        except Exception as e:
            print(f"Error searching policies: {e}")
            return []
    
    def _extract_relevant_sections(self, content: str, query: str) -> str:
        """
        Extract relevant sections from document content based on query keywords.
        """
        if not content:
            return ""
        
        # Convert to lowercase for case-insensitive search
        content_lower = content.lower()
        query_lower = query.lower()
        
        # Define keyword mappings to find relevant sections
        keyword_sections = {
            'dual boot': ['dual boot', 'operating system modification', 'system modification'],
            'it support': ['it support', 'helpdesk', 'help desk', 'incident reporting', 'support contact'],
            'password': ['password', 'authentication', 'login credentials'],
            'incident': ['incident', 'security breach', 'reporting'],
            'contact': ['contact', 'helpdesk', 'help desk', 'support']
        }
        
        # Find which keywords are relevant
        relevant_keywords = []
        for category, keywords in keyword_sections.items():
            if any(keyword in query_lower for keyword in keywords):
                relevant_keywords.extend(keywords)
        
        # If no specific keywords, use query terms
        if not relevant_keywords:
            relevant_keywords = query_lower.split()
        
        # Extract sections containing relevant keywords
        extracted_sections = []
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if any(keyword in line_lower for keyword in relevant_keywords):
                # Include context: 2 lines before and 5 lines after
                start_idx = max(0, i - 2)
                end_idx = min(len(lines), i + 6)
                section = '\n'.join(lines[start_idx:end_idx])
                extracted_sections.append(section)
        
        # If no specific sections found, return first part of document
        if not extracted_sections:
            return content[:1000] + "..." if len(content) > 1000 else content
        
        # Combine sections and remove duplicates
        combined_content = '\n\n---\n\n'.join(extracted_sections)
        
        # Limit length to prevent overwhelming responses
        if len(combined_content) > 2000:
            combined_content = combined_content[:2000] + "..."
        
        return combined_content

# Global instance
policy_search_service = None

def get_policy_search_service():
    """Get or create the policy search service instance."""
    global policy_search_service
    if policy_search_service is None:
        try:
            policy_search_service = PolicySearchService()
        except Exception as e:
            print(f"Failed to initialize PolicySearchService: {e}")
            policy_search_service = None
    return policy_search_service

def search_it_policies_handler(query: str) -> str:
    """
    Handler function for the IT helpdesk agent to search policy documents.
    Enhanced to provide specific, actionable policy information.
    
    Args:
        query: The user's query about IT policies
        
    Returns:
        Formatted response with policy information and compliance guidance
    """
    try:
        service = get_policy_search_service()
        if service is None:
            return "I'm sorry, the policy search service is currently unavailable. Please contact IT support directly for any technical guidance."
        
        results = service.search_policies(query)
        
        if not results:
            return "I couldn't find any relevant policy information for your query. For security and compliance reasons, I cannot provide technical guidance without policy approval. Please contact IT support directly or try rephrasing your question with more specific policy-related terms."
        
        # Enhanced response formatting based on query type
        query_lower = query.lower()
        
        # Check for specific query types and provide targeted responses
        if 'dual boot' in query_lower or 'operating system' in query_lower:
            response = "**Dual Boot / Operating System Modification Policy:**\n\n"
        elif 'it support' in query_lower or 'helpdesk' in query_lower or 'contact' in query_lower:
            response = "**IT Support & Contact Information:**\n\n"
        elif 'incident' in query_lower or 'reporting' in query_lower:
            response = "**IT Incident Reporting Procedures:**\n\n"
        elif 'password' in query_lower:
            response = "**Password Policy Guidelines:**\n\n"
        else:
            response = "Based on our company IT policy documents, here's what I found:\n\n"
        
        for i, result in enumerate(results, 1):
            content = result['content']
            if content.strip():  # Only include results with actual content
                response += f"**From {result['title']}:**\n"
                response += f"{content}\n\n"
        
        # Add specific guidance based on content found
        response_lower = response.lower()
        if 'helpdesk@advangegroup.com' in response_lower or '+81-75 286 9300' in response_lower:
            response += "✅ **Action:** You can contact IT Support using the information provided above.\n\n"
        elif 'prohibited' in response_lower and 'dual boot' in response_lower:
            response += "⚠️ **Important:** Based on company policy, dual boot configurations are strictly prohibited. Please contact IT Support if you need alternative solutions.\n\n"
        else:
            response += "⚠️ **Important:** All technical procedures must comply with the policies shown above. If you need guidance that isn't covered in these policies, please contact IT support directly.\n\n"
        
        return response
        
    except Exception as e:
        return f"I'm sorry, I encountered an error while searching the policy database: {str(e)}. For security reasons, I cannot provide technical guidance without policy verification. Please contact IT support directly."


# =============================================
# CONSOLIDATED: Acknowledgment Messages
# =============================================
ACKNOWLEDGMENT_MESSAGES = {
    "lookup_employee": "Let me look that up for you...",
    "verify_security_answer": "Let me verify that information...",
    "account_recovery": "Let me process the password reset for you...",
    "search_it_policies": "Let me search our company IT policies for you..."
}


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
        """Get session-only configuration for Voice Live. Do not include instructions or tools.

        Note: In hosted Agent mode, system instructions and tools are configured in Azure AI Foundry.
        We therefore only return session-related settings here to avoid overriding hosted configuration.
        """
        return {
            # Only session-related fields; caller may further refine VAD/voice per environment
            "modalities": ["text", "audio"],
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": self.vad_settings,
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
        async def lookup_employee_handler(args: Dict[str, Any]) -> str:
            employeeId = args.get("employeeId", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/employees/{employeeId}"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("employee") and data.get("msg") == "Employee retrieved successfully":
                                employee = data.get("employee", {})
                                fields = employee.get("fields", {})
                                
                                # Try different possible name fields
                                employee_name = (
                                    fields.get("Title") or 
                                    fields.get("Name") or 
                                    fields.get("FullName") or 
                                    fields.get("FirstName", "") + " " + fields.get("LastName", "") or
                                    "there"
                                ).strip()
                                
                                # If we got a composite name like "FirstName LastName", use just first name
                                first_name = employee_name.split()[0] if employee_name and employee_name != "there" else employee_name
                                
                                return f"Employee found: {employee_name}. Thanks, {first_name}. Before we continue, let's confirm your identity with a quick security question. Who is your manager?"
                            else:
                                return f"I couldn't find an employee with that ID. Please double-check your employee ID and try again. The format should be EMP followed by numbers, like EMP123456."
                        else:
                            return "Employee lookup failed due to system error. Please try again or contact IT support."
            except Exception as e:
                logger.error(f"Error calling lookup_employee API: {e}")
                return "Sorry, the employee lookup system is currently unavailable. Please try again in a moment or contact IT support directly."
        
        # Security verification function handler  
        async def verify_security_answer_handler(args: Dict[str, Any]) -> str:
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
                            return "Perfect! Your security answer is correct. Now I'm going to reset your password. Just a moment please..."
                        else:
                            return "That security answer doesn't match our records. Would you like to try again?"
            except Exception as e:
                logger.error(f"Error calling verify_security_answer API: {e}")
                return "Sorry, I'm having trouble verifying your security answer right now. We may need to try again later or escalate this to IT support."
        
        # Policy search function handler
        async def search_policies_handler(args: Dict[str, Any]) -> str:
            query = args.get("query", "")
            try:
                result = search_it_policies_handler(query)
                return result
            except Exception as e:
                logger.error(f"Error searching policies: {e}")
                return "I'm sorry, I couldn't search the policy database at the moment. Please try again later or contact IT support directly."

        # Account recovery function handler
        async def account_recovery_handler(args: Dict[str, Any]) -> str:
            employeeId = args.get("employeeId", "")
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self.base_url}/api/v1/test/account_recovery"
                    payload = {"employeeId": employeeId}
                    async with session.post(url, json=payload) as response:
                        data = await response.json()
                        if data.get("success", False):
                            return "Your password has been securely reset! Please check your email for the new password and next steps. Is there anything else I can help you with today?"
                        else:
                            return "The password reset didn't go through. Let me escalate this to IT support. Would you like me to do that now?"
            except Exception as e:
                logger.error(f"Error calling account_recovery API: {e}")
                return "Sorry, the password reset system is having trouble right now. We can try again shortly or I can escalate this to IT support."
        
        lookup_employee_function = AgentFunction(
            name="lookup_employee",
            description="Look up employee information by employee ID for identity verification during password resets and IT support requests.",
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
            description="Verify caller's security question answer for identity confirmation. Use after successful employee lookup.",
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
            description="Process account recovery and password reset after successful identity verification.",
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
            description="Search company IT policies and procedures for guidance on technical questions, software installation, hardware policies, and IT procedures.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The user's question or keywords to search for in IT policy documents. Include technical terms like 'dual boot', 'software installation', 'hardware modification', etc."
                    }
                },
                "required": ["query"]
            },
            handler=search_policies_handler
        )

        super().__init__(
            name="IT Helpdesk Agent",
            voice=os.getenv("RICHARD_VOICE", "en-US-AndrewNeural"),  # Male multilingual default; env overrides in prod
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
