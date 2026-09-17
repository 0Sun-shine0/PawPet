"""屏幕捕获。

三个关键点：

1. mss 的实例不能跨线程共用，这里按线程各存一份。
2. 送给视觉模型的图会缩小（省 token 也更快），所以必须记住缩放比例，
   否则模型报的坐标会全部偏掉 —— 这是这类功能最常见的 bug。
3. 送给模型用 JPEG（小），给界面预览用 PNG（清晰），都从同一帧编码。
"""

from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field

import numpy as np

try:
    import mss as _mss

    # mss 10.2 起 mss.mss 被标记为废弃，优先用新名字
    _MSS_FACTORY = getattr(_mss, "MSS", None) or getattr(_mss, "mss")
    MSS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MSS_FACTORY = None
    MSS_AVAILABLE = False

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:  # pragma: no cover
    cv2 = None
    CV2_AVAILABLE = False


# 送给模型的图片最长边。太大会烧 token，太小会看不清界面文字。
MODEL_MAX_EDGE = 1400
JPEG_QUALITY = 82

_thread_local = threading.local()


def _capture_device():
    """每个线程各持一个 mss 实例。"""
    device = getattr(_thread_local, "device", None)
    if device is None:
        if not MSS_AVAILABLE:
            return None
        device = _MSS_FACTORY()
        _thread_local.device = device
    return device


@dataclass
class Shot:
    """一次屏幕捕获的结果。"""

    ok: bool
    message: str = ""
    monitor: int = 1
    width: int = 0                    # 真实像素宽
    height: int = 0                   # 真实像素高
    origin_x: int = 0                 # 该显示器在虚拟桌面里的左上角
    origin_y: int = 0
    frame: np.ndarray | None = None   # BGR 原图
    model_width: int = 0              # 发给模型的图宽
    model_height: int = 0
    scale_x: float = 1.0              # 真实宽 / 模型宽
    scale_y: float = 1.0
    preview_png: bytes = b""
    model_jpeg: bytes = b""
    monitors: list = field(default_factory=list)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """把模型给的坐标换算回真实屏幕坐标。"""
        return (
            int(round(self.origin_x + float(x) * self.scale_x)),
            int(round(self.origin_y + float(y) * self.scale_y)),
        )

    def to_model(self, x: float, y: float) -> tuple[int, int]:
        """把真实屏幕坐标换算到模型看到的坐标系。"""
        return (
            int(round((float(x) - self.origin_x) / max(self.scale_x, 1e-6))),
            int(round((float(y) - self.origin_y) / max(self.scale_y, 1e-6))),
        )

    def data_url(self) -> str:
        if not self.model_jpeg:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(self.model_jpeg).decode("ascii")


class ScreenCapture:
    """屏幕采集器。所有方法都可以从任意线程调用。"""

    @property
    def available(self) -> bool:
        return MSS_AVAILABLE and CV2_AVAILABLE

    def status(self) -> str:
        if not MSS_AVAILABLE:
            return "屏幕捕获不可用：缺少 mss"
        if not CV2_AVAILABLE:
            return "屏幕捕获不可用：缺少 opencv-python"
        return "屏幕捕获就绪"

    # ------------------------------------------------------------------ 枚举
    def monitors(self) -> list[dict]:
        device = _capture_device()
        if device is None:
            return []
        try:
            return [dict(m) for m in device.monitors]
        except Exception:  # noqa: BLE001
            return []

    def describe_monitors(self) -> str:
        items = self.monitors()
        if not items:
            return "无法枚举显示器"
        lines = []
        for index, mon in enumerate(items):
            if index == 0:
                lines.append(f"[0] 所有屏幕合计 {mon['width']}x{mon['height']}")
            else:
                lines.append(
                    f"[{index}] {mon['width']}x{mon['height']} "
                    f"位置 ({mon['left']}, {mon['top']})"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------ 抓取
    @staticmethod
    def _capture_hint(exc: Exception) -> str:
        """把 mss 的底层报错翻成用户能懂、能处理的一句话。

        `Windows graphics function failed (no error provided): BitBlt`
        这种原文对用户毫无意义 —— 他不知道这是什么、也不知道该干什么。
        而真实原因绝大多数时候只有一个：**屏幕当前看不到**
        （锁屏了、屏保起来了、或者正在切换用户）。
        """
        raw = str(exc)
        if "BitBlt" in raw or "graphics function failed" in raw:
            return ("现在看不到屏幕内容 —— 通常是屏幕锁上了、屏保起来了，"
                    "或者刚切过用户。解锁之后再让我看就行。")
        return f"截屏失败：{raw}"

    def grab(self, monitor: int = 1, max_edge: int = MODEL_MAX_EDGE,
             with_preview: bool = True) -> Shot:
        if not self.available:
            return Shot(False, self.status())

        device = _capture_device()
        if device is None:
            return Shot(False, self.status())

        monitors = self.monitors()
        if not monitors:
            return Shot(False, "没有检测到可捕获的屏幕")

        if monitor < 0 or monitor >= len(monitors):
            monitor = 1 if len(monitors) > 1 else 0
        target = monitors[monitor]

        try:
            raw = device.grab(target)
        except Exception as exc:  # noqa: BLE001
            # **抓不到就重试一次，然后说人话。**
            #
            # 实测（整套测试连跑时）：BitBlt 会在一段时间里 100% 失败，
            # 过后又自己好了。原因是桌面进入了抓不到的状态 ——
            # 屏保切进来、正在锁屏、或者会话在切换。Windows 不允许
            # 捕获锁屏后的安全桌面，这是系统行为不是我们的 bug。
            #
            # 而用户**天天**会遇到这个（离开工位、屏保起来），所以：
            #   1. 先重试一次 —— 瞬时抖动不该直接判死；
            #   2. 还不行就给一句人话，而不是把 mss 的
            #      「Windows graphics function failed (no error provided):
            #      BitBlt」原样甩给用户。那句话既看不懂、也猜不到怎么办。
            time.sleep(0.25)
            try:
                raw = device.grab(target)
            except Exception:  # noqa: BLE001
                return Shot(False, self._capture_hint(exc))

        frame = np.asarray(raw)[:, :, :3].copy()
        real_h, real_w = frame.shape[:2]

        # 按最长边等比缩小
        longest = max(real_w, real_h)
        ratio = min(1.0, float(max_edge) / float(longest)) if longest else 1.0
        if ratio < 1.0:
            model_frame = cv2.resize(
                frame,
                (max(1, int(round(real_w * ratio))), max(1, int(round(real_h * ratio)))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            model_frame = frame

        model_h, model_w = model_frame.shape[:2]

        ok_jpeg, jpeg_buf = cv2.imencode(
            ".jpg", model_frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
        )
        preview_png = b""
        if with_preview:
            ok_png, png_buf = cv2.imencode(".png", model_frame)
            if ok_png:
                preview_png = png_buf.tobytes()

        shot = Shot(
            ok=bool(ok_jpeg),
            message=(
                f"已捕获屏幕 {real_w}x{real_h}"
                + (f"（缩放为 {model_w}x{model_h} 发送）" if ratio < 1.0 else "")
            ),
            monitor=monitor,
            width=real_w,
            height=real_h,
            origin_x=int(target.get("left", 0)),
            origin_y=int(target.get("top", 0)),
            frame=frame,
            model_width=model_w,
            model_height=model_h,
            scale_x=real_w / max(model_w, 1),
            scale_y=real_h / max(model_h, 1),
            preview_png=preview_png,
            model_jpeg=jpeg_buf.tobytes() if ok_jpeg else b"",
            monitors=monitors,
        )
        return shot

    # ------------------------------------------------------------------ 分析
    @staticmethod
    def mean_color(shot: Shot) -> tuple[float, float, float] | None:
        if shot.frame is None:
            return None
        mean = shot.frame.mean(axis=(0, 1))
        return (float(mean[0]), float(mean[1]), float(mean[2]))


def screen_size() -> tuple[int, int]:
    """当前主屏分辨率（不依赖 pyautogui）。"""
    device = _capture_device()
    if device is None:
        return (0, 0)
    try:
        monitors = device.monitors
        if len(monitors) > 1:
            return (int(monitors[1]["width"]), int(monitors[1]["height"]))
        if monitors:
            return (int(monitors[0]["width"]), int(monitors[0]["height"]))
    except Exception:  # noqa: BLE001
        pass
    return (0, 0)


def downscale_png(png: bytes, max_edge: int = 520) -> bytes:
    """把预览图再缩小一点，避免 QML 里塞一张几 MB 的大图。"""
    if not png or not CV2_AVAILABLE:
        return png
    try:
        buffer = np.frombuffer(png, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is None:
            return png
        h, w = image.shape[:2]
        longest = max(w, h)
        if longest <= max_edge:
            ok, out = cv2.imencode(".png", image)
            return out.tobytes() if ok else png
        ratio = max_edge / float(longest)
        resized = cv2.resize(
            image, (max(1, int(w * ratio)), max(1, int(h * ratio))),
            interpolation=cv2.INTER_AREA,
        )
        ok, out = cv2.imencode(".png", resized)
        return out.tobytes() if ok else png
    except Exception:  # noqa: BLE001
        return png
