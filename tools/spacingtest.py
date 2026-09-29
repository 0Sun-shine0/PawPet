r"""间距 token 化率的**唯一扫描器**。

## 为什么需要这个文件

E1（间距 token 化）要的是一个**单调下降的棘轮**，而棘轮的前提是
「同一个口径反复可测」。原来那些数字是我用一次性脚本算的，
脚本删了之后：

  · 数字**无法复现**（谁也验不了 169 是从哪来的）
  · 更糟的是我**换过两次正则**，于是文档里混了两套口径的数字 ——
    这就是 Codex 看到「65 / 201」和「169」对不上的原因
  · 没有固定口径，棘轮根本做不了

所以口径写进代码，而不是写进文档。文档只是这个脚本的输出快照。

## 口径（Codex 要求的「同一扫描器、同一文件范围、同一排除规则」）

### 文件范围
`pawpet/qml/**/*.qml` —— 所有 QML，不挑文件。
（界面之外的东西没有间距概念，不用排除。）

### 排除规则
**真正剥离注释**，不是「跳过以 // 开头的行」。两种注释都处理：

  · `// ...` 到行尾
  · `/* ... */` 可以跨行

剥离时跟踪字符串字面量，避免把 `"https://..."` 里的 `//` 当注释。
**只排除注释行是不够的**：`spacing: 8  // 微调` 这种行内注释后面
还可能藏着别的内容，而 `/* 示例：spacing: 8 */` 会跨行。

### 大小写
**区分大小写。** `bodySpacing` 和 `spacing` 是两个不同的属性 ——
用不区分大小写的工具（比如 PowerShell 的 `Select-String` 默认行为）
会把 `bodySpacing: 10` 也数进去。**实测这一项差 8 处**
（区分大小写 `182` → 不区分 `190`；早先那次记录是 `169 → 177`，
差额同样是 **8** —— 所以这个 8 是复核过的，不是估的）。

⚠️ **别把两个成因算成一个。** 本扫描器除了区分大小写，还会**剥离注释**、
把 `0` 单独列为「零值」、把表达式列为「间接」。所以拿它的 `literal`
子集（`164`）去比一个不区分大小写的**总数**（`177`），是**拿子集比总数**
—— 差出来的 13 里只有 8 是大小写，剩下 5 是换口径。

（我曾在方案文档里把它写成「差 13 处**全部**来自不区分大小写」，
那是错的。见 `docs/体验优化-技术方案.md` 文末修正记录第五条。）

### 算什么（间距类属性）
| 属性 | 为什么算 |
|---|---|
| `spacing` | 布局内元素之间的距离，最直接 |
| `Layout.{left,right,top,bottom}Margin` | 元素相对布局的外边距 |
| `anchors.{margins,leftMargin,...,bottomMargin}` | 手工锚定的外边距 |
| `padding` / `{left,right,top,bottom}Padding` | 容器内边距 |

### 不算什么（尺寸类，不是间距）
`Layout.preferredWidth` / `minimumHeight` / `width` / `height` /
`implicitHeight` —— 这些是**尺寸**。它们确实会被间距变化牵连
（内容自撑的推导链），但那是**风险**，不是「要迁移的间距」。
混在一起会让棘轮的目标变得含糊。

### 四类值

| 分类 | 判据 | 含义 | 棘轮 |
|---|---|---|---|
| `literal` | 裸数字（`10`） | 什么都没接 | **只许降** |
| `seam` | 值里有字面数字的 `Theme.*` 表达式 | 接进了某个轴，但**数值仍写死** | **只许降** |
| `token` | 纯语义名（`Theme.gap`，不含数字） | 真的接了语义 token | 只许升 |
| `zero` | 数值意义上的 `0` | 不需要迁移（`0 × 密度 = 0`） | 不计入 |
| `indirect` | 标识符（`card.bodySpacing`） | 指向别处，要连源头一起看 | 不计入 |

**`seam` 这一类是必须单独分出来的。** 起因是一次真实的误判：

把 `spacing: 10` 改成 `spacing: Theme.space(10)` 之后，
原来的第一版实现（只看「值是不是 `Theme.` 开头」）会把它算成 `token` ——
于是棘轮的「写死数量」从 164 掉到 **0**，看起来债还完了，
**而 164 个数字一个都没少**，只是多包了一层函数。

> 一个会自己归零的棘轮比没有棘轮更糟 —— 它会让人以为债还完了。

`Theme.px(10)` 和 `Theme.gap * 2` 也有同样的问题（值里含字面数字），
只是当前代码里没人在间距上这么写，所以没暴露。

判据用「**值里有没有字面数字**」最简单可靠：`Theme.gap` 没有 → `token`；
`Theme.space(10)` 有 → `seam`。

### 棘轮保护范围 vs 本批迁移范围

**这两个是不同的东西，输出里要能分辨：**

| | 含义 | 涨了会怎样 |
|---|---|---|
| **棘轮保护范围** | 所有「不该涨的写死值」= `literal` + `seam` | 红 —— 防止有人图快写回裸数字 |
| **本批迁移范围** | 这一批实际要改的（当前是 `spacing`） | 没改完不算失败，只是还没轮到 |

**棘轮该宽、迁移该窄**：棘轮把 `spacing` / `margin` / `padding` 都罩住
（防止退步），但某一批可能只动其中一类。

`indirect` 也单独列出来而不是归进任一边 —— 它既不是已完成、
也不是简单替换。实测有 2 处指向 `Card.bodySpacing: 10`，
说明**要彻底 token 化得连 `Card` 的属性默认值一起改**。

`zero` 也单独列：`spacing: 0` / `padding: 0` 表示「元素紧贴」，
而 `0 × 密度 = 0` —— 紧凑模式下它本来就不变，所以**不是未 token 化的债**，
是「不需要 token 化」。实测 8 处（spacing 5、padding 3）。

把它算进债里会让棘轮永远归不了零，而且会诱导出一个没有意义的改动
（`spacing: 0` → `spacing: Theme.gap(0)`）。**口径要能解释为什么要迁**，
不然数字只是在制造工作量。

用法：
    .venv\Scripts\python.exe tools\spacingtest.py
    .venv\Scripts\python.exe tools\spacingtest.py --detail    # 列出每一处
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QML_DIR = ROOT / "pawpet" / "qml"

# ---------------------------------------------------------------- 棘轮基线
#
# **只允许下降。** 每次迁移一批就把这里改小，不允许改大。
# 抗不住诱惑加回去的话，这个测试就没意义了 —— 它是拿来防退步的，
# 不是拿来记录现状的。
#
# 基线是 2026-09-29 用这个扫描器**首次测量**的实际值（不是估的、
# 也不是从别处抄的）。`zero` 不计入 —— 理由见模块 docstring。
#
# **两项都盯：**
#   `literal` —— 裸数字，什么都没接
#   `seam`    —— 接了轴（`Theme.space(N)` / `Theme.px(N)`）但数值仍写死
#
# 只盯 `literal` 是不够的：把它改成 `Theme.space(N)` 就能让那个数归零，
# 而数字一个都没少。
#
# `seam` 的基线不是 0 —— **首次测量就抓到 3 处历史遗留的 seam**：
#
#     Onboarding.qml:137  anchors.margins: Theme.px(10)
#     Onboarding.qml:157  anchors.margins: Theme.px(26)
#     Onboarding.qml:284  spacing: Theme.px(4)
#
# 它们接了**整体缩放轴**（`ui_scale`）但数值写死 —— 对 E2 的密度轴
# 不会响应，所以是真债。这三处是**第一版口径漏掉的**（当时把它们
# 算成 `token` 了），不是我引入的。
#
# 这也说明口径这件事值得做：旧口径看起来「margin 已 39% token 化」，
# 实际有 2 处是被误算进去的。
RATCHET: dict[str, dict[str, int]] = {
    "spacing": {"literal": 164, "seam": 1},
    "margin": {"literal": 86, "seam": 2},
    "padding": {"literal": 19, "seam": 0},
}

# **本批迁移范围** —— 和上面那个棘轮保护范围**不是一回事**，
# 所以是两个常量，不是一个：
#
#   棘轮保护范围 = RATCHET 的全部键（三类，共 272 处）
#       涨了就红 —— 防止有人图快写回裸数字。
#   本批迁移范围 = 这个常量（一类，共 165 处）
#       没改完不算失败，只是还没轮到。
#
# **棘轮该宽、迁移该窄**：如果棘轮不管 `padding`，那有人把
# `padding: Theme.gap` 改回 `padding: 10` 就溜过去了 —— 而
# 「哪些属性算间距」和「这一批改哪些」本来就没有关系。
#
# E2 只动 `spacing`，因为密度轴对它的效果最直接；`margin` 有 88 处、
# `padding` 有 19 处，一起并进来会让这一批大到没法审。
MIGRATION_SCOPE: tuple[str, ...] = ("spacing",)

# 间距类属性。顺序无所谓，正则拼起来用。
SPACING_PROPS: dict[str, str] = {
    "spacing": r"(?<![A-Za-z_])spacing",
    "margin": (r"(?:"
               r"Layout\.(?:left|right|top|bottom)Margin"
               r"|anchors\.(?:margins|leftMargin|rightMargin"
               r"|topMargin|bottomMargin)"
               r")"),
    "padding": (r"(?:"
                r"(?<![A-Za-z_])(?:left|right|top|bottom)?[Pp]adding"
                r")"),
}

# 数字字面量：整数、小数、负数
LITERAL = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
# Theme 开头的都算「接了某个轴」
TOKEN = re.compile(r"^Theme\.\w+")
# 值里有没有字面数字 —— 用来区分「纯语义 token」和「接了轴但数值仍写死」
DIGIT = re.compile(r"\d")


def strip_comments(text: str) -> str:
    """剥掉 QML 注释，保留字符串字面量里的内容。

    逐字符走一个小状态机，而不是按行过滤 —— 原因见模块 docstring：
    `spacing: 8  // 微调` 这类行内注释、以及跨行的 `/* */` 都要处理。

    换行原样保留，这样报出来的行号和源文件对得上。
    """
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]

        # 字符串字面量：原样复制，直到收尾引号
        if char == '"':
            out.append(char)
            index += 1
            while index < length:
                current = text[index]
                if current == "\\" and index + 1 < length:
                    out.append(text[index:index + 2])
                    index += 2
                    continue
                out.append(current)
                index += 1
                if current == '"':
                    break
            continue

        # 行注释
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                index += 1
            continue

        # 块注释（可跨行）
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index < length:
                if text[index] == "*" and index + 1 < length \
                        and text[index + 1] == "/":
                    index += 2
                    break
                # 换行保留，行号才不会错位
                if text[index] == "\n":
                    out.append("\n")
                index += 1
            continue

        out.append(char)
        index += 1

    return "".join(out)


def classify_value(raw: str) -> str:
    """把属性值分类成 token / seam / literal / zero / indirect。

    判据见模块 docstring。最关键的一条：

        `Theme.gap`        → token （纯语义名，不含数字）
        `Theme.space(10)`  → seam  （**数值仍写死**，只是包了一层）
        `Theme.px(10)`     → seam
        `Theme.gap * 2`    → seam

    **`seam` 必须和 `token` 分开**，否则棘轮会假性归零 ——
    165 处 `spacing: N` 全改成 `Theme.space(N)` 之后，
    「写死数量」变 0 而数字一个没少。
    """
    value = raw.strip().rstrip(";").strip()
    if not value:
        return "indirect"
    if LITERAL.match(value):
        # 数值意义上的零（0 / 0.0 / -0），不按字符串比
        try:
            return "zero" if float(value) == 0.0 else "literal"
        except ValueError:
            return "literal"
    if TOKEN.match(value):
        # **值里有字面数字 → 数值仍然写死，只是接进了某个轴。**
        # 误判方向是保守的：宁可把它算成 seam（欠债）也不当成 token（已完成）。
        return "seam" if DIGIT.search(value) else "token"
    return "indirect"


def scan() -> tuple[Counter, list[tuple[str, int, str, str, str]]]:
    """扫一遍，返回 (分类计数, 明细)。

    明细是 [(相对路径, 行号, 类别, 属性, 值)]。
    """
    counts: Counter = Counter()
    details: list[tuple[str, int, str, str, str]] = []

    # 每个属性名拼一个「属性 : 值」的正则
    patterns = {
        category: re.compile(prop + r"\s*:\s*([^,;\n}]+)")
        for category, prop in SPACING_PROPS.items()
    }

    for path in sorted(QML_DIR.rglob("*.qml")):
        raw = path.read_text(encoding="utf-8")
        code = strip_comments(raw)
        relative = str(path.relative_to(ROOT))

        for number, line in enumerate(code.splitlines(), 1):
            for category, pattern in patterns.items():
                for match in pattern.finditer(line):
                    kind = classify_value(match.group(1))
                    counts[f"{category}.{kind}"] += 1
                    details.append((relative, number, category, kind,
                                    match.group(1).strip()[:40]))

    return counts, details


def main() -> int:
    parser = argparse.ArgumentParser(description="间距 token 化率扫描")
    parser.add_argument("--detail", action="store_true",
                        help="列出每一处（按类别分组）")
    args = parser.parse_args()

    print("间距 token 化率扫描")
    print(f"范围：{QML_DIR.relative_to(ROOT)}/**/*.qml")
    print("口径：剥离注释 / 区分大小写 / 四类值（含 seam） —— 见本文件 docstring\n")

    counts, details = scan()

    print("=" * 74)
    print("按类别")
    print("=" * 74)
    print(f"  {'类别':10s} {'token':>7s} {'写死':>7s} {'seam':>7s} "
          f"{'零值':>6s} {'间接':>6s} {'合计':>7s} {'token 率':>9s}")
    totals: Counter = Counter()
    for category in SPACING_PROPS:
        token = counts.get(f"{category}.token", 0)
        literal = counts.get(f"{category}.literal", 0)
        seam = counts.get(f"{category}.seam", 0)
        zero = counts.get(f"{category}.zero", 0)
        indirect = counts.get(f"{category}.indirect", 0)
        total = token + literal + seam + zero + indirect
        rate = token / total * 100 if total else 0.0
        print(f"  {category:10s} {token:7d} {literal:7d} {seam:7d} "
              f"{zero:6d} {indirect:6d} {total:7d} {rate:8.1f}%")
        totals["token"] += token
        totals["literal"] += literal
        totals["seam"] += seam
        totals["zero"] += zero
        totals["indirect"] += indirect

    grand = sum(totals.values())
    print(f"  {'合计':10s} {totals['token']:7d} {totals['literal']:7d} "
          f"{totals['seam']:7d} {totals['zero']:6d} {totals['indirect']:6d} "
          f"{grand:7d} {totals['token'] / max(1, grand) * 100:8.1f}%")
    print()
    print(f"  （零值 {totals['zero']} 处不计入棘轮 —— 0 × 密度 = 0，"
          f"不需要迁移）")
    print(f"  （`写死` + `seam` = 还没真正接上语义 token 的数量："
          f"{totals['literal'] + totals['seam']} 处）")

    # **保护范围和迁移范围要分开报。** 它们容易被当成同一个进度条：
    # 棘轮绿了不等于迁移做完了（绿只说明没人退步），
    # 迁移没做完也不会让棘轮变红。见本文件 docstring 那一节。
    protected = sum(
        counts.get(f"{c}.{k}", 0)
        for c in SPACING_PROPS for k in ("literal", "seam")
    )
    migrating = sum(
        counts.get(f"{c}.{k}", 0)
        for c in MIGRATION_SCOPE for k in ("literal", "seam")
    )
    print()
    print(f"  棘轮保护范围：全部 {len(SPACING_PROPS)} 类，共 {protected} 处"
          f"（{'/'.join(SPACING_PROPS)}）")
    print(f"  本批迁移范围：只用 {len(MIGRATION_SCOPE)} 类，共 {migrating} 处"
          f"（{'/'.join(MIGRATION_SCOPE)}）"
          f"　← E2 的密度轴只对这类生效")

    print()
    print("=" * 74)
    print("棘轮检查（literal 和 seam 各自只允许下降）")
    print("=" * 74)
    print(f"  {'类别':10s} {'literal':>18s} {'seam':>18s}")
    failures: list[str] = []
    for category, baseline in RATCHET.items():
        cells: list[str] = []
        for kind in ("literal", "seam"):
            current = counts.get(f"{category}.{kind}", 0)
            was = baseline.get(kind, 0)
            if current > was:
                failures.append(
                    f"{category} 的 {kind} 从 {was} 涨到 {current}")
                cells.append(f"  [XX] {was} → {current} 涨了")
            elif current == was:
                cells.append(f"  [ok] {was} → {current} 持平")
            else:
                cells.append(f"  [ok] {was} → {current} 降 {was - current}")
        print(f"  {category:10s} {cells[0]:>18s} {cells[1]:>18s}")

    if args.detail:
        print()
        print("=" * 70)
        print("明细（写死 + seam —— 这两类都是要迁移的）")
        print("=" * 70)
        for category in SPACING_PROPS:
            rows = [item for item in details if item[2] == category
                    and item[3] in ("literal", "seam")]
            if not rows:
                continue
            print(f"\n  --- {category}（{len(rows)} 处）---")
            for relative, number, _c, kind, value in rows:
                print(f"    [{kind}] {relative}:{number}  {value}")

    print()
    print("=" * 70)
    if failures:
        print(f"**棘轮被突破（{len(failures)} 项）**")
        for item in failures:
            print(f"  - {item}")
        print()
        print("  如果这是有意的，请**同时**修改 RATCHET（两种类别的基线都要改）"
              "并在提交信息里说明原因 —— 棘轮不是用来记录现状的，是用来防退步的。")
        return 1
    print("棘轮通过：写死的间距（literal + seam）没有增加")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
