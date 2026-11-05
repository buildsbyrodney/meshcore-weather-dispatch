"""Dependency-light helpers shared by request and poller paths."""
import json
from .devices import scan_devices, resolve_device
def auto_saved_channel(db): return json.loads(db.setting("tx_channel") or "null")
def auto_device_state(db):
    saved=json.loads(db.setting("device") or "null")
    device,status=resolve_device(saved,scan_devices())
    return saved,device,status
