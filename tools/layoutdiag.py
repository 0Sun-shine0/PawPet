"""看 AI 页的布局有没有溢出/压到别的栏。

重点量三件事：
  1. 左栏里的卡片有没有超出左栏的右边界（会不会压到右栏上）
  2. 卡片总高有没有超过可用高度（会让 屏幕预览 被压没）
  3. 「模型设置」展开前后，整个页面的几何怎么变

用法：
    .venv\\Scripts\\python.exe tools\\layoutdiag.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "layoutdiag"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "layoutdiag"

_keep_alive: list = []


def geom(obj) -> str:
    if obj is None:
        return "（找不到）"
    try:
        return (f"x={obj.property('x'):.0f} y={obj.property('y'):.0f} "
                f"w={obj.property('width'):.0f} h={obj.property('height'):.0f}")
    except Exception:  # noqa: BLE001
        return "（读不到几何）"


def walk_layouts(obj, depth=0, out=None):
    """收集所有带 Layout 附着属性的对象。"""
    if out is None:
        out = []
    try:
        cls = obj.metaObject().className()
    except Exception:  # noqa: BLE001
        return out
    if "Layout" in cls or obj.property("Layout.fillWidth") is not None:
        out.append((depth, obj))
    for child in obj.children():
        walk_layouts(child, depth + 1, out)
    return out


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
        str(QML_DIR / "PawPet" / "page" / "AiPage.qml")))

    # AiPage 是个 Item，得有明确的父尺寸才有意义 —— 塞进一个固定大小的容器
    host_component = QQmlComponent(engine)
    host_component.setData(b"""
        import QtQuick
        import PawPet 1.0
        Item {
            id: host
            property real pw: 620
            property real ph: 620
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
    app.processEvents()

    page = host.findChild(QObject, "inner", Qt.FindChildrenRecursively)
    if page is None:
        print("拿不到 AiPage")
        return 1

    # 工作台的尺寸范围是 860x560 ~ 任意；页面可用高度大约 = 窗口高 - 100。
    # 用户可能把窗口缩到了最小，那就是最容易暴露布局问题的情况。
    print("\n########## 各种窗口尺寸下，右栏有没有被内容顶爆 ##########")
    for width, height in ((860, 470), (900, 500), (960, 590),
                          (1100, 700), (620, 620)):
        host.setProperty("pw", width)
        host.setProperty("ph", height)
        app.processEvents()
        app.processEvents()
        right = page.findChild(QObject, "aiRightColumn", Qt.FindChildrenRecursively)
        if right is None:
            continue
        col_h = right.property("height")
        items = []
        for child in right.children():
            try:
                h = child.property("height")
                if h is None:
                    continue
                items.append((child.property("y"), h, child.property("visible")))
            except Exception:  # noqa: BLE001
                continue
        items.sort()
        bottom = max((y + h for y, h, _ in items), default=0)
        overflow = bottom - col_h
        flag = "  [!!] 溢出" if overflow > 2 else ""
        print(f"  页面 {width}x{height}：右栏高 {col_h:.0f}，"
              f"内容底边 {bottom:.0f}，溢出 {overflow:.0f}{flag}")
        # 只把**可见**的项算进去 —— ColumnLayout 会跳过隐藏项，
        # 我先前把隐藏的电池也算进底边，得出了假的溢出。
        visible_heights = [h for _, h, v in items if v]
        vis_total = sum(visible_heights)
        if vis_total > col_h + 2:
            print(f"        可见项高度合计 {vis_total:.0f} > 右栏 {col_h:.0f}"
                  f"  —— 这才是真的挤不下")

    host.setProperty("pw", 620)
    host.setProperty("ph", 620)
    app.processEvents()

    def report(label: str) -> None:
        print(f"\n--- {label} ---")
        print(f"  AiPage        {geom(page)}")
        row = page.findChild(QObject, "aiRow", Qt.FindChildrenRecursively)
        print(f"  RowLayout     {geom(row)}")
        left = page.findChild(QObject, "aiLeftColumn", Qt.FindChildrenRecursively)
        right = page.findChild(QObject, "aiRightColumn", Qt.FindChildrenRecursively)
        print(f"  左栏          {geom(left)}")
        print(f"  右栏          {geom(right)}")
        if left is not None and right is not None:
            left_right_edge = left.property("x") + left.property("width")
            gap = right.property("x") - left_right_edge
            print(f"  左栏右边界={left_right_edge:.0f}  右栏左边界="
                  f"{right.property('x'):.0f}  间距={gap:.0f}")
            if gap < -1:
                print(f"  [!!] 左栏压到右栏了，重叠 {-gap:.0f}px")

        # 左栏里每个子项，看有没有超出左栏右边界
        if left is not None:
            left_w = left.property("width")
            for child in left.children():
                if child.property("width") is None:
                    continue
                cx = child.property("x")
                cw = child.property("width")
                if cw > 0 and cx + cw > left_w + 1:
                    print(f"  [!!] 左栏子项溢出：{child.metaObject().className()} "
                          f"右边界={cx + cw:.0f} > 左栏宽 {left_w:.0f}")
            total = sum(c.property("height") or 0 for c in left.children()
                        if c.property("height") is not None)
            print(f"  左栏子项高度合计 ≈ {total:.0f}，左栏高 "
                  f"{left.property('height'):.0f}")

    report("模型设置收起")

    print("\n（把 showSettings 打开）")
    page.setProperty("showSettings", True)
    app.processEvents()
    app.processEvents()
    report("模型设置展开")

    # 把右栏里每个直接子项按 y 列出来 —— 用户截图里的顺序对不对，一眼就能看出来
    print("\n--- 右栏子项（按 y 排序）---")
    right = page.findChild(QObject, "aiRightColumn", Qt.FindChildrenRecursively)
    if right is not None:
        items = []
        for child in right.children():
            try:
                height = child.property("height")
                if height is None:
                    continue
                items.append((child.property("y"), child.property("height"),
                              child.metaObject().className(),
                              child.property("visible")))
            except Exception:  # noqa: BLE001
                continue
        items.sort()
        for y, height, cls, visible in items:
            mark = "" if visible else "  (隐藏)"
            print(f"    y={y:6.0f}  h={height:6.0f}  {cls}{mark}")
    else:
        print("    找不到右栏")

    print("\n--- 左栏子项 ---")
    left = page.findChild(QObject, "aiLeftColumn", Qt.FindChildrenRecursively)
    if left is not None:
        items = []
        for child in left.children():
            try:
                height = child.property("height")
                if height is None:
                    continue
                items.append((child.property("y"), child.property("height"),
                              child.metaObject().className(),
                              child.property("visible")))
            except Exception:  # noqa: BLE001
                continue
        items.sort()
        for y, height, cls, visible in items:
            mark = "" if visible else "  (隐藏)"
            print(f"    y={y:6.0f}  h={height:6.0f}  {cls}{mark}")

    backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
