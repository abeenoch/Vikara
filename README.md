# Voice Scheduling Agent (Deepgram + Google Calendar)

Production-style real-time voice scheduler:
- Starts a live conversation.
- Collects name, preferred date/time, optional meeting title.
- Confirms final details.
- Creates a real Google Calendar event.

## Architecture
- Frontend: browser mic capture and audio playback(`public/`)
- Backend: FastAPI (`app/main.py`)
- Voice transport: frontend -> backend websocket (`/ws/voice`) -> Deepgram Agent websocket
- LLM: OpenAI model configured via Deepgram Voice Agent `think.provider`
- Calendar: Google OAuth + Calendar API `events.insert`



## Requirements
- Python 3.11+
- Deepgram API key
- Google Cloud OAuth client (Web application) with Calendar API enabled

## Environment
Copy `.env.example` to `.env` and set:

```env
PORT=3000
BASE_URL=http://localhost:3000
DEEPGRAM_API_KEY=...
GOOGLE_CLIENT_ID=...  # optional if using credentials.json
GOOGLE_CLIENT_SECRET=...  # optional if using credentials.json
GOOGLE_REDIRECT_PATH=
```

Google OAuth client config priority:
1. `credentials.json` file (path from `GOOGLE_CREDENTIALS_FILE`)
2. `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` from `.env`

If you use `credentials.json`, place it at the project root.

Google OAuth redirect URIs:
- `http://localhost:3000/auth/google/callback`
- `https://<your-deployed-domain>/auth/google/callback`

## Run locally
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 3000
```

Open `http://localhost:3000`.

## Run with Docker
Build:
```bash
docker build -t vikara-voice-assistant .
```

Run:
```bash
docker run --rm -p 3000:3000 --env-file .env -v "${PWD}/credentials.json:/app/credentials.json" vikara-voice-assistant
```

Alternative (explicit absolute path):
```bash
docker run --rm -p 3000:3000 --env-file .env -v "C:/Users/Administrator/Desktop/ML/vikara/credentials.json:/app/credentials.json" vikara-voice-assistant
```

## Test flow
1. Click `Connect Google` and finish consent.
2. Click `Start Voice Session`.
3. Agent guides you to provide:
   - your name
   - preferred date/time
   - timezone
   - optional meeting title
4. Confirm with explicit `yes`.
5. Check Google Calendar for the newly created event.

## Deployment
Deploy as a standard Python web service (Render, Railway, Fly.io, etc.) with:
- start command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
- environment variables from `.env.example`

Render-specific env checklist:
- `BASE_URL=https://voice-scheduling-agent-33p6.onrender.com` (or your own Render URL)
- `GOOGLE_REDIRECT_PATH=/auth/google/callback` (path only, not a full URL)
- `SESSION_SECRET_KEY=<long-random-string>`
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (or mount `credentials.json`)

Final redirect URI used by the app is:
- `https://voice-scheduling-agent-33p6.onrender.com/auth/google/callback`


## Submission checklist
- GitHub repo: this project
- Deployed URL: `<ADD_DEPLOYED_URL>`
- Loom video: `<ADD_LOOM_LINK>`
- Evidence artifacts: add screenshots/logs under `evidence/`

## Key implementation files
- `app/main.py`
- `app/google_auth.py`
- `app/calendar_service.py`
- `public/app.js`
- `public/audio-worklet-processor.js`
