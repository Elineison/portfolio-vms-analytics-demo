from __future__ import annotations

from datetime import datetime, timezone, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Point(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class AfterHoursRule(BaseModel):
    enabled: bool = False
    start: time = time(18, 0)
    end: time = time(6, 0)
    min_consecutive_hits: int = Field(default=2, ge=1, le=20)
    cooldown_s: int = Field(default=60, ge=5, le=3600)


class GroupLoiteringRule(BaseModel):
    enabled: bool = False
    min_people: int = Field(default=3, ge=2, le=20)
    dwell_s: int = Field(default=120, ge=5, le=3600)
    cooldown_s: int = Field(default=120, ge=5, le=3600)


class AnalyticsConfig(BaseModel):
    enabled: bool = False
    analysis_fps: float = Field(default=2.0, ge=0.2, le=10.0)
    confidence_threshold: float = Field(default=0.35, ge=0.05, le=0.95)
    min_box_area_ratio: float = Field(default=0.005, ge=0.0005, le=0.2)
    notification_email: str | None = Field(default=None, max_length=254)
    roi: list[Point] = Field(default_factory=lambda: [
        Point(x=0.15, y=0.15),
        Point(x=0.85, y=0.15),
        Point(x=0.85, y=0.9),
        Point(x=0.15, y=0.9),
    ])
    after_hours: AfterHoursRule = Field(default_factory=AfterHoursRule)
    group_loitering: GroupLoiteringRule = Field(default_factory=GroupLoiteringRule)

    @field_validator("roi")
    @classmethod
    def roi_has_polygon(cls, value: list[Point]) -> list[Point]:
        if len(value) < 3:
            raise ValueError("ROI must have at least 3 points")
        return value


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    rtsp_url: str = Field(min_length=6, max_length=1000)


class CameraPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    rtsp_url: str | None = Field(default=None, min_length=6, max_length=1000)
    analytics: AnalyticsConfig | None = None


class Camera(CameraCreate):
    id: str
    user_id: str
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)


class Detection(BaseModel):
    bbox: tuple[int, int, int, int]
    confidence: float = 0.0
    label: str = "person"
    track_id: int | None = None
    first_seen_s: float = 0.0
    last_seen_s: float = 0.0
    age_s: float = 0.0
    inside_roi: bool = False


class RuntimeStatus(BaseModel):
    state: Literal["STOPPED", "CONNECTING", "RUNNING", "ERROR"]
    last_frame_age_s: float | None = None
    frames_in: int = 0
    last_error: str | None = None
    width: int | None = None
    height: int | None = None
    detections: int = 0
    analytics_enabled: bool = False
    analysis: dict = Field(default_factory=dict)


class Event(BaseModel):
    id: str
    user_id: str
    camera_id: str
    camera_name: str
    type: Literal["after_hours_intrusion", "group_loitering"]
    title: str
    message: str
    started_at: str
    ended_at: str | None = None
    people_count: int = 0
    snapshot_file: str | None = None
    snapshot_url: str | None = None
    notification_email: str | None = None
    notification_status: str | None = None


class User(BaseModel):
    id: str
    email: str
    name: str | None = None
    picture: str | None = None
    provider: str = "google"
    created_at: str
    last_login_at: str
    trial_started_at: str
    trial_expires_at: str
    trial_extension_days: int = 0

    def trial_active(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        expires = datetime.fromisoformat(self.trial_expires_at)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return current <= expires
