from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones
from pathlib import Path

def system_timezone() -> str:
    try:
        candidate=Path("/etc/timezone").read_text().strip()
        ZoneInfo(candidate); return candidate
    except Exception: return "UTC"

def valid_timezones() -> list[str]: return sorted(available_timezones())

def local_time(value, timezone_name: str) -> str:
    if not value: return "—"
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if parsed.tzinfo is None: return str(value)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %I:%M %p %Z").replace(" 0", " ")
    except (ValueError, TypeError): return str(value)

def compact_local_time(value, timezone_name: str) -> str:
    """A concise local timestamp for dense alert rows; Details retain full dates."""
    if not value: return "—"
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if parsed.tzinfo is None: return str(value)
        local=parsed.astimezone(ZoneInfo(timezone_name)); hour=local.hour % 12 or 12
        return f"{local.strftime('%b')} {local.day}, {hour}:{local.strftime('%M %p %Z')}"
    except (ValueError, TypeError): return str(value)
