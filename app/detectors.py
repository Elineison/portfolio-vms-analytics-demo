from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import cv2
import numpy as np

from app.schemas import Detection

logger = logging.getLogger("portfolio_vms.detectors")


@dataclass
class DetectorStats:
    backend: str
    available: bool
    last_error: str | None = None


class PeopleDetector:
    def __init__(self) -> None:
        self.model_name = os.getenv("VMS_YOLO_MODEL", "yolov8n.pt")
        self.confidence = float(os.getenv("VMS_YOLO_CONF", "0.35"))
        self._model = None
        self.stats = DetectorStats(backend="none", available=False)
        self._load_yolo()

    def _load_yolo(self) -> None:
        try:
            from ultralytics import YOLO

            self._model = YOLO(self.model_name)
            self.stats = DetectorStats(backend="ultralytics", available=True)
            logger.info("people_detector_yolo_loaded model=%s", self.model_name)
        except Exception as exc:
            self._model = None
            self.stats = DetectorStats(
                backend="ultralytics",
                available=False,
                last_error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning("people_detector_yolo_unavailable %s", self.stats.last_error)

    def detect(
        self,
        frame: np.ndarray,
        confidence: float | None = None,
        min_area_ratio: float = 0.0,
    ) -> list[Detection]:
        if self._model is None:
            return []

        height, width = frame.shape[:2]
        frame_area = float(max(1, width * height))
        min_area = max(0.0, float(min_area_ratio)) * frame_area
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._model.predict(
            source=rgb,
            verbose=False,
            conf=float(confidence or self.confidence),
            classes=[0],
            max_det=50,
        )
        detections: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for idx in range(len(boxes)):
                if int(boxes.cls[idx]) != 0:
                    continue
                x1, y1, x2, y2 = [int(v) for v in boxes.xyxy[idx].tolist()]
                x1 = max(0, min(width - 1, x1))
                x2 = max(0, min(width - 1, x2))
                y1 = max(0, min(height - 1, y1))
                y2 = max(0, min(height - 1, y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                if float((x2 - x1) * (y2 - y1)) < min_area:
                    continue
                detections.append(Detection(
                    bbox=(x1, y1, x2, y2),
                    confidence=float(boxes.conf[idx]),
                ))
        return detections
