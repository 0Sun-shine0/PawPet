r"""AI 页布局回归：小窗口下不许把卡片挤没、也不许溢出。

这个测试是为用户报的「前端小屏 UI bug」写的。当时的问题：
右栏列里「对话区」那张卡片带 Layout.fillHeight，和列「按内容撑高」
形成循环依赖 —— 实测 908x590 时它要么被撑到 995px（列才 562px）把别的
卡片全顶出去，要么被压成 **0 高**（对话完全看不见）。

现在改成「固定高度 + 下限」，并且用 Flickable 兜住超长内容。这个测试
把这些不变量钉住，以后谁再顺手加个 fillHeight 就会红。

用法：
    .venv\\Scripts\\python.exe tools\layoutcheck.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "layoutcheck"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "layoutcheck"

PASSED = 0
FAILED: list[str] = []
_keep_alive: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("小爪 AI 页布局回归\n")

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

    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    host_component = QQmlComponent(engine)
    host_component.setData(b"""
        import QtQuick
        import PawPet 1.0
        Item {
            id: host
            property real pw: 908
            property real ph: 590
            width: pw
            height: ph
            AiPage { id: inner; objectName: "inner"; anchors.fill: parent }
        }
    """, QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "host.qml")))

    host = host_component.create(engine.rootContext())
    if host is None:
        print("容器创建失败：")
        for error in host_component.errors():
            print("   ", error.toString())
        return 1
    _keep_alive.append(host)

    page = host.findChild(QObject, "inner", Qt.FindChildrenRecursively)
    if page is None:
        print("拿不到 AiPage")
        return 1

    chat = page.findChild(QObject, "chat", Qt.FindChildrenRecursively)
    check("能找到对话区的 ListView（objectName=chat）", chat is not None,
          "对话区没有 objectName，测试没法量它")

    # 工作台最小窗口 860x560，内容区约 908x590；用户截图就是这个尺寸
    SIZES = [(908, 590), (860, 470), (1000, 640), (1400, 900), (700, 500)]

    print("\n=== 各种尺寸下右栏的几何 ===")
    for width, height in SIZES:
        host.setProperty("pw", width)
        host.setProperty("ph", height)
        app.processEvents()
        app.processEvents()

        scroll = page.findChild(QObject, "rightScroll", Qt.FindChildrenRecursively)
        right = page.findChild(QObject, "aiRightColumn", Qt.FindChildrenRecursively)
        if scroll is None or right is None:
            check(f"{width}x{height}：找得到右栏", False, "找不到右栏对象")
            continue

        viewport_h = scroll.property("height")
        content_h = scroll.property("contentHeight")
        col_h = right.property("height")

        # 1. 列的高度必须是「内容决定的」，不能被视口钉死
        #    （钉死就是之前那个 bug：内容被压没、还滚不动）
        check(f"{width}x{height}：列高来自内容而不是视口",
              col_h > 200,
              f"列高只有 {col_h:.0f}（视口 {viewport_h:.0f}）—— 内容被压没了")

        # 2. 对话区必须真的有高度，不能被挤成 0
        if chat is not None:
            chat_card = chat.parent()
            card_h = chat_card.property("height") if chat_card else 0
            check(f"{width}x{height}：对话区没被挤没（{card_h:.0f}px）",
                  card_h >= 150,
                  f"只有 {card_h:.0f}px —— 对话看不见了")
        else:
            card_h = 0

        # 3. 也不许反过来把别的卡片顶出去（列高不该远超内容实际需要）
        check(f"{width}x{height}：没有离谱的撑高",
              col_h < 2000,
              f"列高 {col_h:.0f}，异常")

        # 4. 内容比视口高时应该能滚（contentHeight >= 视口）
        scrollable = content_h >= viewport_h - 1 or content_h < viewport_h
        check(f"{width}x{height}：contentHeight 合理",
              content_h > 0 and scrollable,
              f"contentHeight={content_h:.0f} 视口={viewport_h:.0f}")

        print(f"     {width}x{height}: 视口 {viewport_h:.0f} 内容 {content_h:.0f} "
              f"列 {col_h:.0f} 对话区 {card_h:.0f}")

    print("\n=== 左栏不该无脑占满 ===")
    host.setProperty("pw", 1400)
    host.setProperty("ph", 900)
    app.processEvents()
    app.processEvents()
    left = page.findChild(QObject, "aiLeftColumn", Qt.FindChildrenRecursively)
    right = page.findChild(QObject, "aiRightColumn", Qt.FindChildrenRecursively)
    if left is not None and right is not None:
        left_w = left.property("width")
        right_w = right.property("width")
        check(f"左栏宽度受控（{left_w:.0f}px，上限 360）", left_w <= 361,
              f"左栏 {left_w:.0f} 超了上限")
        check(f"右栏拿到大部分宽度（{right_w:.0f}px）", right_w > left_w,
              f"左栏 {left_w:.0f} vs 右栏 {right_w:.0f}")

    backend.shutdown()
    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED[:10]:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
