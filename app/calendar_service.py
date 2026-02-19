from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from googleapiclient.discovery import build

from app.google_auth import get_google_credentials


def validate_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    meeting_with_name = str(
        payload.get("meeting_with_name") or payload.get("attendee_name") or ""
    ).strip()
    meeting_title = str(payload.get("meeting_title", "")).strip()
    start_time_iso = str(payload.get("start_time_iso", "")).strip()
    timezone_name = str(payload.get("timezone", "")).strip() or None
    normalized_timezone, timezone_info = parse_timezone(timezone_name)

    duration_raw = payload.get("duration_minutes", 30)
    try:
        duration_minutes = int(duration_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_minutes must be an integer.") from exc

    if not meeting_with_name:
        raise ValueError("meeting_with_name is required.")
    if not start_time_iso:
        raise ValueError("start_time_iso is required.")
    if not timezone_name:
        raise ValueError("timezone is required.")
    if timezone_info is None:
        raise ValueError("timezone must be a valid IANA timezone or UTC offset like UTC+1.")
    if duration_minutes < 5 or duration_minutes > 240:
        raise ValueError("duration_minutes must be between 5 and 240.")

    try:
        start_dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("start_time_iso must be a valid ISO-8601 datetime.") from exc

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone_info)

    now_utc = datetime.now(timezone.utc)
    start_utc = start_dt.astimezone(timezone.utc)
    if start_utc < now_utc - timedelta(minutes=2):
        raise ValueError("Meeting time is in the past. Please provide a future date and time.")

    summary = meeting_title or f"Meeting with {meeting_with_name}"

    return {
        "meeting_with_name": meeting_with_name,
        "summary": summary,
        "start_dt": start_dt,
        "duration_minutes": duration_minutes,
        "timezone": normalized_timezone,
    }


def parse_timezone(value: str | None) -> tuple[str | None, timezone | ZoneInfo | None]:
    if not value:
        return None, None

    raw_value = value.strip()
    if raw_value.upper() == "UTC":
        return "UTC", timezone.utc

    try:
        zone = ZoneInfo(raw_value)
        return raw_value, zone
    except ZoneInfoNotFoundError:
        pass

    normalized = raw_value.lower().strip()
    normalized = normalized.replace("utc plus", "utc+").replace("utc minus", "utc-")
    normalized = normalized.replace("gmt plus", "gmt+").replace("gmt minus", "gmt-")
    normalized = normalized.replace(" ", "")

    word_hours = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
    }

    word_match = re.match(r"^(?:utc|gmt)([+-])(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen)$", normalized)
    if word_match:
        sign = word_match.group(1)
        hour = word_hours[word_match.group(2)]
        delta = timedelta(hours=hour)
        if sign == "-":
            delta = -delta
        return None, timezone(delta)

    numeric_match = re.match(r"^(?:(?:utc|gmt))?([+-])(\d{1,2})(?::?(\d{2}))?$", normalized)
    if numeric_match:
        sign = numeric_match.group(1)
        hour = int(numeric_match.group(2))
        minute = int(numeric_match.group(3) or "0")
        if hour > 14 or minute > 59:
            return None, None
        delta = timedelta(hours=hour, minutes=minute)
        if sign == "-":
            delta = -delta
        return None, timezone(delta)

    return None, None


def create_calendar_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_input = validate_event_payload(payload)
    end_dt = event_input["start_dt"] + timedelta(minutes=event_input["duration_minutes"])

    credentials = get_google_credentials()
    calendar = build("calendar", "v3", credentials=credentials)

    event = {
        "summary": event_input["summary"],
        "description": f"Booked by voice assistant. Meeting with {event_input['meeting_with_name']}.",
        "start": {
            "dateTime": event_input["start_dt"].isoformat(),
        },
        "end": {
            "dateTime": end_dt.isoformat(),
        },
    }
    if event_input["timezone"]:
        event["start"]["timeZone"] = event_input["timezone"]
        event["end"]["timeZone"] = event_input["timezone"]

    created = (
        calendar.events()
        .insert(calendarId="primary", body=event)
        .execute()
    )

    return {
        "ok": True,
        "eventId": created.get("id", ""),
        "eventLink": created.get("htmlLink", ""),
        "meetingWithName": event_input["meeting_with_name"],
        "startsAt": event["start"]["dateTime"],
        "endsAt": event["end"]["dateTime"],
    }
