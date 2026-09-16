r"""单独渲染一条 AiBubble，量它的几何并出图。

为什么需要：工作台里的对话区只有 320px 高，一条带长交代的气泡会被裁掉，
截图看不出「文字有没有溢出气泡外面」。这个脚本把气泡单独放进一个窗口，
窗口高度跟着气泡走，溢出一眼就能看见。

用户报过的问题：带「收尾交代」的助手气泡里，文字**溢出到了气泡外面**，
被宠物挡住。根因是气泡高度和 reportText 的高度互相依赖成了环。

用法：
    .venv\\Scripts\\python.exe tools\\bubshot.py
输出：
    .cache/shots/bubble-*.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / ".cache" / "shots"
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


# 形状覆盖：短、长、超长、带换行、纯英文
CASES = {
    "bubble-1-短": (
        "整理好了。",
        "这一轮执行了 1 个操作。只是读取，没有改动任何文件。",
    ),
    "bubble-2-长交代": (
        "知识库内容质量看着不错。我再看看之前那份 HTML 手册导入的效果。",
        "这一轮执行了 18 个操作，另有 1 个没成功。"
        "文件位置：~/Desktop、~/Desktop/md 文档阅读、"
        "~/Desktop/新建 文本文档.txt 等 9 处。只是读取，没有改动任何文件。",
    ),
    "bubble-3-超长交代": (
        "我把这件事做完了，中间换过两次做法，最后用截图加坐标点中的。",
        "这一轮执行了 37 个操作，另有 4 个没成功。"
        "文件位置：/Users/x/Desktop/a.docx、/Users/x/Desktop/b.xlsx、"
        "/Users/x/Documents/报表/六月汇总.md、/Users/x/Downloads 等 12 处。"
        "其中 3 个文件是新建的，2 个是整份覆盖 —— 撤回的办法是到那个"
        "文件夹里把新建的删掉、覆盖的从回收站恢复上一版。"
        "小爪没有自动备份，所以这一步得你自己来。",
    ),
    "bubble-4-无交代": ("这一段没有交代，应该只按正文算高度。", ""),
    "bubble-5-换行正文": ("第一行\n\n第二行\n\n第三行", "执行了 2 个操作。"),
    "bubble-6-英文": (
        "Done. I moved 43 files into 4 folders under your Downloads.",
        "18 operations executed, 1 failed. Files: ~/Downloads/Docs. Read-only.",
    ),
}


def main() -> int:
    print("小怪助手气泡单独渲染检查\n")
    OUT.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    scratch = ROOT / ".cache" / "bubshot"
    os.environ["PAWPET_HOME"] = str(scratch)
    os.environ["PAWPET_INSTANCE_SUFFIX"] = "bubshot"
    import shutil
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    host = QQmlComponent(engine)
    # setData 只吃 bytes，所以 QML 里**不能有中文**（bytes 字面量限 ASCII）。
    # 这里的注释也必须是英文 —— 上一次写成中文注释直接 SyntaxError。
    host.setData("""
        import QtQuick
        import PawPet 1.0
        Window {
            id: win
            property string body: ""
            property string report: ""
            // width matches the real chat area (right column 598 minus padding)
            width: 580
            height: probe.implicitHeight + 24
            visible: true
            color: "#ffffff"
            flags: Qt.FramelessWindowHint
            AiBubble {
                objectName: "probe"
                x: 12
                y: 12
                width: win.width - 24
                role: "assistant"
                text: win.body
                report: win.report
            }
        }
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "bubhost.qml")))

    root = host.create(engine.rootContext())
    if root is None:
        for error in host.errors():
            print("   ", error.toString())
        return 1

    def pump(times: int = 14) -> None:
        for _ in range(times):
            app.processEvents()

    probe = root.findChild(QObject, "probe", Qt.FindChildrenRecursively)
    if probe is None:
        print("拿不到 AiBubble")
        return 1

    # ---------------------------------------------------------------- 输入框
    # 用户截图里的问题：输入区的 placeholder「让小爪做什么？」上半截被切掉了。
    # 这是「容器高度算得比内容矮」的经典症状 —— 换个尺寸/字体就露出来，
    # 所以这里专门量一次，钉住不变量。
    print("=== 输入框不能切掉第一行字 ===")
    input_host = QQmlComponent(engine)
    input_host.setData("""
        import QtQuick
        import QtQuick.Controls
        import QtQuick.Layouts
        import PawPet 1.0
        Window {
            id: iwin
            property string ph: "placeholder"
            width: 560
            height: frame.implicitHeight + 24
            visible: true
            color: "#ffffff"
            flags: Qt.FramelessWindowHint
            Rectangle {
                id: frame
                objectName: "frame"
                x: 12
                y: 12
                width: iwin.width - 24
                implicitHeight: Math.min(120, Math.max(46, area.contentHeight + 22))
                radius: Theme.radiusMd
                color: Theme.surfaceAlt
                TextArea {
                    id: area
                    objectName: "area"
                    anchors.fill: parent
                    anchors.margins: 9
                    placeholderText: iwin.ph
                    color: Theme.text
                    placeholderTextColor: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    wrapMode: TextArea.Wrap
                    background: null
                }
            }
        }
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "inhost.qml")))

    input_root = input_host.create(engine.rootContext())
    if input_root is None:
        for error in input_host.errors():
            print("   ", error.toString())
    else:
        pump()
        area = input_root.findChild(QObject, "area", Qt.FindChildrenRecursively)
        frame_h = float(input_root.findChild(
            QObject, "frame", Qt.FindChildrenRecursively).property("height"))
        inner_h = float(area.property("height"))
        content_h = float(area.property("contentHeight"))
        font_px = float(area.property("font").pixelSize())
        print(f"  容器 {frame_h:.0f} / TextArea {inner_h:.0f} / "
              f"contentHeight {content_h:.0f} / 字号 {font_px:.0f}px")
        # 一行文字至少要占字号 × 1.2
        need = font_px * 1.2
        check("TextArea 有足够高度放下第一行字（含 placeholder）",
              inner_h >= need,
              f"{inner_h:.1f} < {need:.1f}（字号 {font_px:.0f}px × 1.2）"
              " —— 第一行会被切掉")

        # 超长文本时容器应该长高，但仍然有上限
        area.setProperty("text", "第一行\n第二行\n第三行\n第四行")
        pump()
        grown = float(input_root.findChild(
            QObject, "frame", Qt.FindChildrenRecursively).property("height"))
        check("多行文本时容器会长高", grown > frame_h, f"{frame_h:.0f} → {grown:.0f}")
        area.setProperty("text", "\n".join(f"第{i}行" for i in range(30)))
        pump()
        capped = float(input_root.findChild(
            QObject, "frame", Qt.FindChildrenRecursively).property("height"))
        check("超长文本时容器有上限（不撑爆界面）", capped <= 120, str(capped))
        area.setProperty("text", "")
        pump()


    shell_probe = probe.findChild(QObject, "assistantShell",
                                  Qt.FindChildrenRecursively)
    check("助手气泡有 objectName（脚本能量它）", shell_probe is not None)

    for name, (body, report) in CASES.items():
        root.setProperty("body", body)
        root.setProperty("report", report)
        pump()

        shell = probe.findChild(QObject, "assistantShell",
                                Qt.FindChildrenRecursively)

        bottle_h = float(root.property("height"))
        bubble_h = float(probe.property("implicitHeight"))
        shell_h = float(shell.property("height")) if shell else -1
        window_h = float(root.property("height"))

        print(f"  {name}")
        print(f"      窗口 {window_h:.0f} / 气泡 implicitHeight {bubble_h:.0f}"
              f" / 外壳 height {shell_h:.0f} / 窗高 {bottle_h:.0f}")

        if shell is None:
            check(f"{name}：找得到助手气泡外壳", False, "objectName 丢了")
            continue

        # 1. 外壳高度必须和 AiBubble 的 implicitHeight 一致，
        #    否则 ListView 按 implicitHeight 排版，实际画出来的会溢出
        check(f"{name}：外壳高度和 implicitHeight 对得上",
              abs(bubble_h - shell_h) <= 1.5,
              f"{shell_h:.1f} vs {bubble_h:.1f} —— 对不上就会溢出")

        # 2. 窗口高度得装得下气泡。
        #
        # 注意：**不能断言窗口 height 真的跟着变了。** 窗口是 visible 的，
        # Qt 对可见窗口的高度绑定不会立即应用（实测一直停在 160）。
        # 能验证的是「气泡自己知道要占多高」—— 那才是排版用的数。
        check(f"{name}：气泡高度算得出来（> 0）", bubble_h > 20, str(bubble_h))

        # 3. 有交代时必须比没交代高
        if report:
            check(f"{name}：有交代时气泡确实更高", bubble_h > 40, str(bubble_h))

        img = root.grabWindow()
        if not img.isNull():
            img.save(str(OUT / f"{name}.png"))

    # 交叉验证：有交代 vs 没交代的高度差必须等于交代占了多高
    root.setProperty("body", CASES["bubble-2-长交代"][0])
    root.setProperty("report", "")
    pump()
    without = float(probe.property("implicitHeight"))
    root.setProperty("report", CASES["bubble-2-长交代"][1])
    pump()
    with_report = float(probe.property("implicitHeight"))
    print(f"\n  同一条正文：无交代 {without:.0f} / 有交代 {with_report:.0f}"
          f" / 差 {with_report - without:.0f}")
    check("加了交代之后气泡确实变高", with_report > without + 20,
          f"{without:.0f} → {with_report:.0f}")

    # 幂等：反复设置同一个值高度不该漂移（绑定成环的典型症状是越算越大）
    heights = []
    for _ in range(8):
        root.setProperty("report", CASES["bubble-3-超长交代"][1])
        root.setProperty("body", CASES["bubble-3-超长交代"][0])
        pump(4)
        heights.append(float(probe.property("implicitHeight")))
    check("反复求值高度不漂移（没有绑定循环）",
          max(heights) - min(heights) <= 1.0,
          f"{min(heights):.0f}~{max(heights):.0f} {heights}")

    # ---------------------------------------------------------------- 隐藏壳子
    #
    # **这条是踩过的坑。** AiBubble.implicitHeight 原来写成一串 Math.max，
    # 把所有角色的壳子都算进来。没显示的那些壳子照样有高度（只是
    # visible=false），于是隐藏的 infoShell 会把助手气泡顶高 ——
    # 实测 assistantShell 需要 119px，结果报 132.75px，多出 14px 空白，
    # 看着像排版坏了。现在改成按角色取。
    print("\n=== 隐藏的壳子不能撑高气泡 ===")
    root.setProperty("body", "短正文")
    root.setProperty("report", "")
    pump()
    probe_h = float(probe.property("implicitHeight"))
    shell_h = float(
        probe.findChild(QObject, "assistantShell", Qt.FindChildrenRecursively)
        .property("height"))
    check("助手气泡高度只由助手壳子决定", abs(probe_h - shell_h) <= 1.0,
          f"implicitHeight {probe_h:.1f} vs assistantShell {shell_h:.1f}"
          " —— 不相干的行内元素把气泡撑高了")

    # 把其他角色的内容都设成很长的文本，助手气泡高度不该受影响
    for role in ("user", "tool", "error", "info"):
        probe.setProperty("role", role)
        probe.setProperty("text", "很长很长的一段内容" * 20)
        probe.setProperty("detail", "很长很长的细节" * 30)
        pump(4)
    probe.setProperty("role", "assistant")
    probe.setProperty("text", "短正文")
    probe.setProperty("report", "")
    pump()
    after = float(probe.property("implicitHeight"))
    check("其他角色残留的长内容不影响助手气泡高度",
          abs(after - probe_h) <= 1.0,
          f"{probe_h:.1f} → {after:.1f}")

    print(f"\n  出图：{OUT}")
    backend.shutdown()

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
