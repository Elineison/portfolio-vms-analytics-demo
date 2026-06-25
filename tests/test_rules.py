from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analytics import time_in_window
from app.geometry import bbox_center_distance, bbox_inside_roi, bbox_iou, relative_polygon_to_pixels
from app.schemas import AnalyticsConfig, CameraCreate, CameraPatch, Point
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


if __name__ == "__main__":
    unittest.main()
