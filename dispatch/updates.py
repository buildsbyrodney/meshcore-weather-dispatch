from __future__ import annotations
import json, socket, subprocess
from pathlib import Path
from .version import VERSION
APP="/opt/meshcore-weather-dispatch"; STATE="/var/lib/meshcore-weather-dispatch/web-updates"; UPDATE_SOCKET="/run/meshcore-weather-dispatch/update.sock"
def _run(args,cwd=APP):
 if cwd and not Path(cwd).is_dir(): return subprocess.CompletedProcess(args,1,"","")
 return subprocess.run(args,cwd=cwd,text=True,capture_output=True,timeout=20,check=False)
def installed():
 r=_run(["git","rev-parse","HEAD"]);return {"version":VERSION,"commit":r.stdout.strip() if r.returncode==0 else "unavailable"}
def available():
 r=_run(["git","ls-remote","origin","refs/heads/main"]);remote=r.stdout.split()[0] if r.returncode==0 and r.stdout.split() else None
 return {"commit":remote,"available":bool(remote and remote!=installed()["commit"]),"error":None if remote else "Unable to check GitHub for updates."}
def status(config):
 root=Path(config.get("update_state_dir",STATE));data={"status":"idle","message":"No update has been requested.","log":""}
 try:data.update(json.loads((root/"status.json").read_text(encoding="utf-8")))
 except (OSError,ValueError):pass
 try:data["log"]=(root/"update.log").read_text(encoding="utf-8",errors="replace")[-12000:]
 except OSError:pass
 return data
def launch(config,reboot=False):
 if config.get("TESTING") and config.get("update_helper"): return config["update_helper"]("reboot" if reboot else "update")
 path=config.get("update_socket",UPDATE_SOCKET)
 try:
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
   client.settimeout(5);client.connect(path);client.sendall(("reboot" if reboot else "update").encode()+b"\n")
 except OSError as exc: raise RuntimeError(f"privileged update worker is unavailable: {exc}") from exc
