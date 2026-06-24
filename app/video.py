from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class FrameSnapshot:
    frame: np.ndarray | None
    sequence: int
    captured_at: float


class RtspCameraSession:
    def __init__(self, camera_id: str, rtsp_url: str) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.state = "STOPPED"
        self.last_error: str | None = None
        self.frames_in = 0
        self.width: int | None = None
        self.height: int | None = None
        self._lock = threading.RLock()
        self._frame: np.ndarray | None = None
        self._sequence = 0
        self._captured_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"rtsp-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.state = "STOPPED"

    def snapshot(self) -> FrameSnapshot:
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            return FrameSnapshot(frame=frame, sequence=self._sequence, captured_at=self._captured_at)

    def update_url(self, rtsp_url: str) -> None:
        if rtsp_url == self.rtsp_url:
            return
        self.stop()
        self.rtsp_url = rtsp_url
        self.start()

    def _run(self) -> None:
        backoff_s = 1.0
        while not self._stop.is_set():
            cap = None
            try:
                self.state = "CONNECTING"
                cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                if not cap.isOpened():
                    raise RuntimeError("rtsp_open_failed")
                self.state = "RUNNING"
                self.last_error = None
                backoff_s = 1.0
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise RuntimeError("rtsp_read_failed")
                    height, width = frame.shape[:2]
                    with self._lock:
                        self._frame = frame
                        self._sequence += 1
                        self._captured_at = time.time()
                        self.frames_in += 1
                        self.width = width
                        self.height = height
            except Exception as exc:
                self.state = "ERROR"
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._stop.wait(backoff_s)
                backoff_s = min(backoff_s * 1.8, 15.0)
            finally:
                if cap is not None:
                    cap.release()

