from __future__ import annotations

from collections.abc import Iterable

import cv2
import numpy as np

from app.schemas import Point


def relative_polygon_to_pixels(points: Iterable[Point], width: int, height: int) -> list[tuple[int, int]]:
    polygon: list[tuple[int, int]] = []
    for point in points:
        x = int(max(0, min(width - 1, round(point.x * width))))
        y = int(max(0, min(height - 1, round(point.y * height))))
        polygon.append((x, y))
    return polygon


def bbox_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def point_inside_polygon(point: tuple[float, float], polygon: list[tuple[int, int]]) -> bool:
    if len(polygon) < 3:
        return False
    contour = np.array(polygon, dtype=np.int32)
    return cv2.pointPolygonTest(contour, point, False) >= 0


def bbox_inside_roi(bbox: tuple[int, int, int, int], roi: list[tuple[int, int]]) -> bool:
    return point_inside_polygon(bbox_center(bbox), roi)


def draw_polygon(frame: np.ndarray, polygon: list[tuple[int, int]], color: tuple[int, int, int]) -> None:
    if len(polygon) < 3:
        return
    pts = np.array(polygon, dtype=np.int32)
    overlay = frame.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, 0.16, frame, 0.84, 0, frame)
    cv2.polylines(frame, [pts], True, color, 2)

