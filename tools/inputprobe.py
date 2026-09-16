r"""输入框几何诊断：量真实 AiPage 里的那个输入框，而不是手抄的替身。

为什么单独做：我上一轮写了个「照抄一遍」的替身 QML 来量，结果量的是
我以为的代码而不是**真的**代码，用户反馈「还是这样」。
这个脚本直接加载 AiPage，用 objectName 找到真控件再量。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "inputprobe"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "inputprobe"
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

_keep_alive: list = []


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl, qInstallMessageHandler
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    warnings: list[str] = []
    qInstallMessageHandler(lambda m, c, s: warnings.append(str(s)))

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    host = QQmlComponent(engine)
    host.setData("""
        import QtQuick
        import PawPet 1.0
        Window {
            id: host
            property real pw: 1000
            property real ph: 660
            width: pw
            height: ph
            visible: true
            color: "#ffffff"
            flags: Qt.FramelessWindowHint
            AiPage { objectName: "inner"; anchors.fill: parent }
        }
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "iph.qml")))

    root = host.create(engine.rootContext())
    if root is None:
        for error in host.errors():
            print("   ", error.toString())
        return 1
    _keep_alive.append(root)

    def pump(n: int = 25) -> None:
        for _ in range(n):
            app.processEvents()

    pump()

    def find(name: str):
        return root.findChild(QObject, name, Qt.FindChildrenRecursively)

    area = find("aiInput")
    if area is None:
        print("找不到 aiInput —— AiPage 里的输入框没有 objectName？")
        return 1

    box = area.parent()

    def num(obj, prop, default=-1.0) -> float:
        try:
            value = obj.property(prop)
            return float(value) if value is not None else default
        except Exception:  # noqa: BLE001
            return default

    from PySide6.QtGui import QFontMetrics

    def probe(width: float, height: float, label: str) -> bool:
        """把宿主调到指定尺寸，量输入框。返回「够不够放一行」。"""
        root.setProperty("pw", width)
        root.setProperty("ph", height)
        pump(30)

        font = area.property("font")
        fm = QFontMetrics(font)
        need = fm.lineSpacing()
        px = num(font, "pixelSize")

        print(f"\n  --- {label}（{width:.0f}x{height:.0f}）---")
        print(f"      容器 h={num(box, 'height'):.1f}  "
              f"TextArea h={num(area, 'height'):.1f} "
              f"w={num(area, 'width'):.1f}")
        print(f"      contentHeight={num(area, 'contentHeight'):.1f}  "
              f"implicitHeight={num(area, 'implicitHeight'):.1f}  "
              f"font.pixelSize={px:.1f}  lineSpacing={need:.1f}")
        print(f"      内边距 top={num(area, 'topPadding'):.1f} "
              f"bottom={num(area, 'bottomPadding'):.1f}")

        inner = num(area, "height")
        enough = inner >= need
        print(f"      → 可用 {inner:.1f} / 需要 {need:.1f} "
              f"{'[ok]' if enough else '[XX] 放不下一行'}")
        return enough

    print("=== 真实输入框几何（多种窗口尺寸）===")
    all_ok = True
    for width, height, label in ((1000, 660, "我的测试窗口"),
                                 (1400, 900, "中等"),
                                 (1880, 1020, "接近用户截图"),
                                 (860, 470, "最小窗口")):
        all_ok &= probe(width, height, label)

    # 出图：把输入那一块单独截出来人工看
    from PySide6.QtCore import QSize

    root.setProperty("pw", 1880)
    root.setProperty("ph", 1020)
    pump(30)
    out = ROOT / ".cache" / "shots"
    out.mkdir(parents=True, exist_ok=True)

    # 用 mapToItem 把输入框的坐标映射到窗口坐标系，再截那一块。
    # 上一轮截「底部 200px」截错了地方（那一片是空的），所以这次按坐标来。
    from PySide6.QtCore import QPointF

    img = root.grabWindow()
    if not img.isNull():
        # 输入框整体（容器）的位置
        x = box.mapToItem(root.contentItem(), QPointF(0, 0)).x()
        y = box.mapToItem(root.contentItem(), QPointF(0, 0)).y()
        w = num(box, "width")
        h = num(box, "height")
        print(f"\n  输入框在窗口里的位置：({x:.0f}, {y:.0f}) {w:.0f}x{h:.0f}"
              f" / 窗口 {img.width()}x{img.height()}")
        pad = 24
        left = max(0, int(x) - pad)
        top = max(0, int(y) - pad)
        right = min(img.width(), int(x + w) + pad)
        bottom = min(img.height(), int(y + h) + pad)
        crop = img.copy(left, top, right - left, bottom - top)
        crop.save(str(out / "input-area.png"))
        print(f"  输入区截图：{out / 'input-area.png'} "
              f"({crop.width()}x{crop.height()})")
        print(f"  容器 {h:.0f}px 里，TextArea {num(area, 'height'):.0f}px，"
              f"上下各留 9px margin")

    print()
    print("=== QML 警告 ===")
    noisy = [w for w in warnings
             if "Binding loop" in w or "TypeError" in w or "undefined" in w]
    for item in noisy[:10]:
        print("  " + item.strip()[:150])
    if not noisy:
        print("  没有绑定循环 / 类型错误")

    backend.shutdown()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
