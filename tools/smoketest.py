"""真实启动冒烟测试：把完整应用跑起来几秒，检查托盘/热键/单实例/窗口。

用法：
    .venv\\Scripts\\python.exe tools\\smoketest.py

会在 .cache/smoke/ 下用独立数据文件，不碰你的 pet_data.json。
测试期间屏幕右下角会真的出现小爪，几秒后自动退出。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "smoke"

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
    from pawpet.config import load_env

    load_env()

    from PySide6.QtCore import QObject, QTimer, Qt
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from pawpet import win32
    from pawpet.app import PawPetApp, make_icon
    from pawpet.backend import Backend
    from pawpet.config import APP_NAME, QML_DIR
    from pawpet.services import ping_existing_instance
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    print("小爪助手 启动冒烟测试")
    print(f"数据文件：{SCRATCH / 'pet_data.json'}")

    # ---------------------------------------------------------- 平台能力
    print("\n=== Windows 平台能力 ===")
    check("空闲检测可用", win32.idle_seconds() >= 0)
    check("通知状态可读", win32.notification_state() > 0)
    check("非全屏免打扰状态判定正常", isinstance(win32.is_quiet_context(), bool))

    mutex_name = "Local\\PawPet.SmokeTest"
    first = win32.SingleInstance(mutex_name)
    check("第一个实例拿到互斥体", first.already_running is False)
    second = win32.SingleInstance(mutex_name)
    check("第二个实例被识别为已在运行", second.already_running is True)
    second.release()
    first.release()

    # ---------------------------------------------------------- 真实启动
    print("\n=== 完整应用启动 ===")
    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    # QPixmap 必须在 QGuiApplication 之后才能构造，所以图标在这里才生成
    icon = make_icon()
    check("托盘图标有多个尺寸", len(icon.availableSizes()) >= 4,
          f"实际 {[f'{s.width()}x{s.height()}' for s in icon.availableSizes()]}")
    check("托盘图标不为空", not icon.isNull())

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    store.state["counters"]["sit_since_epoch"] = time.time()

    pawpet = PawPetApp(app, store)
    check("应用对象构造成功", pawpet is not None)
    check("系统托盘可用", QSystemTrayIcon.isSystemTrayAvailable())
    check("托盘图标已创建", pawpet._tray is not None)
    check("QML 根对象已加载", pawpet._qml_root is not None)

    # 一定要把根对象留在 Python 变量里：rootObjects() 返回的临时列表被回收时，
    # 根对象可能连带销毁，之后 findChild 拿到的窗口就是已删除的悬空对象。
    root = pawpet._qml_root

    def window(name: str):
        if not root:
            return None
        return root.findChild(QObject, name)

    # 热键可能被别的程序占用，这里只报告不判定失败
    print(f"  热键已注册：{pawpet._hotkeys.registered or '（无）'}")
    print(f"  热键失败：{pawpet._hotkeys.failed or '（无）'}")

    # 单实例管道：启动服务端之后 ping 一下
    check("管道服务端已启动", pawpet._pipe is not None)

    # ---------------------------------------------------------- 跑一会
    print("\n=== 运行 3 秒（观察心跳与定时器）===")
    ticks = {"focus": 0, "slow": 0}

    def count_focus():
        ticks["focus"] += 1

    def count_slow():
        ticks["slow"] += 1

    pawpet._heartbeat.timeout.connect(count_focus)
    pawpet._slow.timeout.connect(count_slow)

    def finish():
        check("心跳定时器在跑", ticks["focus"] > 10, f"实际 {ticks['focus']} 次")
        check("秒级定时器在跑", ticks["slow"] >= 2, f"实际 {ticks['slow']} 次")

        # 窗口状态
        pet = window("petWindow")
        dash = window("dashboardWindow")
        bar = window("commandBar")
        check("宠物窗口可见", pet is not None and pet.property("visible") is True)
        check("宠物窗口无边框", bool(pet.property("flags") & Qt.FramelessWindowHint))
        check("宠物窗口不在任务栏（Qt.Tool）", bool(pet.property("flags") & Qt.Tool))
        check("工作台默认隐藏", dash is not None and dash.property("visible") is False)
        check("指令栏已创建", bar is not None)
        check("指令栏默认隐藏", bar is not None and bar.property("visible") is False)

        if bar is not None:
            check("指令栏始终置顶", bool(bar.property("flags") & Qt.WindowStaysOnTopHint))
            check("指令栏不占任务栏", bool(bar.property("flags") & Qt.Tool))
            check("指令栏有合理高度", bar.property("height") > 60,
                  f"实际 {bar.property('height')}")

        # ---------------------------------------------------- 指令栏交互
        print("\n=== 指令栏（这次改动的重点）===")
        backend = pawpet._backend
        check("默认单击小爪弹指令栏",
              backend.pet_click_action == "command", backend.pet_click_action)

        backend.showCommandBar()
        QTimer.singleShot(400, check_command_bar)

    def check_command_bar():
        bar = window("commandBar")
        check("showCommandBar 之后可见", bar.property("visible") is True)
        check("指令栏会保证小爪可见", pawpet._backend.petVisible is True)

        # 位置必须落在某块屏幕的可见区域内
        area = pawpet._backend.screenAt(int(bar.property("x") or 0),
                                        int(bar.property("y") or 0))
        bx = int(bar.property("x") or 0)
        by = int(bar.property("y") or 0)
        bw = int(bar.property("width") or 0)
        bh = int(bar.property("height") or 0)
        check("指令栏在屏幕可见区域内",
              bx >= area["x"] - 1 and by >= area["y"] - 1
              and bx + bw <= area["x"] + area["width"] + 1
              and by + bh <= area["y"] + area["height"] + 1,
              f"栏 ({bx},{by},{bw}x{bh}) 屏幕 {area}")

        # 切换行为
        pawpet._backend.toggleCommandBar()
        QTimer.singleShot(250, check_toggle)

    def check_toggle():
        bar = window("commandBar")
        check("再切一次会隐藏", bar.property("visible") is False)

        # 单击/双击分流
        pawpet._backend.pet_click_action = "command"
        pawpet._backend.handlePetClick()
        QTimer.singleShot(250, check_click_routing)

    def check_click_routing():
        bar = window("commandBar")
        dash = window("dashboardWindow")
        check("单击小爪（默认设置）弹出指令栏", bar.property("visible") is True)
        check("单击不会打开工作台", dash.property("visible") is False)

        # 双击永远开工作台
        pawpet._backend.handlePetDoubleClick()
        QTimer.singleShot(350, check_double_click)

    def check_double_click():
        dash = window("dashboardWindow")
        check("双击小爪打开工作台", dash.property("visible") is True)
        check("双击落到专注页", dash.property("currentPage") == "focus",
              f"实际 {dash.property('currentPage')}")

        # 把单击改成开工作台，行为要跟着变
        pawpet._backend.pet_click_action = "dashboard"
        pawpet._backend.commandBarVisible = False
        QTimer.singleShot(200, check_custom_click)

    def check_custom_click():
        bar = window("commandBar")
        pawpet._backend.handlePetClick()
        QTimer.singleShot(250, check_custom_result)

    def check_custom_result():
        bar = window("commandBar")
        dash = window("dashboardWindow")
        check("改成 dashboard 后单击不再弹指令栏", bar.property("visible") is False)
        check("改成 dashboard 后单击打开工作台", dash.property("visible") is True)

        # 恢复默认，免得污染后续手动测试
        pawpet._backend.pet_click_action = "command"
        pawpet._backend.commandBarVisible = False

        # 定位模式
        pawpet._backend.commandBarAnchor = "bottom"
        check("定位模式可切换", pawpet._backend.commandBarAnchor == "bottom")
        pawpet._backend.showCommandBar()

        QTimer.singleShot(400, check_bottom_anchor)

    def check_bottom_anchor():
        bar = window("commandBar")
        area = pawpet._backend.screenAt(int(bar.property("x") or 0),
                                        int(bar.property("y") or 0))
        by = int(bar.property("y") or 0)
        bh = int(bar.property("height") or 0)
        bottom_gap = (area["y"] + area["height"]) - (by + bh)
        check("底部模式下贴近屏幕下方", bottom_gap < 120, f"距底部 {bottom_gap}px")

        pawpet._backend.commandBarAnchor = "pet"
        pawpet._backend.commandBarVisible = False

        # 点击行为与数据落盘
        pawpet._backend.tasks.add("冒烟测试待办", 1)
        check("待办写入成功", pawpet._backend.tasks.pendingCount == 1,
              f"实际 {pawpet._backend.tasks.pendingCount}")
        pawpet._backend.flush()
        check("数据文件已写出", (SCRATCH / "pet_data.json").exists())
        pawpet._backend.testNotification()

        QTimer.singleShot(600, finish_up)

    def finish_up():
        pawpet.shutdown()
        QTimer.singleShot(400, app.quit)

    QTimer.singleShot(2500, finish)
    app.exec()

    # ---------------------------------------------------------- 收尾检查
    print("\n=== 收尾 ===")
    import json

    saved = json.loads((SCRATCH / "pet_data.json").read_text(encoding="utf-8"))
    check("落盘 schema 正确", saved.get("schema") == 2, f"实际 {saved.get('schema')}")
    check("落盘含 settings", "settings" in saved and "focus_minutes" in saved["settings"])
    check("落盘含 focus 墙钟字段", "end_epoch" in saved["focus"])

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
