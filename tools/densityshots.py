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
from animfreeze import freeze as freeze_anim
from animfreeze import walk_visual

configure_utf8()

os.environ["QT_QPA_PLATFORM"] = "windows"

OUT = ROOT / ".cache" / "densityshots"
# **四个档位共用的数据目录** —— 底部状态栏会画这个路径
SHARED = ROOT / ".cache" / "densityshots-data"
# 规范化后的数据**模板** —— 每个档位从它复制一份（见 `build_template`）
TEMPLATE = ROOT / ".cache" / "densityshots-template.json"


# ============================================================ 失败传播
#
# ## 为什么要有这个（Codex 第 14 轮第 1 节）
#
# 第一版**到处都只记日志**、最后只检查「两帧稳不稳」：
#
#   · `store.repaired` 为真        → 只写一行警告，退出码仍是 0
#   · 页面 / 探针找不到            → 写日志后 `continue`，照样通过
#   · 抓图返回空                   → 写日志后 `continue`，照样通过
#   · 最终 `return 0 if not unst else 1` 只看两帧稳定性
#
# **那是一条「假绿路径」**：跑失败了却给出「通过」，
# 比没有检查更糟 —— 它会让人以为验过了（本项目一直在强调这一点，
# 只是这次它以「失败没传播到退出码」的形式出现）。
#
# 所以：**任何一条关键前置条件不成立，都进 `FAILED`，最后返回非零。**
FAILED: list[str] = []


def fail(msg: str) -> None:
    """记一条**导致非零退出**的失败。"""
    FAILED.append(msg)


def _parse_densities() -> list[float]:
    """要走查的档位。默认四档。

    `DENSITYSHOTS_DENSITIES` 可以覆盖（逗号分隔）—— **给注入验证用**：
    跑满四档约 4 分钟，注入验证只关心「失败会不会传播」，
    跑一档就够（约 1 分钟）。
    """
    raw = os.environ.get("DENSITYSHOTS_DENSITIES", "").strip()
    if not raw:
        return [1.00, 0.90, 0.80, 0.70]
    out: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            raise SystemExit(f"DENSITYSHOTS_DENSITIES 里有非法值：{part!r}")
    if not out:
        raise SystemExit("DENSITYSHOTS_DENSITIES 解析后为空")
    return out


def _qml_dir() -> Path:
    """QML 根目录。

    `DENSITYSHOTS_QML_DIR` 可以覆盖 —— **给注入验证用**：
    把 QML 复制到临时目录、删掉某个 `objectName`，就能验
    「页面/探针找不到时会不会报红」，而**不必碰真源码**。
    """
    override = os.environ.get("DENSITYSHOTS_QML_DIR", "").strip()
    if override:
        return Path(override)
    from pawpet.config import QML_DIR
    return QML_DIR


def _template_path() -> Path:
    """数据模板路径。

    `DENSITYSHOTS_TEMPLATE` 可以覆盖 —— **给注入验证用**：
    指向一个**故意不合法**的数据文件，就能验
    「`store.repaired` 为真时会不会报红」。
    """
    override = os.environ.get("DENSITYSHOTS_TEMPLATE", "").strip()
    if override:
        return Path(override)
    return TEMPLATE


# 要覆盖的档位（环境变量可改）
DENSITIES = _parse_densities()

# (页面 key, 页面 objectName, 探针 objectName, 探针读的属性)
#
# 探针的选择**每一个都有理由**，不是随手挑的：
#
#   · `ai`        → `rightScroll`（右侧滚动区），`contentHeight`
#   · `tasks`     → `taskList`（ListView），`contentHeight`
#   · `notes`     → **`notesInner`**，**`height`**
#         ⚠ **不选 `noteBodyArea`** —— 它是个 `TextArea`，它的
#         `contentHeight` 是**文本高度**，边距收紧不会改它。
#         `densitytest.py` 里踩过这个坑（那个探针恒为 19）。
#         ⚠ **也不读 `notesInner.implicitHeight`** —— 那是**内容**撑出来的
#         高度，实测恒为 42、不随 density 变。`notesInner` 是
#         `anchors.fill: parent` + `anchors.margins: Theme.space(10)`，
#         所以 `height = 父高 − 2 × margins`：**边距收紧 → 高度变大**。
#         它是五个页面里唯一「数值随密度变大」的，方向相反但含义一致。
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

    target = _template_path()

    # `DENSITYSHOTS_TEMPLATE` 指定了就**不生成** —— 直接用那个文件。
    # 这是给注入验证用的：指向一个故意不合法的数据文件，
    # 就能验「`store.repaired` 为真时会不会报红」。
    if os.environ.get("DENSITYSHOTS_TEMPLATE", "").strip():
        if not target.exists():
            raise SystemExit(f"DENSITYSHOTS_TEMPLATE 指向的文件不存在：{target}")
        print(f"  模板用外部指定的：{target}")
        return target

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

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp / "pet_data.json", target)
    shutil.rmtree(tmp, ignore_errors=True)

    # ---- 校验**固化后的模板**再次加载的状态 ----
    #
    # ⚠ 第一版这里检查的是**构建过程的中间态**（`store2.load()` 紧跟在
    # 第一次 `save()` 之后）。那个中间态实测 `repaired=True` ——
    # 因为一次 `save()` 写出的还不是最终规范化形态。
    #
    # 但**要判的对象不是它**：截图用的是**固化后的模板**。
    # 判中间态等于「判错了对象」，会报一条**假红**。
    #
    # 真正该判的：**从固化模板加载时到底会不会出现修复横幅**。
    probe_dir = ROOT / ".cache" / "densityshots-template-check"
    shutil.rmtree(probe_dir, ignore_errors=True)
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_live = probe_dir / "pet_data.json"
    shutil.copy2(target, probe_live)
    check = Store(probe_live, probe_dir / "pet_data.backup.json")
    check.load()
    still_repaired = bool(check.repaired)
    shutil.rmtree(probe_dir, ignore_errors=True)

    print(f"  模板已固化：{target.name}"
          f"（构建中间态 repaired={repaired}，"
          f"固化后再次加载 repaired={still_repaired}）")
    if still_repaired:
        # 模板不合法 → 每一档都会带修复横幅 → 四档不可比
        fail("固化后的模板再次加载仍触发 repaired —— 种子数据字段不合法")
    return target


def _reset_data() -> None:
    """每个档位开跑前，把数据文件恢复成模板。"""
    live = SHARED / "pet_data.json"
    backup = SHARED / "pet_data.backup.json"
    shutil.copy2(_template_path(), live)
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


# `QQmlExpression` 求值 `textFormat` 的**缓存**（每项只求一次）
_FMT_CACHE: dict[int, int] = {}
_FMT_DIAG: dict[str, int] = {}


def _text_format(child, ctx) -> int:
    """读 `Text.textFormat`（0=PlainText, 1=RichText, 2=AutoText, 3=Markdown）。

    ## 为什么不能直接 `child.property("textFormat")`

    会抛：

        RuntimeError: Can't find converter for 'QQuickText::TextFormat'

    这是本项目**第三次**撞上同一类问题
    （前两次是 `QQuickAnchors*`、`QQuickPopup*`）——
    PySide6 对某些**枚举/指针类型**没有 converter。

    绕法：用 `QQmlExpression` 在 **QML 的求值上下文**里读。
    实测有效（`PlainText`→0、`RichText`→1、`AutoText`→2）。

    ## ⚠️ 上一版的教训：`except Exception: pass` 把真错误吞了

    第一版是这么写的：

        try:
            fmt = child.property("textFormat")   # ← 每次都抛
            ...
        except Exception:
            return str(child.property("text") or "")   # ← 静默退回 HTML 源码

    于是**每次都走到回退分支**，报告里的标签一直是 HTML 源码。
    而因为异常被 `pass` 掉了，**没有任何迹象显示出了问题**。

    > **静默回退会掩盖真错误。** 回退可以，但必须留痕 ——
    > 所以这里把求解失败记进 `_FMT_DIAG`，并让 `main()` 报出来。
    """
    key = id(child)
    if key in _FMT_CACHE:
        return _FMT_CACHE[key]
    from PySide6.QtQml import QQmlExpression
    value = -1
    try:
        expr = QQmlExpression(ctx, child, "textFormat")
        # ⚠ **PySide6 的 `evaluate()` 返回二元组 `(值, 是否 undefined)`**，
        # 不是单个值。第一版写成 `got = expr.evaluate()` 然后
        # `int(got)` —— 于是抛
        # `TypeError: int() argument must be a string...`。
        #
        # 我在探针里**明明打印过** `(2, False)`，却读成了
        # 「值 2、hasError False」——**看到了数据、读错了结构**。
        # （和前面几次「方向写反」是同一类错误：没先确认数据的形状。）
        res = expr.evaluate()
        if isinstance(res, tuple):
            got, is_undefined = res[0], bool(res[1])
        else:
            got, is_undefined = res, False
        if got is None or is_undefined:
            _FMT_DIAG["求值为 undefined/None"] = \
                _FMT_DIAG.get("求值为 undefined/None", 0) + 1
        elif expr.hasError():
            err = expr.error().toString()
            _FMT_DIAG[f"求值有错: {err[:60]}"] = \
                _FMT_DIAG.get(f"求值有错: {err[:60]}", 0) + 1
        else:
            value = int(got)
    except Exception as exc:  # noqa: BLE001
        # **记下消息本身**，不只记类型 —— 第一版只记
        # `type(exc).__name__`，得到「TypeError: 14 次」，
        # 完全不知道哪里错了，还得再写一个探针去查。
        # 一条不说明原因的诊断等于没有诊断。
        key = f"{type(exc).__name__}: {str(exc)[:70]}"
        _FMT_DIAG[key] = _FMT_DIAG.get(key, 0) + 1
    _FMT_CACHE[key] = value
    return value


def _visible_text(child, ctx) -> str:
    """把 `Text` 的 `text` 属性换成**用户实际看到的文字**。

    ## 为什么必须转（我的报告制造过一次假信号，代价不小）

    第一版用 `str(text)[:24]` 当裁切项的标签 —— 那是 `text`
    **属性**。但对富文本控件，**属性是 HTML 源码、屏幕上是解析后的
    文字**，两者不是一回事。

    实测：`AiBubble.qml:380` 是 `text: "⚠ " + bubble.richText`，
    所以属性是 `"⚠ <p style='margin:0 0 6px 0;'>还没有配置模型…"`。
    报告里就出现了 `⚠ <p style='margin:0 0 6` 这一行。

    **Codex 读到它，判断「走查截图暴露了用户可见的原始 HTML」，
    并据此立了一个修复项。** 但实际上——

    **我把那张图放大 4 倍看过：屏幕上是 `⚠ 还没有配置模型`，
    正常中文，没有任何标签。** 那一行只存在于 `text` 属性里。

    > 一个**报告措辞**问题，让协作者基于错误前提立了任务。
    > 所以这里改成真正解析一遍。

    ## 为什么不能「总是 `QTextDocument.setHtml()`」

    对**纯文本**它会**吃掉内容**（实测）：

        '如果 3 < 5 且 a > b'  →  '如果 3 b'
        '用 <Enter> 确认'      →  '用 确认'

    所以必须先知道 `textFormat`（见 `_text_format`）。
    """
    from PySide6.QtGui import QTextDocument

    try:
        text = child.property("text")
    except Exception:
        return ""
    if text is None:
        return ""
    s = str(text)

    fmt = _text_format(child, ctx)
    # 1 = RichText；2 = AutoText（含 '<' 时走富文本）
    if fmt == 1 or (fmt == 2 and "<" in s):
        doc = QTextDocument()
        doc.setHtml(s)
        parsed = doc.toPlainText()
        if parsed.strip():
            return parsed
        # 解析后为空 = 异常输入，退回原文并留痕（不静默）
        _FMT_DIAG["解析后为空"] = _FMT_DIAG.get("解析后为空", 0) + 1
    return s


def _one_line(s: str) -> str:
    """把多行文本折成单行（报告里一行一个标签，便于 grep 和比对）。

    实测踩过：解析后的可见文本含换行，直接拼进报告会把一行标签断成
    好几行 —— 我只 grep 到 `裁切 ×1: ⚠ `，就以为「内容丢了」，
    其实正文在下一行。**折成单行**可以避免这种误读。
    """
    return " ".join(str(s).split())


def _count_clipped(container, QObject, ctx) -> Counter:
    """数竖向/横向装不下的 `QQuickText`（按**可见文本**计数）。"""
    out: Counter = Counter()
    for child in walk_visual(container):
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
                # key 用**解析后的可见文本**，不是 HTML 源码。
                # 见 `_visible_text`：用属性会把 HTML 源码当成
                # 「用户看到了标签」，那是假信号 —— 已经发生过一次。
                #
                # **折成单行**：解析后的文本含换行，直接拼进报告会把
                # 一行标签断成好几行 —— 我自己就被这个误导过：
                # 报告里只看到 `裁切 ×1: ⚠ `，下一行才是正文，
                # 于是以为「标签只剩 ⚠、内容丢了」。
                label = child.objectName() or _one_line(
                    _visible_text(child, ctx))[:24]
                out[label] += 1
        except Exception:
            continue
    return out


# 可点击控件的类名片段
CLICKABLE_HINTS = ("Button", "CheckBox", "Switch", "TextField",
                   "ComboBox", "SpinBox", "Slider")


def _click_targets(container, QObject) -> list[tuple[str, float, float]]:
    """收集**可见可点击控件**的几何尺寸。

    ## ⚠️ 这**不是**命中测试（Codex 第 14 轮第 2 节）

    它量的是 `width` / `height` 两个属性 —— 即**几何尺寸**。
    「点得到吗」还取决于：Z 序遮挡、`MouseArea` 是否真的覆盖、
    `enabled`、父项 `clip`、以及平台的高 DPI 缩放。
    **这些这个函数都不看。**

    所以报告里**只写「几何尺寸」**，不写「没有交互障碍」。
    真正的可点击性走已有的交互测试（`interactiontest.py` /
    `hitboxtest.py`）。

    ## 也**不设**阈值判失败

    项目还没有统一的「最小可点边长」标准。凭空造一个
    （比如「小于 24px 算失败」）就是给自己发明一条会误报的规则 ——
    而**一条会误报的断言最后一定会被绕过**。
    所以这里只**报数值**，供人工判读。
    """
    out: list[tuple[str, float, float]] = []
    # 走**视觉树** —— `findChildren(QObject)` 会漏掉 Pet 子树（见 `_walk_visual`）
    for child in walk_visual(container):
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
    """在给定 density 下给每个页面抓两帧。返回 (结构数据, 日志)。

    ⚠ **关键前置条件不成立时进 `FAILED`**（不再是只记日志）：
    数据被修复、页面找不到、探针找不到、抓图返回空、图存盘失败。
    """
    from PySide6.QtCore import QObject, QUrl, Qt
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

    from pawpet.backend import Backend
    from pawpet.config import ensure_dirs
    from pawpet.store import Store

    qml_dir = _qml_dir()
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
        # **这是硬失败，不是警告。** 修复横幅会撑高页面内容，
        # 让这一档和别的档不可比 —— 实测它把设置页的收紧率
        # 从 6.7% 扭曲成 3.8%。
        fail(f"density={density:.2f}: 数据被判定需要修复"
             f"（界面会多出修复横幅，四档不可比）")
        log.append("    **数据被判定需要修复** —— 四档不可比")

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_dir))
    engine.rootContext().setContextProperty("backend", backend)
    if abs(density - 1.0) > 1e-9:
        setter = QQmlComponent(engine)
        setter.setData(f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{ Component.onCompleted: Theme.density = {density} }}
        """.encode("utf-8"),
            QUrl.fromLocalFile(str(qml_dir / "PawPet" / "ds_set.qml")))
        setter.create(engine.rootContext())

    engine.load(QUrl.fromLocalFile(str(qml_dir / "PawPet" / "Main.qml")))
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
        frozen = freeze_anim(dash.property("contentItem"))
        _pump(app, 600)
        # 冻结后再冻一次：冻结本身可能触发重绘/新动画
        frozen2 = freeze_anim(dash.property("contentItem"))
        _pump(app, 500)
        data.setdefault("freeze", frozen2)
        if key == PAGES[0][0]:
            log.append(f"    冻结：停了 {frozen2['stopped']} 个动画/定时器；"
                       f"归位 {','.join(frozen2['reset'])}")

        page = dash.findChild(QObject, page_name, Qt.FindChildrenRecursively)
        probe = dash.findChild(QObject, probe_name,
                               Qt.FindChildrenRecursively)

        # ---- 页面找不到：硬失败，不生成结构记录 ----
        if page is None:
            fail(f"density={density:.2f} {key}: 页面 `{page_name}` 找不到")
            log.append(f"    [XX] {key}: 页面 `{page_name}` 找不到")
            continue

        # ---- 探针找不到：硬失败，**不生成结构记录** ----
        #
        # 第一版在这里写 `probeFound: False` + `contentHeight: -1`
        # **然后照样生成记录** —— 于是汇总表里出现 `-1`、
        # 产物断言也不会触发。Codex 明确点了这一条。
        if probe is None:
            fail(f"density={density:.2f} {key}: 探针 `{probe_name}` 找不到")
            log.append(f"    [XX] {key}: 探针 `{probe_name}` 找不到")
            continue

        info: dict = {
            "pageFound": True,
            "probeFound": True,
            "probeProp": probe_prop,
            "contentHeight": float(probe.property(probe_prop) or 0),
            "pageHeight": float(page.property("height") or 0),
        }

        # ---- 抓两帧，比对 ----
        img_a = dash.grabWindow()
        _pump(app, 700)
        img_b = dash.grabWindow()
        if img_a.isNull() or img_b.isNull():
            # **硬失败。** 抓图返回空说明窗口没渲染出来 ——
            # 这时候所有后续测量都无意义。
            fail(f"density={density:.2f} {key}: 抓图返回空")
            log.append(f"    [XX] {key}: 抓图返回空")
            continue
        same = _img_bytes(img_a) == _img_bytes(img_b)
        info["stable"] = same
        info["size"] = (img_a.width(), img_a.height())

        path = OUT / f"d{density:.2f}_{key}.png"
        if not img_a.save(str(path)):
            # `QImage.save()` 返回 bool —— 第一版**没有检查它**，
            # 存盘失败会留下一张缺失/半截的图而退出码仍是 0。
            fail(f"density={density:.2f} {key}: 截图存盘失败 `{path.name}`")
            log.append(f"    [XX] {key}: 截图存盘失败")
            continue
        info["path"] = str(path.relative_to(ROOT))
        info["bytes"] = path.stat().st_size
        if info["bytes"] <= 0:
            fail(f"density={density:.2f} {key}: 截图是 0 字节"
                 f"`{path.name}`")
            log.append(f"    [XX] {key}: 截图是 0 字节")

        info["clipped"] = dict(_count_clipped(page, QObject,
                                              engine.rootContext()))
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
            fail(f"density={d:.2f} {key}: 连续两帧不一致（渲染不稳）")
    else:
        print(f"  ✓ 全部 {len(DENSITIES) * len(PAGES)} 张**两帧逐像素相同** —— "
              f"动画已停、时钟已冻、字体栅格稳定")
    report.append("")
    report.append(f"两帧不一致处：{unst or '无'}")

    # ---------------- 可点击区域（**只是尺寸探针**）----------------
    #
    # ⚠ Codex 第 14 轮第 2 节：`26/28/34 px` 证明的是**几何尺寸**，
    # **不是鼠标命中测试**。所以这里**不写「没有交互障碍」**——
    # 那是个更强的结论，需要真点一次才能说。
    #
    # 也**没有**设阈值判失败：本项目还没有统一的「最小可点边长」标准，
    # 凭空造一个阈值就是给自己发明一条会误报的规则。
    print()
    print("=" * 96)
    print("可点击区域**几何尺寸**探针（不是命中测试！）")
    print("=" * 96)
    print("  下面只是可见可点击控件的**最小边长**（px）。")
    print("  它**不能**证明「点得到」—— 那需要真实命中测试")
    print("  （见 `interactiontest.py` / `hitboxtest.py`）。")
    print("  本工具**不设**阈值判定失败：项目还没有统一的最小可点边长标准。")
    print()
    print(f"  {'页面':<12s}" + "".join(f"{d:>12.2f}" for d in DENSITIES))
    report.append("")
    report.append("可点击区域几何尺寸探针（不是命中测试）")
    report.append("  只报尺寸；不证明可点击性；也不设阈值判定失败。")
    for key, _pn, _pr, _pp in PAGES:
        cells = []
        for d in DENSITIES:
            p = all_data.get(d, {}).get("pages", {}).get(key)
            v = p.get("clickMin", -1) if p else -1
            cells.append(f"{v:12.1f}")
        line = f"  {key:<12s}" + "".join(cells)
        print(line)
        report.append(line)

    # ---------------- `textFormat` 求解诊断（不留静默回退）----------------
    #
    # `_text_format` 求解失败会退回「原样」，那会让标签显示成 HTML 源码 ——
    # 也就是**上一版那个假信号的成因**。所以这里把它报出来：
    # 求解失败**不一定**让整轮失败（可能只是个别控件），
    # 但**必须让人看见**，而不是悄悄退回。
    if _FMT_DIAG:
        print()
        print("=" * 96)
        print("⚠ `textFormat` 求解有失败（裁切项的标签可能显示成 HTML 源码）")
        print("=" * 96)
        for reason, n in sorted(_FMT_DIAG.items()):
            print(f"  {reason}: {n} 次")
        report.append("")
        report.append(f"⚠ textFormat 求解失败：{_FMT_DIAG}")
        print("  这**不一定**是功能缺陷，但会让报告里的标签失真 ——")
        print("  上一版就是因为静默退回，报告里出现了并不存在的「可见 HTML」。")
    elif _FMT_CACHE:
        print()
        print(f"  （`textFormat` 求解 {len(_FMT_CACHE)} 次，全部成功）")

    # ---------------- 产物断言（Codex 第 14 轮第 1 节）----------------
    #
    # 「跑完了」不等于「产物齐了」。第一版没查这个：
    # 少了图、缺了一页，退出码照样是 0。
    print()
    print("=" * 96)
    print("产物完整性")
    print("=" * 96)
    expected = len(DENSITIES) * len(PAGES)
    pngs = sorted(OUT.glob("*.png"))
    report.append("")
    report.append("产物完整性")
    print(f"  预期 {len(PAGES)} 页 × {len(DENSITIES)} 档 = **{expected}** 张")
    print(f"  实际 {len(pngs)} 张")

    if len(pngs) != expected:
        fail(f"截图数量不对：预期 {expected}，实际 {len(pngs)}")
        print(f"  [XX] 数量不对：预期 {expected}，实际 {len(pngs)}")

    missing = []
    empty = []
    for d in DENSITIES:
        for key, _pn, _pr, _pp in PAGES:
            p = OUT / f"d{d:.2f}_{key}.png"
            if not p.exists():
                missing.append(p.name)
            elif p.stat().st_size <= 0:
                empty.append(p.name)
    if missing:
        fail(f"缺截图 {len(missing)} 张：{missing[:6]}")
        print(f"  [XX] 缺 {len(missing)} 张：{', '.join(missing[:6])}")
    if empty:
        fail(f"有 0 字节截图 {len(empty)} 张：{empty[:6]}")
        print(f"  [XX] 0 字节 {len(empty)} 张：{', '.join(empty[:6])}")
    if not missing and not empty and len(pngs) == expected:
        print(f"  [ok] {expected} 张齐全，每页每档各一张，无 0 字节")
    report.append(f"预期 {expected} 张，实际 {len(pngs)} 张；"
                  f"缺 {missing or '无'}，0 字节 {empty or '无'}")

    # ---------------- 结构记录完整性 ----------------
    missing_pages = [
        f"d{d:.2f}/{key}" for d in DENSITIES for key, *_ in PAGES
        if all_data.get(d, {}).get("pages", {}).get(key) is None
    ]
    if missing_pages:
        fail(f"有 {len(missing_pages)} 个「档位/页面」没有结构记录："
             f"{missing_pages[:8]}")
        print(f"  [XX] 缺结构记录 {len(missing_pages)} 处："
              f"{', '.join(missing_pages[:8])}")
    else:
        print(f"  [ok] {expected} 个「档位/页面」都有结构记录")

    report_path = OUT / "report.txt"
    report_path.write_bytes(("\n".join(report) + "\n").encode("utf-8"))
    print()
    print(f"截图目录：{OUT}")
    print(f"报告：{report_path}")

    # ---------------- 退出码：**所有**失败都要传播 ----------------
    print()
    print("=" * 96)
    if FAILED:
        print(f"**失败 {len(FAILED)} 项（退出码 1）**")
        for item in FAILED:
            print(f"  - {item}")
        print()
        print("  ⚠ 这些是**关键前置条件**，不是警告 ——")
        print("    第一版它们只写日志，退出码仍是 0，那是一条假绿路径。")
        report.append("")
        report.append(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            report.append(f"  - {item}")
        report_path.write_bytes(("\n".join(report) + "\n").encode("utf-8"))
        return 1
    print("**全部通过**（稳定性 + 产物完整性 + 结构记录）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
