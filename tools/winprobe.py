r"""在**真实的 Dashboard 窗口**里量输入框，而不是在合成容器里量。

为什么重做这个：之前我在一个自建的 Item 容器里加载 AiPage 来量尺寸，
量出来的数都对，但用户看到的还是不对。区别在于真实的 Dashboard 是
个可缩放窗口、有侧边栏、有一层 StackLayout —— 合成的容器把这些都省了。

用法：
    .venv\\Scripts\\python.exe tools\\winprobe.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "winprobe"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "winprobe"

_keep_alive: list = []


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, QPointF, Qt, QUrl, qInstallMessageHandler
    from PySide6.QtGui import QFontMetrics
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

    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(QML_DIR / "PawPet" / "Main.qml")))
    root = component.create(engine.rootContext())
    if root is None:
        for error in component.errors():
            print("   ", error.toString())
        return 1
    _keep_alive.append(root)

    def pump(n: int = 30) -> None:
        for _ in range(n):
            app.processEvents()

    pump()
    dash = root.findChild(QObject, "dashboardWindow", Qt.FindChildrenRecursively)
    _keep_alive.append(dash)

    print("=== 真实 Dashboard 窗口 ===")
    # 用户截图里的窗口明显比默认 960 宽（输入框横跨约 1500px）,
    # 所以几个宽度都要试
    for width, height, label in ((960, 680, "默认"),
                                 (1200, 800, "中等"),
                                 (1600, 900, "宽"),
                                 (1860, 1010, "接近用户截图")):
        dash.resize(width, height)
        dash.setProperty("visible", True)
        dash.setProperty("currentPage", "ai")
        pump(35)

        area = dash.findChild(QObject, "aiInput", Qt.FindChildrenRecursively)
        if area is None:
            print(f"  {label}：找不到 aiInput")
            continue
        box = area.parent()
        right = dash.findChild(QObject, "aiRightColumn",
                               Qt.FindChildrenRecursively)
        chat = dash.findChild(QObject, "chat", Qt.FindChildrenRecursively)

        fm = QFontMetrics(area.property("font"))
        spot = box.mapToItem(root.contentItem() if hasattr(root, "contentItem")
                             else dash.contentItem(), QPointF(0, 0))

        print(f"\n  --- {label} {width}x{height} ---")
        print(f"      窗口实际 {dash.property('width')}x{dash.property('height')}")
        print(f"      右栏 h={float(right.property('height')):.0f} "
              f"对话区 h={float(chat.property('height')):.0f}")
        print(f"      输入容器 h={float(box.property('height')):.1f} "
              f"w={float(box.property('width')):.1f} "
              f"y={float(box.property('y')):.1f} "
              f"在右栏内 y={spot.y():.1f}")
        print(f"      TextArea h={float(area.property('height')):.1f} "
              f"contentH={float(area.property('contentHeight')):.1f} "
              f"lineSpacing={fm.lineSpacing()}")
        enough = float(area.property("height")) >= fm.lineSpacing()
        print(f"      → {'[ok]' if enough else '[XX] 放不下一行'}")

        # 输入框必须贴着右栏底部、而且在滚动区之外。
        # 用户报过两次：「被推到折叠线以下」和「悬在中间、下面一大片空白」。
        #
        # 注意量的是 **inputCard**（外层卡片）而不是 area.parent() ——
        # 后者是卡片里那个包住 TextArea 的小 Rectangle，位置是卡片内部的
        # 相对坐标，拿它算「距底部多远」会得出 530px 这种假数字。这个坑
        # 第一次就踩了。
        pane = dash.findChild(QObject, "aiRightPane", Qt.FindChildrenRecursively)
        scroll = dash.findChild(QObject, "rightScroll", Qt.FindChildrenRecursively)
        card = dash.findChild(QObject, "inputCard", Qt.FindChildrenRecursively)
        pane_h = float(pane.property("height")) if pane else 0.0
        scroll_h = float(scroll.property("height")) if scroll else 0.0
        card_y = float(card.property("y")) if card else -1.0
        card_h = float(card.property("height")) if card else 0.0
        gap = pane_h - (card_y + card_h)
        print(f"      inputCard y={card_y:.0f} h={card_h:.0f} w="
              f"{float(card.property('width')) if card else 0:.0f}")
        print(f"      输入框底部距右栏底部 {gap:.0f}px"
              f"{'  ← 悬空了' if gap > 20 else '  [ok]'}")
        print(f"      滚动区高 {scroll_h:.0f}，输入框 y={card_y:.0f}"
              f"{'  ← 还在滚动区里' if card_y < scroll_h - 2 else '  [ok] 在滚动区之外'}")

    img = dash.grabWindow()
    out = ROOT / ".cache" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    if not img.isNull():
        img.save(str(out / "dashboard-wide.png"))
        print(f"\n  截图：{out / 'dashboard-wide.png'} "
              f"({img.width()}x{img.height()})")

    print("\n=== QML 警告 ===")
    noisy = [w for w in warnings if "Binding loop" in w or "TypeError" in w]
    for item in noisy[:10]:
        print("  " + item.strip()[:150])
    if not noisy:
        print("  没有绑定循环 / 类型错误")

    backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
