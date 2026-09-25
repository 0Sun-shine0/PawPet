r"""真实宠物窗口的透明碰撞箱回归。

用法：
    .venv\Scripts\python.exe tools\hitboxtest.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "hitboxtest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "hitboxtest"
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

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


def alpha_bbox(image) -> tuple[int, int, int, int] | None:
    left = image.width()
    top = image.height()
    right = bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if ((image.pixel(x, y) >> 24) & 0xFF) == 0:
                continue
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if right < left or bottom < top:
        return None
    return left, top, right, bottom


def main() -> int:
    print("小爪透明碰撞箱回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, QPoint, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.hitbox import PetHitbox
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    store.settings["ai_mcp_enabled"] = False
    store.settings["onboarding_done"] = True
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")),
    )
    root = component.create(engine.rootContext())
    if root is None:
        for error in component.errors():
            print("  ", error.toString())
        return 1

    pet = root.findChild(QObject, "petWindow", Qt.FindChildrenRecursively)
    check("拿到宠物窗口", pet is not None)
    if pet is None:
        return 1

    hitbox = PetHitbox(pet, root, interval_ms=80)

    def pump(milliseconds: int) -> None:
        deadline = time.time() + milliseconds / 1000
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.008)

    def verify_shape(label: str) -> None:
        pump(260)
        hitbox.refresh()
        # refresh() 已经同步抓取并应用了这一帧；这里不能再等一拍，
        # 否则高 DPI 下宠物的呼吸位移会被放大成几像素的假偏差。
        image = pet.grabWindow()
        mask = pet.mask()
        bbox = alpha_bbox(image)
        full = (0, 0, int(pet.width()), int(pet.height()))
        box = mask.boundingRect().getRect()
        check(f"{label} 有有效 mask", not mask.isEmpty())
        check(f"{label} 不是完整矩形", box != full, f"实际 {box} / 窗口 {full}")
        check(f"{label} 透明角点不在碰撞区", not mask.contains(QPoint(0, 0)))
        expected = None if bbox is None else (
            bbox[0],
            bbox[1],
            bbox[2] - bbox[0] + 1,
            bbox[3] - bbox[1] + 1,
        )
        aligned = expected is not None and all(
            abs(actual - wanted) <= 2
            for actual, wanted in zip(box, expected)
        )
        check(f"{label} mask 与 alpha 范围对齐", aligned,
              f"alpha={bbox} mask={box}")

    backend.petVisible = True
    pump(700)
    check("宠物窗口已显示", pet.isVisible() and pet.isExposed())

    for style in ("mochi", "shiba", "penguin", "fox"):
        backend.pet_style = style
        verify_shape(style)

    backend.pet_scale = 2.4
    verify_shape("2.4 倍缩放")

    backend.petVisible = False
    pump(200)
    check("隐藏后清除旧 mask", pet.mask().isEmpty())

    backend.petVisible = True
    backend.pet_scale = 1.0
    verify_shape("恢复显示")

    hitbox.stop()
    root.deleteLater()
    app.processEvents()
    backend.shutdown()
    app.quit()

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
