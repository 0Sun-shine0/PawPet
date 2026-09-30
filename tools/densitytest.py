r"""E2 局部紧凑：在多个 `density` 下渲染页面，量结构 + 存截图。

## 这个文件要回答什么

E2 是**第一次真正改变视觉**的步骤，而项目没有视觉回归设施。
Codex 第 5 轮定了两层验收，这里是第一层（进 `regress.py` 的自动部分）：

  1. `density` 在**运行时**改了，界面会不会当场重排（E2 的前置条件）
  2. 页面能加载、关键控件还在
  3. 滚动区域边界正常
  4. **没有新增被裁切的文本**（相对 `density=1.0` 基线）
  5. 内容高度随 `density` 下降而下降（方向对）

第二层（截图归档 + 人工比对）用 `--shots` 出图，**不进回归**（太慢）。

## 第 4 条为什么是「新增」而不是「没有」

第一版写的是「没有文本被裁切」，结果在 **`density=1.0`（E2 之前的行为）
就有 3 处报红** —— `AiSettingsPanel.qml` 里三个 `QQuickText`：

    '每轮开始自动看一眼屏幕'   350x14   内容 176x19
    '自动记住我的习惯'         330x14   内容 128x19
    '先在上面配置模型 API Key' 332x16   内容 188x19

高度 14/16 而内容要 19 —— 是**既有现象**，不是 E2 造成的。

如果断言写成「没有裁切」，这条套件会从第一天就是红的，而红的原因和 E2
无关 —— **那种断言最后一定会被人删掉或绕过**（这正是 V2 的反面）。
所以判据是「**相对 `density=1.0` 基线，没有新增**」：
E2 的职责是不让它变坏，不是去修既有的问题。

既有的 3 处仍然**报出来**（信息行），交给 --shots 的人工比对那一层判断。

用法：
    .venv\Scripts\python.exe tools\densitytest.py            # 只跑断言
    .venv\Scripts\python.exe tools\densitytest.py --shots    # 另外存截图
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8

configure_utf8()

os.environ["QT_QPA_PLATFORM"] = "windows"
SCRATCH = ROOT / ".cache" / "densitytest"
SHOTS = ROOT / ".cache" / "density"

BASE_DENSITY = 1.0
DENSITIES = [1.0, 0.90, 0.85, 0.70]
PAGES = [("ai", "aiPage", "rightScroll"),
         ("tasks", "tasksPage", "taskList")]

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


def _seed_tasks(store) -> None:
    """灌几条待办 —— 空列表量不出高度（`contentHeight` 会是 0）。"""
    now = time.time()
    store.tasks[:] = [
        {"id": f"t{i}", "text": f"第 {i} 条待办事项，用来量高度",
         "done": False, "created": now, "done_at": None,
         "priority": 0, "due": None, "pomodoros": 0}
        for i in range(1, 7)
    ]


def _new_app(density: float):
    """每个 density 用一个**全新的 engine**。

    不靠「改属性触发重算」—— 那正是第 1 节单独要验的东西，
    不能让它成为别的断言的前提。这样 2~5 节测的是「给定 density 的渲染
    结果」，第 1 节测的是「运行时能不能切换」，两件事分开。
    """
    from PySide6.QtCore import QEventLoop, QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR, ensure_dirs
    from pawpet.store import Store

    ensure_dirs()
    app = QApplication.instance() or QApplication(sys.argv[:1])

    scratch = SCRATCH / f"d{density}"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()
    store.settings.update({"ai_mcp_enabled": False, "onboarding_done": True,
                           "update_check": False})
    store.state["pet_edge"] = ""
    _seed_tasks(store)
    backend = Backend(store)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    # **在加载 Main.qml 之前**把 density 设好
    setter = QQmlComponent(engine)
    setter.setData(f"""
        import QtQuick
        import PawPet 1.0
        QtObject {{ Component.onCompleted: Theme.density = {density} }}
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "density_set.qml")))
    setter.create(engine.rootContext())

    engine.load(QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")))
    if not engine.rootObjects():
        return None
    root = engine.rootObjects()[0]

    def pump(ms: int) -> None:
        loop = QEventLoop()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    return app, engine, root, store, backend, pump


def _clipped_texts(container, Qt, QObject) -> Counter:
    """数出**真的 `QQuickText`** 里竖向或横向装不下的（按标签计数）。

    `text` 属性不只 `Text` 有（`PawSwitch` 这类 `Control` 也有），
    所以必须按类名过滤 —— 第一版没过滤，把控件也算进来了。
    实测那 3 处**确实是 `QQuickText`**，不是假阳性。
    """
    out: Counter = Counter()
    for child in container.findChildren(QObject):
        try:
            if "Text" not in child.metaObject().className():
                continue
            if not child.isVisible():
                continue
            text = child.property("text")
            if text is None:
                continue
            cw = child.property("contentWidth")
            ch = child.property("contentHeight")
            w = child.property("width")
            h = child.property("height")
            if None in (cw, ch, w, h):
                continue
            if float(cw) - float(w) > 1.0 or float(ch) - float(h) > 1.0:
                # **key 只用文本，不带尺寸。** 第一版把 `(350x14<-176x19)`
                # 拼进了 key，结果缩紧之后同一个文本的宽度从 350 变成 356，
                # key 变了 → 被当成「新增裁切」。而它本来就是那 3 处既有的。
                out[child.objectName() or str(text)[:24]] += 1
        except Exception:
            continue
    return out


def main() -> int:
    from PySide6.QtCore import QObject, QUrl, Qt
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    # **QQuickStyle 必须在任何 QML 加载之前设**，而且只能设一次。
    # 放在 _new_app 里会对第 2 个 engine 报
    # 「must be called before loading QML that imports Qt Quick Controls 2」。
    QQuickStyle.setStyle("Basic")
    app0 = QApplication.instance() or QApplication(sys.argv[:1])

    want_shots = "--shots" in sys.argv
    print("E2 局部紧凑：多 density 结构回归\n")
    if want_shots:
        SHOTS.mkdir(parents=True, exist_ok=True)

    # ====================================== 一、运行时能切换吗
    print("=== 一、`density` 在运行时改，界面会当场重排吗（E2 的前置条件）===")
    print("  量的是**已知会随 density 变**的量：AI 页右栏的 contentHeight。")
    print("  （第一版量的是待办首行高度 338，而它受小值门槛和裸字面量影响")
    print("   **根本不随 density 变** —— 断言写成 `after <= before` 就恒真了，")
    print("   等于什么都没测。V2 说的「从不失败的断言」正是这种。）")
    boot = _new_app(1.0)
    if boot is None:
        print("QML 加载失败")
        return 1
    app, engine, root, store, backend, pump = boot
    try:
        dash = root.findChild(QObject, "dashboardWindow",
                              Qt.FindChildrenRecursively)
        dash.setProperty("x", -3600)
        dash.setProperty("y", 40)
        dash.setProperty("visible", True)
        pump(1200)
        dash.setProperty("currentPage", "ai")
        pump(1200)

        def ai_content_height() -> float:
            probe = dash.findChild(QObject, "rightScroll",
                                   Qt.FindChildrenRecursively)
            if probe is None:
                return -1.0
            return float(probe.property("contentHeight") or 0.0)

        before = ai_content_height()
        live = QQmlComponent(engine)
        live.setData(b"""
            import QtQuick
            import PawPet 1.0
            QtObject { Component.onCompleted: Theme.density = 0.70 }
        """, QUrl.fromLocalFile(
            str(ROOT / "pawpet" / "qml" / "PawPet" / "density_live.qml")))
        live.create(engine.rootContext())
        pump(1200)
        after = ai_content_height()

        print(f"  AI 页内容高：density=1.00 → {before:.0f}px，"
              f"运行时改成 0.70 → {after:.0f}px")
        # **严格变矮**，不是 `<=` —— 相等说明根本没重排，那就没证明任何事
        check("改 density 之后界面**当场**重排（绑定穿过函数调用也生效）",
              before > 0 and after > 0 and after < before,
              f"没有变矮（{before:.0f} → {after:.0f}）—— "
              f"E2 需要另想办法（比如重建界面）")
    finally:
        for obj in engine.rootObjects():
            obj.deleteLater()
        del engine
        for _ in range(6):
            app0.processEvents()
            time.sleep(0.03)

    # ====================================== 二、各 density 的结构
    print("\n=== 二、各 density 下各页面的结构 ===")
    heights: dict[str, list[tuple[float, float]]] = {}
    clipped_by: dict[tuple[float, str], Counter] = {}
    for density in DENSITIES:
        boot = _new_app(density)
        if boot is None:
            check(f"density={density} 能加载", False, "QML 加载失败")
            continue
        app, engine, root, store, backend, pump = boot
        try:
            dash = root.findChild(QObject, "dashboardWindow",
                                  Qt.FindChildrenRecursively)
            check(f"density={density}: 工作台窗创建出来了", dash is not None)
            if dash is None:
                continue
            dash.setProperty("x", -3600)
            dash.setProperty("y", 40)
            dash.setProperty("visible", True)
            pump(1200)

            for page_key, page_name, probe_name in PAGES:
                dash.setProperty("currentPage", page_key)
                pump(900)
                page = dash.findChild(QObject, page_name,
                                      Qt.FindChildrenRecursively)
                probe = dash.findChild(QObject, probe_name,
                                       Qt.FindChildrenRecursively)
                check(f"density={density} {page_key}: 页面可见",
                      page is not None and page.isVisible())
                check(f"density={density} {page_key}: 探针在（{probe_name}）",
                      probe is not None)
                if probe is None or page is None:
                    continue
                ch = float(probe.property("contentHeight") or 0.0)
                heights.setdefault(page_key, []).append((density, ch))
                clipped_by[(density, page_key)] = _clipped_texts(page, Qt, QObject)

                if want_shots:
                    pump(300)
                    image = dash.grabWindow()
                    if not image.isNull():
                        p = SHOTS / f"d{density:.2f}_{page_key}.png"
                        image.save(str(p))
                        print(f"      截图 {p.name} "
                              f"({image.width()}x{image.height()})")
        finally:
            for obj in engine.rootObjects():
                obj.deleteLater()
            del engine
            for _ in range(6):
                app0.processEvents()
                time.sleep(0.03)

    # ====================================== 三、裁切：相对基线
    print("\n=== 三、文本裁切：相对 density=1.0 基线，**没有新增** ===")
    for page_key, _pn, _pr in PAGES:
        base = clipped_by.get((BASE_DENSITY, page_key), Counter())
        if base:
            print(f"  [info] {page_key} 在 density=1.0（E2 之前）就有 "
                  f"{sum(base.values())} 处既有裁切：")
            for label, n in base.most_common():
                print(f"           {label} × {n}")
        for density in DENSITIES:
            if density == BASE_DENSITY:
                continue
            cur = clipped_by.get((density, page_key), Counter())
            new = cur - base
            check(f"density={density} {page_key}: 没有**新增**裁切",
                  not new,
                  f"新增 {dict(new)}")

    # ====================================== 四、方向
    print("\n=== 四、内容高度随 density ===")
    print(f"  {'density':>8s} {'页面':>7s} {'内容高':>10s}")
    for page_key, rows in heights.items():
        for density, ch in sorted(rows):
            print(f"  {density:8.2f} {page_key:>7s} {ch:10.1f}")
    print()
    for page_key, rows in heights.items():
        rows_sorted = sorted(rows)          # 按 density **升序**
        vals = [ch for _d, ch in rows_sorted]
        # density 升 → 内容高应当**不降**（density 越小越矮）。
        # 第一版写成 `sorted(vals, reverse=True)`（降序），方向反了 ——
        # 而实际数据是升序，于是每次都报红。**先看清楚再写方向。**
        check(f"{page_key}: density 越小内容越矮（单调不增）",
              vals == sorted(vals), f"实际 {vals}")
        if rows_sorted:
            top = rows_sorted[-1][1]
            bottom = rows_sorted[0][1]
            if top > 0:
                print(f"  [info] {page_key}: density "
                      f"{rows_sorted[-1][0]:.2f}→{rows_sorted[0][0]:.2f} 时 "
                      f"内容高 {top:.0f} → {bottom:.0f}px"
                      f"（{(top - bottom) / top * 100:.1f}%）")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
