from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2

from app.detectors import PeopleDetector
from app.geometry import bbox_center_distance, bbox_diag, bbox_inside_roi, bbox_iou, draw_polygon, relative_polygon_to_pixels
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


@dataclass
class PersonTrack:
    id: int
    bbox: tuple[int, int, int, int]
    confidence: float
    first_seen_s: float
    last_seen_s: float
    missed: int = 0
    hits: int = 1
    inside_roi: bool = False
    best_face_crop: object | None = None
    best_face_score: float = 0.0
    best_face_seen_s: float = 0.0


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
        self._tracks: dict[int, PersonTrack] = {}
        self._next_track_id = 1
        self._face_cascade = self._load_face_cascade()

    def _load_face_cascade(self):
        try:
            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(str(cascade_path))
            return None if cascade.empty() else cascade
        except Exception:
            return None

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
        self.latest_detections = self._update_tracks(detections, roi, frame)
        self.latest_total_count = len(detections)
        self.latest_roi_count = len(roi_detections)
        self._apply_after_hours(len(roi_detections), frame)
        self._apply_group_loitering(len(roi_detections), frame)

    def _update_tracks(self, detections: list[Detection], roi: list[tuple[int, int]], frame) -> list[Detection]:
        now = time.time()
        unmatched_track_ids = set(self._tracks)
        tracked: list[Detection] = []

        for detection in detections:
            best_id: int | None = None
            best_score = 0.0
            for track_id in list(unmatched_track_ids):
                track = self._tracks[track_id]
                iou = bbox_iou(track.bbox, detection.bbox)
                distance = bbox_center_distance(track.bbox, detection.bbox)
                max_distance = max(42.0, bbox_diag(track.bbox) * 0.75)
                distance_score = max(0.0, 1.0 - (distance / max_distance))
                score = max(iou, distance_score * 0.72)
                if score > best_score:
                    best_id = track_id
                    best_score = score

            inside = bbox_inside_roi(detection.bbox, roi)
            if best_id is not None and best_score >= 0.22:
                track = self._tracks[best_id]
                track.bbox = detection.bbox
                track.confidence = detection.confidence
                track.last_seen_s = now
                track.missed = 0
                track.hits += 1
                track.inside_roi = inside
                unmatched_track_ids.discard(best_id)
            else:
                best_id = self._next_track_id
                self._next_track_id += 1
                self._tracks[best_id] = PersonTrack(
                    id=best_id,
                    bbox=detection.bbox,
                    confidence=detection.confidence,
                    first_seen_s=now,
                    last_seen_s=now,
                    inside_roi=inside,
                )

            track = self._tracks[best_id]
            if inside and self.camera.analytics.capture_face_snapshots:
                self._update_best_face_crop(track, detection, frame)
            tracked.append(detection.model_copy(update={
                "track_id": best_id,
                "first_seen_s": track.first_seen_s,
                "last_seen_s": track.last_seen_s,
                "age_s": max(0.0, now - track.first_seen_s),
                "inside_roi": inside,
            }))

        for track_id in list(unmatched_track_ids):
            track = self._tracks[track_id]
            track.missed += 1
            if now - track.last_seen_s > 3.0 or track.missed > 8:
                self._tracks.pop(track_id, None)

        return tracked

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

    def _person_crop_bbox(self, detection: Detection, width: int, height: int) -> tuple[int, int, int, int] | None:
        x1, y1, x2, y2 = detection.bbox
        box_width = max(1, x2 - x1)
        box_height = max(1, y2 - y1)
        crop_x1 = max(0, x1 + int(box_width * 0.15))
        crop_x2 = min(width, x2 - int(box_width * 0.15))
        crop_y1 = max(0, y1 - int(box_height * 0.03))
        crop_y2 = min(height, y1 + int(box_height * 0.42))
        if crop_x2 - crop_x1 < 24 or crop_y2 - crop_y1 < 24:
            return None
        return crop_x1, crop_y1, crop_x2, crop_y2

    def _face_crop_score(self, crop, face_found: bool, face_area_ratio: float, detection_confidence: float) -> float:
        if crop is None or crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharpness = min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 500.0)
        mean_light = float(gray.mean())
        exposure = max(0.0, 1.0 - abs(mean_light - 118.0) / 118.0)
        size_score = min(1.0, float(crop.shape[0] * crop.shape[1]) / (180.0 * 180.0))
        frontal_bonus = 2.5 if face_found else 0.0
        return frontal_bonus + sharpness + (exposure * 0.6) + size_score + face_area_ratio + (detection_confidence * 0.25)

    def _extract_best_face_crop(self, frame, detection: Detection):
        height, width = frame.shape[:2]
        crop_bbox = self._person_crop_bbox(detection, width, height)
        if crop_bbox is None:
            return None, 0.0
        x1, y1, x2, y2 = crop_bbox
        upper_crop = frame[y1:y2, x1:x2]
        if upper_crop.size == 0:
            return None, 0.0

        best_crop = upper_crop
        face_found = False
        face_area_ratio = 0.0
        if self._face_cascade is not None:
            gray = cv2.cvtColor(upper_crop, cv2.COLOR_BGR2GRAY)
            faces = self._face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.08,
                minNeighbors=4,
                minSize=(24, 24),
            )
            if faces is not None and len(faces) > 0:
                fx, fy, fw, fh = max(faces, key=lambda face: int(face[2]) * int(face[3]))
                margin_x = int(fw * 0.45)
                margin_top = int(fh * 0.45)
                margin_bottom = int(fh * 0.75)
                cx1 = max(0, int(fx) - margin_x)
                cy1 = max(0, int(fy) - margin_top)
                cx2 = min(upper_crop.shape[1], int(fx + fw) + margin_x)
                cy2 = min(upper_crop.shape[0], int(fy + fh) + margin_bottom)
                candidate = upper_crop[cy1:cy2, cx1:cx2]
                if candidate.size > 0:
                    best_crop = candidate
                    face_found = True
                    face_area_ratio = min(1.0, float(fw * fh) / float(max(1, upper_crop.shape[0] * upper_crop.shape[1])))

        score = self._face_crop_score(best_crop, face_found, face_area_ratio, detection.confidence)
        return best_crop.copy(), score

    def _update_best_face_crop(self, track: PersonTrack, detection: Detection, frame) -> None:
        crop, score = self._extract_best_face_crop(frame, detection)
        if crop is None:
            return
        if score > track.best_face_score:
            track.best_face_crop = crop
            track.best_face_score = score
            track.best_face_seen_s = time.time()

    def _save_face_snapshots(self, event_id: str, frame) -> list[str]:
        if not self.camera.analytics.capture_face_snapshots:
            return []
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            candidates = [
                detection
                for detection in self.latest_detections
                if detection.inside_roi
            ]
            candidates.sort(key=lambda detection: (detection.confidence, detection.age_s), reverse=True)
            saved: list[str] = []
            used_track_ids: set[int] = set()
            for index, detection in enumerate(candidates[:4]):
                track = self._tracks.get(detection.track_id or -1)
                if track and track.id in used_track_ids:
                    continue
                crop = track.best_face_crop if track and track.best_face_crop is not None else None
                if crop is None:
                    crop, _ = self._extract_best_face_crop(frame, detection)
                if crop is None:
                    continue
                if crop.size == 0:
                    continue
                if track:
                    used_track_ids.add(track.id)
                filename = f"{event_id}_pessoa_{index + 1}.jpg"
                path = self.evidence_dir / filename
                ok = cv2.imwrite(str(path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                if ok:
                    saved.append(filename)
            return saved
        except Exception:
            return []

    def _emit_event(self, event_type: str, title: str, message: str, people_count: int, frame) -> None:
        event_id = str(uuid.uuid4())
        snapshot_file = self._save_snapshot(event_id, frame)
        face_snapshot_files = self._save_face_snapshots(event_id, frame)
        owner = self.store.get_user(self.camera.user_id)
        recipient = owner.email if owner else None
        event = Event(
            id=event_id,
            user_id=self.camera.user_id,
            camera_id=self.camera.id,
            camera_name=self.camera.name,
            type=event_type,
            title=title,
            message=message,
            started_at=utc_now_iso(),
            people_count=people_count,
            snapshot_file=snapshot_file,
            snapshot_url=f"/api/events/{event_id}/snapshot" if snapshot_file else None,
            face_snapshot_files=face_snapshot_files,
            face_snapshot_urls=[
                f"/api/events/{event_id}/faces/{index}"
                for index, _ in enumerate(face_snapshot_files)
            ],
            notification_email=recipient,
            notification_status="not_configured",
        )
        if recipient:
            try:
                snapshot_path = self.evidence_dir / snapshot_file if snapshot_file else None
                face_paths = [self.evidence_dir / filename for filename in face_snapshot_files]
                event.notification_status = self.mailer.send_event(event, recipient, snapshot_path, face_paths)
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
            "tracks": [
                {
                    "id": det.track_id,
                    "bbox": det.bbox,
                    "confidence": round(det.confidence, 3),
                    "age_s": round(det.age_s, 1),
                    "inside_roi": det.inside_roi,
                }
                for det in self.latest_detections
            ],
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


def draw_analytics_overlay(
    frame,
    camera: Camera,
    detections: list[Detection],
    roi_count: int,
    show_roi: bool = True,
) -> None:
    height, width = frame.shape[:2]
    roi = relative_polygon_to_pixels(camera.analytics.roi, width, height)
    if show_roi and camera.analytics.enabled and len(roi) >= 3:
        draw_polygon(frame, roi, (208, 88, 255))
    for detection in detections:
        x1, y1, x2, y2 = detection.bbox
        inside = detection.inside_roi if detection.track_id is not None else bbox_inside_roi(detection.bbox, roi)
        color = (255, 110, 48) if inside else (148, 163, 184)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    lines = [
        f"{roi_count} pessoa(s) na area",
        f"Analise {'ativa' if camera.analytics.enabled else 'pausada'}",
    ]
    tracked = [det for det in detections if det.track_id is not None]
    if tracked:
        track_line = "Tracks " + " | ".join(
            f"#{det.track_id} {det.age_s:.0f}s" for det in tracked[:4]
        )
        lines.append(track_line)
    y = 28
    for line in lines:
        (tw, th), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        cv2.rectangle(frame, (12, y - th - 8), (24 + tw, y + 6), (15, 23, 42), -1)
        cv2.putText(frame, line, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        y += 32
