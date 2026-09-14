"""复现「右键菜单点了别处关不掉」。

做法：
  1. 起完整应用，把宠物窗口挪到屏幕角落。
  2. 右键宠物，弹出菜单。
  3. 用 pyautogui 真的在别处点一下（点在一个我们自己创建的、无害的测试窗口上）。
  4. 看菜单有没有关。

测试前后会保存并恢复鼠标位置，不会点坏任何东西。

用法：
    .venv\\Scripts\\python.exe tools\\menudiag.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

SCRATCH = ROOT / ".cache" / "menudiag"

# 把一切挪到屏幕外做测试，避免干扰用户
OFFSCREEN_X = -3800


def main() -> int:
    import pyautogui  # 真实鼠标操作
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR, ensure_dirs
    from pawpet.store import Store

    ensure_dirs()
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")))
    if not engine.rootObjects():
        print("QML 加载失败")
        return 1
    root = engine.rootObjects()[0]

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    pet = root.findChild(QObject, "petWindow")
    menu = root.findChild(QObject, "petContextMenu")

    if pet is None:
        print("找不到 petWindow")
        return 1
    if menu is None:
        print("找不到 petContextMenu —— 菜单没有 objectName，请先加上")
        return 1

    # 把宠物放到屏幕外，测试期间不挡用户
    pet.setProperty("x", OFFSCREEN_X)
    pet.setProperty("y", 100)
    pump(1200)

    saved_mouse = pyautogui.position()
    print(f"原鼠标位置 = {saved_mouse}")

    results: list[tuple[str, bool, str]] = []

    def record(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        print(f"  [{'ok' if ok else 'XX'}] {label} {detail}")

    try:
        # ---------------------------------------------------- 场景 1：开菜单
        print("\n=== 1. 右键能不能弹出菜单 ===")
        pet_x = int(pet.property("x"))
        pet_y = int(pet.property("y"))
        pet_w = int(pet.property("width"))
        pet_h = int(pet.property("height"))
        # 屏幕外的窗口点不到，所以先把它移回屏幕内一个角落再操作
        safe_x = 40
        safe_y = 40
        pet.setProperty("x", safe_x)
        pet.setProperty("y", safe_y)
        pump(600)

        pyautogui.click(safe_x + pet_w // 2, safe_y + pet_h // 2, button="right")
        pump(900)
        record("右键后菜单可见", bool(menu.property("visible")),
               f"visible={menu.property('visible')}")

        if not menu.property("visible"):
            print("\n菜单根本没弹出来，后面的测试没法做")
            return 1

        # ------------------------------------------- 场景 2：点别处关不关
        print("\n=== 2. 点别的地方，菜单该关掉 ===")
        # 点宠物窗口之外的空白区域（屏幕另一角）
        away_x = 60
        away_y = 700
        pyautogui.click(away_x, away_y, button="left")
        pump(900)
        closed = not bool(menu.property("visible"))
        record("点击远处后菜单关闭", closed, f"visible={menu.property('visible')}")

        # ------------------------------------------- 场景 3：Esc 关不关
        print("\n=== 3. Esc 应该关掉菜单 ===")
        if not menu.property("visible"):
            pyautogui.click(safe_x + pet_w // 2, safe_y + pet_h // 2, button="right")
            pump(700)
        pyautogui.press("escape")
        pump(700)
        record("Esc 后菜单关闭", not bool(menu.property("visible")),
               f"visible={menu.property('visible')}")

        # --------------------------------------- 场景 4：反复开关（抓竞态）
        # 「右键偶尔没反应」是个间歇性 bug，单跑一次很容易蒙混过关。
        # 这里连做 5 轮「右键 → Esc」，任何一轮打不开都算失败。
        print("\n=== 4. 反复开关 5 轮（抓间歇性竞态）===")
        reopen_ok = True
        failed_round = 0
        for round_index in range(1, 6):
            pyautogui.click(safe_x + pet_w // 2, safe_y + pet_h // 2, button="right")
            pump(700)
            if not menu.property("visible"):
                reopen_ok = False
                failed_round = round_index
                break
            pyautogui.press("escape")
            pump(500)
            if menu.property("visible"):
                reopen_ok = False
                failed_round = round_index
                break
        record("反复开关 5 轮都正常", reopen_ok,
               f"第 {failed_round} 轮失败" if not reopen_ok else "")

        # 确保菜单是开着的，供后面的场景用
        if not menu.property("visible"):
            pyautogui.click(safe_x + pet_w // 2, safe_y + pet_h // 2, button="right")
            pump(700)

        # --------------------------------------- 场景 5：开着时点小爪本身
        print("\n=== 5. 菜单开着时单击小爪 ===")
        pyautogui.click(safe_x + pet_w // 2, safe_y + pet_h // 2, button="left")
        pump(800)
        bar = root.findChild(QObject, "commandBar")
        menu_still = bool(menu.property("visible"))
        bar_open = bool(bar.property("visible")) if bar else False
        record("点小爪后菜单关闭", not menu_still, f"visible={menu_still}")
        record("点小爪不该同时弹出指令栏", not (menu_still and bar_open),
               f"菜单={menu_still} 指令栏={bar_open}")

        # 收尾：确保都关掉
        if bar:
            backend.commandBarVisible = False
        if menu.property("visible"):
            menu.setProperty("visible", False)
        pump(400)

    finally:
        pyautogui.moveTo(saved_mouse.x, saved_mouse.y)
        pump(200)

    # ------------------------------------------------------------ 汇总
    print(f"\n{'=' * 52}")
    failed = [r for r in results if not r[1]]
    if failed:
        print(f"失败 {len(failed)} 项：")
        for label, _ok, detail in failed:
            print(f"  - {label} {detail}")
        print("\n结论：右键菜单的取消行为有问题，需要修。")
        code = 1
    else:
        print(f"全部通过（{len(results)} 项）")
        code = 0

    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(4):
        app.processEvents()
        pump(30)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
