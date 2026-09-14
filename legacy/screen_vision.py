"""本地屏幕采集与轻量视觉分析。

依赖是可选的：安装 mss + opencv-python + numpy 后启用；否则返回清晰的能力提示。
"""
from __future__ import annotations
import base64, io
from dataclasses import dataclass

try:
    import mss
    import numpy as np
    import cv2
except ImportError:
    mss = np = cv2 = None

@dataclass
class VisionResult:
    ok: bool
    message: str
    image_png: bytes | None = None
    width: int = 0
    height: int = 0
    mean_bgr: tuple[float, float, float] | None = None

class ScreenVision:
    def __init__(self):
        self.enabled = all((mss, np, cv2))
        self.sct = mss.mss() if self.enabled else None

    def status(self) -> str:
        return "视觉模块已就绪（mss + OpenCV + NumPy）" if self.enabled else "未安装视觉依赖：pip install -r requirements.txt"

    def capture(self, monitor: int = 1) -> VisionResult:
        if not self.enabled:
            return VisionResult(False, self.status())
        monitors = self.sct.monitors
        if monitor < 1 or monitor >= len(monitors):
            monitor = 1
        shot = self.sct.grab(monitors[monitor])
        frame = np.asarray(shot)[:, :, :3]
        ok, encoded = cv2.imencode('.png', frame)
        if not ok:
            return VisionResult(False, "屏幕截图编码失败")
        mean = tuple(float(x) for x in frame.mean(axis=(0, 1)))
        return VisionResult(True, f"已捕获屏幕 {frame.shape[1]}×{frame.shape[0]}", encoded.tobytes(), frame.shape[1], frame.shape[0], mean)

    @staticmethod
    def as_data_url(png: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
