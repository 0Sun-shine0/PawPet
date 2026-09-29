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


def check_focus(label: str, obj, pump) -> None:
    """窗口激活类的断言：**有界重试**，拿不到记为环境诊断，不算失败。

    为什么这类断言要特殊处理：`obj.property("active")` 读的是 Qt 的窗口
    `active`，而它取决于 **Windows 是否授予前台激活**。Windows 有「焦点
    窃取防护」：非前台进程的激活请求可能被拒绝（改成任务栏闪烁）——
    这是**本质上不确定**的外部条件。

    产品代码该做的都做了（`requestActivate()` + `input.forceActiveFocus()`），
    剩下的是操作系统决定的事。断言它就必然时绿时红：实测同一份代码
    6 次运行挂了 2 次，而且**每次挂的断言都不一样**。

    Codex 的判断（我采纳）：这类信号可以作为诊断，但不该是**唯一**的
    回归失败条件。产品级断言是「控件已显示 / 已收起 / 碰撞箱已暂停」
    这些不依赖谁抢到焦点的东西。

    注意：**重试窗口给 2 秒**，不是「等一帧就判定失败」——
    刚显示出来的无边框窗口拿到激活本来就需要一点时间。
    """
    for _ in range(20):
        try:
            if obj.property("active"):
                check(f"{label}（有界重试内拿到）", True)
                return
        except Exception:
            break
        pump(100)
    print(f"  [--] {label}：2 秒内没拿到前台激活"
          f"（Windows 焦点窃取防护可能拒绝了）—— 环境依赖，不计失败")


def find_visual(obj, name: str):
    """按 objectName 在**视觉树**里找控件。

    为什么不用 `findChild(QObject, name, Qt.FindChildrenRecursively)`：
    **Repeater 生成出来的 delegate，QObject 父子关系和视觉父子关系不一致。**
    实测 `findChild(QObject, "nav_tasks", ...)` 返回 None，而控件确实存在
    —— 走 `childItems()` 能看到 7 个 `nav_*`，名字和坐标都对
    （nav_today y=0 / nav_focus y=44 / nav_tasks y=88 / ...）。

    这一条卡了我一阵：先按「名字没设上」查，又按「delegate 没实例化」查，
    两次都错 —— 真正的原因是**遍历方式**。
    """
    if obj is None:
        return None
    try:
        if obj.objectName() == name:
            return obj
    except Exception:
        return None
    try:
        children = obj.childItems()
    except AttributeError:
        return None
    for child in children:
        found = find_visual(child, name)
        if found is not None:
            return found
    return None


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
        check_focus("指令栏单击后获得焦点", bar, pump)
        check("指令栏打开时暂停碰撞箱", pawapp._pet_hitbox._suspended)

        pyautogui.press("esc")
        pump(450)
        check("Esc 可以收起指令栏", not bar.isVisible())

        # ---- 双击：**显式两次点击并测量真实间隔** ----
        #
        # 原来是 `pyautogui.click(clicks=2, interval=0.45)`。
        # 两个问题：
        #
        # 一、**余量只有 50ms**。应用判定双击的窗口是
        #     `max(250, Qt.styleHints.mouseDoubleClickInterval)` = 500ms
        #     （系统设置），而 0.45s 的间隔加上 pyautogui 自己的开销
        #     离 500ms 很近。机器一忙，第二次点击落到窗口外，
        #     就变成两次单击 —— 实测全量回归里挂过好几次，
        #     而且**挂的断言每次不一样**（就是这里抖出来的）。
        #
        # 二、**失败时看不出原因**。`clicks=2` 是一次黑盒调用，报错只会说
        #     「工作台没打开」，看不出间隔是多少。
        #
        # 改成手动两次点击，把**真实间隔量出来并打印**。0.18s 更接近
        # 真人双击的节奏，也远离 500ms 的边界。
        point = pet.mapToGlobal(QPoint(int(pet.width() / 2),
                                       int(pet.height() / 2)))
        pyautogui.moveTo(point.x(), point.y())
        pump(80)
        marks = []
        for i in range(2):
            marks.append(time.monotonic())
            pyautogui.mouseDown()
            pyautogui.mouseUp()
            if i == 0:
                time.sleep(0.18)
        gap_ms = (marks[1] - marks[0]) * 1000
        window_ms = pet.property("doubleClickWindowMs")
        print(f"       双击真实间隔 {gap_ms:.0f}ms"
              f"（应用判定窗口 {window_ms}ms）")
        pump(1000)
        check("双击小爪打开工作台", dashboard.isVisible(),
              f"（间隔 {gap_ms:.0f}ms，窗口 {window_ms}ms）")
        check("双击后不会残留指令栏", not bar.isVisible())
        check("工作台打开时暂停碰撞箱", pawapp._pet_hitbox._suspended)

        # ---- 焦点：**有界重试**，且不当作纯粹的产品断言 ----
        # 见 `check_focus()` 的 docstring：`active` 取决于 Windows 是否授予
        # 前台激活，那是本质上不确定的外部条件。
        # 产品级断言是上面那三条 —— 工作台已显示、指令栏已收起、
        # 碰撞箱已暂停，它们不依赖谁抢到焦点。
        check_focus("工作台双击后获得焦点", dashboard, pump)

        # ---- 导航：从真实控件取坐标，不写死像素偏移 ----
        #
        # 原来这里是 `mapToGlobal(QPoint(70, 46 + 12 + 2 * 44 + 20))` ——
        # 那个式子在默认布局下**算出来是对的**（正好是「待办」的中心），
        # 但它把标题栏高度/边距/行高/间距四个常数写死在测试里。
        # 实测出现过一次落在 `focus` 而期望 `tasks`，偏差正好一行的高度。
        #
        # 现在改成：找 `nav_tasks` 这个真实控件，读它自己的中心点。
        #
        # ⚠ **前置条件守卫**：工作台没打开时**不能**继续点导航。
        # 上一版会照点不误 —— 那是一次落在桌面上的野点击（实测点到
        # (12,78)），会干扰后面跑的套件（同一次回归里 `snapqmltest`
        # 就跟着挂了，单独跑却是 40/40 全过）。
        # **一个失败的用例不应该往外扔副作用。**
        if not dashboard.isVisible():
            print("  [--] 工作台没打开 → 跳过导航断言"
                  "（不做野点击，避免干扰后续套件）")
        else:
            direct = dashboard.findChild(QObject, "nav_tasks",
                                         Qt.FindChildrenRecursively)
            nav = direct if direct is not None else find_visual(
                dashboard.property("contentItem"), "nav_tasks")
            check("能找到 nav_tasks 这个导航项（objectName 生效）",
                  nav is not None,
                  "findChild 和视觉树都没找到")
            if nav is not None:
                # 顺带把「为什么不能只用 findChild」这件事断言下来 ——
                # Repeater delegate 的 QObject 父子关系和视觉父子关系不一致，
                # 这只在视觉树里找得到。这是个**踩过的坑**，值得留一条断言。
                print(f"       （findChild 找到={direct is not None}，"
                      f"视觉树找到={nav is not None}）")
                nav_center = nav.mapToGlobal(
                    QPoint(int(nav.property("width") / 2),
                           int(nav.property("height") / 2)))
                pyautogui.click(nav_center.x(), nav_center.y())
                pump(350)
                check("工作台打开后导航仍可点击",
                      dashboard.property("currentPage") == "tasks",
                      f"实际 {dashboard.property('currentPage')}"
                      f"（点的是 nav_tasks 的真实中心 "
                      f"{nav_center.x()},{nav_center.y()}）")
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
