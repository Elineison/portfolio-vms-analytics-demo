from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from app.analytics import CameraAnalysisTask, time_in_window
from app.auth import is_admin_email
from app.geometry import bbox_center_distance, bbox_inside_roi, bbox_iou, relative_polygon_to_pixels
from app.schemas import AnalyticsConfig, Camera, CameraCreate, CameraPatch, Detection, Event, GroupLoiteringRule, Point
from app.store import JsonStore


class RuleTests(unittest.TestCase):
    def test_time_window_can_cross_midnight(self):
        self.assertTrue(time_in_window(time(23, 0), time(18, 0), time(6, 0)))
        self.assertTrue(time_in_window(time(5, 30), time(18, 0), time(6, 0)))
        self.assertFalse(time_in_window(time(12, 0), time(18, 0), time(6, 0)))

    def test_relative_roi_filters_bbox_center(self):
        roi = relative_polygon_to_pixels(
            [
                Point(x=0.25, y=0.25),
                Point(x=0.75, y=0.25),
                Point(x=0.75, y=0.75),
                Point(x=0.25, y=0.75),
            ],
            width=400,
            height=200,
        )
        self.assertTrue(bbox_inside_roi((150, 70, 210, 130), roi))
        self.assertFalse(bbox_inside_roi((10, 10, 40, 40), roi))

    def test_roi_accepts_many_points(self):
        cfg = AnalyticsConfig(
            enabled=True,
            roi=[
                Point(x=0.10, y=0.20),
                Point(x=0.35, y=0.10),
                Point(x=0.65, y=0.12),
                Point(x=0.90, y=0.30),
                Point(x=0.82, y=0.82),
                Point(x=0.20, y=0.88),
            ],
        )
        self.assertEqual(len(cfg.roi), 6)

    def test_group_rule_accepts_one_person(self):
        rule = GroupLoiteringRule(enabled=True, min_people=1, dwell_s=5)
        self.assertEqual(rule.min_people, 1)

    def test_face_snapshot_capture_is_configurable(self):
        default_config = AnalyticsConfig()
        enabled_config = AnalyticsConfig(capture_face_snapshots=True)
        self.assertFalse(default_config.capture_face_snapshots)
        self.assertTrue(enabled_config.capture_face_snapshots)

    def test_event_accepts_face_snapshot_urls(self):
        event = Event(
            id="evt",
            user_id="user",
            camera_id="cam",
            camera_name="Camera",
            type="group_loitering",
            title="Alerta",
            message="Evento confirmado",
            started_at="2026-06-25T00:00:00+00:00",
            face_snapshot_files=["evt_pessoa_1.jpg"],
            face_snapshot_urls=["/api/events/evt/faces/0"],
        )
        self.assertEqual(event.face_snapshot_files[0], "evt_pessoa_1.jpg")
        self.assertEqual(event.face_snapshot_urls[0], "/api/events/evt/faces/0")

    def test_face_snapshot_ignores_crop_without_detected_face(self):
        with TemporaryDirectory() as temp_dir:
            camera = Camera(
                id="cam",
                user_id="user",
                name="Camera",
                rtsp_url="rtsp://example/stream",
                analytics=AnalyticsConfig(enabled=True, capture_face_snapshots=True),
            )
            task = CameraAnalysisTask(
                camera=camera,
                runtime=None,
                detector=None,
                store=None,
                mailer=None,
                evidence_dir=Path(temp_dir),
                fps=2,
            )
            frame = np.zeros((200, 200, 3), dtype=np.uint8)
            task.latest_detections = [
                Detection(
                    bbox=(50, 40, 130, 180),
                    confidence=0.92,
                    track_id=1,
                    age_s=6,
                    inside_roi=True,
                )
            ]
            files = task._save_face_snapshots("evento", frame)
            self.assertEqual(files, [])

    def test_face_snapshot_saves_best_detected_face_crop(self):
        class FakeFaceCascade:
            def detectMultiScale(self, *_args, **_kwargs):
                return np.array([[12, 10, 42, 42]])

        with TemporaryDirectory() as temp_dir:
            camera = Camera(
                id="cam",
                user_id="user",
                name="Camera",
                rtsp_url="rtsp://example/stream",
                analytics=AnalyticsConfig(enabled=True, capture_face_snapshots=True),
            )
            task = CameraAnalysisTask(
                camera=camera,
                runtime=None,
                detector=None,
                store=None,
                mailer=None,
                evidence_dir=Path(temp_dir),
                fps=2,
            )
            task._face_cascades = [FakeFaceCascade()]
            frame = np.zeros((200, 200, 3), dtype=np.uint8)
            detection = Detection(
                bbox=(50, 40, 130, 180),
                confidence=0.92,
                track_id=1,
                age_s=6,
                inside_roi=True,
            )
            task.latest_detections = [detection]
            crop, score = task._extract_best_face_crop(frame, detection)
            self.assertIsNotNone(crop)
            self.assertGreater(score, 0)
            task.latest_detections = [detection]
            files = task._save_face_snapshots("evento", frame)
            self.assertEqual(len(files), 1)
            self.assertTrue((Path(temp_dir) / files[0]).exists())

    def test_tracking_geometry_scores(self):
        self.assertGreater(bbox_iou((10, 10, 80, 100), (20, 20, 90, 110)), 0.4)
        self.assertLess(bbox_iou((10, 10, 40, 40), (200, 200, 240, 240)), 0.01)
        self.assertLess(bbox_center_distance((10, 10, 50, 50), (14, 14, 54, 54)), 8)

    def test_store_revalidates_nested_analytics_patch(self):
        with TemporaryDirectory() as temp_dir:
            store = JsonStore(Path(temp_dir))
            user = store.upsert_google_user("demo@example.com", name="Demo")
            camera = store.create_camera(user.id, CameraCreate(name="Demo", rtsp_url="rtsp://example/stream"))
            patched = store.patch_camera(
                user.id,
                camera.id,
                CameraPatch(
                    analytics=AnalyticsConfig(
                        enabled=True,
                        roi=[
                            Point(x=0.1, y=0.1),
                            Point(x=0.9, y=0.1),
                            Point(x=0.9, y=0.9),
                        ],
                    )
                ),
            )
            self.assertIsNotNone(patched)
            self.assertTrue(patched.analytics.enabled)
            self.assertEqual(len(patched.analytics.roi), 3)

    def test_store_can_reset_trial_by_email(self):
        with TemporaryDirectory() as temp_dir:
            store = JsonStore(Path(temp_dir))
            user = store.upsert_google_user("demo@example.com", name="Demo")
            reset = store.reset_user_trial_by_email(user.email, days=7)
            self.assertIsNotNone(reset)
            self.assertEqual(reset.email, user.email)
            self.assertEqual(reset.trial_extension_days, 7)

    def test_store_can_delete_event_by_user(self):
        with TemporaryDirectory() as temp_dir:
            store = JsonStore(Path(temp_dir))
            user = store.upsert_google_user("demo@example.com", name="Demo")
            event = Event(
                id="evt",
                user_id=user.id,
                camera_id="cam",
                camera_name="Camera",
                type="group_loitering",
                title="Alerta",
                message="Evento confirmado",
                started_at="2026-06-25T00:00:00+00:00",
            )
            store.add_event(event)
            deleted = store.delete_event(user.id, event.id)
            self.assertIsNotNone(deleted)
            self.assertIsNone(store.get_event(user.id, event.id))

    def test_admin_email_helper_defaults_to_false(self):
        self.assertFalse(is_admin_email("demo@example.com"))


if __name__ == "__main__":
    unittest.main()
