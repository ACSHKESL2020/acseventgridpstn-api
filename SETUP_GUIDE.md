# 🍳 Recipe Voice Agent Demo Setup Guide

## Overview
This demo creates a voice agent that helps users find recipes through phone calls using Azure Communication Services and Azure OpenAI real-time capabilities.

## Prerequisites ✅
- Python 3.13+ installed
- Azure Communication Services resource with phone number
- Azure OpenAI resource with GPT-4o-mini-realtime-preview deployment
- ngrok or similar tunneling service

## Quick Setup Steps

### 1. Install Dependencies
```bash
# Install uv package manager if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv sync
```

### 2. Configure Environment Variables
The `.env` file has been created with your provided values:
- ✅ ACS_CONNECTION_STRING (configured)
- ✅ AZURE_OPENAI_REALTIME_ENDPOINT (configured) 
- ✅ AZURE_OPENAI_REALTIME_SERVICE_KEY (configured)
- ✅ AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME (configured)
- ✅ CALLBACK_URI_HOST (configured with your ngrok URL)
- ⚠️  Bing Search API disabled (using dummy recipe data)

### 3. Start ngrok Tunnel
Make sure your ngrok tunnel is running on port 8000:
```bash
ngrok http 8000
```
Your callback URL should be: `https://ec631f98f69c.ngrok-free.app`

### 4. Configure Azure Event Grid Webhook
In your Azure Communication Services resource:
1. Go to Events → Event Subscriptions
2. Create a new subscription for "Incoming Call" events
3. Set endpoint URL to: `https://ec631f98f69c.ngrok-free.app/api/incomingCall`

### 5. Run the Application
```bash
# Activate the virtual environment and run
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Testing the Demo 🎯

### Test the Application
1. **Health Check**: Visit `http://localhost:8000` - should return `{"message": "Hello World!"}`

2. **Call Your ACS Phone Number**: The voice agent will:
   - Answer the call automatically
   - Greet you as "Chef's Assistant"
   - Ask about cuisine preferences and available ingredients
   - Suggest recipes based on your input
   - Since Bing API is disabled, it will return dummy recipe data

### Sample Conversation Flow
```
Agent: "Hi! I'm Chef's Assistant. What type of cuisine are you interested in today?"
You: "Italian food"
Agent: "Great choice! What ingredients do you have available?"
You: "I have tomatoes, basil, and mozzarella"
Agent: "Perfect! I found some delicious Italian recipes for you..."
```

## Features 🌟
- ✅ Real-time voice conversation
- ✅ Natural language processing
- ✅ Recipe suggestions (dummy data)
- ✅ Phone call integration
- ✅ Azure OpenAI real-time model

## Troubleshooting 🔧

### Common Issues:
1. **"Failed to connect to Azure OpenAI"**
   - Verify your endpoint URL and API key
   - Ensure the deployment name is correct

2. **"No incoming calls received"**
   - Check ngrok tunnel is active
   - Verify Event Grid webhook configuration
   - Ensure phone number is properly configured

3. **"Application won't start"**
   - Run `uv sync` to install dependencies
   - Check Python version (3.13+ required)

### Logs to Monitor:
- FastAPI server logs for HTTP requests
- WebSocket connection logs for real-time audio
- Recipe finder logs (will show "using dummy data" messages)

## Next Steps 🚀
Once the demo is working, you can:
1. Add a real Bing Search API key to get actual recipes
2. Customize the system prompt for different use cases
3. Add SMS capabilities for sharing recipe links
4. Enhance the conversation flow

## Demo Limitations
- ⚠️  Using dummy recipe data (no real Bing search)
- ⚠️  Recipes are generated examples, not real SeriousEats links
- ⚠️  SMS features may not work without phone number configuration

The dummy data will provide realistic-looking recipe suggestions to test the voice conversation flow!
