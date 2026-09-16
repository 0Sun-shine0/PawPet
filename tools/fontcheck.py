r"""字体诊断：报告 QML 里实际的字体家族、字号、行高落到了什么值。

用户反馈「前端的字体是不是有些问题」。光看源码只能看到写了什么，
看不到 Qt 最终选到了哪个字体、什么字号。这个脚本把真实页面拉起来，
遍历所有 Text/TextInput/Button 的 font 属性，报告：

* 请求的家族名 vs **Qt 实际匹配到的家族名**（后者才是关键 ——
  写一个系统里没有的字体名，Qt 会静默回退，中文可能落到别的字体上）
* pixelSize 的实际数值（是否为整数、有没有因为缩放算出小数）
* styleStrategy / hintingPreference
* 中英文混排时的字形来源

用法：
    .venv\Scripts\python.exe tools\fontcheck.py
"""

from __future__ import annotations

import os
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "fontcheck"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "fontcheck"

_keep_alive: list = []


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    print("=" * 74)
    print("一、系统里到底有没有这几个字体")
    print("=" * 74)

    families = set(QFontDatabase.families())
    print(f"系统字体家族总数：{len(families)}")

    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                 "Segoe UI", "Consolas", "SimSun", "宋体",
                 "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC"):
        exists = name in families
        # 关键：名字不在列表里时 Qt 会怎么处理
        info = QFontInfo(QFont(name))
        resolved = info.family()
        exact = info.exactMatch()
        print(f"  {name:24s} 在列表里={str(exists):5s} "
              f"Qt 实际用={resolved!r} 精确匹配={exact}")
        if exists and not exact:
            print(f"      ^^^ 名字在列表里但 Qt 不认为精确匹配 —— 值得查")

    print()
    print("=" * 74)
    print("二、QML 里请求的家族 vs Qt 实际匹配到的")
    print("=" * 74)

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

    def pump(n: int = 20) -> None:
        for _ in range(n):
            app.processEvents()

    pump()
    dash = root.findChild(QObject, "dashboardWindow", Qt.FindChildrenRecursively)
    _keep_alive.append(dash)

    # 遍历所有页面，把所有 Text 的字体收集起来
    collected: list[tuple] = []
    seen_classes: Counter = Counter()
    font_errors: list[str] = []

    def walk(obj, depth=0):
        if depth > 16 or obj is None:
            return
        cls = obj.metaObject().className()
        seen_classes[cls] += 1
        try:
            mo = obj.metaObject()
            if mo.indexOfProperty("font") >= 0:
                font = obj.property("font")
                if font is not None:
                    f = font if isinstance(font, QFont) else QFont(font)
                    info = QFontInfo(f)
                    collected.append((
                        cls,
                        f.family(),
                        info.family(),
                        f.pixelSize(),
                        f.pointSizeF(),
                        f.weight(),
                        f.bold(),
                        int(f.hintingPreference().value),
                        int(f.styleStrategy().value),
                        str(obj.property("text") or "")[:60],
                    ))
        except Exception as exc:  # noqa: BLE001
            if len(font_errors) < 10:
                font_errors.append(f"{cls}: {type(exc).__name__}: {exc}")
        for child in obj.children():
            walk(child, depth + 1)

    for page in ("today", "focus", "tasks", "notes", "reminders", "ai", "settings"):
        dash.resize(1000, 660)
        dash.setProperty("visible", True)
        dash.setProperty("currentPage", page)
        pump(25)
        walk(dash)

    # 弹窗也要看
    for obj_name in ("commandBar", "bubbleWindow"):
        win = root.findChild(QObject, obj_name, Qt.FindChildrenRecursively)
        if win is not None:
            _keep_alive.append(win)
            walk(win)

    if not collected:
        print("  一个带 font 属性的对象都没找到 —— 遍历逻辑有问题")
        print(f"  遍历到的对象类型（前 12）：{seen_classes.most_common(12)}")
        for item in font_errors:
            print("   err:", item)
        return 1

    print(f"采样到 {len(collected)} 个带字体的对象\n")

    print("按「请求家族 → Qt 实际匹配」汇总：")
    by_family = Counter((req, got) for _, req, got, *_ in collected)
    for (req, got), count in by_family.most_common():
        flag = "" if req == got else "   <<< 回退了！"
        print(f"  请求 {req!r:26s} → 实际 {got!r:26s} ×{count}{flag}")

    print()
    print("按字号汇总（pixelSize）：")
    sizes = Counter(row[3] for row in collected)
    for size, count in sorted(sizes.items()):
        whole = "整数" if float(size).is_integer() else f"**小数** {size}"
        print(f"  pixelSize={size:>8} ×{count:4d}   {whole}")

    # 界面缩放 = 1.15 时基准值应该是：
    #   fsTiny 11→13  fsSmall 12→14  fsBody 14→16
    # 也就是正文字号只有 13 / 14 / 16 三档。出现 15、17、18 这些
    # 「中间值」说明有用例没走 Theme.px()，字号谱系会显得参差。
    print()
    print("各字号下用中文字体渲染的样本（看字号谱系是否一致）：")
    for size in sorted(sizes):
        if size < 12 or size > 22:
            continue
        rows = [row for row in collected
                if row[3] == size and "YaHei" in row[1]
                and any("\u4e00" <= ch <= "\u9fff" for ch in row[9])]
        samples = [row[9] for row in rows[:3]]
        print(f"  px={size:>3} 中文样本 ×{len(rows):4d}  {samples}")

    # pixelSize = -1 意味着「没设字号」，Qt 会退回默认值 12pt 左右 ——
    # 这是最容易被忽略的一类：看着有字，但是尺寸不受 Theme 控制，
    # 界面缩放的设置对它无效。必须把这些对象揪出来。
    if -1 in sizes:
        print(f"\n  **{sizes[-1]} 个对象没设 pixelSize（=-1）**，逐个看：")
        bad = [row for row in collected if row[3] == -1]
        by_cls: dict[str, int] = {}
        for row in bad:
            by_cls[row[0]] = by_cls.get(row[0], 0) + 1
        for cls, count in sorted(by_cls.items(), key=lambda kv: -kv[1]):
            print(f"     {cls} ×{count}")

    # 等宽字体渲染中文：Consolas 不含汉字，会走 Qt 回退，
    # 中英混排时字形不统一 —— 这是「字体看着不对」的经典来源
    print()
    print("用等宽字体的对象（Consolas 不含汉字）：")
    mono = [row for row in collected if "Consolas" in row[1]]
    print(f"  共 {len(mono)} 个")
    mono_cls: Counter = Counter(row[0] for row in mono)
    for cls, count in mono_cls.most_common(10):
        print(f"     {cls} ×{count}")

    # 最要命的一种：等宽字体 + 文本里真的有汉字 → 汉字会被换成别的字体，
    # 和旁边的拉丁字符不是同一套字形，看起来就是「字体怪怪的」
    han = [row for row in mono if any("\u4e00" <= ch <= "\u9fff" for ch in row[9])]
    print(f"\n  其中**文本里含汉字**的：{len(han)} 个  <<< 这些是问题所在")
    for row in han[:12]:
        print(f"     {row[0]:24s} px={row[3]:>3} text={row[9]!r}")

    print()
    print("按字重汇总：")
    weights = Counter((row[5], row[6]) for row in collected)
    for (weight, bold), count in sorted(weights.items()):
        print(f"  weight={weight:>4} bold={str(bold):5s} ×{count}")

    print()
    print("hinting / styleStrategy 汇总：")
    combos = Counter((row[7], row[8]) for row in collected)
    for (hint, strat), count in combos.items():
        print(f"  hinting={hint} styleStrategy={strat} ×{count}")

    # 中文到底落到哪个字体 —— 这是最容易出问题的地方
    print()
    print("=" * 74)
    print("三、中文/英文分别落到哪个字体（关键）")
    print("=" * 74)

    from PySide6.QtGui import QRawFont

    for label, name in (("界面主字体", "Microsoft YaHei UI"),
                        ("等宽字体", "Consolas"),
                        ("拉丁字体", "Segoe UI")):
        f = QFont(name)
        f.setPixelSize(14)
        raw = QRawFont.fromFont(f)
        print(f"  {label} {name!r}")
        print(f"      raw family      = {raw.familyName()!r}")
        print(f"      raw style       = {raw.styleName()!r}")
        print(f"      pixelSize       = {raw.pixelSize()}")
        print(f"      支持「中」字     = {raw.supportsCharacter(ord('中'))}")
        print(f"      支持「A」        = {raw.supportsCharacter(ord('A'))}")
        # 关键：如果主字体不支持中文，中文会由 Qt 的字体回退机制挑一个
        # 别的字体渲染 —— 那就是「字体看起来不对」的典型来源
        if not raw.supportsCharacter(ord("中")):
            print("      ^^^ 这个字体不支持中文！中文会走 Qt 回退，字形可能不统一")

    # 实际渲染时中文的替代字体
    print()
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(14)
    info = QFontInfo(f)
    print(f"  QFontInfo('Microsoft YaHei UI') → family={info.family()!r} "
          f"exactMatch={info.exactMatch()} pixelSize={info.pixelSize()} "
          f"pointSizeF={info.pointSizeF()}")

    backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
