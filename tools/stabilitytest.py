r"""真实应用长期运行回归。

默认运行 30 秒，期间让 Qt 心跳、贴图碰撞箱、工作台窗口和定时刷新一起
工作，并周期性检查数据文件。可用 PAWPET_STABILITY_SECONDS 调整时长。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from console import configure_utf8

configure_utf8()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "stabilitytest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "stabilitytest"
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
    seconds = max(5, min(300, int(os.environ.get(
        "PAWPET_STABILITY_SECONDS", "30"))))
    print(f"小爪长期运行回归（{seconds} 秒）\n")
    if not SCRATCH.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"拒绝清理工作区外目录：{SCRATCH}")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt
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
        "update_check": False,
        "onboarding_done": True,
    })

    pawapp = PawPetApp(app, store)
    pawapp._backend.flush()
    root = pawapp._qml_root
    pet = root.findChild(QObject, "petWindow", Qt.FindChildrenRecursively)
    clock_ticks = [0]
    pawapp._backend.clockChanged.connect(
        lambda: clock_ticks.__setitem__(0, clock_ticks[0] + 1))

    def pump(milliseconds: int) -> None:
        deadline = time.monotonic() + milliseconds / 1000
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    pump(1200)
    check("宠物窗口正常显示", pet is not None and pet.isVisible())
    check("心跳定时器已运行", pawapp._heartbeat.isActive())
    check("慢速刷新定时器已运行", pawapp._slow.isActive())

    # 模拟窗口原本在被拔掉的屏幕上，验证屏幕拓扑信号会把独立窗口
    # 一起收回，而不是只有宠物被修复。
    dashboard = root.findChild(QObject, "dashboardWindow", Qt.FindChildrenRecursively)
    pawapp._backend.showDashboard("focus")
    pump(350)
    area = pawapp._backend.primaryScreenArea()
    if dashboard is not None:
        dashboard.setX(area["x"] - 2400)
        dashboard.setY(area["y"] - 1400)
        pawapp._backend.screenGeometryChanged.emit()
        pump(350)
        inside = (dashboard.x() >= area["x"]
                  and dashboard.y() >= area["y"]
                  and dashboard.x() + dashboard.width()
                  <= area["x"] + area["width"]
                  and dashboard.y() + dashboard.height()
                  <= area["y"] + area["height"])
        check("工作台脱离屏幕后能自动收回", inside,
              f"位置=({dashboard.x()},{dashboard.y()}) 区域={area}")
    else:
        check("工作台脱离屏幕后能自动收回", False, "找不到 dashboardWindow")
    pawapp._backend.hideDashboard()

    styles = ("mochi", "shiba", "penguin", "fox")
    next_cycle = time.monotonic()
    end = time.monotonic() + seconds
    json_errors: list[str] = []
    cycles = 0
    try:
        while time.monotonic() < end:
            app.processEvents()
            now = time.monotonic()
            if now >= next_cycle:
                style = styles[cycles % len(styles)]
                pawapp._backend.pet_style = style
                pawapp._backend.showDashboard("tasks" if cycles % 2 else "focus")
                pump(180)
                pawapp._backend.hideDashboard()
                try:
                    json.loads((SCRATCH / "pet_data.json").read_text(
                        encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    json_errors.append(f"cycle {cycles}: {exc}")
                cycles += 1
                next_cycle = now + 4.0
            time.sleep(0.01)
    finally:
        pawapp.shutdown()
        app.processEvents()

    check("长期运行期间未出现数据文件解析错误", not json_errors,
          "; ".join(json_errors[:3]))
    check("长期运行期间心跳持续产生刷新", clock_ticks[0] >= 2,
          f"clockChanged={clock_ticks[0]}")
    check("长期运行完成了多轮界面切换", cycles >= 2, f"cycles={cycles}")

    try:
        payload = json.loads((SCRATCH / "pet_data.json").read_text(
            encoding="utf-8"))
        final_json_ok = isinstance(payload, dict) and payload.get("schema") == 2
    except Exception as exc:  # noqa: BLE001
        final_json_ok = False
        detail = str(exc)
    else:
        detail = ""
    check("退出后数据文件仍可读取", final_json_ok, detail)

    app.quit()
    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print("  - " + item)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
