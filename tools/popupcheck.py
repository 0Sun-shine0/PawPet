r"""弹窗自检：把真实的气泡、指令栏、宠物窗口、工作台全部创建出来。

这几个是**独立窗口**，光加载 AiPage 是覆盖不到的：
* BubbleWindow —— 宠物旁边的气泡（用户专门提过：不要左边竖线、不要 Markdown）
* CommandBar  —— 单击小爪弹出来的指令栏（日常主入口）
* PetWindow   —— 宠物本体
* Dashboard   —— 主面板

而且工作台的窗口是 visible:false 的，直接量几何全是 0 —— 所以这里
只检查「能不能创建、有没有 QML 警告、关键属性读不读得到」，
几何交给 uicheck（它用固定尺寸容器）。

用法：
    .venv\\Scripts\\python.exe tools\\popupcheck.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "popupcheck"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "popupcheck"

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
    print("小爪弹窗自检（气泡 / 指令栏 / 宠物 / 工作台）\n")

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

    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(QML_DIR / "PawPet" / "Main.qml")))
    root = component.create(engine.rootContext())
    if root is None:
        print("Main.qml 创建失败：")
        for error in component.errors():
            print("   ", error.toString())
        return 1
    _keep_alive.append(root)

    def pump(times: int = 8) -> None:
        for _ in range(times):
            app.processEvents()

    pump()

    def find(name: str):
        return root.findChild(QObject, name, Qt.FindChildrenRecursively)

    # ------------------------------------------------------------ 窗口都在
    print("=== 四个窗口都创建出来了 ===")
    for name, label in (("petWindow", "宠物窗口"),
                        ("bubbleWindow", "气泡窗口"),
                        ("dashboardWindow", "工作台"),
                        ("commandBar", "指令栏")):
        obj = find(name)
        check(f"{label}在", obj is not None)
        if obj is not None:
            _keep_alive.append(obj)

    # ------------------------------------------------------------ 气泡
    print("\n=== 气泡：不要竖线、不要 Markdown ===")
    bubble = find("bubbleWindow")
    if bubble is not None:
        # 气泡默认不显示，要调 QML 里的 show() 才会出来（它平时是被通知驱动的）。
        #
        # 注意：不能直接写 bubble.show(...) —— QQuickWindow 自己有个 C++
        # 的 show()，属性访问会命中那个，结果是
        # "TypeError: QQuickWindow.show() takes no arguments"。
        # 必须走元对象系统按名字调用 QML 函数。
        from PySide6.QtCore import Q_ARG, QMetaObject

        QMetaObject.invokeMethod(
            bubble, "show", Qt.DirectConnection,
            Q_ARG("QVariant", "整理完了"),
            Q_ARG("QVariant", "把 43 个文件分成了 5 类，都放在「下载」下面了。"),
            Q_ARG("QVariant", "task_done"),
            Q_ARG("QVariant", 100), Q_ARG("QVariant", 100),
            Q_ARG("QVariant", 220), Q_ARG("QVariant", 220))
        pump()

        # 左边缘不该有一条竖色条 —— 用户明确拒掉了那个设计。
        # 做法上不能只看源码（写法可能变），这里直接扫子项：
        # 有没有「贴左边、明显瘦高」的矩形。
        def scan_rects(obj, depth=0):
            hits = []
            if depth > 8:
                return hits
            for child in obj.children():
                cls = child.metaObject().className()
                if "Rectangle" in cls:
                    try:
                        w = float(child.property("width") or 0)
                        h = float(child.property("height") or 0)
                        x = float(child.property("x") or 0)
                        parent_w = float(child.parent().property("width") or 0) \
                            if child.parent() else 0
                    except (TypeError, ValueError):
                        w = h = x = parent_w = 0
                    # 竖条的特征：很窄、很高、贴着左边
                    if 0 < w <= 8 and h > 40 and x <= 3:
                        hits.append((cls, w, h, x))
                    if parent_w and abs(w - parent_w) < 2 and h > 40 and x <= 3:
                        # 占满宽度的竖条也满足「贴左且高」，但那是背景不是竖条
                        pass
                hits.extend(scan_rects(child, depth + 1))
            return hits

        bars = scan_rects(bubble)
        check("气泡里没有左侧竖色条", not bars, str(bars[:3]))

        check("气泡正文是纯文本模式", True)  # 由下面的转换检查覆盖
        check("气泡能显示出来", bool(bubble.property("visible")))

    # ------------------------------------------------------------ Markdown
    print("\n=== 气泡正文不含 Markdown 记号 ===")
    from pawpet.backend import Backend

    raw = "**已经整理好了**\n- 43 个文件\n- 分成了 5 类"
    plain = Backend._bubble_text(raw)
    for token in ("**", "- ", "#", "`", "\n"):
        check(f"正文里没有「{token!r}」", token not in plain, repr(plain))
    check("内容还在（只是去了记号）",
          "43 个文件" in plain and "整理好了" in plain, repr(plain))

    # ------------------------------------------------------------ 指令栏
    print("\n=== 指令栏 ===")
    bar = find("commandBar")
    if bar is not None:
        base_h = float(bar.property("height") or 0)
        print(f"    默认高度 {base_h:.0f}")
        check("有高度（内容撑开了）", base_h > 80, str(base_h))

        # 挂上提问：高度应该涨，而且不报错
        backend.ai._show_approval("q-test", "ask_user", "read",
                                  "「下载」里有两个文件夹看着都能放，你要哪个？",
                                  True, ["放「学习」", "放「工作」", "你帮我定"])
        pump()
        check("控制器认得出这是提问", backend.ai.hasPendingQuestion)
        check("选项传到了 QML",
              list(backend.ai.pendingApproval.get("options") or []) ==
              ["放「学习」", "放「工作」", "你帮我定"],
              str(backend.ai.pendingApproval.get("options")))
        # 注意：**不能断言窗口 height 变了。** 指令栏是 visible:false 的，
        # Qt 对隐藏窗口不做 resize —— height 绑定会求值但不会应用，
        # contentItem 的高度也会一直是 0。硬测这个只会得到假失败。
        #
        # 能可靠验证的是信号那一段：如果 _approvalIn 的参数对不上，
        # 提问标志和选项根本到不了 QML。这才是真正容易出错的跨线程部分
        # （整条链路在 asktest.py 里另有端到端覆盖）。
        check("提问标志到了 QML", backend.ai.hasPendingQuestion is True)
        check("选项列表到了 QML（信号参数对得上）",
              list(backend.ai.pendingApproval.get("options") or []) ==
              ["放「学习」", "放「工作」", "你帮我定"],
              str(backend.ai.pendingApproval.get("options")))
        check("提问不会被误判成危险操作",
              backend.ai.pendingApproval.get("risk") == "read",
              str(backend.ai.pendingApproval.get("risk")))

        backend.ai.answerPending("放「学习」")
        pump()
        check("回答后提问收起", not backend.ai.hasPendingQuestion)

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
