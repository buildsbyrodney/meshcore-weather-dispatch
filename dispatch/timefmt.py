from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

def local_time(value, timezone_name: str) -> str:
    if not value: return "—"
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if parsed.tzinfo is None: return str(value)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %I:%M %p %Z").replace(" 0", " ")
    except (ValueError, TypeError): return str(value)
