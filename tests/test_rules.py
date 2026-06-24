from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analytics import time_in_window
from app.geometry import bbox_inside_roi, relative_polygon_to_pixels
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

    def test_store_revalidates_nested_analytics_patch(self):
        with TemporaryDirectory() as temp_dir:
            store = JsonStore(Path(temp_dir))
            camera = store.create_camera(CameraCreate(name="Demo", rtsp_url="rtsp://example/stream"))
            patched = store.patch_camera(
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


if __name__ == "__main__":
    unittest.main()

