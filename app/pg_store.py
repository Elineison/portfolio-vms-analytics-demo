from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from threading import RLock

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas import AnalyticsConfig, Camera, CameraCreate, CameraPatch, Event, User


class PostgresStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._lock = RLock()
        self._init_schema()

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    camera_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cameras_user_id ON cameras(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user_camera ON events(user_id, camera_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_started_at ON events(started_at)")

    def upsert_google_user(self, email: str, name: str | None = None, picture: str | None = None) -> User:
        with self._lock, self._connect() as conn:
            now = datetime.now(timezone.utc)
            row = conn.execute("SELECT payload FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()
            if row:
                existing = User.model_validate(row["payload"])
                updated = existing.model_copy(update={
                    "name": name or existing.name,
                    "picture": picture or existing.picture,
                    "last_login_at": now.isoformat(),
                })
                conn.execute("UPDATE users SET email = %s, payload = %s WHERE id = %s", (updated.email, Jsonb(updated.model_dump(mode="json")), updated.id))
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
            conn.execute("INSERT INTO users (id, email, payload) VALUES (%s, %s, %s)", (user.id, user.email, Jsonb(user.model_dump(mode="json"))))
            return user

    def get_user(self, user_id: str) -> User | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM users WHERE id = %s", (user_id,)).fetchone()
            return User.model_validate(row["payload"]) if row else None

    def reset_user_trial_by_email(self, email: str, days: int = 7) -> User | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()
            if row is None:
                return None
            existing = User.model_validate(row["payload"])
            now = datetime.now(timezone.utc)
            updated = existing.model_copy(update={
                "trial_started_at": now.isoformat(),
                "trial_expires_at": (now + timedelta(days=days)).isoformat(),
                "trial_extension_days": existing.trial_extension_days + days,
            })
            conn.execute("UPDATE users SET payload = %s WHERE id = %s", (Jsonb(updated.model_dump(mode="json")), updated.id))
            return updated

    def delete_user_by_email(self, email: str) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute("DELETE FROM users WHERE lower(email) = lower(%s)", (email,))
            return result.rowcount > 0

    def list_cameras(self, user_id: str) -> list[Camera]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM cameras WHERE user_id = %s ORDER BY payload->>'name'", (user_id,)).fetchall()
            return [Camera.model_validate(row["payload"]) for row in rows]

    def list_all_cameras(self) -> list[Camera]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM cameras", ()).fetchall()
            return [Camera.model_validate(row["payload"]) for row in rows]

    def get_camera(self, user_id: str, camera_id: str) -> Camera | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM cameras WHERE id = %s AND user_id = %s", (camera_id, user_id)).fetchone()
            return Camera.model_validate(row["payload"]) if row else None

    def create_camera(self, user_id: str, data: CameraCreate) -> Camera:
        with self._lock, self._connect() as conn:
            camera = Camera(
                id=str(uuid.uuid4()),
                user_id=user_id,
                analytics=AnalyticsConfig(enabled=True),
                **data.model_dump(),
            )
            conn.execute(
                "INSERT INTO cameras (id, user_id, payload) VALUES (%s, %s, %s)",
                (camera.id, user_id, Jsonb(camera.model_dump(mode="json"))),
            )
            return camera

    def patch_camera(self, user_id: str, camera_id: str, data: CameraPatch) -> Camera | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM cameras WHERE id = %s AND user_id = %s", (camera_id, user_id)).fetchone()
            if row is None:
                return None
            camera = Camera.model_validate(row["payload"])
            update = data.model_dump(exclude_unset=True)
            patched = Camera.model_validate({**camera.model_dump(mode="json"), **update})
            conn.execute("UPDATE cameras SET payload = %s WHERE id = %s", (Jsonb(patched.model_dump(mode="json")), camera_id))
            return patched

    def delete_camera(self, user_id: str, camera_id: str) -> bool:
        with self._lock, self._connect() as conn:
            result = conn.execute("DELETE FROM cameras WHERE id = %s AND user_id = %s", (camera_id, user_id))
            return result.rowcount > 0

    def add_event(self, event: Event) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO events (id, user_id, camera_id, started_at, payload) VALUES (%s, %s, %s, %s, %s)",
                (event.id, event.user_id, event.camera_id, event.started_at, Jsonb(event.model_dump(mode="json"))),
            )

    def list_events(self, user_id: str, camera_id: str | None = None) -> list[Event]:
        with self._lock, self._connect() as conn:
            if camera_id:
                rows = conn.execute(
                    "SELECT payload FROM events WHERE user_id = %s AND camera_id = %s ORDER BY started_at DESC LIMIT 100",
                    (user_id, camera_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM events WHERE user_id = %s ORDER BY started_at DESC LIMIT 100",
                    (user_id,),
                ).fetchall()
            return [Event.model_validate(row["payload"]) for row in rows]

    def get_event(self, user_id: str, event_id: str) -> Event | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM events WHERE id = %s AND user_id = %s", (event_id, user_id)).fetchone()
            return Event.model_validate(row["payload"]) if row else None

    def delete_event(self, user_id: str, event_id: str) -> Event | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM events WHERE id = %s AND user_id = %s", (event_id, user_id)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM events WHERE id = %s AND user_id = %s", (event_id, user_id))
            return Event.model_validate(row["payload"])
