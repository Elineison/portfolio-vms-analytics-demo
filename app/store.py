from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import RLock

from app.schemas import Camera, CameraCreate, CameraPatch, Event


class JsonStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "store.json"
        self._lock = RLock()
        self._cameras: dict[str, Camera] = {}
        self._events: list[Event] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._cameras = {
            item["id"]: Camera.model_validate(item)
            for item in raw.get("cameras", [])
        }
        self._events = [Event.model_validate(item) for item in raw.get("events", [])]

    def _save(self) -> None:
        payload = {
            "cameras": [camera.model_dump(mode="json") for camera in self._cameras.values()],
            "events": [event.model_dump(mode="json") for event in self._events[-250:]],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(self.path)

    def list_cameras(self) -> list[Camera]:
        with self._lock:
            return list(self._cameras.values())

    def get_camera(self, camera_id: str) -> Camera | None:
        with self._lock:
            return self._cameras.get(camera_id)

    def create_camera(self, data: CameraCreate) -> Camera:
        with self._lock:
            camera = Camera(id=str(uuid.uuid4()), **data.model_dump())
            self._cameras[camera.id] = camera
            self._save()
            return camera

    def patch_camera(self, camera_id: str, data: CameraPatch) -> Camera | None:
        with self._lock:
            camera = self._cameras.get(camera_id)
            if camera is None:
                return None
            update = data.model_dump(exclude_unset=True)
            patched = Camera.model_validate({**camera.model_dump(mode="json"), **update})
            self._cameras[camera_id] = patched
            self._save()
            return patched

    def delete_camera(self, camera_id: str) -> bool:
        with self._lock:
            found = self._cameras.pop(camera_id, None) is not None
            if found:
                self._save()
            return found

    def add_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)
            self._events = self._events[-250:]
            self._save()

    def list_events(self, camera_id: str | None = None) -> list[Event]:
        with self._lock:
            events = self._events
            if camera_id:
                events = [event for event in events if event.camera_id == camera_id]
            return list(reversed(events[-100:]))

    def get_event(self, event_id: str) -> Event | None:
        with self._lock:
            for event in self._events:
                if event.id == event_id:
                    return event
            return None
