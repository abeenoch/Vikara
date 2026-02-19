from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from app.config import google_credentials_path, settings
from app.token_store import load_google_tokens, save_google_tokens

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _load_google_client_from_file() -> tuple[str, str] | None:
    path = google_credentials_path()
    if not path.exists():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    root = payload.get("web") or payload.get("installed") or {}
    client_id = str(root.get("client_id", "")).strip()
    client_secret = str(root.get("client_secret", "")).strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def _google_client_config() -> tuple[str, str]:
    file_config = _load_google_client_from_file()
    if file_config:
        return file_config
    if settings.google_client_id and settings.google_client_secret:
        return settings.google_client_id, settings.google_client_secret
    raise RuntimeError(
        "Google OAuth config missing. Provide credentials.json via GOOGLE_CREDENTIALS_FILE "
        "or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
    )


def generate_google_auth_url(session: dict[str, Any]) -> tuple[str, str]:
    client_id, _client_secret = _google_client_config()
    state = secrets.token_urlsafe(24)
    
    # Store state in session
    if "oauth_states" not in session:
        session["oauth_states"] = []
    session["oauth_states"].append(state)
    
    params = {
        "client_id": client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(CALENDAR_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state


def exchange_code_for_tokens(
    code: str,
    state: str | None = None,
    session: dict[str, Any] | None = None,
    cookie_state: str | None = None,
) -> None:
    client_id, client_secret = _google_client_config()

    if not state:
        raise RuntimeError("Missing OAuth state.")

    state_valid = False

    # Primary validation: in-session state list.
    if session is not None:
        oauth_states = session.get("oauth_states", [])
        if state in oauth_states:
            oauth_states.remove(state)
            session["oauth_states"] = oauth_states
            state_valid = True

    # Fallback validation: short-lived HTTP-only state cookie.
    if cookie_state and secrets.compare_digest(state, cookie_state):
        state_valid = True

    if not state_valid:
        raise RuntimeError("Invalid OAuth state.")

    payload = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        token_data = json.loads(response.read().decode("utf-8"))

    credentials = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=GOOGLE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=CALENDAR_SCOPES,
    )
    save_google_tokens(credentials_to_dict(credentials))


def is_google_connected() -> bool:
    tokens = load_google_tokens()
    return bool(tokens and (tokens.get("refresh_token") or tokens.get("access_token")))


def get_google_credentials() -> Credentials:
    tokens = load_google_tokens()
    if not tokens:
        raise RuntimeError("Google Calendar is not connected. Complete OAuth first.")
    credentials = Credentials.from_authorized_user_info(tokens, CALENDAR_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleRequest())
        save_google_tokens(credentials_to_dict(credentials))
    return credentials


def credentials_to_dict(credentials: Credentials) -> dict[str, Any]:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }
