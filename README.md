Node Express port of the ACS call-handling Python server.

Structure:
- src/app.js : Express server with routes mirrored from Python `app/main.py`.
- .env : copied from repository root (contains ACS and Azure credentials) — placed here per request.

Notes:
- This is an initial scaffold. I will progressively port the handlers in `app/` to `nodeexpress/src/` modules using Azure SDKs.
- You can run:

```bash
cd nodeexpress
npm install
npm start
```

I'll continue by translating the modules: get_access_token, voice_live_handler, and main logic into Node equivalents.


lsof -ti:8080 | xargs kill -9

# Find process ID first, then kill
lsof -i:8080
kill -9 <PID>

# Kill all node processes (be careful!)
pkill -f node

# Kill specific node app
pkill -f "node.*app.js"
