from __future__ import annotations
from pathlib import Path
from serial.tools import list_ports

def scan_devices() -> list[dict]:
    """Pure discovery: deliberately no automatic selection or serial I/O."""
    by_id = {str(link.resolve()): str(link) for link in Path("/dev/serial/by-id").glob("*")} if Path("/dev/serial/by-id").exists() else {}
    results=[]
    for p in list_ports.comports():
        stable=by_id.get(p.device)
        results.append({"device":p.device, "stable_path":stable, "vid":f"{p.vid:04x}" if p.vid is not None else None, "pid":f"{p.pid:04x}" if p.pid is not None else None, "serial_number":p.serial_number, "manufacturer":p.manufacturer, "product":p.product, "friendly": " · ".join(x for x in [p.manufacturer, p.product, p.serial_number, p.device] if x)})
    return results

def resolve_device(saved: dict, found: list[dict]) -> tuple[dict | None, str]:
    if not saved: return None, "No Companion selected"
    stable=saved.get("stable_path")
    if stable and Path(stable).exists():
        matching=[d for d in found if d["stable_path"] == stable]
        if len(matching)==1: return matching[0], "Connected by stable path"
    identity=(saved.get("vid"),saved.get("pid"),saved.get("serial_number"))
    matches=[d for d in found if (d["vid"],d["pid"],d["serial_number"]) == identity and all(identity)]
    if len(matches)==1: return matches[0], "Connected by unique USB identity"
    if len(matches)>1: return None, "Ambiguous USB identity; select the radio again"
    return None, "Selected Companion is disconnected"
