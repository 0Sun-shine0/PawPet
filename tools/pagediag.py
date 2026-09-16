"""复现「点设置后界面串页」：加载**真实工作台**，切到设置，量各页面的几何。

用法：
    .venv\\Scripts\\python.exe tools\\pagediag.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "pagediag"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "pagediag"

# 防止 QML 创建出来的窗口被 Python 回收（QWindow 不能 setParent(QObject)）
_keep_alive: list = []


def show(obj, label: str) -> None:
    if obj is None:
        print(f"    {label}: 找不到")
        return
    print(f"    {label}: visible={obj.property('visible')} "
          f"x={obj.property('x')} y={obj.property('y')} "
          f"w={obj.property('width')} h={obj.property('height')}")


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(QML_DIR / "PawPet" / "Dashboard.qml")))
    dash = component.create(engine.rootContext())
    if dash is None:
        print("Dashboard 加载失败：")
        for error in component.errors():
            print("   ", error.toString())
        return 1
    # QWindow 不能 setParent(QObject)，留着 Python 引用防被回收就行
    _keep_alive.append(dash)
    app.processEvents()

    def find(name):
        return dash.findChild(QObject, name, Qt.FindChildrenRecursively)

    print("=== 默认（今日页）===")
    show(find("aiPage"), "aiPage")
    show(find("settingsPage"), "settingsPage")

    print("\n=== 切到 ai ===")
    dash.setProperty("currentPage", "ai")
    app.processEvents()
    show(find("aiPage"), "aiPage")
    show(find("settingsPage"), "settingsPage")

    print("\n=== 切到 settings（用户出问题的这一步）===")
    dash.setProperty("currentPage", "settings")
    app.processEvents()
    app.processEvents()
    show(find("aiPage"), "aiPage")
    show(find("settingsPage"), "settingsPage")

    # 把 StackLayout 找出来，看它认为当前是谁
    def find_stack(obj):
        if obj.metaObject().className().startswith("QQuickStackLayout") or \
           obj.metaObject().className().startswith("QQuickStackLayout"):
            return obj
        for child in obj.children():
            found = find_stack(child)
            if found is not None:
                return found
        return None

    stack = find_stack(dash)
    if stack is not None:
        print(f"\n  StackLayout count={stack.property('count')} "
              f"currentIndex={stack.property('currentIndex')}")
        children = [c for c in stack.children()]
        for child in children:
            cls = child.metaObject().className()
            try:
                print(f"    child {cls} visible={child.property('visible')} "
                      f"w={child.property('width')}")
            except Exception:  # noqa: BLE001
                pass
    else:
        print("\n  找不到 StackLayout")

    backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
