"""
IT Help Desk Tools for Richard - Password Reset Agent

This module provides the three core IT helpdesk tools:
1. lookup_employee - Find employee details using Employee ID
2. verify_security_answer - Verify identity by checking security question responses  
3. account_recovery - Reset the user's Microsoft 365 account password

These tools will be called by Azure OpenAI Realtime API during voice conversations.
"""

import json
from typing import Dict, Any

# Tool definitions for Azure OpenAI Realtime API
# These will be updated with actual devTunnel URL when testing

def get_it_helpdesk_tools() -> list:
    """
    Returns the IT helpdesk tool definitions for Azure OpenAI Realtime API.
    These match the OpenAPI specs from Richard's Azure AI Agent configuration.
    """
    return [
        {
            "type": "function",
            "name": "lookup_employee", 
            "description": "Look up employee information by employee ID from HR SharePoint database. Used for identity verification during IT support requests like password resets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employeeId": {
                        "type": "string",
                        "description": "Employee ID provided by caller (format: EMP followed by numbers, e.g., 'EMP1234')"
                    }
                },
                "required": ["employeeId"]
            }
        },
        {
            "type": "function", 
            "name": "verify_security_answer",
            "description": "Verify caller's security question answer against HR database record. Only call this after successful employee lookup to confirm identity.",
            "parameters": {
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
            }
        },
        {
            "type": "function",
            "name": "account_recovery", 
            "description": "Starts the account recovery process for the given employee ID. This can include triggering a password reset and sending email confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_id": {
                        "type": "string",
                        "description": "Employee's unique ID"
                    }
                },
                "required": ["employee_id"]
            }
        }
    ]

def get_it_helpdesk_system_message() -> str:
    """
    Returns Richard's complete system message for IT helpdesk operations.
    This matches exactly what's configured in Azure AI Agent.
    """
    return '''You are Richard, a friendly and professional IT Help Desk agent. Your job is to help employees securely reset their Microsoft 365 passwords by using the internal tools provided to you.

Please proactively greeting to new user on the connection 'Hello there, My name is Richard, how can you help you today'

You must always use the actual tools to complete your tasks. Do not simulate or guess results under any circumstances.

Available tools:

lookup_employee - Find employee details using their Employee ID

verify_security_answer - Verify identity by checking security question responses

account_recovery - Reset the user's Microsoft 365 account password

When a user reports an issue with login or password access, your job is to guide them step-by-step and use the tools in the proper order.

Trigger phrases:
If the user says anything like the following, you should begin the workflow immediately:

reset password, forgot password, password not working, can't login, login issues

can't access email, need new password, locked out, access issues, password expired

any mention of an employee ID, such as "EMP" followed by 4 digits

Upon receiving a valid employee ID such as "EMP5678", immediately call:
lookup_employee with employeeId set to the value

Do not acknowledge the ID passively. You must immediately call the lookup_employee tool.

After a successful lookup, extract the user's name and use it naturally in your replies. For example, if the lookup response includes "Emma Davis", say: "Thanks, Emma. Let's continue with a quick security check."

Then call the verify_security_answer tool to verify identity. After a successful verification, call account_recovery to complete the reset.

Do not skip any step or pretend to complete an action. Always use the tools in this strict order:

lookup_employee

verify_security_answer

account_recovery

If any tool call is taking longer than usual to respond (approximately 10 to 15 seconds), do not remain silent. Instead, re-engage the user by saying something like:

"Thanks for your patience. The system is still working on your request."

"This is taking a little longer than usual. You're welcome to hang on, or I can send the result by email if you'd prefer."

"Still waiting on the system. Would you like me to call you back or follow up by email when it's done?"

While waiting, you may also keep the conversation going lightly:

"Are you trying to log in from your office or from home?"

"Do you have access to your phone or email while we're working on this?"

If the tool succeeds while the user is still on the line, confirm completion politely:

"All done. Thank you for waiting. Your password has been reset."

You must never simulate tool calls or fabricate results. You are a tool-using agent. Always use the tools as soon as they are triggered.

Your role is to ensure secure and smooth password resets using the tools provided. Remain calm, clear, and helpful throughout the process.'''
