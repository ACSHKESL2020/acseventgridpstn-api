# ⚠️ CRITICAL TOKEN HANDLING WARNING ⚠️

## 🤦 SIMPLEST THING IN THE WORLD - DON'T OVERCOMPLICATE IT 🤦

### 💡 IT'S JUST A STRING:
```python
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # That's it. Just a string.
```

## 🚨 HOW I MANAGED TO BREAK SOMETHING THIS SIMPLE 🚨

### Major Issues Identified:

1. **❌ WRONG: Token in URL Parameters**
   ```python
   ws_url = f"{base_ws}...&agent-access-token={ACCESS_TOKEN}"  # SECURITY RISK - NEVER DO THIS
   ```

2. **❌ WRONG: Modified WebSocket Headers**
   ```python
   headers = {
       "Authorization": f"Bearer {ACCESS_TOKEN}",  # WRONG IMPLEMENTATION
       "Content-Type": "application/json"  # UNNECESSARY FOR WS
   }
   ```

3. **❌ WRONG: Changed WebSocket Parameters**
   ```python
   extra_headers=headers,  # WRONG PARAMETER NAME
   close_timeout=5,       # UNNECESSARY
   max_size=None         # UNNECESSARY
   ```

### ✅ CORRECT Implementation:

```python
# 1. Keep token in query parameter as specified in Azure Voice Live API docs
ws_url = (
    f"{base_ws}?api-version={API_VERSION}"
    f"&agent-project-name={PROJECT_NAME}"
    f"&agent-id={AGENT_ID}"
    f"&agent-access-token={ACCESS_TOKEN}"
)

# 2. Use minimal WebSocket configuration
self.voice_live_ws = await websockets.connect(
    ws_url,
    additional_headers={"x-ms-client-request-id": str(uuid.uuid4())},
    ping_interval=20,
    ping_timeout=20
)
```

### 🔑 Key Lessons:

1. **DO NOT** modify Azure Voice Live API's token handling mechanism
2. **DO NOT** try to "improve" security by moving token to headers
3. **DO NOT** add unnecessary WebSocket parameters
4. **ALWAYS** follow Azure Voice Live API documentation exactly
5. **ALWAYS** use the exact parameter names from the websockets library
6. **WHEN IN DOUBT**, roll back to the last working version

### 📝 Documentation References:

- Azure Voice Live API requires token in URL parameters
- WebSocket connection should be kept as simple as possible
- Any deviation from the documented approach will result in:
  - 401 Unauthorized
  - WebSocket 1007 errors
  - Connection failures

---

## 🚫 NEVER MODIFY THIS PATTERN AGAIN 🚫

Remember: The API expects the token in the URL. Any "security improvements" will break the connection.
