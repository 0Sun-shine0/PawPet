r"""逐像素回归**工具**：`density=1` 下，迁移前后不该有超出噪声的差异。

## ⚠ 这是**迁移期工具**，**不进 `regress.py`**

它比较的是「**`HEAD` 版本的 QML**」和「**工作区的 QML**」。
迁移一提交，两者就一样了，差异**恒为 0** —— 放进回归，它会变成
一条**永远通过**的断言（本项目反复强调的那种缺陷）。

所以它有两个使用场景：

  · **迁移进行中**（有未提交的 QML 改动）：跑它验「默认档视觉没变」
  · **迁移已提交**：它**拒绝运行**并说明原因（见 `main()` 开头的守卫），
    而不是给你一个空的「通过」

`densitytest.py` 才是进回归的那个 —— 它比较**不同 `density`**，
在提交前后都有意义。

## 关键设计：**噪声地板**

同一份源码连续渲两次**也会差**（抗锯齿、动画残留）。所以判据不是
「差异 == 0」，而是「**迁移差异 ≤ 噪声地板 × 2 + 底噪**」。

而且**容差本身要被检查**：噪声如果占到整屏 2% 以上，容差就大到
没有判别力，那条断言等于没写（加了 `MAX_TOLERANCE_RATIO` 断言）。
实测 `ai` 页一开始噪声 4.5% —— 切页时导航项的 `ColorAnimation` 没停，
加长等待后降到 0。

## 两个坑（都实测踩过）

  · **底部状态栏画的是数据目录路径**：每个渲染必须用**同一个**
    数据目录，否则那一行文字不同，会产生 ~2300 个假差异
  · **`today` 页有实时时钟**：两次渲染之间跳一秒就是 19 万像素不同
    （占整屏 30%）。所以它只比结构量，不做像素比较

用法：
    .venv\Scripts\python.exe tools\pxdiff.py
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8

configure_utf8()

os.environ["QT_QPA_PLATFORM"] = "windows"
BEFORE = ROOT / ".cache" / "pxdiff-before"
SHOTS = ROOT / ".cache" / "pxdiff"

# 批 1 动过的页面（后续每批把新页面加进来）
#
# ⚠ **`today` 不进像素比较。** 它上面有**实时时钟**和按时段变的问候语
# （「上午好」/「下午好」），两次渲染之间时钟跳一秒就是 19 万个像素不同。
# 实测噪声地板 193611/652800（30%）—— 拿它当容差，那条断言就**恒真**了。
# 结构量（contentHeight / height）对它仍然有意义，所以留在结构检查里。
PIXEL_PAGES = ["tasks", "notes", "settings", "reminders", "ai"]
STRUCTURE_PAGES = PIXEL_PAGES + ["today"]

# 每个页面用来量结构的探针（`None` = 只比像素，不量结构）
PROBES = {
    "tasks": "taskList",
    "notes": "noteBodyArea",
    "settings": "settingsScroll",
    "today": "todayTasksAction",
    "reminders": "remindersPage",
    "ai": "rightScroll",
}

# **容差不能超过整屏的这个比例** —— 否则断言没有判别力。
# 一条容差 30% 的「有没有差异」断言等于没写。
MAX_TOLERANCE_RATIO = 0.02

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


def export_head_qml() -> Path:
    """把 HEAD 版本的 pawpet/qml 导出到 BEFORE，返回那个 qml 根目录。"""
    if BEFORE.exists():
        shutil.rmtree(BEFORE, ignore_errors=True)
    BEFORE.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "archive", "HEAD", "pawpet/qml"],
                       cwd=ROOT, capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        tar.extractall(BEFORE, filter="data")
    return BEFORE / "pawpet" / "qml"


def render(qml_root: Path, density: float, page: str,
           scratch_name: str):
    """渲染一个页面，返回 (QImage, 结构量 dict)。

    `scratch_name` 必须由调用方显式指定：底部状态栏会画出数据目录路径，
    用不同目录会产生 ~2300 个假差异像素。
    """
    from PySide6.QtCore import QEventLoop, QObject, QTimer, QUrl, Qt
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import ensure_dirs
    from pawpet.store import Store

    ensure_dirs()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    scratch = ROOT / ".cache" / scratch_name
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    store.settings.update({"ai_mcp_enabled": False, "onboarding_done": True,
                           "update_check": False})
    store.state["pet_edge"] = ""
    now = time.time()
    store.tasks[:] = [
        {"id": f"t{i}", "text": f"第 {i} 条待办事项，用来量高度",
         "done": False, "created": now, "done_at": None,
         "priority": 0, "due": None, "pomodoros": 0}
        for i in range(1, 5)
    ]
    store.notes[:] = [{"id": "n1", "title": "测试便签",
                       "body": "内容" * 40, "created": now,
                       "updated": now}]
    backend = Backend(store)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_root))
    engine.rootContext().setContextProperty("backend", backend)
    if abs(density - 1.0) > 1e-9:
        setter = QQmlComponent(engine)
        setter.setData(f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{ Component.onCompleted: Theme.density = {density} }}
        """.encode("utf-8"),
            QUrl.fromLocalFile(str(qml_root / "PawPet" / "pxdiff_set.qml")))
        setter.create(engine.rootContext())
    engine.load(QUrl.fromLocalFile(str(qml_root / "PawPet" / "Main.qml")))
    if not engine.rootObjects():
        return None, {}
    root = engine.rootObjects()[0]

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    dash = root.findChild(QObject, "dashboardWindow",
                          Qt.FindChildrenRecursively)
    dash.setProperty("x", -3600)
    dash.setProperty("y", 40)
    dash.setProperty("visible", True)
    pump(1200)
    dash.setProperty("currentPage", page)
    pump(1000)
    # **等动画停稳再截图。**
    #
    # 实测 `ai` 页的噪声是双峰的：[0, 14584, 430] —— 侧边栏导航项有
    # `Behavior on color { ColorAnimation { duration: Theme.animFast } }`，
    # 切页时上一个选中项在渐变。原来只 pump 300ms 就截，会撞上动画中途。
    # 区域很小（x 12~155, y 278~317 = 导航项那一格 430 像素），但**不稳定**，
    # 而「偶发大差异」正是会让像素断言变得不可信的东西。
    pump(1200)

    structure: dict = {}
    probe_name = PROBES.get(page)
    if probe_name:
        item = dash.findChild(QObject, probe_name, Qt.FindChildrenRecursively)
        if item is not None:
            structure["contentHeight"] = float(
                item.property("contentHeight") or 0.0)
            structure["height"] = float(item.property("height") or 0.0)
    pump(300)
    image = dash.grabWindow()

    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(6):
        app.processEvents()
        time.sleep(0.03)
    return image, structure


def diff_pixels(a, b) -> int:
    if a is None or b is None:
        return -1
    if a.width() != b.width() or a.height() != b.height():
        return -1
    n = 0
    for y in range(a.height()):
        for x in range(a.width()):
            if a.pixel(x, y) != b.pixel(x, y):
                n += 1
    return n


def main() -> int:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    # ============================================================ 守卫
    #
    # **工作区没有未提交的 QML 改动时，这个工具没有意义** ——
    # 它比较的是 HEAD 和工作区，两者相同则差异恒为 0。
    # 那种情况下输出「全部通过」是**假信号**，所以直接拒绝运行。
    #
    # （这正是本项目反复强调的：一条永远通过的断言比没有更糟。）
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "pawpet/qml"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    changed = [ln for ln in changed.split("\n") if ln.strip()]
    if not changed:
        print("**拒绝运行：工作区没有未提交的 QML 改动。**\n")
        print("  这个工具比较「HEAD 版本的 QML」和「工作区的 QML」。")
        print("  两者相同时差异恒为 0 —— 跑出来的「通过」是空的，")
        print("  会让人以为「验过了」。")
        print()
        print("  它只在**迁移进行中**有意义（比如 margin/padding 的某一批")
        print("  改完但还没提交时）。迁移已提交的话请跑 densitytest.py。")
        return 2

    print(f"工作区有 {len(changed)} 个 QML 文件未提交 —— 可以做前后比较：")
    for ln in changed[:8]:
        print(f"    {ln.strip()}")
    if len(changed) > 8:
        print(f"    ……等 {len(changed)} 个")
    print()

    QQuickStyle.setStyle("Basic")
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    SHOTS.mkdir(parents=True, exist_ok=True)

    print("逐像素回归：density=1 下，迁移前后应当**没有超出噪声的差异**\n")
    old_root = export_head_qml()
    cur_root = ROOT / "pawpet" / "qml"
    print(f"  HEAD 版本导出到 {old_root.parent.parent}")
    print(f"  当前版本 {cur_root}")
    print(f"  像素比较的页面：{PIXEL_PAGES}")
    print(f"  只比结构的页面："
          f"{sorted(set(STRUCTURE_PAGES) - set(PIXEL_PAGES))}"
          f"（实时时钟 → 像素不可比）\n")

    for page in STRUCTURE_PAGES:
        print(f"=== {page} ===")
        # 1) 迁移前后（同目录）
        img_old, st_old = render(old_root, 1.0, page, "pxd-ab")
        img_new, st_new = render(cur_root, 1.0, page, "pxd-ab")

        # ---- 结构量：所有页面都比（不受时钟影响）----
        if st_old and st_new:
            for key in st_old:
                if key in st_new:
                    check(f"{page}: {key} 一致"
                          f"（{st_old[key]:.0f} / {st_new[key]:.0f}）",
                          abs(st_old[key] - st_new[key]) < 0.5)
        if img_old and img_new:
            img_old.save(str(SHOTS / f"{page}_before.png"))
            img_new.save(str(SHOTS / f"{page}_after.png"))

        if page not in PIXEL_PAGES:
            print("  （页面有实时时钟 —— 跳过像素比较，只比结构）\n")
            continue

        # 2) 噪声地板：同源码三次（同目录），取最大。
        # 取最大是保守做法，**但下面有一条「容差不能太大」的断言兜着** ——
        # 否则「噪声很大」会悄悄变成「断言恒真」。
        samples = [diff_pixels(*[render(cur_root, 1.0, page, "pxd-noise")[0]
                                 for _ in range(2)]) for _ in range(3)]
        floor = max([s for s in samples if s >= 0] or [0])

        moved = diff_pixels(img_old, img_new)
        total = img_old.width() * img_old.height() if img_old else 1
        tolerance = floor * 2 + 200
        ratio = tolerance / total

        print(f"  噪声地板 {floor}（三次 {samples}）  迁移差异 {moved}")
        print(f"  容差 {tolerance}（占整屏 {ratio * 100:.2f}%）")

        # **先查容差本身有没有判别力。** 太大 = 断言恒真 = 缺陷。
        check(f"{page}: 容差 {ratio * 100:.2f}% 有判别力"
              f"（上限 {MAX_TOLERANCE_RATIO * 100:.0f}%）",
              ratio <= MAX_TOLERANCE_RATIO,
              "噪声太大 —— 这条断言等于没写，该页面要先让渲染确定下来")
        check(f"{page}: density=1 迁移差异不超出噪声（{moved} ≤ {tolerance}）",
              moved >= 0 and moved <= tolerance,
              f"差异 {moved}/{total}")

        # 3) 反向检查：density=0.70 必须**超出**容差（否则没接上）
        dense, _ = render(cur_root, 0.70, page, "pxd-ab")
        dense_diff = diff_pixels(img_new, dense)
        check(f"{page}: density=0.70 与 1.0 的差异超出容差（真的接上了）",
              dense_diff > tolerance,
              f"差异 {dense_diff} ≤ 容差 {tolerance} —— "
              f"这个页面可能没接上密度轴")
        print()

    print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
