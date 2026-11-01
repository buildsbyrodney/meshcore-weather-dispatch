from __future__ import annotations
import re
from .timefmt import local_time

class MessageError(ValueError): pass

def truncate_utf8(text: str, cap: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= cap: return text
    ellipsis = "…".encode()
    keep = max(0, cap - len(ellipsis))
    return encoded[:keep].decode("utf-8", "ignore").rstrip() + "…"

def radio_message(feature: dict, timezone_name="UTC") -> str:
    props = feature["properties"]
    event = props.get("event", "Weather alert").upper();areas=" ".join(str(props.get("areaDesc") or "configured area").split())
    prefix="TEST ONLY " if props.get("test_only") else ""
    text=f"{prefix}{event}: {areas}. Expires {local_time(props.get('expires'),timezone_name)}."
    headline=" ".join(str(props.get("headline") or "").split())
    return text+(f" {headline}" if headline and headline.casefold() not in text.casefold() else "")

def append_comment(base: str, comment: str, byte_cap: int) -> str:
    comment = " ".join(comment.split())
    if not comment: return base
    marker = " [OPERATOR: "
    suffix = "]"
    available = byte_cap - len((base + marker + suffix).encode("utf-8"))
    if available < len(comment.encode("utf-8")): raise MessageError("essential warning details and operator comment exceed the configured UTF-8 limit")
    return base + marker + comment + suffix
