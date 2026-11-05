"""Small, bounded, memory-only automatic warning queue."""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json, threading, time
from .meshcore_adapter import RadioError, verify_channel, send
from .runtime import runtime_config
from .routes_support import auto_device_state, auto_saved_channel

MAX_AGE_SECONDS=300
MIN_ATTEMPT_INTERVAL=5.0
MAX_QUEUE=5

def _parsed(value):
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except (TypeError,ValueError): return None

def eligible_auto(app, alert):
    db=app.extensions["db"];cfg=runtime_config(app.config,db)
    if alert["test_only"] or not cfg["auto_sending_enabled"] or alert["event"] not in cfg["auto_warning_types"]: return False
    props=json.loads(alert["raw_json"])["properties"]
    kind=str(props.get("_mesh_kind") or "").upper()
    sent=_parsed(alert["sent"]);floor=_parsed(db.setting("auto_dispatch_not_before"));current=datetime.now(timezone.utc)
    fresh=bool(sent and floor and sent>=floor and 0 <= (current-sent).total_seconds() <= MAX_AGE_SECONDS)
    if kind=="CANCELED": return fresh and db.family_was_auto_sent(alert["family_id"])
    # A direct ingest caller must not bypass the poller's official overlap
    # filter.  Empty codes are never treated as all selected counties.
    if not str(props.get("_mesh_matched_areas") or "").strip(): return False
    if alert["status"]!="pending": return False
    return fresh

def _attempt(app, cap_id):
    db=app.extensions["db"];alert=db.get(cap_id)
    if not alert or not eligible_auto(app,alert): return None
    # One bounded, non-retrying attempt per newly observed CAP revision.
    if alert["status"]=="pending" and not db.claim(cap_id,"automatic"): return None
    _,device,status=auto_device_state(db);channel=auto_saved_channel(db)
    try:
        verify_channel(device,channel)
        from .messages import radio_message
        message=radio_message(json.loads(alert["raw_json"]),db.setting("timezone") or app.config.get("timezone","UTC"),app.config.get("max_message_bytes",180))
        detail=send(runtime_config(app.config,db),device,channel,message)
        db.finish(cap_id,"auto_sent","automatic",detail,message)
        return True
    except (RadioError,ValueError,KeyError) as exc:
        # A failed attempt is terminal: no reconnect/backlog retry storm.
        db.finish(cap_id,"auto_failed","automatic",f"{status}. {exc}",alert["message"])
        return False

class AutomaticQueue:
    """One immediate attempt, then five-second-spaced bounded work."""
    def __init__(self, app, clock=time.monotonic, timer=threading.Timer):
        self.app=app;self.clock=clock;self.timer_factory=timer
        self.items=deque();self.queued=set();self.lock=threading.RLock()
        self.last_attempt=None;self.timer=None;self.running=False
    def enqueue(self, cap_id):
        with self.lock:
            if cap_id in self.queued: return False
            if len(self.items)>=MAX_QUEUE:
                self.app.extensions["db"].audit(cap_id,"auto_dropped","automatic","automatic queue full; no retry")
                return False
            self.items.append(cap_id);self.queued.add(cap_id)
            if self.running: return True
            if self.last_attempt is None or self.clock()-self.last_attempt>=MIN_ATTEMPT_INTERVAL: self.running=True
            else: self._schedule_locked();return True
        self._run_one();return True
    def _schedule_locked(self):
        if self.timer or not self.items: return
        delay=max(0,MIN_ATTEMPT_INTERVAL-(self.clock()-self.last_attempt)) if self.last_attempt is not None else 0
        self.timer=self.timer_factory(delay,self._timer_fired);self.timer.daemon=True;self.timer.start()
    def _timer_fired(self):
        with self.lock:
            self.timer=None
            if self.running or not self.items: return
            if self.last_attempt is not None and self.clock()-self.last_attempt<MIN_ATTEMPT_INTERVAL:
                self._schedule_locked();return
            self.running=True
        self._run_one()
    def _run_one(self):
        with self.lock:
            cap_id=self.items.popleft() if self.items else None
            if cap_id: self.queued.discard(cap_id)
        if cap_id:
            # Meter at the attempt boundary, including failures, to avoid a
            # reconnect/failure storm. Ineligible queued entries are discarded.
            with self.app.app_context(): attempted=_attempt(self.app,cap_id)
            if attempted is not None: self.last_attempt=self.clock()
        with self.lock:
            self.running=False;self._schedule_locked()

def queue_for(app):
    queue=app.extensions.get("auto_queue")
    if queue is None: queue=app.extensions.setdefault("auto_queue",AutomaticQueue(app))
    return queue

def dispatch(app, cap_id):
    alert=app.extensions["db"].get(cap_id)
    return bool(alert and eligible_auto(app,alert) and queue_for(app).enqueue(cap_id))
