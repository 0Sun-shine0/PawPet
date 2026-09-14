"""把 4 套宠物形象并排渲染出来，方便挑选和检查。

用法：
    .venv\\Scripts\\python.exe tools\\petgallery.py

输出 .cache/preview/pets.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

STYLES = ["mochi", "shiba", "penguin", "fox"]
LABELS = {"mochi": "麻薯猫", "shiba": "柴犬", "penguin": "企鹅", "fox": "小狐狸"}

OUT = ROOT / ".cache" / "preview"

KEEP: list = []


def main() -> int:
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.config import QML_DIR, ensure_dirs

    ensure_dirs()
    OUT.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    # 一个临时的画廊：并排摆四只，外加不同缩放
    gallery_qml = ROOT / ".cache" / "petgallery.qml"
    gallery_qml.write_text(
        """
import QtQuick
import PawPet 1.0

Window {
    id: win
    width: 860
    height: 300
    color: "#14121c"
    visible: true
    flags: Qt.FramelessWindowHint

    Row {
        anchors.centerIn: parent
        spacing: 18

        Repeater {
            model: ["mochi", "shiba", "penguin", "fox"]
            delegate: Column {
                spacing: 4
                Rectangle {
                    width: 200
                    height: 220
                    color: "transparent"
                    Pet {
                        anchors.fill: parent
                        style: modelData
                        badgeText: "3"
                        ringProgress: modelData === "mochi" ? 0.62 : 0.0
                        running: modelData === "mochi"
                        mode: "focus"
                        tailAngle: modelData === "shiba" ? -6 : 5
                    }
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: modelData
                    color: "#a99fc4"
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 12
                }
            }
        }
    }
}
""",
        encoding="utf-8",
    )

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.load(QUrl.fromLocalFile(str(gallery_qml)))
    if not engine.rootObjects():
        print("画廊加载失败")
        return 1
    root = engine.rootObjects()[0]

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    root.setProperty("x", -3600)
    root.setProperty("y", 40)
    pump(1500)

    image = root.grabWindow()
    if image.isNull():
        print("抓图失败")
        return 1
    target = OUT / "pets.png"
    image.save(str(target))
    print(f"已输出：{target}  ({image.width()}x{image.height()})")

    # 再单独导出每只的放大版，方便看细节
    for index, style in enumerate(STYLES):
        single = ROOT / ".cache" / f"pet_{style}.qml"
        single.write_text(
            f"""
import QtQuick
import PawPet 1.0

Window {{
    id: win
    width: 200
    height: 220
    color: "#14121c"
    visible: true
    flags: Qt.FramelessWindowHint

    Pet {{
        anchors.fill: parent
        style: "{style}"
        badgeText: "3"
        ringProgress: 0.62
        mode: "focus"
        tailAngle: 5
    }}
}}
""",
            encoding="utf-8",
        )
        sub = QQmlApplicationEngine()
        sub.addImportPath(str(QML_DIR))
        sub.load(QUrl.fromLocalFile(str(single)))
        if not sub.rootObjects():
            print(f"  {style} 加载失败")
            continue
        window = sub.rootObjects()[0]
        window.setProperty("x", -3600 - index * 240)
        window.setProperty("y", 400)
        pump(900)
        shot = window.grabWindow()
        if not shot.isNull():
            sub_target = OUT / f"pet_{style}.png"
            shot.save(str(sub_target))
            print(f"  {sub_target.name}  ({shot.width()}x{shot.height()})")
        KEEP.append(sub)

    KEEP.extend([engine, root, app])

    # 先拆 QML 再退出，避免刷出 of null 噪音
    for sub in KEEP:
        if isinstance(sub, QQmlApplicationEngine):
            for obj in sub.rootObjects():
                obj.deleteLater()
    for _ in range(6):
        app.processEvents()
        pump(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
