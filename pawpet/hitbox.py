"""Build a native hit region from the pet window's rendered alpha channel.

The pet is drawn from several QML items and Canvas nodes.  Repeating that
geometry in Python would make the mouse target drift whenever an animation,
edge pose, mood, or badge changes.  The rendered image is the single source
of truth, so the window mask is rebuilt from the pixels that are actually
visible.
"""

from __future__ import annotations

import math
import re
import time

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Slot
from PySide6.QtGui import QGuiApplication, QImage, QRegion


class PetHitbox(QObject):
    """Keep a transparent pet window's native input region in sync."""

    def __init__(
        self,
        window: QObject,
        parent: QObject | None = None,
        *,
        interval_ms: int = 200,
        alpha_threshold: int = 1,
        min_apply_interval_ms: int = 200,
    ) -> None:
        """interval_ms 决定多久抓一次窗口。

        **不需要高频率。** 这个是给「点击能不能打到宠物」用的，而宠物的
        逐帧动画（呼吸、眨眼、摇尾）对轮廓的影响只有一两像素 —— 眨眼睛
        更是完全不影响轮廓（只有眼睛在变）。真正会大改轮廓的都是**离散
        事件**：换宠物风格、贴边翻身、改缩放，而且它们各自有更直接的
        触发路径（宽高变化走 visibleChanged/widthChanged/heightChanged，
        见下面连的信号）。

        原来定的是 60ms（每秒 16 次），每次都要 `grabWindow()`：
        强制一次同步渲染 + GPU 回读。实测这个抓取把事件循环的 p95 延迟
        从 16ms 抬到 31ms —— **翻倍**，而且工作台开着的时候更明显。
        降到 200ms（每秒 5 次）后这笔开销降到三分之一，而点击区域最多
        滞后 200ms —— 用户感觉不到（睡一觉也不会有人点宠物边缘那一像素）。
        """
        super().__init__(parent)
        self._window = window
        self._alpha_threshold = max(1, min(255, int(alpha_threshold)))
        # 两次真正改窗口区域之间至少隔这么久（定时器通常已经限住了，
        # 这是给 schedule_refresh 那种突发调用兜底的）。
        self._min_apply_interval = max(0, int(min_apply_interval_ms)) / 1000.0
        self._last_region: QRegion | None = None
        self._last_size: tuple[int, int] | None = None
        self._last_apply_at = 0.0
        self._refresh_pending = False

        self._timer = QTimer(self)
        self._timer.setInterval(max(33, int(interval_ms)))
        self._timer.timeout.connect(self.refresh)

        for signal_name in ("visibleChanged", "widthChanged", "heightChanged"):
            signal = getattr(window, signal_name, None)
            if signal is not None:
                signal.connect(self.schedule_refresh)

        self.clear()
        self._timer.start()
        QTimer.singleShot(250, self.refresh)

    @Slot()
    def schedule_refresh(self) -> None:
        """Coalesce bursts of QML property changes into one refresh.

        **走的是「立即生效」那条路**：可见性、宽高这些是离散变化，
        坐标系或者可见状态都变了，不能等限频（那会让点击区域在
        好几百毫秒里停在旧状态上）。
        """
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self.refresh_now)

    @Slot()
    def refresh(self) -> None:
        """限频的常规刷新（定时器走这条）。"""
        self._refresh(force=False)

    def refresh_now(self) -> None:
        """立即重算并应用，跳过限频。

        窗口尺寸变化、重新显示这类**离散**变化必须走这条 —— 限频是为
        动画准备的手段，不该把离散变化也一起拖慢。
        """
        self._refresh(force=True)

    def _refresh(self, *, force: bool) -> None:
        """Capture the current frame and apply its non-transparent pixels."""
        self._refresh_pending = False
        window = self._window
        if window is None:
            return

        # **用户正在操作鼠标时，绝不动窗口区域。**
        #
        # `setMask()` 在 Windows 上是 `SetWindowRgn`：它会让窗口失效并
        # **重新评估鼠标捕获**。如果这个调用正好夹在一次点击的「按下」和
        # 「抬起」之间，那次点击的配对就断了 —— 表现就是「点了没反应」。
        # 拖动时更明显：`startSystemMove()` 进的是 Windows 自己的模态移动
        # 循环，中途改区域会让移动卡住或者把窗口丢在一个奇怪的位置。
        #
        # 所以按着鼠标的这段时间一律跳过（`force` 也不例外 —— 这条是
        # 正确性要求，不是性能优化）。
        if self.interaction_in_progress():
            return

        try:
            visible = bool(window.isVisible())
        except RuntimeError:
            return

        if not visible:
            self.clear()
            return

        try:
            image = window.grabWindow()
        except (RuntimeError, TypeError):
            self._restore_last_region()
            return

        if not isinstance(image, QImage) or image.isNull():
            self._restore_last_region()
            return

        region = self._region_from_image(image)
        if region.isEmpty():
            self._restore_last_region()
            return

        # **没必要时不动窗口区域。**
        #
        # 原来这里是无条件调 `setMask()` —— 于是每秒调 16 次
        # `SetWindowRgn`。在 Windows 上那意味着：让窗口失效、打断绘制、
        # **重新评估鼠标捕获**。实测把事件循环的 p95 延迟从 16ms 拉到
        # 32ms（翻倍），而且如上所说会打断点击。
        #
        # 常规路径（force=False）两条规则：
        #
        #   1. 区域和上次完全一样 → 跳过（宠物真的静止时开销为 0）
        #   2. 距上次真正设置不足 min_apply_interval → 跳过
        #
        # 第 2 条是必需的：宠物一直在呼吸和眨眼，轮廓几乎每帧都差一两
        # 像素，只比「相不相等」的话 12 次刷新里仍有 9 次会判成「变了」
        # （实测）。而**按包围盒给容差是错的** —— 换个宠物风格
        # （fox → mochi）包围盒几乎一样，容差会当成没变化，点击区域就
        # 停在上一只宠物的形状上（这个也实测到了）。
        #
        # 限频的代价是「动画中最多滞后 min_apply_interval」，
        # 而离散变化走 refresh_now() 不受这个限制。
        try:
            size = (int(window.width()), int(window.height()))
        except (RuntimeError, TypeError, ValueError):
            size = self._last_size

        now = time.monotonic()
        if not force:
            if self._last_region is not None and self._last_region == region:
                return
            if now - self._last_apply_at < self._min_apply_interval:
                return

        self._set_native_mask(region)
        self._last_region = QRegion(region)
        self._last_size = size
        self._last_apply_at = now

    @Slot()
    def clear(self) -> None:
        """Remove the native mask so a hidden/recreated window is unrestricted."""
        self._last_region = None
        self._last_size = None
        self._last_apply_at = 0.0
        self._clear_native_mask()

    def interaction_in_progress(self) -> bool:
        """鼠标正按着（点击中 / 拖动中）时为 True。

        抽成一个方法而不是内联判断，是为了**可测**：
        测试里覆盖它就能验证「按着鼠标时不改窗口区域」这条规则，
        不用真的去合成系统级鼠标事件（那需要一个有焦点、没被别的
        全屏窗口盖住的会话，CI 里不一定有）。
        """
        try:
            return QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton
        except (RuntimeError, AttributeError):
            return False

    def stop(self) -> None:
        """Stop refreshing and restore the normal rectangular window."""
        self._timer.stop()
        self.clear()

    def _clear_native_mask(self) -> None:
        self._set_native_mask(QRegion())

    def _restore_last_region(self) -> None:
        if self._last_region is not None and not self._last_region.isEmpty():
            self._set_native_mask(self._last_region)

    def _set_native_mask(self, region: QRegion) -> None:
        try:
            self._window.setMask(region)
        except (RuntimeError, TypeError):
            # QML may already have destroyed the window during shutdown.
            pass

    def _region_from_image(self, image: QImage) -> QRegion:
        """Convert non-transparent image spans into a logical-pixel region."""
        image_width = image.width()
        image_height = image.height()
        if image_width <= 0 or image_height <= 0:
            return QRegion()

        try:
            window_width = max(1, int(self._window.width()))
            window_height = max(1, int(self._window.height()))
        except (RuntimeError, TypeError, ValueError):
            window_width = image_width
            window_height = image_height

        scale_x = image_width / window_width
        scale_y = image_height / window_height
        threshold = self._alpha_threshold
        rects: list[QRect] = []

        active: dict[tuple[int, int], list[int]] = {}
        for image_y, alpha_row in self._alpha_rows(image):
            spans = self._alpha_spans(alpha_row, threshold)
            current: dict[tuple[int, int], list[int]] = {}
            for image_left, image_right in spans:
                left = max(0, math.floor(image_left / scale_x))
                right = min(
                    window_width - 1,
                    math.ceil((image_right + 1) / scale_x) - 1,
                )
                if right < left:
                    continue
                key = (left, right)
                previous = active.get(key)
                if previous is not None and previous[3] == image_y:
                    previous[3] = image_y + 1
                    current[key] = previous
                else:
                    current[key] = [left, right, image_y, image_y + 1]

            for key, previous in active.items():
                if key not in current:
                    rects.append(self._to_rect(previous, scale_y))
            active = current

        for previous in active.values():
            rects.append(self._to_rect(previous, scale_y))

        region = QRegion()
        for rect in rects:
            region = region.united(QRegion(rect))
        return region

    @staticmethod
    def _to_rect(previous: list[int], scale_y: float) -> QRect:
        return QRect(
            previous[0],
            max(0, math.floor(previous[2] / scale_y)),
            previous[1] - previous[0] + 1,
            max(
                1,
                math.ceil(previous[3] / scale_y)
                - math.floor(previous[2] / scale_y),
            ),
        )

    @staticmethod
    def _rgba_image(image: QImage) -> QImage:
        """取一份确定的 RGBA8888 图像，好按固定字节布局去读。

        不假设 `grabWindow()` 一定给某种格式 —— 高 DPI、不同平台、
        或者 Qt 版本变化都可能换格式。不认识的格式就转一下（转换是
        C++ 做的，很快）。
        """
        if image.format() in (QImage.Format.Format_RGBA8888,
                              QImage.Format.Format_RGBA8888_Premultiplied):
            return image
        return image.convertToFormat(QImage.Format.Format_RGBA8888)

    def _alpha_rows(self, image: QImage):
        """逐行产出 (y, 这一行的 alpha 字节串)。

        **为什么不用 `image.pixel(x, y)`。**
        那是最直观的写法，但每个像素都是一次 Python→C++ 调用。150×165
        就是 24750 次，实测光这一段就要 **7.06ms** —— 占整个刷新的 74%
        （`grabWindow()` 才 1.18ms）。按每秒 16 次算，等于每秒吃掉主线程
        131ms，界面会明显发涩。

        改成一次性拿到整块字节（`constBits()`），再用切片把每行的 alpha
        抽出来。RGBA8888 里每个像素 4 字节、alpha 在第 4 个，所以
        `row[3::4]` 就是这一行的 alpha —— 切片是 C 层做的。
        """
        converted = self._rgba_image(image)
        raw = bytes(converted.constBits())
        stride = converted.bytesPerLine()
        row_bytes = converted.width() * 4
        height = converted.height()
        for y in range(height):
            start = y * stride
            row = raw[start:start + row_bytes]
            if len(row) < row_bytes:
                break
            yield y, row[3::4]

    # 连续不透明段。用正则一次扫完，比 Python 逐字节循环快得多。
    _OPAQUE_RUN = re.compile(rb"\x01+")
    _TABLES: dict[int, bytes] = {}

    @classmethod
    def _threshold_table(cls, threshold: int) -> bytes:
        """把 alpha 值映射成 0/1 的查表（按阈值）。"""
        table = cls._TABLES.get(threshold)
        if table is None:
            table = bytes(1 if value >= threshold else 0
                          for value in range(256))
            cls._TABLES[threshold] = table
        return table

    @classmethod
    def _alpha_spans(cls, alpha_row: bytes,
                     threshold: int = 1) -> list[tuple[int, int]]:
        """一行里所有「不透明」的连续段，返回 [(起点, 终点)]。

        先按阈值把 alpha **压成 0/1**（`translate` 是 C 层），再用正则找
        连续的 1。

        **压这一步不能省。** 我第一版想「阈值是 1 时不用压，直接找非零」，
        于是正则写成 `\\x01+` —— 结果它只匹配 alpha 恰好等于 1 的字节。
        真实 alpha 是 0x80、0xFF 这些，匹配到的只有抗锯齿边缘那几个
        像素，mask 于是小了一大圈（测试立刻报「mask 与 alpha 范围对齐」
        失败，128/165 行不一致）。压成 0/1 之后正则才是在找「不透明」。
        """
        alpha_row = alpha_row.translate(cls._threshold_table(threshold))
        return [(match.start(), match.end() - 1)
                for match in cls._OPAQUE_RUN.finditer(alpha_row)]
