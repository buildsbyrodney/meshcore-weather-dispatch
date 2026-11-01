from __future__ import annotations
import re

def truncate_utf8(text: str, cap: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= cap: return text
    ellipsis = "…".encode()
    keep = max(0, cap - len(ellipsis))
    return encoded[:keep].decode("utf-8", "ignore").rstrip() + "…"

def radio_message(feature: dict, byte_cap: int) -> str:
    props = feature["properties"]
    event = props.get("event", "Weather alert").upper()
    areas = props.get("areaDesc", "configured area")
    ends = props.get("expires", "")
    instruction = re.sub(r"\s+", " ", props.get("instruction") or props.get("description") or "").strip()
    prefix="TEST ONLY NWS" if props.get("test_only") else "NWS"
    return truncate_utf8(f"{prefix} {event}: {areas}. Until {ends}. {instruction}", byte_cap)

def append_comment(base: str, comment: str, byte_cap: int) -> str:
    comment = " ".join(comment.split())
    if not comment: return base
    marker = " [OPERATOR: "
    suffix = "]"
    available = byte_cap - len((base + marker + suffix).encode("utf-8"))
    if available < 1: return base
    return base + marker + truncate_utf8(comment, available) + suffix
