"""Build a native hit region from the pet window's rendered alpha channel.

The pet is drawn from several QML items and Canvas nodes.  Repeating that
geometry in Python would make the mouse target drift whenever an animation,
edge pose, mood, or badge changes.  The rendered image is the single source
of truth, so the window mask is rebuilt from the pixels that are actually
visible.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QRect, QTimer, Slot
from PySide6.QtGui import QImage, QRegion


class PetHitbox(QObject):
    """Keep a transparent pet window's native input region in sync."""

    def __init__(
        self,
        window: QObject,
        parent: QObject | None = None,
        *,
        interval_ms: int = 60,
        alpha_threshold: int = 1,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._alpha_threshold = max(1, min(255, int(alpha_threshold)))
        self._last_region: QRegion | None = None
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
        """Coalesce bursts of QML property changes into one refresh."""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self.refresh)

    @Slot()
    def refresh(self) -> None:
        """Capture the current frame and apply its non-transparent pixels."""
        self._refresh_pending = False
        window = self._window
        if window is None:
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

        self._set_native_mask(region)
        self._last_region = QRegion(region)

    @Slot()
    def clear(self) -> None:
        """Remove the native mask so a hidden/recreated window is unrestricted."""
        self._last_region = None
        self._clear_native_mask()

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
        for image_y in range(image_height):
            spans = self._alpha_spans(image, image_y, threshold)
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
                    rects.append(
                        QRect(
                            previous[0],
                            max(0, math.floor(previous[2] / scale_y)),
                            previous[1] - previous[0] + 1,
                            max(
                                1,
                                math.ceil(previous[3] / scale_y)
                                - math.floor(previous[2] / scale_y),
                            ),
                        )
                    )
            active = current

        for previous in active.values():
            rects.append(
                QRect(
                    previous[0],
                    max(0, math.floor(previous[2] / scale_y)),
                    previous[1] - previous[0] + 1,
                    max(
                        1,
                        math.ceil(previous[3] / scale_y)
                        - math.floor(previous[2] / scale_y),
                    ),
                )
            )

        region = QRegion()
        for rect in rects:
            region = region.united(QRegion(rect))
        return region

    @staticmethod
    def _alpha_spans(
        image: QImage,
        image_y: int,
        threshold: int,
    ) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        start = -1
        for image_x in range(image.width()):
            opaque = ((image.pixel(image_x, image_y) >> 24) & 0xFF) >= threshold
            if opaque and start < 0:
                start = image_x
            elif not opaque and start >= 0:
                spans.append((start, image_x - 1))
                start = -1
        if start >= 0:
            spans.append((start, image.width() - 1))
        return spans
