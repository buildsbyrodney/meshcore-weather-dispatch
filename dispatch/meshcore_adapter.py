"""Conservative, read-first adapter for the official ``meshcore`` library."""
from __future__ import annotations
import asyncio, os, hashlib, re, threading

READ_TIMEOUT = 10
radio_lock = threading.Lock()
class RadioError(Exception): pass

async def _close(radio):
    disconnect=getattr(radio,"disconnect",None)
    if disconnect:
        try:
            outcome=disconnect()
            if hasattr(outcome,"__await__"): await outcome
        except Exception:
            # A USB unplug during cleanup must not turn a handled read/send
            # result into an HTTP 500. The original operation records its result.
            pass

async def _open(path: str):
    from meshcore import MeshCore
    try:
        return await asyncio.wait_for(MeshCore.create_serial(path), READ_TIMEOUT)
    except PermissionError as exc: raise RadioError("serial permission denied; add weatherdispatch to the dialout group") from exc
    except asyncio.TimeoutError as exc: raise RadioError("Companion connection timed out") from exc
    except Exception as exc: raise RadioError(f"Companion connection failed: {exc}") from exc

def _channel_name(payload) -> str | None:
    if isinstance(payload, dict): return payload.get("name") or payload.get("channel_name")
    return getattr(payload,"name",None) or getattr(payload,"channel_name",None)

def _channel_secret(payload):
    value=payload.get("secret") if isinstance(payload,dict) else getattr(payload,"secret",None)
    if isinstance(value,str):
        try: return bytes.fromhex(value)
        except ValueError: return None
    return bytes(value) if isinstance(value,(bytes,bytearray)) else None

async def _slots_serial(path: str) -> dict:
    from meshcore import EventType
    radio=await _open(path)
    try:
        info=await asyncio.wait_for(radio.commands.send_device_query(), READ_TIMEOUT)
        if info.type == EventType.ERROR: raise RadioError(f"Companion device query failed: {info.payload}")
        payload=info.payload or {}
        count=payload.get("max_channels") if isinstance(payload,dict) else getattr(payload,"max_channels",None)
        if not isinstance(count,int) or not 0 < count <= 256: raise RadioError("Companion did not report a usable channel count")
        slots=[]
        for index in range(count):
            result=await asyncio.wait_for(radio.commands.get_channel(index), READ_TIMEOUT)
            if result.type == EventType.ERROR:
                slots.append({"index":index,"name":"","occupied":True,"known":False});continue
            name=str(_channel_name(result.payload) or "")
            secret=_channel_secret(result.payload)
            # Unknown secret state is deliberately not considered available.
            known=secret is not None
            occupied=bool(name) or not known or any(secret)
            slots.append({"index":index,"name":name,"occupied":occupied,"known":known})
        return {"capacity":count,"slots":slots}
    except RadioError: raise
    except asyncio.TimeoutError as exc: raise RadioError("Companion channel query timed out") from exc
    except Exception as exc: raise RadioError(f"Companion channel query failed: {exc}") from exc
    finally: await _close(radio)

def list_channels(device: dict | None) -> list[dict]:
    """Read configured channel slots only; never creates or changes a channel."""
    if not device: raise RadioError("no verified Companion device")
    return [{"index":slot["index"],"name":slot["name"]} for slot in asyncio.run(_slots_serial(device["device"]))["slots"] if slot["name"]]

def discover_channels(device: dict | None) -> dict:
    if not device: raise RadioError("no verified Companion device")
    return asyncio.run(_slots_serial(device["device"]))

def validate_hashtag(name: str) -> str:
    name=name.strip()
    if not re.fullmatch(r"#[A-Za-z0-9][A-Za-z0-9_-]{0,31}",name): raise RadioError("channel must be a hashtag with 1–32 letters, digits, _ or - after #")
    return name

def hashtag_secret(name: str) -> bytes:
    return hashlib.sha256(validate_hashtag(name).encode("utf-8")).digest()[:16]

async def _write_channel_serial(path: str, slot: int, name: str) -> None:
    from meshcore import EventType
    radio=await _open(path)
    try:
        existing=await asyncio.wait_for(radio.commands.get_channel(slot),READ_TIMEOUT)
        if existing.type == EventType.ERROR: raise RadioError("selected slot could not be re-read before writing")
        existing_name=str(_channel_name(existing.payload) or "")
        existing_secret=_channel_secret(existing.payload)
        if existing_name or existing_secret is None or any(existing_secret): raise RadioError("selected slot is no longer confirmed empty")
        result=await asyncio.wait_for(radio.commands.set_channel(slot,name,hashtag_secret(name)),READ_TIMEOUT)
        if result.type == EventType.ERROR: raise RadioError(f"Companion rejected channel configuration: {result.payload}")
    finally: await _close(radio)

def add_hashtag_channel(device: dict | None, slot: int, name: str) -> dict:
    if not device: raise RadioError("no verified Companion device")
    name=validate_hashtag(name); before=discover_channels(device)
    target=next((x for x in before["slots"] if x["index"]==slot),None)
    if not target or target["occupied"]: raise RadioError("selected slot is not confirmed empty")
    asyncio.run(_write_channel_serial(device["device"],slot,name))
    after=discover_channels(device); verified=next((x for x in after["slots"] if x["index"]==slot),None)
    if not verified or verified["name"] != name: raise RadioError("channel write was not verified by read-back")
    return {"index":slot,"name":name}

async def _delete_channel_serial(path, slot, expected):
    from meshcore import EventType
    radio=await _open(path)
    try:
        current=await asyncio.wait_for(radio.commands.get_channel(slot),READ_TIMEOUT)
        if current.type==EventType.ERROR: raise RadioError("selected slot could not be re-read before deletion")
        if str(_channel_name(current.payload) or "") != expected: raise RadioError("channel changed since confirmation; deletion blocked")
        # Official Companion protocol channel-clear operation: empty name + zero 16-byte secret.
        result=await asyncio.wait_for(radio.commands.set_channel(slot,"",bytes(16)),READ_TIMEOUT)
        if result.type==EventType.ERROR: raise RadioError(f"Companion rejected channel deletion: {result.payload}")
    finally: await _close(radio)

def delete_channel(device: dict | None, slot: int, expected: str) -> None:
    if not device: raise RadioError("no verified Companion device")
    if expected.strip().casefold() in {"public","#public"}: raise RadioError("Public channel is protected from deletion")
    asyncio.run(_delete_channel_serial(device["device"],slot,expected))
    after=discover_channels(device); verified=next((x for x in after["slots"] if x["index"]==slot),None)
    if not verified or verified["occupied"]: raise RadioError("channel deletion was not verified by read-back")

def verify_channel(device: dict | None, selected: dict | None) -> dict:
    if not selected: raise RadioError("no transmit channel selected")
    channels=list_channels(device)
    expected=next((c for c in channels if c["index"]==selected.get("index")),None)
    if not expected: raise RadioError("saved transmit channel slot no longer exists; confirm selection in Settings")
    if expected["name"] != selected.get("name"): raise RadioError("saved transmit channel name no longer matches its slot; confirm selection in Settings")
    return expected

def send(config: dict, device: dict | None, channel: dict, message: str) -> str:
    if config.get("dry_run"): return "dry-run: no serial connection or RF transmission attempted"
    if not config.get("transmit_enabled") or os.environ.get("MESHCORE_SEND_ENABLED") != "1":
        raise RadioError("transmission is disabled (set config and explicit environment interlock)")
    if not device: raise RadioError("no verified Companion device")
    with radio_lock: return asyncio.run(_send_serial(device["device"], int(channel["index"]), message))

def message_limit(config: dict) -> int: return int(config.get("max_message_bytes",180))

def read_device_name(device: dict | None) -> str:
    if not device: raise RadioError("no verified Companion device")
    return asyncio.run(_read_name(device["device"]))
async def _read_name(path):
    from meshcore import EventType
    radio=await _open(path)
    try:
        result=await asyncio.wait_for(radio.commands.send_appstart(),READ_TIMEOUT)
        if result.type==EventType.ERROR: raise RadioError(f"Companion name query failed: {result.payload}")
        payload=result.payload or {}; name=payload.get("name") if isinstance(payload,dict) else getattr(payload,"name",None)
        return str(name or "")
    finally: await _close(radio)
def set_device_name(device: dict | None, name: str) -> str:
    if not device: raise RadioError("no verified Companion device")
    if not name or "\n" in name or "\r" in name: raise RadioError("device name must be a single non-empty line")
    return asyncio.run(_set_name(device["device"],name))
async def _set_name(path,name):
    from meshcore import EventType
    radio=await _open(path)
    try:
        info=await asyncio.wait_for(radio.commands.send_device_query(),READ_TIMEOUT); payload=info.payload or {}
        maximum=(payload.get("max_name_len") or payload.get("max_name_length")) if isinstance(payload,dict) else None
        if not isinstance(maximum,int): raise RadioError("firmware did not report supported device-name length")
        if len(name.encode("utf-8"))>maximum: raise RadioError(f"device name exceeds firmware limit of {maximum} UTF-8 bytes")
        result=await asyncio.wait_for(radio.commands.set_name(name),READ_TIMEOUT)
        if result.type==EventType.ERROR: raise RadioError(f"Companion rejected name: {result.payload}")
    finally: await _close(radio)
    actual=await _read_name(path)
    if actual!=name: raise RadioError("device-name write was not verified by read-back")
    return actual

async def _send_serial(path: str, channel_index: int, message: str) -> str:
    from meshcore import EventType
    radio=await _open(path)
    try:
        result=await asyncio.wait_for(radio.commands.send_chan_msg(channel_index,message), READ_TIMEOUT)
        if result.type == EventType.ERROR: raise RadioError(f"Companion rejected channel message: {result.payload}")
        return f"Companion accepted channel command (event {result.type}); over-air delivery is unknown"
    except RadioError: raise
    except asyncio.TimeoutError as exc: raise RadioError("Companion send timed out; no retry was attempted") from exc
    except Exception as exc: raise RadioError(f"Companion communication failed: {exc}") from exc
    finally: await _close(radio)

def verify_connection(config: dict, device: dict | None) -> str:
    if not device: raise RadioError("no verified Companion device")
    if config.get("dry_run"): return "dry-run: selection resolved; serial connection intentionally not opened"
    return asyncio.run(_verify_serial(device["device"]))

async def _verify_serial(path: str) -> str:
    from meshcore import EventType
    radio=await _open(path)
    try:
        result=await asyncio.wait_for(radio.commands.send_appstart(), READ_TIMEOUT)
        if result.type == EventType.ERROR: raise RadioError(f"Companion handshake rejected: {result.payload}")
        return "Companion read-only handshake succeeded"
    except RadioError: raise
    except asyncio.TimeoutError as exc: raise RadioError("Companion handshake timed out") from exc
    except Exception as exc: raise RadioError(f"Companion connection failed: {exc}") from exc
    finally: await _close(radio)
