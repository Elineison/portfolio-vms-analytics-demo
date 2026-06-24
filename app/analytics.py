from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

import cv2

from app.detectors import PeopleDetector
from app.geometry import bbox_inside_roi, draw_polygon, relative_polygon_to_pixels
from app.runtime import RuntimeManager
from app.schemas import Camera, Detection, Event
from app.store import JsonStore


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def time_in_window(now_time, start, end) -> bool:
    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end


class CameraAnalysisTask:
    def __init__(
        self,
        camera: Camera,
        runtime: RuntimeManager,
        detector: PeopleDetector,
        store: JsonStore,
        fps: float,
    ) -> None:
        self.camera = camera
        self.runtime = runtime
        self.detector = detector
        self.store = store
        self.fps = max(0.2, fps)
        self.task: asyncio.Task | None = None
        self.latest_detections: list[Detection] = []
        self.latest_roi_count = 0
        self._after_hits = 0
        self._after_cooldown_until = 0.0
        self._group_started_at: float | None = None
        self._group_cooldown_until = 0.0

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self._run(), name=f"analysis-{self.camera.id}")

    async def stop(self) -> None:
        if not self.task:
            return
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        interval = 1.0 / self.fps
        while True:
            await self._tick()
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        session = self.runtime.ensure(self.camera)
        snap = session.snapshot()
        if snap.frame is None:
            return
        frame = snap.frame
        height, width = frame.shape[:2]
        roi = relative_polygon_to_pixels(self.camera.analytics.roi, width, height)
        detections = self.detector.detect(frame)
        roi_detections = [det for det in detections if bbox_inside_roi(det.bbox, roi)]
        self.latest_detections = detections
        self.latest_roi_count = len(roi_detections)
        self._apply_after_hours(len(roi_detections))
        self._apply_group_loitering(len(roi_detections))

    def _emit_event(self, event_type: str, title: str, message: str, people_count: int) -> None:
        self.store.add_event(Event(
            id=str(uuid.uuid4()),
            camera_id=self.camera.id,
            camera_name=self.camera.name,
            type=event_type,
            title=title,
            message=message,
            started_at=utc_now_iso(),
            people_count=people_count,
        ))

    def _apply_after_hours(self, people_count: int) -> None:
        rule = self.camera.analytics.after_hours
        if not rule.enabled:
            self._after_hits = 0
            return
        now = time.time()
        if now < self._after_cooldown_until:
            return
        current = datetime.now().time()
        if not time_in_window(current, rule.start, rule.end):
            self._after_hits = 0
            return
        if people_count > 0:
            self._after_hits += 1
        else:
            self._after_hits = 0
        if self._after_hits >= rule.min_consecutive_hits:
            self._after_cooldown_until = now + rule.cooldown_s
            self._after_hits = 0
            self._emit_event(
                "after_hours_intrusion",
                "Pessoa detectada fora do horario",
                f"{people_count} pessoa(s) dentro da area monitorada fora do horario permitido.",
                people_count,
            )

    def _apply_group_loitering(self, people_count: int) -> None:
        rule = self.camera.analytics.group_loitering
        if not rule.enabled:
            self._group_started_at = None
            return
        now = time.time()
        if now < self._group_cooldown_until:
            return
        if people_count >= rule.min_people:
            if self._group_started_at is None:
                self._group_started_at = now
            if now - self._group_started_at >= rule.dwell_s:
                self._group_cooldown_until = now + rule.cooldown_s
                self._group_started_at = None
                self._emit_event(
                    "group_loitering",
                    "Grupo parado por tempo excessivo",
                    f"{people_count} pessoa(s) permaneceram na area por mais de {rule.dwell_s}s.",
                    people_count,
                )
        else:
            self._group_started_at = None


class AnalysisManager:
    def __init__(self, runtime: RuntimeManager, detector: PeopleDetector, store: JsonStore, fps: float) -> None:
        self.runtime = runtime
        self.detector = detector
        self.store = store
        self.fps = fps
        self._tasks: dict[str, CameraAnalysisTask] = {}

    async def sync_camera(self, camera: Camera) -> None:
        existing = self._tasks.get(camera.id)
        if not camera.analytics.enabled:
            if existing:
                await existing.stop()
                self._tasks.pop(camera.id, None)
            return
        if existing:
            existing.camera = camera
            return
        task = CameraAnalysisTask(camera, self.runtime, self.detector, self.store, self.fps)
        self._tasks[camera.id] = task
        task.start()

    async def sync_all(self, cameras: list[Camera]) -> None:
        enabled = {camera.id for camera in cameras if camera.analytics.enabled}
        for camera in cameras:
            await self.sync_camera(camera)
        for camera_id in list(set(self._tasks) - enabled):
            await self._tasks[camera_id].stop()
            self._tasks.pop(camera_id, None)

    def detections_for(self, camera_id: str) -> list[Detection]:
        task = self._tasks.get(camera_id)
        return task.latest_detections if task else []

    def roi_count_for(self, camera_id: str) -> int:
        task = self._tasks.get(camera_id)
        return task.latest_roi_count if task else 0

    async def stop_all(self) -> None:
        for task in list(self._tasks.values()):
            await task.stop()
        self._tasks.clear()


def draw_analytics_overlay(frame, camera: Camera, detections: list[Detection], roi_count: int) -> None:
    height, width = frame.shape[:2]
    roi = relative_polygon_to_pixels(camera.analytics.roi, width, height)
    draw_polygon(frame, roi, (22, 163, 74))
    for detection in detections:
        x1, y1, x2, y2 = detection.bbox
        inside = bbox_inside_roi(detection.bbox, roi)
        color = (32, 120, 255) if inside else (150, 150, 150)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"{detection.confidence:.2f}",
            (x1, max(20, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        frame,
        f"ROI: {roi_count} pessoa(s)",
        (16, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

