from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from websockets.exceptions import ConnectionClosed

from app.calendar_service import create_calendar_event
from app.config import settings
from app.google_auth import exchange_code_for_tokens, generate_google_auth_url, is_google_connected

PUBLIC_DIR = Path("public")
DEEPGRAM_WS_URL = "wss://agent.deepgram.com/v1/agent/converse"

app = FastAPI(title="Voice Scheduling Agent")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    same_site="lax",
    https_only=settings.base_url.startswith("https://"),
)
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")
logger = logging.getLogger("voice_agent")


def build_agent_settings() -> dict[str, Any]:
    if not settings.deepgram_api_key:
        raise RuntimeError("DEEPGRAM_API_KEY is required.")

    now_utc = datetime.now(timezone.utc)
    current_date = now_utc.date().isoformat()
    current_timestamp = now_utc.isoformat()

    prompt = (
        "You are a voice scheduling assistant. "
        f"Today's date is {current_date}. Current UTC time is {current_timestamp}. "
        "Ask one short question at a time and wait for the user's answer. "
        "Never ask multiple questions in one turn. "
        "Never repeat a question unless the user asks you to repeat it. "
        "Default to step-by-step collection. "
        "Collect fields in this strict order: meeting_with_name, start date/time, timezone, optional meeting_title. "
        "Accept natural language date/time from the user and convert it internally to start_time_iso. "
        "Never ask the user for ISO format. "
        "If year is omitted, infer a future date in the user's context; never pick a past year. "
        "For relative terms like today/tomorrow/next Monday, resolve them using today's date above. "
        "Never output start_time_iso with a year earlier than today's year unless the user explicitly requested that exact year. "
        "Before calling create_calendar_event, ensure the interpreted start time is in the future; if not, ask one clarification question and correct it. "
        "Do not ask for duration. Use duration_minutes=30 by default unless the user explicitly provides a different duration. "
        "Always ask for timezone as a separate short question after collecting date/time. "
        "Accept timezone in either IANA format (for example Europe/Berlin) or UTC offset format (for example UTC+1 or UTC plus one). "
        "Always ask a separate optional-title question before confirmation, for example: "
        "'Would you like to add a meeting title, or proceed without one?' "
        "If the user declines, proceed with no title. "
        "If the interpreted datetime could be past, ask a clarifying question before confirmation. "
        "If details are ambiguous, ask one clarifying question. "
        "Before booking, recap details in one sentence including the 30-minute default duration (or user-provided duration) and ask for explicit yes or no. "
        "Call create_calendar_event only after clear yes. "
        "Never output markdown. Never output or spell out any URL or link. "
        "After successful booking, say one short confirmation sentence and then stop speaking."
    )

    return {
        "type": "Settings",
        "flags": {"history": False},
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": 24000},
            "output": {"encoding": "linear16", "sample_rate": 24000, "container": "none"},
        },
        "agent": {
            "language": "en",
            "greeting": "Hi. Who are you meeting with?",
            "listen": {
                "provider": {
                    "type": "deepgram",
                    "model": "nova-3",
                    "smart_format": True,
                    "keyterms": ["Adebola", "Abulwaran", "ENOC", "UTC+1"],
                }
            },
            "think": {
                "provider": {
                    "type": "open_ai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.0,
                },
                "prompt": prompt,
                "functions": [
                    {
                        "name": "create_calendar_event",
                        "description": (
                            "Creates a Google Calendar event. "
                            "Only call this after explicit user confirmation."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "meeting_with_name": {
                                    "type": "string",
                                    "description": "Name of the person the user is meeting with.",
                                },
                                "start_time_iso": {
                                    "type": "string",
                                    "description": (
                                        "Internal normalized datetime with timezone offset. "
                                        "Derive this from natural language user input."
                                    ),
                                },
                                "duration_minutes": {
                                    "type": "integer",
                                    "description": "Meeting duration in minutes. Default 30.",
                                },
                                "meeting_title": {
                                    "type": "string",
                                    "description": "Optional title for the meeting.",
                                },
                                "timezone": {
                                    "type": "string",
                                    "description": "Required timezone. Accept IANA (Europe/Berlin) or UTC offset (UTC+1).",
                                },
                            },
                            "required": ["meeting_with_name", "start_time_iso", "timezone"],
                        },
                    }
                ],
            },
            "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
        },
    }


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/google/status")
def google_status() -> dict[str, bool]:
    try:
        return {"connected": is_google_connected()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/auth/google/start")
def auth_google_start(request: Request) -> RedirectResponse:
    try:
        auth_url, state = generate_google_auth_url(request.session)
        response = RedirectResponse(auth_url)
        response.set_cookie(
            "oauth_state",
            state,
            max_age=600,
            httponly=True,
            secure=settings.base_url.startswith("https://"),
            samesite="lax",
            path="/",
        )
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/auth/google/callback")
def auth_google_callback(request: Request, code: str = Query(default=""), state: str = Query(default="")) -> RedirectResponse:
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code.")
    try:
        exchange_code_for_tokens(
            code=code,
            state=state or None,
            session=request.session,
            cookie_state=request.cookies.get("oauth_state"),
        )
        response = RedirectResponse("/?google_connected=1")
        response.delete_cookie("oauth_state", path="/")
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth failed: {exc}") from exc


@app.post("/api/calendar/events")
def calendar_events(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return create_calendar_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
def root() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/app.js")
def app_js() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "app.js")


@app.get("/audio-worklet-processor.js")
def worklet_js() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "audio-worklet-processor.js")


@app.get("/styles.css")
def styles_css() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "styles.css")


@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket) -> None:
    session_id = uuid4().hex[:8]
    await websocket.accept()
    logger.info("voice_session_start session=%s", session_id)

    if not settings.deepgram_api_key:
        await websocket.close(code=1011, reason="DEEPGRAM_API_KEY missing.")
        return

    deepgram_headers = {"Authorization": f"Token {settings.deepgram_api_key}"}

    try:
        async with websockets.connect(
            DEEPGRAM_WS_URL,
            additional_headers=deepgram_headers,
            max_size=None,
            ping_interval=20,
            ping_timeout=60,
        ) as deepgram_ws:
            welcome_event = asyncio.Event()
            settings_applied_event = asyncio.Event()

            async def client_to_deepgram() -> None:
                try:
                    await settings_applied_event.wait()
                    while True:
                        message = await websocket.receive()
                        message_type = message.get("type")
                        if message_type == "websocket.disconnect":
                            break
                        if message.get("bytes") is not None:
                            await deepgram_ws.send(message["bytes"])
                            continue
                        if message.get("text") is not None:
                            await deepgram_ws.send(message["text"])
                except (WebSocketDisconnect, ConnectionClosed):
                    logger.info("client_to_deepgram_closed session=%s", session_id)
                    return

            async def deepgram_to_client() -> None:
                try:
                    async for message in deepgram_ws:
                        if isinstance(message, bytes):
                            if not settings_applied_event.is_set():
                                continue
                            await websocket.send_bytes(message)
                        else:
                            try:
                                parsed = json.loads(message)
                                msg_type = parsed.get("type")
                                if msg_type == "Welcome":
                                    welcome_event.set()
                                elif msg_type == "SettingsApplied":
                                    settings_applied_event.set()
                            except json.JSONDecodeError:
                                pass
                            await websocket.send_text(message)
                except ConnectionClosed as exc:
                    logger.warning(
                        "deepgram_closed session=%s code=%s reason=%s",
                        session_id,
                        getattr(exc, "code", None),
                        getattr(exc, "reason", ""),
                    )
                    return
                except WebSocketDisconnect:
                    logger.info("client_websocket_disconnected session=%s", session_id)
                    return

            task_deepgram = asyncio.create_task(deepgram_to_client())
            await asyncio.wait_for(welcome_event.wait(), timeout=5)
            await deepgram_ws.send(json.dumps(build_agent_settings()))
            await asyncio.wait_for(settings_applied_event.wait(), timeout=5)
            task_client = asyncio.create_task(client_to_deepgram())

            done, pending = await asyncio.wait(
                {task_client, task_deepgram},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
            results = await asyncio.gather(*done, *pending, return_exceptions=True)
            for result in results:
                if isinstance(result, (asyncio.CancelledError, WebSocketDisconnect, ConnectionClosed)):
                    continue
                if isinstance(result, Exception):
                    raise result
    except WebSocketDisconnect:
        logger.info("voice_session_client_disconnect session=%s", session_id)
        return
    except Exception:
        logger.exception("voice_session_failed session=%s", session_id)
        await websocket.close(code=1011, reason="Voice session failed.")
    finally:
        logger.info("voice_session_end session=%s", session_id)
