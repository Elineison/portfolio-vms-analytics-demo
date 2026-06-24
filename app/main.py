from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import cv2
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from app.analytics import AnalysisManager, draw_analytics_overlay
from app.auth import active_trial_user, current_user, google_auth_configured, oauth, public_user, SESSION_SECRET
from app.detectors import PeopleDetector
from app.mailer import EvidenceMailer
from app.runtime import RuntimeManager
from app.schemas import CameraCreate, CameraPatch, RuntimeStatus, User
from app.store import JsonStore

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.getenv("VMS_DATA_DIR", str(BASE_DIR / "data")))
EVIDENCE_DIR = DATA_DIR / "events"
OUTBOX_DIR = DATA_DIR / "outbox"
SESSION_TIMEOUT_S = int(os.getenv("VMS_SESSION_TIMEOUT_S", "300"))
ANALYSIS_FPS = float(os.getenv("VMS_ANALYSIS_FPS", "2"))
ADMIN_TOKEN = os.getenv("VMS_ADMIN_TOKEN", "")

app = FastAPI(title="AVM - Analise de Video para Monitoramento")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("VMS_COOKIE_HTTPS_ONLY", "0") == "1",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def startup() -> None:
    app.state.store = JsonStore(DATA_DIR)
    app.state.runtime = RuntimeManager()
    app.state.detector = PeopleDetector()
    app.state.mailer = EvidenceMailer(OUTBOX_DIR)
    app.state.analysis = AnalysisManager(
        app.state.runtime,
        app.state.detector,
        app.state.store,
        app.state.mailer,
        EVIDENCE_DIR,
        ANALYSIS_FPS,
    )
    await app.state.analysis.sync_all(app.state.store.list_all_cameras())


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.analysis.stop_all()
    app.state.runtime.stop_all()


@app.get("/")
async def index() -> Response:
    return Response((STATIC_DIR / "index.html").read_text(encoding="utf-8"), media_type="text/html")


@app.get("/api/system")
async def system_info() -> dict:
    detector = app.state.detector.stats
    return {
        "name": "AVM - Analise de Video para Monitoramento",
        "stream_standard": "websocket_jpeg",
        "session_timeout_s": SESSION_TIMEOUT_S,
        "detector": detector.__dict__,
        "auth": {
            "google_configured": google_auth_configured(),
            "provider": "google",
            "trial_days": 7,
        },
    }


@app.get("/auth/google")
async def auth_google(request: Request):
    if not google_auth_configured():
        raise HTTPException(status_code=503, detail="google_oauth_not_configured")
    redirect_uri = request.url_for("auth_google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    if not google_auth_configured():
        raise HTTPException(status_code=503, detail="google_oauth_not_configured")
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if user_info is None:
        user_info = await oauth.google.parse_id_token(request, token)
    email = user_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="google_email_missing")
    user = request.app.state.store.upsert_google_user(
        email=email,
        name=user_info.get("name"),
        picture=user_info.get("picture"),
    )
    request.session["user_id"] = user.id
    return RedirectResponse("/")


@app.post("/auth/logout")
async def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(user: User = Depends(current_user)) -> dict:
    return public_user(user)


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="admin_token_required")


@app.post("/api/admin/users/{email}/reset-trial")
async def admin_reset_trial(email: str, days: int = 7, _: None = Depends(require_admin_token)) -> dict:
    user = app.state.store.reset_user_trial_by_email(email, days=days)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return public_user(user)


@app.delete("/api/admin/users/{email}")
async def admin_delete_user(email: str, _: None = Depends(require_admin_token)) -> dict:
    deleted = app.state.store.delete_user_by_email(email)
    if not deleted:
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True}


@app.get("/api/cameras")
async def list_cameras(user: User = Depends(current_user)) -> list[dict]:
    return [camera.model_dump(mode="json") for camera in app.state.store.list_cameras(user.id)]


@app.post("/api/cameras")
async def create_camera(payload: CameraCreate, user: User = Depends(active_trial_user)) -> dict:
    camera = app.state.store.create_camera(user.id, payload)
    await app.state.analysis.sync_camera(camera)
    return camera.model_dump(mode="json")


@app.patch("/api/cameras/{camera_id}")
async def patch_camera(camera_id: str, payload: CameraPatch, user: User = Depends(active_trial_user)) -> dict:
    camera = app.state.store.patch_camera(user.id, camera_id, payload)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    await app.state.analysis.sync_camera(camera)
    return camera.model_dump(mode="json")


@app.delete("/api/cameras/{camera_id}")
async def delete_camera(camera_id: str, user: User = Depends(active_trial_user)) -> dict:
    if not app.state.store.delete_camera(user.id, camera_id):
        raise HTTPException(status_code=404, detail="camera not found")
    await app.state.analysis.sync_all(app.state.store.list_all_cameras())
    app.state.runtime.stop(camera_id)
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/start")
async def start_camera(camera_id: str, user: User = Depends(active_trial_user)) -> dict:
    camera = app.state.store.get_camera(user.id, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    app.state.runtime.ensure(camera)
    await app.state.analysis.sync_camera(camera)
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/stop")
async def stop_camera(camera_id: str, user: User = Depends(active_trial_user)) -> dict:
    if app.state.store.get_camera(user.id, camera_id) is None:
        raise HTTPException(status_code=404, detail="camera not found")
    app.state.runtime.stop(camera_id)
    return {"ok": True}


@app.get("/api/cameras/{camera_id}/status")
async def camera_status(camera_id: str, user: User = Depends(current_user)) -> dict:
    camera = app.state.store.get_camera(user.id, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    session = app.state.runtime.get(camera_id)
    last_age = None
    if session and session.snapshot().captured_at:
        last_age = max(0.0, time.time() - session.snapshot().captured_at)
    return RuntimeStatus(
        state=session.state if session else "STOPPED",
        last_frame_age_s=last_age,
        frames_in=session.frames_in if session else 0,
        last_error=session.last_error if session else None,
        width=session.width if session else None,
        height=session.height if session else None,
        detections=app.state.analysis.roi_count_for(camera_id),
        analytics_enabled=camera.analytics.enabled,
        analysis=app.state.analysis.status_for(camera_id),
    ).model_dump(mode="json")


@app.get("/api/events")
async def list_events(camera_id: str | None = None, user: User = Depends(current_user)) -> list[dict]:
    return [event.model_dump(mode="json") for event in app.state.store.list_events(user.id, camera_id)]


@app.get("/api/events/{event_id}/snapshot")
async def event_snapshot(event_id: str, user: User = Depends(current_user)) -> FileResponse:
    event = app.state.store.get_event(user.id, event_id)
    if event is None or not event.snapshot_file:
        raise HTTPException(status_code=404, detail="snapshot not found")
    path = EVIDENCE_DIR / event.snapshot_file
    if not path.exists():
        raise HTTPException(status_code=404, detail="snapshot file not found")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.get("/api/cameras/{camera_id}/snapshot")
async def snapshot(camera_id: str, user: User = Depends(current_user)) -> Response:
    camera = app.state.store.get_camera(user.id, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    session = app.state.runtime.ensure(camera)
    snap = session.snapshot()
    if snap.frame is None:
        return Response(status_code=204)
    frame = snap.frame
    draw_analytics_overlay(
        frame,
        camera,
        app.state.analysis.detections_for(camera_id),
        app.state.analysis.roi_count_for(camera_id),
    )
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not ok:
        return Response(status_code=204)
    return Response(content=encoded.tobytes(), media_type="image/jpeg")


@app.websocket("/ws/preview/{camera_id}")
async def websocket_preview(websocket: WebSocket, camera_id: str, fps: int = Query(default=12, ge=1, le=30)) -> None:
    await websocket.accept()
    user_id = websocket.session.get("user_id")
    if not user_id:
        await websocket.close(code=4401, reason="authentication_required")
        return
    user = app.state.store.get_user(user_id)
    if user is None or not user.trial_active():
        await websocket.close(code=4402, reason="trial_expired")
        return
    camera = app.state.store.get_camera(user.id, camera_id)
    if camera is None:
        await websocket.close(code=4404, reason="camera_not_found")
        return

    session = app.state.runtime.ensure(camera)
    min_interval = 1.0 / float(fps)
    deadline = time.monotonic() + SESSION_TIMEOUT_S
    last_sequence = -1
    try:
        while True:
            if time.monotonic() >= deadline:
                await websocket.close(code=4000, reason=f"session_timeout_{SESSION_TIMEOUT_S}s")
                return
            snap = session.snapshot()
            if snap.frame is None or snap.sequence == last_sequence:
                await asyncio.sleep(0.05)
                continue
            frame = snap.frame
            draw_analytics_overlay(
                frame,
                camera,
                app.state.analysis.detections_for(camera_id),
                app.state.analysis.roi_count_for(camera_id),
            )
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                await websocket.send_bytes(encoded.tobytes())
                last_sequence = snap.sequence
            await asyncio.sleep(min_interval)
    except WebSocketDisconnect:
        return
