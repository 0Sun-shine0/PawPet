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
会把 `bodySpacing: 10` 也数进去，这是实测踩过的（169 vs 177 的差）。

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

### 三类值
| 分类 | 判据 | 迁移动作 |
|---|---|---|
| `token` | 值是 `Theme.xxx` | 已完成 |
| `literal` | 值是数字 | **要迁移** |
| `indirect` | 值是标识符/表达式（`card.bodySpacing`） | 看情况：间接引用最后仍指向某处的写死值 |
| `zero` | 值是 `0` | **不用迁移**，见下 |

`indirect` 单独列出来而不是归进任一边 —— 它既不是已完成、
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
RATCHET_LITERAL: dict[str, int] = {
    "spacing": 164,
    "margin": 86,
    "padding": 19,
}

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
# Theme 开头的都算走 token
TOKEN = re.compile(r"^Theme\.\w+")


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
    """把属性值分类成 token / literal / zero / indirect。

    `zero` 单独分出来：`0 × 密度 = 0`，紧凑模式下它本来就不变，
    所以不是「未 token 化的债」。算进债里会让棘轮永远归不了零。
    """
    value = raw.strip().rstrip(";").strip()
    if not value:
        return "indirect"
    if TOKEN.match(value):
        return "token"
    if LITERAL.match(value):
        # 数值意义上的零（0 / 0.0 / -0），不按字符串比
        try:
            return "zero" if float(value) == 0.0 else "literal"
        except ValueError:
            return "literal"
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
    print("口径：剥离注释 / 区分大小写 / 三类值 —— 见本文件 docstring\n")

    counts, details = scan()

    print("=" * 70)
    print("按类别")
    print("=" * 70)
    print(f"  {'类别':10s} {'token':>7s} {'写死':>7s} {'零值':>6s} "
          f"{'间接':>6s} {'合计':>7s} {'token 率':>9s}")
    totals: Counter = Counter()
    for category in SPACING_PROPS:
        token = counts.get(f"{category}.token", 0)
        literal = counts.get(f"{category}.literal", 0)
        zero = counts.get(f"{category}.zero", 0)
        indirect = counts.get(f"{category}.indirect", 0)
        total = token + literal + zero + indirect
        rate = token / total * 100 if total else 0.0
        print(f"  {category:10s} {token:7d} {literal:7d} {zero:6d} "
              f"{indirect:6d} {total:7d} {rate:8.1f}%")
        totals["token"] += token
        totals["literal"] += literal
        totals["zero"] += zero
        totals["indirect"] += indirect

    grand = sum(totals.values())
    print(f"  {'合计':10s} {totals['token']:7d} {totals['literal']:7d} "
          f"{totals['zero']:6d} {totals['indirect']:6d} {grand:7d} "
          f"{totals['token'] / max(1, grand) * 100:8.1f}%")
    print()
    print(f"  （零值 {totals['zero']} 处不计入棘轮 —— "
          f"0 × 密度 = 0，不需要迁移）")

    print()
    print("=" * 70)
    print("棘轮检查（只允许下降）")
    print("=" * 70)
    failures: list[str] = []
    for category, baseline in RATCHET_LITERAL.items():
        current = counts.get(f"{category}.literal", 0)
        if current > baseline:
            failures.append(
                f"{category} 的写死数量从 {baseline} 涨到 {current}")
            print(f"  [XX] {category:10s} {baseline} → {current}  **涨了**")
        else:
            note = "（持平）" if current == baseline else \
                   f"（降了 {baseline - current}）"
            print(f"  [ok] {category:10s} {baseline} → {current}  {note}")

    if args.detail:
        print()
        print("=" * 70)
        print("明细（只看写死的，那是要迁移的）")
        print("=" * 70)
        for category in SPACING_PROPS:
            rows = [item for item in details if item[2] == category
                    and item[3] == "literal"]
            if not rows:
                continue
            print(f"\n  --- {category}（{len(rows)} 处）---")
            for relative, number, _c, _k, value in rows:
                print(f"    {relative}:{number}  {value}")

    print()
    print("=" * 70)
    if failures:
        print(f"**棘轮被突破（{len(failures)} 项）**")
        for item in failures:
            print(f"  - {item}")
        print()
        print("  如果这是有意的，请**同时**修改 RATCHET_LITERAL 并在提交信息里"
              "说明原因 —— 棘轮不是用来记录现状的，是用来防退步的。")
        return 1
    print("棘轮通过：写死的间距没有增加")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
