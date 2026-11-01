from __future__ import annotations
import json, time, requests, hashlib, re
from datetime import datetime, timezone
from .db import now
from .messages import radio_message

NWS = "https://api.weather.gov/alerts/active"
def iso_past(value):
    try: return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    except (ValueError, AttributeError): return False

def cap_reference_ids(references) -> list[str]:
    """Extract identifiers from CAP `sender,identifier,sent` reference tuples."""
    if not isinstance(references, str): return []
    return [parts[1].strip() for item in references.split() if len(parts := item.strip().split(",")) >= 2 and parts[1].strip()]

def configured_counties(cfg) -> set[str]:
    """Accept NWS SAME FIPS, UGC codes, and the final segment of an NWS URL."""
    return {normalize_geo_code(str(value).rstrip("/").rsplit("/",1)[-1]) for value in cfg.get("counties",[]) if str(value).strip()}

def normalize_geo_code(value: str) -> str:
    value=value.upper()
    # SAME is six digits (subdivision + state FIPS + county FIPS); accept a
    # shorter user-entered numeric code while comparing it in canonical form.
    return value.zfill(6) if value.isdigit() and len(value) <= 6 else value

def eligible(feature, cfg):
    p=feature["properties"]
    if p.get("event") not in cfg.get("alert_types", []): return False
    counties=configured_counties(cfg)
    if not counties: return True
    geocode=p.get("geocode") or {}
    # A null/missing geometry is immaterial: no polygon parsing is performed.
    codes={normalize_geo_code(str(code)) for code in (geocode.get("SAME") or [])+(geocode.get("UGC") or [])}
    return bool(codes & set(counties))

def content_fingerprint(props: dict) -> str:
    """Stable comparison that deliberately excludes alert ID, CAP references and sent time."""
    fields=("event","areaDesc","headline","description","instruction","effective","onset","expires","severity","urgency","certainty")
    normalized="\n".join(re.sub(r"\s+"," ",(props.get(x) or "").strip()).casefold() for x in fields)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def mark(c, cap_id, status, detail=None):
    c.execute("UPDATE alerts SET status=?,updated_at=? WHERE cap_id=?",(status,now(),cap_id))
    c.execute("INSERT INTO audit(cap_id,action,detail,created_at) VALUES(?,?,?,?)",(cap_id,status,detail,now()))

def ingest(app, features):
    db=app.extensions["db"]; cfg=app.config
    for f in features:
        p=f["properties"]; cap_id=p["id"]
        references=cap_reference_ids(p.get("references"))
        with db.connect() as c:
            existing=c.execute("SELECT cap_id FROM alerts WHERE cap_id=?",(cap_id,)).fetchone()
            if existing: continue
            prior_rows=c.execute("SELECT * FROM alerts WHERE cap_id IN (%s) ORDER BY created_at DESC" % ",".join("?"*len(references)),references).fetchall() if references else []
            family_id=prior_rows[0]["family_id"] if prior_rows and prior_rows[0]["family_id"] else (prior_rows[0]["cap_id"] if prior_rows else cap_id)
            fingerprint=content_fingerprint(p)
            cancel=(p.get("messageType") or "").lower()=="cancel"
            # A cancellation closes actionable revisions but never changes sent/ignored history.
            if cancel:
                for row in c.execute("SELECT cap_id FROM alerts WHERE family_id=? AND status IN ('pending','sending')",(family_id,)).fetchall(): mark(c,row["cap_id"],"canceled",cap_id)
                status="canceled"; label="Cancellation"
            else:
                same=next((r for r in prior_rows if r["content_hash"]==fingerprint),None)
                if same:
                    status="unchanged"; label="Unchanged revision"
                else:
                    prior_statuses={r["status"] for r in prior_rows}
                    for row in c.execute("SELECT cap_id FROM alerts WHERE family_id=? AND status IN ('pending','sending')",(family_id,)).fetchall(): mark(c,row["cap_id"],"superseded",cap_id)
                    status="pending"
                    label="Updated Warning — Previously Sent" if "sent" in prior_statuses else ("Updated Warning — Previously Ignored" if "ignored" in prior_statuses else ("Updated Warning" if prior_rows else "New Warning"))
            if iso_past(p.get("expires")): status="expired"
            message=radio_message(f,cfg.get("max_message_bytes",180))
            c.execute("INSERT INTO alerts(cap_id,family_id,content_hash,revision_label,event,headline,description,areas,sent,effective,expires,status,message,replaces_id,raw_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cap_id,family_id,fingerprint,label,p.get("event",""),p.get("headline"),p.get("description"),p.get("areaDesc"),p.get("sent"),p.get("effective"),p.get("expires"),status,message,references[0] if references else None,json.dumps(f),now(),now()))
            c.execute("INSERT INTO audit(cap_id,action,detail,message,created_at) VALUES(?,?,?,?,?)",(cap_id,status,label,message,now()))
    with db.connect() as c:
        rows=c.execute("SELECT cap_id FROM alerts WHERE status='pending' AND expires IS NOT NULL AND expires <= ?",(now(),)).fetchall()
        for r in rows:
            c.execute("UPDATE alerts SET status='expired',updated_at=? WHERE cap_id=?",(now(),r[0]))
            c.execute("INSERT INTO audit(cap_id,action,detail,created_at) VALUES(?,?,?,?)",(r[0],"expired","local expiration",now()))

def fetch(app):
    cfg=app.config; params=[]
    for state in cfg.get("states",[]): params.append(("area",state.upper()))
    try:
        response=requests.get(NWS, params=params, headers={"User-Agent":cfg["nws_user_agent"],"Accept":"application/geo+json"}, timeout=15)
        response.raise_for_status(); data=response.json()
        ingest(app,[f for f in data.get("features",[]) if eligible(f,cfg)])
        app.extensions["db"].save_setting("nws_last_success",now())
        app.extensions["db"].save_setting("nws_last_error","")
    except Exception as exc:
        app.extensions["db"].save_setting("nws_last_error",str(exc)[:500])
        raise

def run_poller(app):
    while True:
        try:
            with app.app_context(): fetch(app)
        except Exception as exc:
            app.logger.exception("NWS polling failed: %s",exc)
        time.sleep(app.config.get("poll_seconds",60))
