from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class MessageError(ValueError): pass

def truncate_utf8(text: str, cap: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= cap: return text
    ellipsis = "…".encode()
    keep = max(0, cap - len(ellipsis))
    return encoded[:keep].decode("utf-8", "ignore").rstrip() + "…"

def _expiration_label(value, timezone_name: str) -> str:
    """Render an NWS expiry in the configured local zone without guessing."""
    if not value:
        return "unavailable"
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if parsed.tzinfo is None:
            return "unavailable"
        local=parsed.astimezone(ZoneInfo(timezone_name))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return "unavailable"
    hour=local.hour % 12 or 12
    abbreviation=local.tzname()
    if not abbreviation:
        return "unavailable"
    return f"{local.strftime('%B')} {local.day} at {hour}:{local.strftime('%M')}{local.strftime('%p')} {abbreviation}"

def _short_expiration(value, timezone_name: str) -> str:
    label=_expiration_label(value,timezone_name)
    return label.replace(" at "," ").replace("AM ","AM ").replace("PM ","PM ")

def _auto_message(props, timezone_name, byte_cap=None):
    event=" ".join(str(props.get("event") or "Weather alert").split()).upper()
    kind=str(props.get("_mesh_kind") or "NEW").upper()
    areas=[x.strip() for x in str(props.get("_mesh_matched_areas") or "").split("|") if x.strip()]
    area_text=", ".join(areas) if areas else "SELECTED COUNTIES"
    office=" ".join(str(props.get("senderName") or "").split())
    expiry="" if kind=="CANCELED" else f" Expires {_short_expiration(props.get('expires'),timezone_name)}"
    suffix=f" {office}" if office else ""
    text=f"{kind} {event} — {area_text}.{expiry}{suffix}".strip()
    if byte_cap is None or len(text.encode("utf-8"))<=byte_cap: return text
    # Never retain only some county names. The replacement explicitly states
    # how many selected counties are involved, so it cannot be mistaken for a
    # complete geographic list.
    summary=f"{len(areas)} SELECTED COUNTIES" if areas else "SELECTED COUNTIES"
    text=f"{kind} {event} — {summary}.{expiry}{suffix}".strip()
    if len(text.encode("utf-8"))>byte_cap: raise MessageError("essential warning details exceed the configured UTF-8 limit")
    return text

def radio_message(feature: dict, timezone_name="UTC", byte_cap=None) -> str:
    props = feature["properties"]
    if props.get("_mesh_kind"):
        return _auto_message(props,timezone_name,byte_cap)
    event=" ".join(str(props.get("event") or "Weather alert").split()).upper()
    prefix="TEST ONLY " if props.get("test_only") else ""
    text=f"{prefix}{event} Expires {_expiration_label(props.get('expires'),timezone_name)}"
    office=" ".join(str(props.get("senderName") or "").split())
    return f"{text} by {office}" if office else text

def append_comment(base: str, comment: str, byte_cap: int) -> str:
    if len(base.encode("utf-8")) > byte_cap:
        raise MessageError("essential warning details exceed the configured UTF-8 limit")
    comment = " ".join(comment.split())
    if not comment: return base
    marker = " [OPERATOR: "
    suffix = "]"
    available = byte_cap - len((base + marker + suffix).encode("utf-8"))
    if available < len(comment.encode("utf-8")): raise MessageError("essential warning details and operator comment exceed the configured UTF-8 limit")
    return base + marker + comment + suffix
