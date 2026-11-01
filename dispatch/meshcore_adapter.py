"""Conservative adapter for the official ``meshcore`` Python library."""
from __future__ import annotations
import asyncio, os

class RadioError(Exception): pass

def send(config: dict, device: dict | None, channel: str, message: str) -> str:
    if config.get("dry_run"):
        return "dry-run: no serial connection or RF transmission attempted"
    if not config.get("transmit_enabled") or os.environ.get("MESHCORE_SEND_ENABLED") != "1":
        raise RadioError("transmission is disabled (set config and explicit environment interlock)")
    if not device: raise RadioError("no verified Companion device")
    return asyncio.run(_send_serial(device["device"], int(config.get("channel_index", 0)), message))

async def _send_serial(path: str, channel_index: int, message: str) -> str:
    from meshcore import MeshCore, EventType
    radio = await MeshCore.create_serial(path)
    try:
        result = await radio.commands.send_chan_msg(channel_index, message)
        if result.type == EventType.ERROR:
            raise RadioError(f"Companion rejected channel message: {result.payload}")
        return f"Companion accepted channel command (event {result.type}); over-air delivery is unknown"
    except RadioError: raise
    except Exception as exc: raise RadioError(f"Companion communication failed: {exc}") from exc
    finally:
        disconnect = getattr(radio, "disconnect", None)
        if disconnect:
            outcome = disconnect()
            if hasattr(outcome, "__await__"): await outcome

def verify_connection(config: dict, device: dict | None) -> str:
    """Read-only handshake; does not issue a radio-message command."""
    if not device: raise RadioError("no verified Companion device")
    if config.get("dry_run"): return "dry-run: selection resolved; serial connection intentionally not opened"
    return asyncio.run(_verify_serial(device["device"]))

async def _verify_serial(path: str) -> str:
    from meshcore import MeshCore, EventType
    radio = await MeshCore.create_serial(path)
    try:
        result = await radio.commands.send_appstart()
        if result.type == EventType.ERROR: raise RadioError(f"Companion handshake rejected: {result.payload}")
        return "Companion read-only handshake succeeded"
    except RadioError: raise
    except Exception as exc: raise RadioError(f"Companion connection failed: {exc}") from exc
    finally:
        disconnect=getattr(radio,"disconnect",None)
        if disconnect:
            outcome=disconnect()
            if hasattr(outcome,"__await__"): await outcome
