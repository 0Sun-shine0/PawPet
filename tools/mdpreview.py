"""把用户实际遇到的那段 Markdown 输出渲染出来，肉眼确认可读性。

输出 .cache/preview/30_md_before.png（纯文本，问题现场）
     .cache/preview/31_md_after.png （富文本，修复后）
     .cache/preview/32_cmdbar.png   （指令栏形态）
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

OUT = ROOT / ".cache" / "preview"

# 就是用户截图里那段输出的结构
SAMPLE = """现在你的屏幕上有这些内容：

**1. 背景是一个 WPS Office 文字文档（占据大部分屏幕）**

- 打开了好几个标签页：`AI模板`、`售后服务运营规范2026.pdf`、`新员工入职指引及培训计划`
- 正文是一份《新员工入职指引及培训计划》，能看到「5.3 第三阶段：软件安装」「5.6 第六阶段：交接培训」
- 底部状态栏：第 11/13 页，字数 4864，缩放 100%

**2. 屏幕中间浮动着一个「小爪工作台」窗口**

- 左侧菜单：今日 / 专注 / 待办 / 便签 / 提醒 / AI 操作 / 设置
- 当前停在「AI 操作」页

> 需要我帮你做什么吗？"""

KEEP: list = []


def main() -> int:
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.agent import StepEvent
    from pawpet.backend import Backend
    from pawpet.config import QML_DIR, ensure_dirs
    from pawpet.store import Store

    ensure_dirs()
    OUT.mkdir(parents=True, exist_ok=True)

    scratch = ROOT / ".cache" / "md-preview"
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

    ai._push("user", "看看我的屏幕上有什么")
    ai._push("assistant", SAMPLE)
    ai._apply_event(StepEvent(
        kind="tool", tool="screenshot", risk="read", ok=True,
        text="看屏幕（显示器 1）", detail="已捕获屏幕 1920x1080（缩放为 1400x788 发送）",
        seconds=0.38,
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
        pump(500)
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
    pump(1100)
    dash.setProperty("currentPage", "ai")
    pump(1400)
    print("[1] 工作台里的对话")
    shot_window(dash, "31_md_rich")

    # 指令栏形态
    print("[2] 指令栏")
    dash.setProperty("visible", False)
    pump(300)
    bar = root.findChild(QObject, "commandBar")
    bar.setProperty("x", -3600)
    bar.setProperty("y", 300)
    backend.commandBarVisible = True
    pump(1000)
    shot_window(bar, "32_cmdbar")

    KEEP.extend([backend, engine, root, app, store])

    # 按正确顺序拆除，否则解释器退出时 QML 绑定会引用已回收的 backend，
    # 刷出几百行 "Cannot read property ... of null" 噪音。
    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(6):
        app.processEvents()
        pump(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
