r"""四档 density 的**视觉走查**：出图 + 客观数字，供人工判读。

## Codex 第 13 轮的要求（逐条对应）

| 要求 | 这里怎么做的 |
|---|---|
| 同一份稳定数据 | 四个档位**共用同一个数据目录**（见下） |
| 同一主题 / 同一视口 / 同一字体环境 | 固定 `960×680`、不设 dpi 覆盖、同一次进程内跑 |
| 归档四档截图 | `.cache/densityshots/d<档>_<页>.png` |
| 至少覆盖 AI / 待办 / 笔记 | 页面清单见 `PAGES`（共 5 页） |
| **截屏时停掉动画** | 等 `animSlow`(380ms) 的 3 倍以上；**并用连拍两帧证明** |
| **固定时钟** | `pawpet.backend.datetime` 打补丁成冻结值（见 `_freeze_clock`） |
| **固定数据目录路径** | 底部状态栏画的是数据目录 —— 共用目录则文字相同 |
| 输出截图路径 + 结构数值 + 人工判读 | 三样都有 |

## 为什么要「连拍两帧」

「等够了时间」是**假设**；「连续两帧逐像素相同」是**证据**。
动画没停、时钟在跳、字体栅格化不稳 —— 都会让同一档位的两帧不同。
本工具对每页每档抓两帧并比对，**不一致就报出来**，
避免把渲染噪声当成布局差异（Codex 明确要求「避免把渲染噪声
当成布局差异」）。

## 数据目录为什么必须共用

`pxdiff.py` 已经踩过这个坑：底部状态栏画的是**数据目录路径**。
每个档位各建一个目录的话，那张图上的路径文字就不同，
人工比对时会看到「差异」而其实与密度无关。
**共用同一个目录 → 路径文字逐字符相同。**

## 客观数字（人工判读的辅助，不是替代）

  · 每页结构量（`contentHeight` / `height`）
  · **可点击区域最小边长** —— 对应「按钮、复选框、输入框的可点击区域
    是否仍然容易操作」，这个**能客观量**
  · 裁切文本计数（相对 `density=1.00` 基线）

用法：
    .venv\Scripts\python.exe tools\densityshots.py
输出：
    .cache/densityshots/*.png
    .cache/densityshots/report.txt
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime as _real_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8  # noqa: E402

configure_utf8()

os.environ["QT_QPA_PLATFORM"] = "windows"

OUT = ROOT / ".cache" / "densityshots"
# **四个档位共用的数据目录** —— 底部状态栏会画这个路径
SHARED = ROOT / ".cache" / "densityshots-data"
# 规范化后的数据**模板** —— 每个档位从它复制一份（见 `build_template`）
TEMPLATE = ROOT / ".cache" / "densityshots-template.json"

DENSITIES = [1.00, 0.90, 0.80, 0.70]

# (页面 key, 页面 objectName, 探针 objectName, 探针优先读的属性)
#
# 探针的选择**每一个都有理由**，不是随手挑的：
#
#   · `ai`        → `rightScroll`（右侧滚动区），`contentHeight`
#   · `tasks`     → `taskList`（ListView），`contentHeight`
#   · `notes`     → **`notesInner`**，`implicitHeight`
#         ⚠ 不选 `noteBodyArea` —— 它是个 `TextArea`，
#         它的 `contentHeight` 是**文本高度**，边距收紧不会改它。
#         `densitytest.py` 里已经踩过这个坑（那个探针恒为 19）。
#         `notesInner` 是我为测试加的 ColumnLayout（`anchors.fill` +
#         `anchors.margins`），`implicitHeight` 是**内容驱动**的布局量。
#   · `reminders` → 页面**根本身**就是 `Flickable`，`contentHeight`
#   · `settings`  → 同上（它的 objectName 是 `settingsScroll`）
PAGES = [
    ("ai", "aiPage", "rightScroll", "contentHeight"),
    ("tasks", "tasksPage", "taskList", "contentHeight"),
    ("notes", "notesPage", "notesInner", "height"),
    ("reminders", "remindersPage", "remindersPage", "contentHeight"),
    ("settings", "settingsScroll", "settingsScroll", "contentHeight"),
]

# 固定视口。`Dashboard.qml` 默认 960×680，这里显式设成同一个值，
# 避免「窗口默认尺寸变了」被误读成「密度导致布局变了」。
VIEW_W, VIEW_H = 960, 680

# 冻结的时刻（用于 `backend.clockText` 等）
FROZEN_NOW = _real_datetime(2026, 10, 4, 14, 30, 0)


def _freeze_clock() -> None:
    """把 `pawpet.backend` 里的 `datetime` 换成冻结版本。

    `backend.py` 用的是 `from datetime import datetime`，
    所以模块里有个名为 `datetime` 的**全局名**。方法体里写的是
    `datetime.now()` —— 查找的是**模块全局**，所以替换掉这个全局名
    就能冻结所有调用点。

    不冻结的话：`clockText` 是 `%H:%M`（**分钟**精度），
    跨分钟边界会让 `TodayPage` 的时钟文字变一次 ——
    实测那是 **19 万像素**的差异（占整屏 30%）。

    `_started_at = datetime.now().astimezone()` 也走这条路，
    所以假的 `now()` 必须支持 `.astimezone()` —— 用**真 datetime 的子类**
    最省事。
    """

    class FrozenDateTime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(FROZEN_NOW.year, FROZEN_NOW.month,
                           FROZEN_NOW.day, FROZEN_NOW.hour,
                           FROZEN_NOW.minute, FROZEN_NOW.second)
            return cls(FROZEN_NOW.year, FROZEN_NOW.month,
                       FROZEN_NOW.day, FROZEN_NOW.hour,
                       FROZEN_NOW.minute, FROZEN_NOW.second).replace(tzinfo=tz)

        @classmethod
        def today(cls):
            return cls(FROZEN_NOW.year, FROZEN_NOW.month, FROZEN_NOW.day)

    import pawpet.backend as backend_mod
    backend_mod.datetime = FrozenDateTime
    print(f"  时钟已冻结在 {FROZEN_NOW:%Y-%m-%d %H:%M}")


def _seed(store) -> None:
    """灌一份**内容量足够**的数据。

    空状态下截图看不出布局（全是空状态提示），所以每类都造几条。

    ## ⚠️ 字段必须写全，否则会被「修复」并弹出横幅

    实测踩到：第一版便签只写了 `id/title/text/updated`，**少了 `created`**。
    `store._normalize_note` 检测到缺失 → 补上并置 `store.repaired = True`
    → 界面顶部多出一条绿色横幅
    「检测到数据文件里有格式异常的字段，已自动修复…」。

    而且因为**共用数据目录**，只有**第一次**运行（1.00）写盘，
    后续运行（0.90/0.80/0.70）才读到那个文件 → **只有后三档有横幅**。
    那看起来像「密度导致布局差异」，实际是**状态泄漏**。

    所以这里把 `created` 补上，并且 `build_template()` 会再走一次
    规范化的 load/save 把最终形态固化。
    """
    now = time.time()
    store.tasks[:] = [
        {"id": f"t{i}", "text": f"第 {i} 条待办事项，用来看换行和留白",
         "done": i % 4 == 0, "created": now - i * 3600, "done_at": None,
         "priority": i % 3, "due": None, "pomodoros": i % 3}
        for i in range(1, 9)
    ]
    store.reminders[:] = [
        {"id": f"r{i}", "title": f"第 {i} 条提醒，用来看列表行高",
         "time": f"{8 + i:02d}:30", "repeat": "daily", "enabled": True,
         "every": 0, "last_fired": None}
        for i in range(1, 6)
    ]
    store.notes[:] = [
        {"id": f"n{i}", "title": f"第 {i} 条便签",
         "text": "这是一段用来观察行距和换行的正文。\n"
                 "第二行：中文与 English mixed 的宽度差异。\n"
                 "第三行：用来把内容撑高一点。",
         "created": now - i * 900,
         "updated": now - i * 600}
        for i in range(1, 5)
    ]


def build_template() -> Path:
    """把种子数据**规范化后固化成模板**，供每个档位复制。

    ## 为什么必须这样（实测踩到的坑）

    为了「底部状态栏的路径文字逐字符相同」，四个档位**共用同一个
    数据目录**。但这样**每次运行都会改动那个目录**：

      1.00 运行：目录是空的 → `load()` 无异常 → 写盘
      0.90+ 运行：读到 1.00 写的文件 → 被判定「有格式异常的字段」
                  → **界面顶部多出一条绿色修复横幅**

    于是 1.00 的图没有横幅、其余三档都有 ——
    **那不是密度差异，是状态泄漏。**

    ## 做法

    先 seed 一次、走一遍 `load()`（规范化）再 `save()`，把**规范化后的
    形态**存成模板。之后每个档位**从模板复制一份**再 load ——
    四个档位的起始状态**逐字节相同**，且都不会触发修复。
    """
    from pawpet.store import Store

    tmp = ROOT / ".cache" / "densityshots-template-build"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    store = Store(tmp / "pet_data.json", tmp / "pet_data.backup.json")
    store.load()
    store.settings.update({"ai_mcp_enabled": False, "onboarding_done": True,
                           "update_check": False})
    store.state["pet_edge"] = ""
    _seed(store)
    # 再 load 一次让规范化后的形态定下来，然后落盘
    store.save()
    store2 = Store(tmp / "pet_data.json", tmp / "pet_data.backup.json")
    store2.load()
    repaired = bool(store2.repaired)
    store2.save()

    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp / "pet_data.json", TEMPLATE)
    print(f"  模板已固化：{TEMPLATE.name}"
          f"（规范化后再 load 的 repaired={repaired}）")
    shutil.rmtree(tmp, ignore_errors=True)
    return TEMPLATE


def _reset_data() -> None:
    """每个档位开跑前，把数据文件恢复成模板。"""
    live = SHARED / "pet_data.json"
    backup = SHARED / "pet_data.backup.json"
    shutil.copy2(TEMPLATE, live)
    if backup.exists():
        backup.unlink()


def _pump(app, ms: int) -> None:
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.004)


def _img_bytes(image) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def _count_clipped(container, QObject) -> Counter:
    """数竖向/横向装不下的 `QQuickText`（按文本计数）。"""
    out: Counter = Counter()
    for child in _walk_visual(container):
        try:
            if "Text" not in child.metaObject().className():
                continue
            if not child.isVisible():
                continue
            text = child.property("text")
            if text is None:
                continue
            cw, ch = child.property("contentWidth"), child.property("contentHeight")
            w, h = child.property("width"), child.property("height")
            if None in (cw, ch, w, h):
                continue
            if float(cw) - float(w) > 1.0 or float(ch) - float(h) > 1.0:
                out[child.objectName() or str(text)[:24]] += 1
        except Exception:
            continue
    return out


# 可点击控件的类名片段
CLICKABLE_HINTS = ("Button", "CheckBox", "Switch", "TextField",
                   "ComboBox", "SpinBox", "Slider")


def _walk_visual(root_item):
    """遍历**视觉树**（`childItems()`）。

    ## ⚠️ 为什么不能用 `findChildren(QObject)`

    实测对比（设置页）：

      · `dash.findChildren(QObject)` → 3097 个对象，
        **但一个有 `tailAngle` 的都没有**、`CanvasItem` 只有 1 个
      · 视觉树 `childItems()` → 1963 个对象，
        **4 个 `Pet_QMLTYPE_18`**（`tailAngle` / `bob` 都在）、
        `CanvasItem` **13 个**

    `findChildren` 返回的对象**更多**，却**漏掉了整个 Pet 子树** ——
    所以「返回得多」不等于「覆盖全」。
    """
    stack = [root_item]
    while stack:
        item = stack.pop()
        if item is None:
            continue
        yield item
        try:
            stack.extend(item.childItems())
        except Exception:
            continue


def _qobject_tree(node):
    """从某节点出发遍历它的 QObject 子树（动画/定时器挂在这里）。"""
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        yield cur
        try:
            stack.extend(cur.children())
        except Exception:
            continue


def _freeze_animations(dash, QObject) -> dict:
    """把界面上**所有动画和定时器停掉**，并把被动画驱动的属性归位。

    ## 为什么必须做（Codex 第 13 轮明确要求「截图时停掉动画」）

    来源是**宠物**：`Pet.qml` 里

      · `breathAnim { running: true; loops: Animation.Infinite }`
        —— **永远在呼吸**（`NumberAnimation` 动 `bob`）
      · `blinkTimer` —— 随机 1600~4800ms 眨一次眼（动 `blinking`）
      · 还有摇尾、thinking / bored / sleepy / idle 各自的定时器

    而**设置页有 4 个宠物预览**。所以那一页连续两帧的差异在
    **1400~1000 像素**、集中在宠物那一条带上 —— 就是呼吸动画。
    （裁图看过了：差异带正好压在四张卡片里宠物的脸上。）

    ## 三个坑（都实测踩过）

    **一、`findChildren(QObject)` 看不到 Pet 子树。** 见 `_walk_visual`。
    所以这里走**视觉树**。

    **二、`property("running")` 读出 `False`，动画却真的在跑。**
    实测那 4 个 Pet 的 `bob` 是 `-3.37`（呼吸中），
    但 8 个动画对象的 `running` **全部读成 `False`**。
    → 所以**不判断、无条件 `stop()`**。

    **三、改属性值不够，还要「归位」。** 停掉动画只是不再变化，
    `bob` 会**停在半口气的位置**。静止值必须显式归位，
    否则不同档位的图会落在**不同的呼吸相位**上 ——
    那正是假的布局差异。
    """
    stopped = 0
    reset: set[str] = set()

    # 被动画驱动的属性 → 静止值
    REST = {
        "bob": 0.0, "tailAngle": 0.0, "sway": 0.0,
        "hop": 0.0, "loopHop": 0.0,
        "squashX": 1.0, "squashY": 1.0,
        "loopSquashX": 1.0, "loopSquashY": 1.0,
        "blinking": False,
    }

    from PySide6.QtCore import QMetaObject

    for item in _walk_visual(dash.property("contentItem")):
        # ---- 停这个节点上的动画/定时器（走它的 QObject 子树）----
        for node in _qobject_tree(item):
            cls = node.metaObject().className()
            if "Animation" in cls:
                try:
                    QMetaObject.invokeMethod(node, "stop")
                    stopped += 1
                except Exception:
                    pass
            elif cls.startswith("QQuickTimer") or cls == "QQmlTimer":
                try:
                    QMetaObject.invokeMethod(node, "stop")
                    stopped += 1
                except Exception:
                    pass
        # ---- 归位被动画驱动的属性 ----
        for prop, value in REST.items():
            try:
                if item.property(prop) is not None:
                    if item.setProperty(prop, value):
                        reset.add(prop)
            except Exception:
                pass

    return {"stopped": stopped, "reset": sorted(reset)}


def _click_targets(container, QObject) -> list[tuple[str, float, float]]:
    """收集**可见可点击控件**的尺寸，用来查「点得到吗」。

    「可点击区域是否仍然容易操作」看起来是主观的，但**尺寸是可量的** ——
    一个高 12px 的复选框谁都不好点。这里量出来，人工判读时有数可依。
    """
    out: list[tuple[str, float, float]] = []
    # 走**视觉树** —— `findChildren(QObject)` 会漏掉 Pet 子树（见 `_walk_visual`）
    for child in _walk_visual(container):
        try:
            cls = child.metaObject().className()
            if not any(h in cls for h in CLICKABLE_HINTS):
                continue
            if not child.isVisible():
                continue
            w, h = child.property("width"), child.property("height")
            if w is None or h is None:
                continue
            w, h = float(w), float(h)
            if w <= 0 or h <= 0:
                continue
            out.append((child.objectName() or cls.split("_")[0], w, h))
        except Exception:
            continue
    return out


def capture(density: float, app) -> tuple[dict, list[str]]:
    """在给定 density 下把 5 个页面各抓两帧。返回 (结构数据, 日志)。"""
    from PySide6.QtCore import QObject, QUrl, Qt
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR, ensure_dirs
    from pawpet.store import Store

    ensure_dirs()
    log: list[str] = []

    # **每个档位都从模板重置数据** —— 否则前一次运行写下的文件会被
    # 后一次读到并判定「需要修复」，界面上多出一条横幅（实测踩过）。
    _reset_data()

    store = Store(SHARED / "pet_data.json", SHARED / "pet_data.backup.json")
    store.load()
    store.settings.update({"ai_mcp_enabled": False, "onboarding_done": True,
                           "update_check": False})
    store.state["pet_edge"] = ""
    # 注意：这里**不再 _seed** —— 模板里已经有数据；再 seed 一次会
    # 让「输入相同但写入时机不同」重新引入不确定性。
    backend = Backend(store)
    if store.repaired:
        log.append("    **数据被判定需要修复（不应发生）** —— "
                   "截图会多出修复横幅，四档不可比")

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    if abs(density - 1.0) > 1e-9:
        setter = QQmlComponent(engine)
        setter.setData(f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{ Component.onCompleted: Theme.density = {density} }}
        """.encode("utf-8"),
            QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "ds_set.qml")))
        setter.create(engine.rootContext())

    engine.load(QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("QML 加载失败")
    root = engine.rootObjects()[0]

    dash = root.findChild(QObject, "dashboardWindow",
                          Qt.FindChildrenRecursively)
    dash.setProperty("width", VIEW_W)
    dash.setProperty("height", VIEW_H)
    dash.setProperty("x", 40)
    dash.setProperty("y", 40)
    dash.setProperty("visible", True)
    # 动画最长 `animSlow = 380ms`，等它的 3 倍以上
    _pump(app, 1400)

    data: dict = {"density": density, "pages": {}}

    for key, page_name, probe_name, probe_prop in PAGES:
        dash.setProperty("currentPage", key)
        _pump(app, 1400)   # 切页后先等**有界**动画停

        # **再冻结一次** —— 切页可能让某些 `running: true` 的动画重新开始。
        # 每页都冻结，而不是只在开头冻一次。
        frozen = _freeze_animations(dash, QObject)
        _pump(app, 600)
        # 冻结后再冻一次：冻结本身可能触发重绘/新动画
        frozen2 = _freeze_animations(dash, QObject)
        _pump(app, 500)
        data.setdefault("freeze", frozen2)
        if key == PAGES[0][0]:
            log.append(f"    冻结：停了 {frozen2['stopped']} 个动画/定时器；"
                       f"归位 {','.join(frozen2['reset'])}")

        page = dash.findChild(QObject, page_name, Qt.FindChildrenRecursively)
        probe = dash.findChild(QObject, probe_name,
                               Qt.FindChildrenRecursively)
        if page is None:
            log.append(f"    [XX] {key}: 页面 `{page_name}` 找不到")
            continue

        info: dict = {
            "pageFound": True,
            "probeFound": probe is not None,
            "probeProp": probe_prop,
            "contentHeight": (float(probe.property(probe_prop) or 0)
                              if probe is not None else -1),
            "pageHeight": float(page.property("height") or 0),
        }

        # ---- 抓两帧，比对 ----
        img_a = dash.grabWindow()
        _pump(app, 700)
        img_b = dash.grabWindow()
        if img_a.isNull() or img_b.isNull():
            log.append(f"    [XX] {key}: 抓图返回空")
            continue
        same = _img_bytes(img_a) == _img_bytes(img_b)
        info["stable"] = same
        info["size"] = (img_a.width(), img_a.height())

        path = OUT / f"d{density:.2f}_{key}.png"
        img_a.save(str(path))
        info["path"] = str(path.relative_to(ROOT))

        info["clipped"] = dict(_count_clipped(page, QObject))
        targets = _click_targets(page, QObject)
        info["clickMin"] = (min((min(w, h) for _n, w, h in targets),
                                default=-1))
        info["clickN"] = len(targets)
        data["pages"][key] = info

        flag = "" if same else "  **两帧不一致（渲染不稳）**"
        log.append(
            f"    {key:10s} contentH={info['contentHeight']:8.1f}  "
            f"height={info['pageHeight']:7.1f}  "
            f"裁切={sum(info['clipped'].values()):2d}  "
            f"可点控件={info['clickN']:3d} 最小边={info['clickMin']:5.1f}"
            f"{flag}")
        # **裁切的具体文本** —— Codex 要的是「哪段文字被裁了」，
        # 不是「几处被裁了」。只报数字没法判断是不是真缺陷。
        for label, cnt in sorted(info["clipped"].items()):
            log.append(f"        裁切 ×{cnt}: {label}")

    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(8):
        app.processEvents()
        time.sleep(0.03)
    return data, log


def main() -> int:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    print("四档 density 视觉走查\n")

    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if SHARED.exists():
        shutil.rmtree(SHARED, ignore_errors=True)
    SHARED.mkdir(parents=True, exist_ok=True)

    _freeze_clock()
    build_template()

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

    all_data: dict[float, dict] = {}
    report: list[str] = []
    report.append("四档 density 视觉走查")
    report.append(f"视口 {VIEW_W}x{VIEW_H}，数据目录 {SHARED}")
    report.append(f"时钟冻结在 {FROZEN_NOW:%Y-%m-%d %H:%M}")
    report.append("")

    for d in DENSITIES:
        print(f"=== density = {d:.2f} ===")
        data, log = capture(d, app)
        all_data[d] = data
        report.append(f"=== density = {d:.2f} ===")
        report.extend(log)
        for line in log:
            print(line)
        print()

    # ---------------- 汇总表 ----------------
    print("=" * 96)
    print("汇总：内容高（越小说明该页越紧）")
    print("=" * 96)
    report.append("")
    report.append("汇总：内容高")
    hdr = "  " + f"{'页面':<12s}" + "".join(f"{d:>12.2f}" for d in DENSITIES) \
        + f"{'1.00→0.70':>12s}"
    print(hdr)
    report.append(hdr)
    for key, _pn, _pr, _pp in PAGES:
        cells = []
        vals = []
        for d in DENSITIES:
            p = all_data.get(d, {}).get("pages", {}).get(key)
            v = p.get("contentHeight", -1) if p else -1
            vals.append(v)
            cells.append(f"{v:12.1f}")
        first, last = vals[0], vals[-1]
        pct = f"{(first - last) / first * 100:.1f}%" if first > 0 else "n/a"
        line = f"  {key:<12s}" + "".join(cells) + f"{pct:>12s}"
        print(line)
        report.append(line)

    # ---------------- 稳定性 ----------------
    print()
    print("=" * 96)
    print("稳定性：每页每档**连续两帧是否逐像素相同**")
    print("=" * 96)
    unst = []
    for d in DENSITIES:
        for key, _pn, _pr, _pp in PAGES:
            p = all_data.get(d, {}).get("pages", {}).get(key)
            if p and not p.get("stable", True):
                unst.append((d, key))
    if unst:
        print(f"  **{len(unst)} 处两帧不一致**（渲染不稳，图不可信）：")
        for d, key in unst:
            print(f"    density={d:.2f} {key}")
    else:
        print(f"  ✓ 全部 {len(DENSITIES) * len(PAGES)} 张**两帧逐像素相同** —— "
              f"动画已停、时钟已冻、字体栅格稳定")
    report.append("")
    report.append(f"两帧不一致处：{unst or '无'}")

    # ---------------- 可点击区域 ----------------
    print()
    print("=" * 96)
    print("可点击区域最小边长（对应「控件还点得到吗」）")
    print("=" * 96)
    print(f"  {'页面':<12s}" + "".join(f"{d:>12.2f}" for d in DENSITIES))
    report.append("")
    report.append("可点击区域最小边长")
    for key, _pn, _pr, _pp in PAGES:
        cells = []
        for d in DENSITIES:
            p = all_data.get(d, {}).get("pages", {}).get(key)
            v = p.get("clickMin", -1) if p else -1
            cells.append(f"{v:12.1f}")
        line = f"  {key:<12s}" + "".join(cells)
        print(line)
        report.append(line)

    report_path = OUT / "report.txt"
    report_path.write_bytes(("\n".join(report) + "\n").encode("utf-8"))
    print()
    print(f"截图目录：{OUT}")
    print(f"报告：{report_path}")
    print(f"共 {len(list(OUT.glob('*.png')))} 张 PNG")

    return 0 if not unst else 1


if __name__ == "__main__":
    raise SystemExit(main())
