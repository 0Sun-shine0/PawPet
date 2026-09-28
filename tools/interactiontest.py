"""桌面级鼠标交互回归。"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from console import configure_utf8

configure_utf8()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "interactiontest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "interactiontest"
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


def main() -> int:
    print("小爪桌面鼠标交互回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    import pyautogui
    from PySide6.QtCore import QObject, QPoint, Qt
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.app import PawPetApp
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    store.settings.update({
        "ai_mcp_enabled": False,
        "onboarding_done": True,
        "update_check": False,
    })
    store.state["pet_edge"] = ""

    pawapp = PawPetApp(app, store)
    root = pawapp._qml_root
    backend = pawapp._backend
    pet = root.findChild(QObject, "petWindow", Qt.FindChildrenRecursively)
    bar = root.findChild(QObject, "commandBar", Qt.FindChildrenRecursively)
    dashboard = root.findChild(QObject, "dashboardWindow",
                               Qt.FindChildrenRecursively)

    def pump(milliseconds: int) -> None:
        deadline = time.monotonic() + milliseconds / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)

    try:
        check("真实窗口已创建", pet is not None and bar is not None
              and dashboard is not None)
        if pet is None or bar is None or dashboard is None:
            return 1

        pet.setX(500)
        pet.setY(300)
        backend.commandBarVisible = False
        backend.dashboardVisible = False
        # Let PetWindow finish its asynchronous restore before setting the
        # deterministic coordinates used by the desktop interaction test.
        pump(450)
        pet.setX(500)
        pet.setY(300)
        pump(300)

        point = pet.mapToGlobal(QPoint(int(pet.width() / 2),
                                       int(pet.height() / 2)))
        pyautogui.click(point.x(), point.y())
        pump(700)
        check("单击小爪弹出指令栏", bar.isVisible())
        check("指令栏单击后获得焦点", bool(bar.property("active")))
        check("指令栏打开时暂停碰撞箱", pawapp._pet_hitbox._suspended)

        pyautogui.press("esc")
        pump(450)
        check("Esc 可以收起指令栏", not bar.isVisible())

        point = pet.mapToGlobal(QPoint(int(pet.width() / 2),
                                       int(pet.height() / 2)))
        pyautogui.click(point.x(), point.y(), clicks=2, interval=0.45)
        pump(1000)
        check("双击小爪打开工作台", dashboard.isVisible())
        check("工作台双击后获得焦点", bool(dashboard.property("active")))
        check("双击后不会残留指令栏", not bar.isVisible())
        check("工作台打开时暂停碰撞箱", pawapp._pet_hitbox._suspended)

        nav_point = dashboard.mapToGlobal(QPoint(70, 46 + 12 + 2 * 44 + 20))
        pyautogui.click(nav_point.x(), nav_point.y())
        pump(350)
        check("工作台打开后导航仍可点击",
              dashboard.property("currentPage") == "tasks",
              str(dashboard.property("currentPage")))
    finally:
        pawapp.shutdown()
        app.processEvents()
        app.quit()

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
