"""确认 AI 页面在窄窗口下也不会被挤坏。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

KEEP: list = []


def main() -> int:
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.agent import StepEvent
    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    scratch = ROOT / ".cache" / "ai-narrow"
    scratch.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()
    backend = Backend(store)
    ai = backend.ai
    ai.context.backend = backend

    ai._push("user", "帮我看看屏幕上这个报错是什么意思，然后记一下要修复它")
    ai._push("assistant",
             "这是一个 Python 的缩进错误，第 42 行多了一个空格，删掉就能跑通。")
    ai._apply_event(StepEvent(
        kind="tool", tool="add_task", risk="read", ok=True,
        text="添加待办：「修复缩进错误」", detail="已加入待办", seconds=0.05,
    ))

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")))
    root = engine.rootObjects()[0]

    def pump(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    dash = root.findChild(QObject, "dashboardWindow")
    dash.setProperty("x", -3600)
    dash.setProperty("y", 40)
    dash.setProperty("visible", True)
    pump(1000)
    dash.setProperty("currentPage", "ai")
    pump(900)

    # 逐级收窄，检查两栏宽度是否都还合理
    print("窗口宽度 -> 左栏宽 / 右栏宽")
    failures = []
    for width in (960, 880, 820, 780, 760):
        dash.setProperty("width", width)
        pump(500)

        # 从 QML 里把两栏的实际宽度读出来
        page = dash.findChild(QObject, "aiPage")
        if page is None:
            print("  找不到 aiPage")
            failures.append("找不到 aiPage")
            break
        left = page.property("leftColumnWidth")
        right = page.property("rightColumnWidth")
        print(f"  {width:4d} -> {left:5.1f} / {right:5.1f}")
        if right < 300:
            failures.append(f"宽度 {width} 时右栏只剩 {right:.0f}px")
        if left < 200:
            failures.append(f"宽度 {width} 时左栏只剩 {left:.0f}px")

    KEEP.extend([backend, engine, root, app, store])

    # 先拆 QML 再退出，避免刷出 of null 噪音
    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(4):
        app.processEvents()
        pump(30)

    if failures:
        print("\n有问题：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n两栏在任何测试宽度下都正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
