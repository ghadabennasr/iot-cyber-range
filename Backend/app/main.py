"""IoT Cyber Range - application backend (MQTT ingestion + REST API)."""
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import config, db
from .mqtt_service import MqttService
from .service import Processor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

mqtt_service = MqttService()
processor = Processor(mqtt_service)
mqtt_service.processor = processor


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    mqtt_service.start()
    yield
    mqtt_service.stop()
    db.close_pool()


app = FastAPI(title="IoT Cyber Range backend", version="1.0", lifespan=lifespan)


def require_token(authorization: Optional[str] = Header(default=None)):
    """Bearer token for write endpoints; fails closed when API_TOKEN is not configured."""
    if not config.API_TOKEN:
        raise HTTPException(503, "Write endpoints disabled: set API_TOKEN in .env")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), config.API_TOKEN.encode()):
        raise HTTPException(401, "Missing or invalid token")


class LockCommand(BaseModel):
    cmd: Literal["LOCK", "UNLOCK", "ALARM", "ALARM_OFF"]
    reason: Optional[str] = Field(default=None, max_length=100)


class Badge(BaseModel):
    badge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    holder: Optional[str] = Field(default=None, max_length=80)
    active: bool = True


@app.get("/health")
def health():
    database = db.is_up()
    return {
        "status": "ok" if database else "degraded",
        "database": database,
        "mqtt_ingest": mqtt_service.ingest_connected,
        "mqtt_controller": mqtt_service.controller_connected,
        "dropped_by_rate_limit": processor.dropped,
    }


@app.get("/api/devices")
def devices():
    return db.list_devices()


@app.get("/api/events")
def events(limit: int = Query(100, ge=1, le=500), event_type: Optional[str] = None,
           device_id: Optional[str] = None):
    return db.list_events(limit, event_type, device_id)


@app.get("/api/badge-events")
def badge_events(limit: int = Query(100, ge=1, le=500)):
    return db.list_badge_events(limit)


@app.get("/api/motion")
def motion(limit: int = Query(100, ge=1, le=500), plausible_only: bool = False):
    return db.list_motion(limit, plausible_only)


@app.get("/api/lock/state")
def lock_state():
    commands = db.list_lock_commands(1)
    return {"state": db.latest_lock_state(), "last_command": commands[0] if commands else None}


@app.get("/api/lock/commands")
def lock_commands(limit: int = Query(50, ge=1, le=500)):
    return db.list_lock_commands(limit)


@app.post("/api/lock/command", status_code=202, dependencies=[Depends(require_token)])
def send_lock_command(body: LockCommand):
    result = mqtt_service.send(body.cmd, body.reason or "manual command", "api")
    if not result["published"]:
        raise HTTPException(503, result["error"])
    return result


@app.get("/api/badges")
def badges():
    return db.list_badges()


@app.post("/api/badges", status_code=201, dependencies=[Depends(require_token)])
def upsert_badge(body: Badge):
    db.upsert_badge(body.badge_id, body.holder, body.active)
    return body


@app.get("/api/stats")
def stats():
    return db.stats()
