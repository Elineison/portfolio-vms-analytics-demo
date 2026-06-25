from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from app.schemas import AnalyticsConfig, Camera, CameraCreate, CameraPatch, Event, User


class JsonStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "store.json"
        self._lock = RLock()
        self._cameras: dict[str, Camera] = {}
        self._events: list[Event] = []
        self._users: dict[str, User] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._users = {
            item["id"]: User.model_validate(item)
            for item in raw.get("users", [])
            if item.get("id")
        }
        self._cameras = {}
        for item in raw.get("cameras", []):
            if not item.get("user_id"):
                continue
            self._cameras[item["id"]] = Camera.model_validate(item)
        self._events = [
            Event.model_validate(item)
            for item in raw.get("events", [])
            if item.get("user_id")
        ]

    def _save(self) -> None:
        payload = {
            "cameras": [camera.model_dump(mode="json") for camera in self._cameras.values()],
            "events": [event.model_dump(mode="json") for event in self._events[-250:]],
            "users": [user.model_dump(mode="json") for user in self._users.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(self.path)

    def upsert_google_user(self, email: str, name: str | None = None, picture: str | None = None) -> User:
        with self._lock:
            now = datetime.now(timezone.utc)
            existing = next((user for user in self._users.values() if user.email.lower() == email.lower()), None)
            if existing:
                updated = existing.model_copy(update={
                    "name": name or existing.name,
                    "picture": picture or existing.picture,
                    "last_login_at": now.isoformat(),
                })
                self._users[updated.id] = updated
                self._save()
                return updated

            user = User(
                id=str(uuid.uuid4()),
                email=email,
                name=name,
                picture=picture,
                created_at=now.isoformat(),
                last_login_at=now.isoformat(),
                trial_started_at=now.isoformat(),
                trial_expires_at=(now + timedelta(days=7)).isoformat(),
            )
            self._users[user.id] = user
            self._save()
            return user

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def reset_user_trial_by_email(self, email: str, days: int = 7) -> User | None:
        with self._lock:
            existing = next((user for user in self._users.values() if user.email.lower() == email.lower()), None)
            if existing is None:
                return None
            now = datetime.now(timezone.utc)
            updated = existing.model_copy(update={
                "trial_started_at": now.isoformat(),
                "trial_expires_at": (now + timedelta(days=days)).isoformat(),
                "trial_extension_days": existing.trial_extension_days + days,
            })
            self._users[updated.id] = updated
            self._save()
            return updated

    def delete_user_by_email(self, email: str) -> bool:
        with self._lock:
            existing = next((user for user in self._users.values() if user.email.lower() == email.lower()), None)
            if existing is None:
                return False
            self._users.pop(existing.id, None)
            camera_ids = [camera.id for camera in self._cameras.values() if camera.user_id == existing.id]
            for camera_id in camera_ids:
                self._cameras.pop(camera_id, None)
            self._events = [event for event in self._events if event.user_id != existing.id]
            self._save()
            return True

    def list_cameras(self, user_id: str) -> list[Camera]:
        with self._lock:
            return [camera for camera in self._cameras.values() if camera.user_id == user_id]

    def list_all_cameras(self) -> list[Camera]:
        with self._lock:
            return list(self._cameras.values())

    def get_camera(self, user_id: str, camera_id: str) -> Camera | None:
        with self._lock:
            camera = self._cameras.get(camera_id)
            if camera is None or camera.user_id != user_id:
                return None
            return camera

    def create_camera(self, user_id: str, data: CameraCreate) -> Camera:
        with self._lock:
            camera = Camera(
                id=str(uuid.uuid4()),
                user_id=user_id,
                analytics=AnalyticsConfig(enabled=True),
                **data.model_dump(),
            )
            self._cameras[camera.id] = camera
            self._save()
            return camera

    def patch_camera(self, user_id: str, camera_id: str, data: CameraPatch) -> Camera | None:
        with self._lock:
            camera = self.get_camera(user_id, camera_id)
            if camera is None:
                return None
            update = data.model_dump(exclude_unset=True)
            patched = Camera.model_validate({**camera.model_dump(mode="json"), **update})
            self._cameras[camera_id] = patched
            self._save()
            return patched

    def delete_camera(self, user_id: str, camera_id: str) -> bool:
        with self._lock:
            camera = self.get_camera(user_id, camera_id)
            found = False
            if camera:
                self._cameras.pop(camera_id, None)
                found = True
            if found:
                self._save()
            return found

    def add_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)
            self._events = self._events[-250:]
            self._save()

    def list_events(self, user_id: str, camera_id: str | None = None) -> list[Event]:
        with self._lock:
            events = [event for event in self._events if event.user_id == user_id]
            if camera_id:
                events = [event for event in events if event.camera_id == camera_id]
            return list(reversed(events[-100:]))

    def get_event(self, user_id: str, event_id: str) -> Event | None:
        with self._lock:
            for event in self._events:
                if event.id == event_id and event.user_id == user_id:
                    return event
            return None

    def delete_event(self, user_id: str, event_id: str) -> Event | None:
        with self._lock:
            for index, event in enumerate(self._events):
                if event.id == event_id and event.user_id == user_id:
                    removed = self._events.pop(index)
                    self._save()
                    return removed
            return None
