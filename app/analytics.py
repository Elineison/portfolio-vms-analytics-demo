from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2

from app.detectors import PeopleDetector
from app.geometry import bbox_inside_roi, draw_polygon, relative_polygon_to_pixels
from app.mailer import EvidenceMailer
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
        mailer: EvidenceMailer,
        evidence_dir: Path,
        fps: float,
    ) -> None:
        self.camera = camera
        self.runtime = runtime
        self.detector = detector
        self.store = store
        self.mailer = mailer
        self.evidence_dir = evidence_dir
        self.fps = max(0.2, fps)
        self.task: asyncio.Task | None = None
        self.latest_detections: list[Detection] = []
        self.latest_roi_count = 0
        self.latest_total_count = 0
        self.latest_infer_ms = 0.0
        self.latest_analyzed_at: float | None = None
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
        while True:
            interval = 1.0 / max(0.2, float(self.camera.analytics.analysis_fps or self.fps))
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
        t0 = time.perf_counter()
        detections = await asyncio.to_thread(
            self.detector.detect,
            frame,
            self.camera.analytics.confidence_threshold,
            self.camera.analytics.min_box_area_ratio,
        )
        self.latest_infer_ms = (time.perf_counter() - t0) * 1000.0
        self.latest_analyzed_at = time.time()
        roi_detections = [det for det in detections if bbox_inside_roi(det.bbox, roi)]
        self.latest_detections = detections
        self.latest_total_count = len(detections)
        self.latest_roi_count = len(roi_detections)
        self._apply_after_hours(len(roi_detections), frame)
        self._apply_group_loitering(len(roi_detections), frame)

    def _save_snapshot(self, event_id: str, frame) -> str | None:
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            annotated = frame.copy()
            draw_analytics_overlay(annotated, self.camera, self.latest_detections, self.latest_roi_count)
            filename = f"{event_id}.jpg"
            path = self.evidence_dir / filename
            ok = cv2.imwrite(str(path), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            return filename if ok else None
        except Exception:
            return None

    def _emit_event(self, event_type: str, title: str, message: str, people_count: int, frame) -> None:
        event_id = str(uuid.uuid4())
        snapshot_file = self._save_snapshot(event_id, frame)
        recipient = (self.camera.analytics.notification_email or "").strip() or None
        event = Event(
            id=event_id,
            camera_id=self.camera.id,
            camera_name=self.camera.name,
            type=event_type,
            title=title,
            message=message,
            started_at=utc_now_iso(),
            people_count=people_count,
            snapshot_file=snapshot_file,
            snapshot_url=f"/api/events/{event_id}/snapshot" if snapshot_file else None,
            notification_email=recipient,
            notification_status="not_configured",
        )
        if recipient:
            try:
                snapshot_path = self.evidence_dir / snapshot_file if snapshot_file else None
                event.notification_status = self.mailer.send_event(event, recipient, snapshot_path)
            except Exception as exc:
                event.notification_status = f"failed:{type(exc).__name__}"
        self.store.add_event(event)

    def _apply_after_hours(self, people_count: int, frame) -> None:
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
                frame,
            )

    def _apply_group_loitering(self, people_count: int, frame) -> None:
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
                    frame,
                )
        else:
            self._group_started_at = None

    def status(self) -> dict:
        now = time.time()
        group_rule = self.camera.analytics.group_loitering
        group_elapsed = 0.0
        if self._group_started_at is not None:
            group_elapsed = max(0.0, now - self._group_started_at)
        group_target = float(group_rule.dwell_s or 1)
        after_rule = self.camera.analytics.after_hours
        return {
            "state": "RUNNING",
            "total_people": self.latest_total_count,
            "roi_people": self.latest_roi_count,
            "last_infer_ms": round(self.latest_infer_ms, 1),
            "last_analyzed_age_s": None if self.latest_analyzed_at is None else round(max(0.0, now - self.latest_analyzed_at), 1),
            "after_hours": {
                "enabled": after_rule.enabled,
                "active_window_now": time_in_window(datetime.now().time(), after_rule.start, after_rule.end),
                "hits": self._after_hits,
                "required_hits": after_rule.min_consecutive_hits,
                "cooldown_remaining_s": round(max(0.0, self._after_cooldown_until - now), 1),
            },
            "group_loitering": {
                "enabled": group_rule.enabled,
                "min_people": group_rule.min_people,
                "dwell_s": group_rule.dwell_s,
                "elapsed_s": round(group_elapsed, 1),
                "progress": round(min(1.0, group_elapsed / group_target), 3),
                "cooldown_remaining_s": round(max(0.0, self._group_cooldown_until - now), 1),
            },
        }


class AnalysisManager:
    def __init__(
        self,
        runtime: RuntimeManager,
        detector: PeopleDetector,
        store: JsonStore,
        mailer: EvidenceMailer,
        evidence_dir: Path,
        fps: float,
    ) -> None:
        self.runtime = runtime
        self.detector = detector
        self.store = store
        self.mailer = mailer
        self.evidence_dir = evidence_dir
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
        task = CameraAnalysisTask(camera, self.runtime, self.detector, self.store, self.mailer, self.evidence_dir, self.fps)
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

    def status_for(self, camera_id: str) -> dict:
        task = self._tasks.get(camera_id)
        if not task:
            return {"state": "STOPPED"}
        return task.status()

    async def stop_all(self) -> None:
        for task in list(self._tasks.values()):
            await task.stop()
        self._tasks.clear()


def draw_analytics_overlay(frame, camera: Camera, detections: list[Detection], roi_count: int) -> None:
    height, width = frame.shape[:2]
    roi = relative_polygon_to_pixels(camera.analytics.roi, width, height)
    draw_polygon(frame, roi, (20, 184, 166))
    for detection in detections:
        x1, y1, x2, y2 = detection.bbox
        inside = bbox_inside_roi(detection.bbox, roi)
        color = (28, 126, 255) if inside else (148, 163, 184)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"ROI {detection.confidence:.2f}" if inside else f"fora {detection.confidence:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - th - 10)), (x1 + tw + 8, y1), color, -1)
        cv2.putText(
            frame,
            label,
            (x1 + 4, max(14, y1 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    lines = [
        f"ROI: {roi_count} pessoa(s)",
        f"Analise: {'ON' if camera.analytics.enabled else 'OFF'}",
    ]
    y = 28
    for line in lines:
        (tw, th), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        cv2.rectangle(frame, (12, y - th - 8), (24 + tw, y + 6), (15, 23, 42), -1)
        cv2.putText(frame, line, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        y += 32
