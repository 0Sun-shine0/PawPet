r"""`Theme.space()` / `Theme.scaledSpace()` 的契约测试。

## 为什么需要这个文件

E1 要往 QML 里塞一个新的间距接缝，而它的**唯一**硬前提是
「density=1 时逐像素保持原样」。这句话必须由断言保证，不能靠读代码
「看起来对」—— 因为接缝一旦错了，错的是**全部**迁移过的页面，
而且只在用户改了界面缩放时才显形。

## 契约是什么（Codex 第 4 轮定的，我复核过）

    space(value)        只接密度轴，**不碰 uiScale** —— 给裸数字用
    scaledSpace(value)  缩放轴 × 密度轴 —— 给本来就在缩放轴上的值用
    px(value)           保持原样，只有缩放轴

两条轴分开的理由是实测的：`spacing` 这一类里**跟 uiScale 的 11 处、
不跟的 164 处**。如果只做一个含 scale 的 `space()`，把裸数字换过去会让
那 164 处突然开始跟 uiScale —— 滑块 0.8~2.0，裸 `8` 在 2.0 时变 16，
而没迁移的仍是 8，反而制造新的「又紧又不紧」。

## 本文件测的六件事

  1. **恒等**：density=1 时，代码里**每一个真实间距值** `space(v) == v`
  2. **两条轴独立**：`space()` 不随 uiScale 变，`scaledSpace()` 随
  3. **等价**：density=1 时 `scaledSpace(v) == px(v)`（多个 uiScale 取样）
  4. **小值规则**：density≠1 时 `|v| <= 4` 不变、`> 4` 缩放
  5. **`gap`/`gapLg`/`pad` 改了实现之后逐点不变**
  6. **接缝不是终点**：扫描器必须把 `space(N)` 算成 `seam` 而不是 `token`

第 1 条刻意**不写死一组数字**，而是用 `spacingtest.scan()` 读代码里
真实的间距值（方案 V5：断言读产品实际值，不写字面量）——
这样以后有人加了一个破坏恒等的值（比如小数且大于门槛），
测试会红，而不是悄悄改布局。

用法：
    .venv\Scripts\python.exe tools\spacetest.py
"""

from __future__ import annotations

import math
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8

configure_utf8()

SCRATCH = ROOT / ".cache" / "spacetest"
os.environ["PAWPET_HOME"] = str(SCRATCH)

PASSED = 0
FAILED: list[str] = []


def js_round(value: float) -> int:
    """JS 的 `Math.round` —— **不是** Python 的 `round`。

    两者在 `.5` 上不同，而这个测试的期望值全是 `.5` 附近：

        Python round(4.5) = 4   （银行家舍入，取偶）
        JS     Math.round(4.5) = 5   （一律向 +∞ 取半）

    `round(5 * 0.9) = round(4.5)`、`round(10 * 0.85) = round(8.5)` ——
    都在这个点上。第一版测试用 Python 的 `round` 算期望，
    于是 3 条断言报红，而**产品是对的、测试是错的**。

    产品跑在 QML/JS 里，所以期望值必须按 JS 的语义算。
    （`Math.floor(x + 0.5)` 就是 `Math.round` 的定义，对负数也成立：
     `Math.round(-34.5) = -34` → `floor(-34.0) = -34` ✓）
    """
    return math.floor(value + 0.5)


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("间距接缝契约测试（Theme.space / scaledSpace）\n")

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    def set_scale(value: float) -> None:
        store.settings["ui_scale"] = value
        backend.settingsChanged.emit()
        app.processEvents()

    def set_density(value: float) -> None:
        """把 `Theme.density` 设成指定值（走 QML 赋值，和产品同一条路径）。

        **每次都赋值，包括 1.0。** 第一版只在 `≠1` 时才设，于是第 四 节
        把 density 留在 0.70，第 五 节的 `gap` 就成了
        `round(12 × 0.8 × 0.7) = 7`，报了 15 条红 ——
        **那是测试的状态泄漏，不是产品缺陷。**
        """
        setter = QQmlComponent(engine)
        setter.setData(f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{
                Component.onCompleted: Theme.density = {value}
            }}
        """.encode("utf-8"),
            QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "spacetest_set.qml")))
        setter.create(engine.rootContext())
        app.processEvents()

    def probe(expressions: dict[str, str]):
        """建一个 QtObject，把每个表达式绑成一个属性并读回来。

        **每个配置都新建一个 probe**，不靠改 density 触发重求值 ——
        后者依赖 QML 的绑定依赖追踪穿过函数调用，能work但那是另一件
        要单独验证的事。这里要测的是接缝的算术，不是绑定机制。
        """
        names = list(expressions)
        body = "\n".join(
            f"            property real p{i}: {expressions[n]}"
            for i, n in enumerate(names))
        src = f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{
{body}
            }}
        """.encode("utf-8")
        comp = QQmlComponent(engine)
        comp.setData(src, QUrl.fromLocalFile(
            str(QML_DIR / "PawPet" / "spacetest_probe.qml")))
        obj = comp.create(engine.rootContext())
        errors = [e.toString() for e in comp.errors()
                  if "Cannot read property" not in e.toString()]
        if obj is None:
            return None, errors
        out = {n: obj.property(f"p{i}") for i, n in enumerate(names)}
        return out, errors

    def read(expressions: dict[str, str], density: float = 1.0):
        # **先设 density 再建 probe** —— 建的时候绑定就用新值求值，
        # 不依赖「改属性触发重算」这个更微妙的行为。
        set_density(density)
        values, errors = probe(expressions)
        if values is None:
            raise RuntimeError(f"probe 建不起来：{errors[:2]}")
        return values

    try:
        # ================================================ 一、真实值恒等
        print("=== 一、density=1 时，代码里每一个真实间距值都恒等 ===")
        from spacingtest import scan

        _counts, details = scan()
        real_values: set[float] = set()
        for _rel, _num, _cat, kind, raw in details:
            if kind not in ("literal", "seam"):
                continue
            import re
            for n in re.findall(r"-?\d+(?:\.\d+)?", str(raw)):
                real_values.add(float(n))
        real_values.discard(0.0)
        ordered = sorted(real_values)
        print(f"  从 QML 里读到 {len(ordered)} 个不同的间距值"
              f"（{ordered[0]:g} ~ {ordered[-1]:g}）")

        set_scale(1.0)
        exprs = {f"v{i}": f"Theme.space({v:g})" for i, v in enumerate(ordered)}
        got = read(exprs)
        bad = [(v, got[f"v{i}"]) for i, v in enumerate(ordered)
               if abs(float(got[f"v{i}"]) - v) > 1e-9]
        check(f"space() 对这 {len(ordered)} 个值全部恒等",
              not bad,
              "；".join(f"{v:g}→{g:g}" for v, g in bad[:5]) if bad else "")

        # 小数那一个是**单独**要盯的：`Math.round(3.5) = 4` 会改变布局。
        # 这不是假想的边界 —— `Pet.qml:529` 就是 `spacing: 3.5`。
        fractional = [v for v in ordered if abs(v - round(v)) > 1e-9]
        print(f"  其中小数 {len(fractional)} 个："
              f"{[f'{v:g}' for v in fractional] or '无'}")
        for v in fractional:
            one = read({"x": f"Theme.space({v:g})"})["x"]
            check(f"小数 {v:g} 不被 round 吃掉（space({v:g}) = {one:g}）",
                  abs(float(one) - v) < 1e-9, f"得到 {one:g}")

        # ================================================ 二、两条轴独立
        print("\n=== 二、space() 不跟 uiScale，scaledSpace() 跟 ===")
        samples = [0.80, 1.00, 1.25, 1.50, 2.00]
        for value in (8, 12, 3.5):
            seen_space, seen_scaled = [], []
            for s in samples:
                set_scale(s)
                r = read({"a": f"Theme.space({value:g})",
                          "b": f"Theme.scaledSpace({value:g})"})
                seen_space.append(float(r["a"]))
                seen_scaled.append(float(r["b"]))
            check(f"space({value:g}) 不随 uiScale 变（{seen_space}）",
                  len(set(seen_space)) == 1,
                  f"出现了 {len(set(seen_space))} 个不同的值")
            expect = [js_round(value * s) for s in samples]
            check(f"scaledSpace({value:g}) 按 round(value×scale) 变"
                  f"（{seen_scaled}）",
                  seen_scaled == expect, f"期望 {expect}")

        # ================================================ 三、与 px 等价
        print("\n=== 三、density=1 时 scaledSpace(v) 逐点等于 px(v) ===")
        mismatches = []
        for s in samples:
            set_scale(s)
            exprs = {}
            for i, v in enumerate(ordered):
                exprs[f"s{i}"] = f"Theme.scaledSpace({v:g})"
                exprs[f"p{i}"] = f"Theme.px({v:g})"
            r = read(exprs)
            for i, v in enumerate(ordered):
                if abs(float(r[f"s{i}"]) - float(r[f"p{i}"])) > 1e-9:
                    mismatches.append((s, v, r[f"s{i}"], r[f"p{i}"]))
        check(f"对 {len(samples)} 个 uiScale × {len(ordered)} 个值全部一致",
              not mismatches,
              "；".join(f"scale={s} v={v:g} {a}≠{b}"
                        for s, v, a, b in mismatches[:4]) if mismatches else "")

        # ================================================ 四、小值规则
        print("\n=== 四、小值规则：|value| <= 4 不压缩，> 4 压缩 ===")
        floor = int(read({"f": "Theme.denseFloor"})["f"])
        check("门槛是 4", floor == 4, f"实际 {floor}")

        for density in (0.90, 0.85, 0.70):
            set_scale(1.0)
            smalls = [v for v in ordered if abs(v) <= floor]
            bigs = [v for v in ordered if abs(v) > floor]
            exprs = {f"s{i}": f"Theme.space({v:g})" for i, v in enumerate(smalls)}
            exprs.update({f"b{i}": f"Theme.space({v:g})"
                          for i, v in enumerate(bigs)})
            r = read(exprs, density)
            small_bad = [(v, float(r[f"s{i}"]))
                         for i, v in enumerate(smalls)
                         if abs(float(r[f"s{i}"]) - v) > 1e-9]
            big_bad = [(v, float(r[f"b{i}"]), js_round(v * density))
                       for i, v in enumerate(bigs)
                       if abs(float(r[f"b{i}"]) - js_round(v * density)) > 1e-9]
            check(f"density={density}: {len(smalls)} 个小值一个都没动",
                  not small_bad,
                  "；".join(f"{v:g}→{a:g}" for v, a in small_bad[:4]) if small_bad else "")
            check(f"density={density}: {len(bigs)} 个大值都乘了 density",
                  not big_bad,
                  "；".join(f"{v:g}→{a:g}(期望{w:g})"
                            for v, a, w in big_bad[:4]) if big_bad else "")

        # 门槛的真实效果：它保护 |v|<=4 那一段的层级，**但不保护 5/6、8/9**。
        # 这是规则的已知边界，不是失败 —— 断言的是「它确实保护了该保护的」，
        # 同时把还存在的塌陷**量出来报出来**，免得以后有人以为全保护了。
        print("\n  门槛挡住的和挡不住的（density=0.70，用代码里真实的值）：")
        set_scale(1.0)
        pairs = [(1, 2), (2, 3), (3, 4), (5, 6), (8, 9)]
        exprs = {}
        for i, (a, b) in enumerate(pairs):
            exprs[f"a{i}"] = f"Theme.space({a})"
            exprs[f"b{i}"] = f"Theme.space({b})"
        r = read(exprs, 0.70)
        protected_ok, collapsed = [], []
        for i, (a, b) in enumerate(pairs):
            got_a, got_b = float(r[f"a{i}"]), float(r[f"b{i}"])
            naive_a, naive_b = js_round(a * 0.70), js_round(b * 0.70)
            kept = got_a != got_b
            naive_kept = naive_a != naive_b
            tag = ("保住" if kept else "**仍塌陷**")
            note = ""
            if naive_kept and not kept:
                note = " ← 比不做保护更差！"
            elif not naive_kept and kept:
                note = " ← 保护生效"
            print(f"    {a}px vs {b}px → {got_a:g} / {got_b:g}"
                  f"（不做保护 {naive_a}/{naive_b}） {tag}{note}")
            if not kept:
                collapsed.append((a, b))
            if not naive_kept and kept:
                protected_ok.append((a, b))

        check("门槛确实保住了该保的层级（1/2 与 2/3 不再被压成同一档）",
              bool(protected_ok),
              "一个都没保住 —— 这条规则没起作用")
        check("门槛不会把原本分得开的层级压到一起（不比不做更差）",
              all(js_round(a * 0.70) == js_round(b * 0.70)
                  for a, b in collapsed),
              f"塌陷的 {collapsed} 里有原本能分开的")
        if collapsed:
            print(f"    ⚠ 仍会塌陷的：{collapsed} —— 它们在门槛之外（>4px），"
                  f"规则没覆盖到")

        # ================================================ 五、gap 等不变
        print("\n=== 五、gap / gapLg / pad 换了实现之后逐点不变 ===")
        for s in samples:
            set_scale(s)
            r = read({"gap": "Theme.gap", "gapLg": "Theme.gapLg",
                      "pad": "Theme.pad"})
            for name, base in (("gap", 12), ("gapLg", 18), ("pad", 18)):
                want = js_round(base * s)
                check(f"uiScale={s}: Theme.{name} = {want}"
                      f"（旧实现 px({base})）",
                      int(r[name]) == want, f"实际 {r[name]}")

        # ================================================ 六、不是终点
        print("\n=== 六、接缝不是终点：扫描器把 space(N) 算成 seam ===")
        from spacingtest import classify_value, seam_axis

        cases = [("10", "literal"), ("Theme.gap", "token"),
                 ("Theme.space(10)", "seam"),
                 ("Theme.scaledSpace(12)", "seam"),
                 ("Theme.px(10)", "seam"), ("Theme.gap * 2", "seam"),
                 ("0", "zero")]
        for raw, want in cases:
            got = classify_value(raw)
            check(f"{raw:24s} → {want}", got == want, f"实际 {got}")

        print("\n  如果 space(N) 被算成 token，棘轮会在迁移后假性归零 ——")
        print("  164 个数字一个没少，却显示债还完了。这条断言就是防它的。")

        # ================================================ 七、接缝的**轴**
        print("\n=== 七、接缝接在哪条轴上（接错轴 = 计数看不出来的行为改变）===")
        print("  裸数字不跟 uiScale。迁成 Theme.space(N) 保持不跟（对），")
        print("  迁成 Theme.px(N) 就开始跟（错）—— 而两者的 literal/seam/debt")
        print("  计数**完全一样**，所以只看总量的棘轮分辨不出来。\n")

        axis_cases = [
            ("Theme.space(10)", "density", "裸数字迁到这里的正确形态"),
            ("Theme.space(3.5)", "density", "同上（小数）"),
            ("Theme.px(10)", "scale", "会开始跟 uiScale —— 迁移时不该用"),
            ("Theme.scaledSpace(12)", "scale", "同上"),
            ("Theme.gap * 2", "scale", "gap 是 px() 派生，跟着 uiScale"),
        ]
        for raw, want, why in axis_cases:
            got = seam_axis(raw)
            check(f"{raw:24s} → {want:8s}（{why}）", got == want,
                  f"实际 {got}")

        # 关键的**因果**断言：接错轴时，总量计数确实**看不出区别**。
        # 这条是整个守卫存在的理由 —— 如果总量能看出来，就不需要轴这一层。
        total_before = sum(
            1 for v in ("Theme.space(8)",) if classify_value(v) == "seam")
        total_after = sum(
            1 for v in ("Theme.px(8)",) if classify_value(v) == "seam")
        check("接对轴和接错轴的 debt 计数**确实相同**"
              "（所以必须单独看轴）",
              total_before == total_after == 1,
              "两者计数不同的话，轴那一层就是多余的")

        only_axis_differs = (classify_value("Theme.space(8)")
                             == classify_value("Theme.px(8)")
                             and seam_axis("Theme.space(8)")
                             != seam_axis("Theme.px(8)"))
        check("分类相同、只有轴不同 → 轴必须单独棘轮化", only_axis_differs,
              "分类就已经不同了，说明轴那一层没必要")

        print("\n  实测（真扫描器）：把一处 spacing: Theme.space(8) 换成")
        print("  Theme.px(8)，literal 和 debt 都持平 —— 加轴之前**棘轮通过**，")
        print("  加轴之后报 `[XX] 1 → 2 涨了`。")

    finally:
        engine.deleteLater()
        app.processEvents()

    # ================================================ 八、不把「读」当「写」
    #
    # 这一节**不需要 QML engine**（下面是纯文本扫描），所以放在 finally 之后。
    print("\n=== 八、扫描器不把「读属性」当成「写属性」===")
    print("  误报现场（真实存在于 PawSwitch.qml:18）：")
    print("      x: control.text.length > 0 ? control.leftPadding : 0")
    print("                                     ^^^^^^^^^^^^^ 曾经被数成一处 padding")
    print()

    import tempfile
    import re as _re
    import spacingtest

    def scan_snippet(source: str) -> tuple:
        """把一段 QML 当临时文件扫一遍，返回 (计数, 明细)。

        `scan()` 读的是模块级的 `QML_DIR`，这里临时替换掉再还原 ——
        比在真源码里插桩干净。

        ⚠ **临时目录必须放在 `ROOT` 下面**：`scan()` 会算
        `path.relative_to(ROOT)`，放系统临时目录会 ValueError。
        第一版就踩了这个。
        """
        original_dir = spacingtest.QML_DIR
        holder = ROOT / ".cache" / "spacetest-snippet"
        holder.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=holder) as tmp:
                probe = Path(tmp) / "Probe.qml"
                probe.write_text(source, encoding="utf-8")
                spacingtest.QML_DIR = Path(tmp)
                try:
                    return spacingtest.scan()
                finally:
                    spacingtest.QML_DIR = original_dir
        finally:
            pass

    # 误报的三种典型形态，都必须**不计入**
    FALSE_POSITIVES = [
        ("三元表达式里读 padding",
         'QtObject { x: control.text.length > 0 ? control.leftPadding : 0 }'),
        ("读 spacing",
         'QtObject { x: other.spacing }'),
        ("读 margin",
         'QtObject { x: other.anchors.leftMargin + 1 }'),
    ]
    for label, src in FALSE_POSITIVES:
        counts, _details = scan_snippet(src)
        total = sum(v for k, v in counts.items())
        check(f"不计入：{label}", total == 0,
              f"却数出了 {total} 处：{dict(counts)}")

    # 真赋值必须**仍然计入** —— 否则修过头，全漏了
    TRUE_POSITIVES = [
        ("行首 padding", 'QtObject { padding: 10 }', "padding"),
        ("行首 leftPadding", 'QtObject { leftPadding: 10 }', "padding"),
        ("anchors.margins（属性名自带点）",
         'QtObject { anchors.margins: 10 }', "margin"),
        ("Layout.leftMargin（属性名自带点）",
         'QtObject { Layout.leftMargin: 10 }', "margin"),
        ("spacing", 'QtObject { spacing: 10 }', "spacing"),
    ]
    for label, src, cat in TRUE_POSITIVES:
        counts, _details = scan_snippet(src)
        got = sum(v for k, v in counts.items() if k.startswith(f"{cat}."))
        check(f"仍然计入：{label}", got == 1, f"数出 {got} 处")

    # 同一行里「先写后读」：只能算一次（写那次）
    counts, _d = scan_snippet(
        'QtObject { padding: other.padding + 2 }')
    got = sum(v for k, v in counts.items() if k.startswith("padding."))
    check("同一行里「写 padding + 读 other.padding」只算 1 处",
          got == 1, f"数出 {got} 处")

    # 对照：**修之前**的正则在同一行会数出 1 处假债。
    # 用旧正则复现一次，证明这条断言确实在测「排除点号」这件事，
    # 而不是碰巧永远成立。
    #
    # ⚠ 第一版对照写的是 `padding: other.padding + 2`，**结论错了**：
    # 旧正则会把 `other.padding + 2` 整个当成**值**吞掉，所以也只数出 1 处。
    # 真正会误报的形态是「点号开头的引用**出现在值的位置**」——
    # 也就是 `PawSwitch.qml:18` 那种三元表达式。用真实那行做对照。
    old_pattern = r"(?:(?<![A-Za-z_])(?:left|right|top|bottom)?[Pp]adding)"
    false_line = "x: control.text.length > 0 ? control.leftPadding : 0"
    old_hits = len(_re.findall(old_pattern + r"\s*:\s*([^,;\n}]+)",
                               false_line))
    new_hits = len(_re.findall(
        r"(?:(?<![A-Za-z_.])(?:left|right|top|bottom)?[Pp]adding)"
        r"\s*:\s*([^,;\n}]+)", false_line))
    check(f"对照：真实的误报行 —— 旧正则数出 {old_hits} 处、新正则 {new_hits} 处",
          old_hits == 1 and new_hits == 0,
          f"旧 {old_hits} / 新 {new_hits}")

    print("\n  这一条修的是一个**假债**：`control.leftPadding` 是读值，")
    print("  迁移时**无处可改** —— 棘轮盯着一个永远降不下去的数字。")

    # 先跑真实扫描器，确认「假债」已经从基线里去掉
    real_counts, _real_details = spacingtest.scan()
    zero_padding = real_counts.get("padding.zero", 0)
    check("真实代码里 padding 的零值现在是 2 处（原 3 处，去掉 1 处假债）",
          zero_padding == 2, f"实际 {zero_padding}")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
