from __future__ import annotations
import json
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import check_password_hash
from .devices import scan_devices, resolve_device
from .messages import append_comment
from .meshcore_adapter import send, verify_connection, RadioError

bp=Blueprint("console",__name__)
def admin(fn):
 @wraps(fn)
 def wrapped(*a,**kw):
  if not session.get("user"): return redirect(url_for("console.login"))
  return fn(*a,**kw)
 return wrapped

@bp.route("/login",methods=["GET","POST"])
def login():
 if request.method=="POST":
  if request.form.get("username")==current_app.config["admin_username"] and check_password_hash(current_app.config["admin_password_hash"],request.form.get("password", "")):
   session.clear();session["user"]=request.form["username"];return redirect(url_for("console.index"))
  flash("Invalid credentials")
 return render_template("login.html")
@bp.post("/logout")
@admin
def logout(): session.clear();return redirect(url_for("console.login"))
@bp.get("/")
@admin
def index():
 db=current_app.extensions["db"]
 last=db.setting("nws_last_success"); error=db.setting("nws_last_error"); stale=True
 if last:
  try: stale=(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>current_app.config.get("poll_seconds",60)*3
  except ValueError: pass
 health="stale" if stale else ("error" if error else "healthy")
 return render_template("index.html",alerts=db.actionable(),history=db.history(),channels=current_app.config.get("channels",[]), cap=current_app.config.get("max_message_bytes",180),last_sync=last,feed_error=error,feed_health=health)
@bp.get("/api/state")
@admin
def state():
 db=current_app.extensions["db"]
 alerts=db.actionable(); history=db.history(); last=db.setting("nws_last_success"); error=db.setting("nws_last_error"); stale=True
 if last:
  try: stale=(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>current_app.config.get("poll_seconds",60)*3
  except ValueError: pass
 return jsonify(alerts_html=render_template("_alerts.html",alerts=alerts,cap=current_app.config.get("max_message_bytes",180)),history_html=render_template("_history.html",history=history),nws_last_success=last,nws_last_error=error,feed_health="stale" if stale else ("error" if error else "healthy"))
@bp.post("/alerts/<cap_id>/ignore")
@admin
def ignore(cap_id):
 db=current_app.extensions["db"]
 if db.claim(cap_id,session["user"]): db.finish(cap_id,"ignored",session["user"])
 else: flash("Alert is no longer actionable.")
 return redirect(url_for("console.index"))
@bp.post("/alerts/<cap_id>/send")
@admin
def transmit(cap_id):
 db=current_app.extensions["db"]; actor=session["user"]
 if not db.claim(cap_id,actor): flash("Alert is no longer actionable.");return redirect(url_for("console.index"))
 alert=db.get(cap_id); message=append_comment(alert["message"],request.form.get("comment",""),current_app.config.get("max_message_bytes",180))
 saved=json.loads(db.setting("device") or "null"); dev,status=resolve_device(saved,scan_devices())
 try:
  detail=send(current_app.config,dev,current_app.config.get("channels",[""])[0],message)
  db.finish(cap_id,"sent",actor,detail,message);flash("Dispatch recorded. Radio acceptance is not proof of mesh delivery.")
 except RadioError as exc:
  db.finish(cap_id,"failed",actor,str(exc),message);flash(f"No transmission: {exc}")
 return redirect(url_for("console.index"))
@bp.route("/settings",methods=["GET","POST"])
@admin
def settings():
 db=current_app.extensions["db"]
 if request.method=="POST":
  value=request.form.get("device")
  db.save_setting("device",value) if value else db.save_setting("device","null")
  flash("Device selection saved; weather monitoring is unchanged.");return redirect(url_for("console.settings"))
 found=scan_devices();saved=json.loads(db.setting("device") or "null");_,status=resolve_device(saved,found)
 return render_template("settings.html",devices=found,saved=saved,status=status)
@bp.post("/settings/test")
@admin
def test_device():
 saved=json.loads(current_app.extensions["db"].setting("device") or "null");dev,status=resolve_device(saved,scan_devices())
 try:
  result=verify_connection(current_app.config,dev);flash(status+". "+result)
 except RadioError as exc: flash(status+". "+str(exc))
 return redirect(url_for("console.settings"))
