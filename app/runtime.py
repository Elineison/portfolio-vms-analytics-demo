from __future__ import annotations

from app.schemas import Camera
from app.video import RtspCameraSession


class RuntimeManager:
    def __init__(self) -> None:
        self._sessions: dict[str, RtspCameraSession] = {}

    def ensure(self, camera: Camera) -> RtspCameraSession:
        session = self._sessions.get(camera.id)
        if session is None:
            session = RtspCameraSession(camera.id, camera.rtsp_url)
            self._sessions[camera.id] = session
        else:
            session.update_url(camera.rtsp_url)
        session.start()
        return session

    def get(self, camera_id: str) -> RtspCameraSession | None:
        return self._sessions.get(camera_id)

    def stop(self, camera_id: str) -> None:
        session = self._sessions.pop(camera_id, None)
        if session:
            session.stop()

    def stop_all(self) -> None:
        for camera_id in list(self._sessions):
            self.stop(camera_id)

