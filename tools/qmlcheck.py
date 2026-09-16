"""QML 静态检查：把所有 .qml 逐个喂给 QQmlComponent，报告语法/属性错误。

为什么要单独做这个：QML 是运行时解析的，语法错了只在**加载那一刻**炸，
而且是打到 stderr 里 —— 打包成 windowed exe 之后用户根本看不到。
这个脚本把错误抓出来按文件列清楚。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402

QML_DIR = ROOT / "pawpet" / "qml"


def main() -> int:
    app = QGuiApplication(sys.argv)
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))

    files = sorted(QML_DIR.rglob("*.qml"))
    bad = 0
    for path in files:
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
        if component.isReady():
            continue
        rel = path.relative_to(ROOT)
        for err in component.errors():
            bad += 1
            print(f"[FAIL] {rel}:{err.line()}: {err.description()}")
    print(f"检查 {len(files)} 个 QML 文件，{bad} 个错误")
    del app
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
