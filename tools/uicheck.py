r"""界面自检：把真实的 AiPage 放进可控尺寸的容器里量几何，并抓 QML 警告。

为什么要这个：QML 的**绑定循环**和**高度算成 0** 这两类问题不会报错 ——
它只是安安静静地画得不对（之前「对话区被压成 0 高」就是这么来的）。
所以这里把页面真正创建出来并给一个确定的尺寸，量每张卡片的高度，
同时把 Qt 打到 stderr 的警告全抓下来（绑定循环会以
"QML ... Binding loop detected" 出现）。

顺带覆盖这一批新加的界面：现成任务卡片、边做边说进度条、提问卡片。

用法：
    .venv\\Scripts\\python.exe tools\\uicheck.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "uicheck"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "uicheck"

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
    print("小爪界面自检（真实页面 + QML 警告抓取）\n")

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
    qInstallMessageHandler(lambda mode, ctx, msg: warnings.append(str(msg)))

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    # 用固定尺寸的宿主包住页面 —— 直接加载 Dashboard 是不行的：
    # 它 visible:false，没有场景就没有布局，所有几何都会是 0。
    host_component = QQmlComponent(engine)
    host_component.setData(b"""
        import QtQuick
        import PawPet 1.0
        Item {
            id: host
            property real pw: 1000
            property real ph: 640
            width: pw
            height: ph
            AiPage { id: inner; objectName: "inner"; anchors.fill: parent }
        }
    """, QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "uihost.qml")))

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

    def pump(times: int = 8) -> None:
        for _ in range(times):
            app.processEvents()

    def find(name: str):
        return page.findChild(QObject, name, Qt.FindChildrenRecursively)

    pump()

    # ------------------------------------------------------------ 基本几何
    print("=== 基本几何（1000x640）===")
    left = find("aiLeftColumn")
    right = find("aiRightColumn")
    chat = find("chat")
    scroll = find("rightScroll")

    for name, obj in (("左栏", left), ("右栏", right),
                      ("滚动区", scroll), ("对话区", chat)):
        if obj is None:
            check(f"{name}在", False, "找不到")
            continue
        print(f"    {name}: {float(obj.property('width') or 0):.0f} x "
              f"{float(obj.property('height') or 0):.0f}")

    check("左栏有宽度", left is not None and float(left.property("width")) > 200,
          str(left.property("width") if left else None))
    check("对话区没被压成 0 高",
          chat is not None and float(chat.property("height")) > 100,
          str(chat.property("height") if chat else None))
    check("右栏高度来自内容",
          right is not None and float(right.property("height")) > 300,
          str(right.property("height") if right else None))

    # ------------------------------------------------------------ 新卡片
    print("\n=== 现成任务卡片 ===")
    templates = find("templateCard")
    check("卡片存在", templates is not None)
    if templates is not None:
        h = float(templates.property("height") or 0)
        print(f"    高度 {h:.0f}，可见 {bool(templates.property('visible'))}")
        check("有高度（内容没塌）", h > 160, str(h))
        check("空闲时可见", bool(templates.property("visible")))
        check("默认停在第一组", int(templates.property("activeGroup")) == 0)

        # 分组切换要真的能切
        for index, label in ((1, "上班党"), (2, "日常"), (0, "学生")):
            templates.setProperty("activeGroup", index)
            pump(4)
            check(f"能切到「{label}」分组",
                  int(templates.property("activeGroup")) == index)
            check(f"切到「{label}」后高度仍正常",
                  float(templates.property("height") or 0) > 160,
                  str(templates.property("height")))

    print("\n=== 边做边说进度条 ===")
    # 它是 visible: backend.ai.running 驱动的，跑起来才出现。
    # 这里直接检查控制器属性可读 + 空时间线时不显示。
    check("progressLine 默认为空", backend.ai.progressLine == "",
          repr(backend.ai.progressLine))
    check("stepTimeline 默认空", backend.ai.stepTimeline == [],
          str(backend.ai.stepTimeline))

    print("\n=== 提问卡片 ===")
    check("没有待回答时 hasPendingQuestion 为 False",
          backend.ai.hasPendingQuestion is False)
    check("提问和审批不会同时挂着",
          not (backend.ai.hasPendingQuestion and backend.ai.hasPendingApproval))

    # ------------------------------------------------------------ 尺寸遍历
    print("\n=== 小窗口下不许挤坏 ===")
    for width, height in ((908, 590), (760, 500)):
        host.setProperty("pw", width)
        host.setProperty("ph", height)
        pump()
        card_h = float(templates.property("height") or 0) if templates else 0
        chat_h = float(chat.property("height") or 0) if chat else 0
        right_h = float(right.property("height") or 0) if right else 0
        print(f"    {width}x{height}: 任务卡 {card_h:.0f} 对话区 {chat_h:.0f} "
              f"右栏 {right_h:.0f}")
        check(f"{width}x{height}：任务卡没塌", card_h > 160, str(card_h))
        check(f"{width}x{height}：对话区还在", chat_h > 100, str(chat_h))
        check(f"{width}x{height}：右栏没被撑爆", 300 < right_h < 2600, str(right_h))

    # ------------------------------------------------------------ 警告
    print("\n=== QML 警告 ===")
    noisy = [w for w in warnings
             if any(token in w for token in
                    ("Binding loop", "TypeError", "Unable to assign",
                     "is not a function", "Cannot assign", "undefined",
                     "Unable to determine"))]
    if noisy:
        for item in noisy[:12]:
            print("    " + item.strip()[:160])
    else:
        print("    干净：没有绑定循环 / 类型错误 / undefined")
    check("没有绑定循环", not any("Binding loop" in w for w in warnings),
          str([w for w in warnings if "Binding loop" in w][:2]))
    check("没有 undefined / 类型错误", not noisy, str(noisy[:2]))

    backend.shutdown()
    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
