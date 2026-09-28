"""渲染 AI 页面在「有对话、有审批」状态下的样子。

用一个假 Agent 把消息灌进 AiController，然后截图，检查气泡和审批卡片。
输出 .cache/preview/11_ai_*.png
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

OUT = ROOT / ".cache" / "preview"
KEEP: list = []
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.agent import StepEvent
    from pawpet.backend import Backend
    from pawpet.config import QML_DIR, ensure_dirs
    from pawpet.store import Store

    os.environ.pop("OPENAI_API_KEY", None)
    ensure_dirs()
    OUT.mkdir(parents=True, exist_ok=True)

    scratch = ROOT / ".cache" / "ai-preview"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()
    backend = Backend(store)
    ai = backend.ai
    ai.context.backend = backend

    # 造一段有代表性的对话：提问 → 看屏幕（带图）→ 输入 → 完成
    ai._push("user", "帮我看看屏幕上这个报错是什么意思")

    shot = ai.capture.grab(monitor=1 if len(ai.capture.monitors()) > 1 else 0)
    preview_path = ""
    if shot.ok:
        from pawpet.ai.vision import downscale_png

        preview_path = ai._write_preview(downscale_png(shot.preview_png, 900))

    ai._apply_event(StepEvent(
        kind="tool", tool="screenshot", risk="read", ok=True,
        text="看屏幕（显示器 1）",
        detail=shot.message if shot.ok else "截图失败",
        image_png=(shot.preview_png if shot.ok else b""),
        seconds=0.42,
    ))
    ai._push("assistant",
             "这是一个 Python 的缩进错误。第 42 行多了一个空格，"
             "删掉它就能跑通了。要我帮你打开那个文件吗？")
    ai._apply_event(StepEvent(
        kind="tool", tool="add_task", risk="read", ok=True,
        text="添加待办：「修复缩进错误」", detail="已加入待办：修复缩进错误", seconds=0.05,
    ))
    ai._apply_event(StepEvent(
        kind="tool", tool="click", risk="confirm", ok=True,
        text="点击 (640, 360) · 单击", detail="(640, 360) 左键单击", seconds=0.21,
    ))
    ai._apply_event(StepEvent(
        kind="tool", tool="run_command", risk="danger", ok=False,
        text="执行命令：del /q *", detail="这个命令在禁用列表里，拒绝执行", seconds=0.01,
    ))

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

    def shot_window(window, name: str) -> None:
        pump(400)
        image = window.grabWindow()
        if image.isNull():
            print(f"  x {name} 抓图失败")
            return
        path = OUT / f"{name}.png"
        image.save(str(path))
        print(f"  v {name}.png ({image.width()}x{image.height()})")

    dash = root.findChild(QObject, "dashboardWindow")
    dash.setProperty("x", -3600)
    dash.setProperty("y", 40)
    dash.setProperty("visible", True)
    pump(1200)
    dash.setProperty("currentPage", "ai")
    pump(1400)

    ai_page = dash.findChild(QObject, "aiPage", Qt.FindChildrenRecursively)
    right_scroll = dash.findChild(QObject, "rightScroll", Qt.FindChildrenRecursively)
    settings_section = dash.findChild(QObject, "settingsSection", Qt.FindChildrenRecursively)
    settings_panel = dash.findChild(QObject, "settingsCard", Qt.FindChildrenRecursively)
    base_field = dash.findChild(QObject, "aiBaseField", Qt.FindChildrenRecursively)

    def is_visible(item) -> bool:
        return bool(item.isVisible()) if isinstance(item, QQuickItem) else bool(
            item and item.property("visible"))

    check("AI 页面探针齐全", all((ai_page, right_scroll, settings_section,
                                  settings_panel, base_field)))
    check("未配置时模型设置强制展开",
          not backend.ai.configured and is_visible(settings_panel))
    check("未配置时接口地址可交互",
          is_visible(base_field) and base_field.isEnabled())

    os.environ["OPENAI_API_KEY"] = "preview-test-key"
    backend.ai.settingsChanged.emit()
    ai_page.setProperty("showSettings", False)
    ai_page.setProperty("showHistory", False)
    ai_page.setProperty("showMcpPanel", False)
    pump(500)
    configured_height = float(right_scroll.property("contentHeight"))
    check("配置完成后模型设置可收起", backend.ai.configured
          and not is_visible(settings_panel))
    check("已配置且默认收起时内容高度 < 900",
          configured_height < 900, f"实际 {configured_height:.1f}px")
    print(f"  [info] 已配置默认内容高度：{configured_height:.1f}px")

    print("[1] 对话状态")
    shot_window(dash, "11_ai_chat")

    # 审批卡片
    print("[2] 审批卡片")
    ai._approvalIn.emit("a1", "click", "confirm", "点击 (640, 360) · 单击",
                        False, [])
    pump(800)
    approval = dash.findChild(QObject, "approvalCard", Qt.FindChildrenRecursively)
    approval_buttons = [
        dash.findChild(QObject, name, Qt.FindChildrenRecursively)
        for name in ("approvalAllowButton", "approvalRejectButton")
    ]
    pending_height = float(right_scroll.property("contentHeight"))
    pending_y = float(right_scroll.property("contentY"))
    pending_max = max(0.0, pending_height - float(right_scroll.property("height")))
    check("待确认卡片保持可见", approval is not None and is_visible(approval))
    check("待确认按钮可见且可用",
          all(approval_buttons)
          and all(is_visible(button) and button.isEnabled()
                  for button in approval_buttons))
    check("待确认卡片自动滚到可操作位置",
          pending_y >= pending_max - 4, f"当前位置 {pending_y:.1f} / {pending_max:.1f}")
    shot_window(dash, "12_ai_approval")

    # 高危审批
    ai._approvalIn.emit("a2", "run_command", "danger",
                        "执行命令：pip install requests", False, [])
    pump(800)
    shot_window(dash, "13_ai_approval_danger")

    # 权限设置展开
    print("[3] 模型设置")
    ai.resolvePending(False)
    pump(300)
    for child in dash.findChildren(QObject):
        pass
    dash.setProperty("currentPage", "ai")
    pump(300)
    shot_window(dash, "14_ai_page")

    KEEP.extend([backend, engine, root, app, store])

    # 先拆 QML 再放 backend，否则退出时会刷一堆 of null
    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(6):
        app.processEvents()
        pump(30)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print("AI 折叠与审批可见性检查全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
