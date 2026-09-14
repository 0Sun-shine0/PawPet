"""探测：Qt.Tool + 无边框 + 置顶的窗口，到底能不能被激活。

这决定了右键菜单的修法：
  * 如果能激活 -> 用「失活即关闭」（简单、无侵入）
  * 如果不能   -> 必须上 Win32 的鼠标钩子

用法：
    .venv\\Scripts\\python.exe tools\\probe_focus.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

PROBE_QML = """
import QtQuick

// 一个文件只能有一个根对象，所以三个窗口挂在 QtObject 的具名属性下
QtObject {
    id: root

    property Window toolWin: Window {
        objectName: "toolWin"
        width: 200; height: 120
        x: -3600; y: 40
        color: "#202030"
        visible: true
        flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        Text { anchors.centerIn: parent; text: "tool"; color: "white" }
    }

    property Window plainWin: Window {
        objectName: "plainWin"
        width: 200; height: 120
        x: -3600; y: 200
        color: "#303020"
        visible: false
        flags: Qt.Window
        Text { anchors.centerIn: parent; text: "plain"; color: "white" }
    }

    property Window dialogWin: Window {
        objectName: "dialogWin"
        width: 200; height: 120
        x: -3600; y: 360
        color: "#203020"
        visible: false
        flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        Text { anchors.centerIn: parent; text: "dialog"; color: "white" }
    }
}
"""


def main() -> int:
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])

    qml_path = ROOT / ".cache" / "probe_focus.qml"
    qml_path.parent.mkdir(parents=True, exist_ok=True)
    qml_path.write_text(PROBE_QML, encoding="utf-8")

    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    root = engine.rootObjects()[0]

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    tool = root.findChild(QObject, "toolWin")
    plain = root.findChild(QObject, "plainWin")
    dialog = root.findChild(QObject, "dialogWin")

    pump(900)

    print("=== 1. Qt.Tool + 无边框 + 置顶（就是小爪的 flags）===")
    print(f"  显示后 active      = {tool.property('active')}")
    tool.setProperty("requestActivate", True)
    QTimer.singleShot(0, lambda: None)
    # requestActivate 是个方法，得用 metaobject 调
    from PySide6.QtCore import QMetaObject, Qt as QtNS

    QMetaObject.invokeMethod(tool, "requestActivate", QtNS.DirectConnection)
    pump(700)
    tool_active = bool(tool.property("active"))
    print(f"  requestActivate 后 = {tool_active}")

    print("\n=== 2. 激活另一个普通窗口，看 tool 会不会失活 ===")
    plain.setProperty("visible", True)
    QMetaObject.invokeMethod(plain, "requestActivate", QtNS.DirectConnection)
    pump(700)
    print(f"  plain active = {plain.property('active')}")
    print(f"  tool  active = {tool.property('active')}")
    deactivated = tool_active and not bool(tool.property("active"))
    print(f"  => tool 失活了吗：{deactivated}")

    print("\n=== 3. Qt.Tool 窗口能不能收到键盘事件（决定 Esc 能否用）===")
    QMetaObject.invokeMethod(tool, "requestActivate", QtNS.DirectConnection)
    pump(500)
    print(f"  tool active = {tool.property('active')}")
    can_receive_keys = bool(tool.property("active"))

    print("\n=== 4. 系统前台窗口是谁（Win32 视角）===")
    import ctypes

    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(fg)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(fg, buf, length + 1)
    print(f"  前台 hwnd = {fg}, 标题 = {buf.value!r}")
    print(f"  tool 的 hwnd = {int(tool.property('winId') or 0) if hasattr(tool, 'property') else '?'}")

    print("\n" + "=" * 52)
    print(f"结论：")
    print(f"  Tool 窗口可被激活      : {tool_active}")
    print(f"  激活其它窗口后会失活   : {deactivated}")
    print(f"  可以接收键盘事件       : {can_receive_keys}")
    if tool_active and deactivated:
        print("  => 可以用「失活即关闭」的方案，不需要 Win32 钩子")
    else:
        print("  => 激活不可靠，需要 Win32 鼠标钩子来检测点击外部")

    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(4):
        app.processEvents()
        pump(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
