r"""多显示器、任务栏偏移和断开后的宠物位置回归。

不改动用户的显示设置；用 QScreen 替身模拟负坐标和拓扑变化。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

from console import configure_utf8

configure_utf8()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "screengeometrytest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "screengeometrytest"

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("小爪多屏几何回归\n")
    if not SCRATCH.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"拒绝清理工作区外目录：{SCRATCH}")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QApplication(sys.argv[:1])
    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()
    store.settings["ai_mcp_enabled"] = False
    backend = Backend(store)

    class Screen:
        def __init__(self, geometry: QRect, available: QRect) -> None:
            self.geometry = geometry
            self.available = available

        def availableGeometry(self) -> QRect:
            return self.available

        def devicePixelRatio(self) -> float:
            return 1.0

    primary = Screen(QRect(0, 0, 1920, 1080), QRect(0, 0, 1920, 1032))
    left = Screen(QRect(-1600, -120, 1600, 900),
                  QRect(-1600, -80, 1600, 860))
    right = Screen(QRect(1920, 80, 2560, 1440),
                   QRect(1920, 80, 2560, 1400))
    large = Screen(QRect(0, 0, 3840, 2160),
                   QRect(0, 0, 3840, 2136))
    screens = [left, primary, right]

    def screen_at(point):
        return next((screen for screen in screens
                     if screen.geometry.contains(point)), None)

    with patch.object(QGuiApplication, "screenAt", side_effect=screen_at), \
            patch.object(QGuiApplication, "primaryScreen", return_value=primary):
        check("负坐标屏幕保留任务栏顶部偏移",
              backend.screenAt(-1400, 0) == {
                  "x": -1600, "y": -80, "width": 1600, "height": 860,
              })
        check("右侧屏幕保留纵向偏移",
              backend.screenAt(2500, 300)["y"] == 80)
        check("主屏区域不依赖 (0,0) 坐标",
              backend.primaryScreenArea() == {
                  "x": 0, "y": 0, "width": 1920, "height": 1032,
              })
        scale_spy = QSignalSpy(backend.uiScaleChanged)
        check("自动缩放按当前主屏分辨率计算",
              abs(backend.uiScale - 1.15) < 1e-6,
              str(backend.uiScale))
        primary_available = primary.available
        primary.available = large.available
        try:
            backend.screenGeometryChanged.emit()
            large_scale = backend.uiScale
        finally:
            primary.available = primary_available
        check("切换到大屏后自动缩放立即更新",
              abs(large_scale - 1.4) < 1e-6,
              str(large_scale))
        check("屏幕变化会通知 QML 重新计算缩放",
              scale_spy.count() >= 1, str(scale_spy.count()))

        width, height = backend._pet_size()
        cases = (
            ("left", -1600 + 5, 100, left),
            ("right", 4480 - width - 5, 100, right),
            ("top", -1100, -80 + 5, left),
            ("bottom", 2500, 80 + 1400 - height - 5, right),
        )
        for edge, x, y, screen in cases:
            backend.petDetach()
            result = backend.petSnap(x, y)
            area = screen.available
            inside = (area.x() <= result["x"]
                      and area.y() <= result["y"]
                      and result["x"] + width <= area.x() + area.width()
                      and result["y"] + height <= area.y() + area.height())
            check(f"跨屏吸附到 {edge} 边并完全可见",
                  result["edge"] == edge and inside, str(result))

        backend.petDetach()
        backend.petSnap(-1595, 120)
        backend._pet_peek = False
        store.state["pet_x"] = -1600
        store.state["pet_y"] = 120
        screens.remove(left)
        geometry = backend.petEdgeGeometry()
        check("显示器断开后贴边宠物回到主屏",
              geometry["active"] and geometry["edge"] == "left"
              and geometry["x"] < primary.available.x()
              and geometry["x"] + width > primary.available.x()
              and primary.available.y() <= geometry["y"]
              <= primary.available.y() + primary.available.height() - height,
              str(geometry))
        check("断开后滑出位置可完整显示",
              backend._pet_geometry_for("left", -1600, 120, peek=True)[0]
              == primary.available.x())
        check("屏外点回退到主屏可用区域",
              backend.screenAt(-5000, -5000)["height"] == 1032)

    backend.shutdown()
    app.quit()
    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print("  - " + item)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
