r"""语义 token 化 **盘点**：把剩余 seam 逐项判出「属性 / 值 / 轴 / 语义」。

## 这个工具为什么存在

E7 把 172 处裸数字接到了密度轴上，但**数值仍然写死** ——
`Theme.space(8)` 里的 `8` 和一个裸 `8` 一样，改一处不影响别处。
「语义 token 化」就是把 `8` 换成 `Theme.dFormGap` 这种**有名字**的量。

Codex 第 15 轮定的前提：**先盘点，不直接改**。本工具就是那份盘点的
**可复现来源** —— 数字是它算的，它留在仓库里，谁都能重跑。

## 判语义的规则（以及为什么**不能**只看子元素）

第一版只用「子元素类型」判语义，**抽检 5 处错了 3 处**：

  · `AiPage.qml:1119` 判成「段落堆叠」，实际是 `RowLayout` 里
    **图标 ↔ 文字**的水平间距
  · `TasksPage.qml:530` 判成「段落堆叠」，实际是**任务行内**的水平间距
  · `AiSettingsPanel.qml:275` 判成「段落堆叠」，实际是 `RowLayout` 的
    **两个 Text 之间**的水平间距

根因：`spacing` 在 `ColumnLayout` 上是**垂直节奏**，在 `RowLayout` 上是
**水平间距** —— 这是两个不同的语义。所以判读必须**先看布局方向**。

> **教训**：自动分类可以用于「分组统计」，**不能当最终映射表**。
> 盘点表里的每一处都要人过一遍 —— 那正是 Codex 复核要做的事。

## 用法

    .venv\\Scripts\\python.exe tools\\tokenmap.py            # 汇总
    .venv\\Scripts\\python.exe tools\\tokenmap.py --full     # 逐处明细
    .venv\\Scripts\\python.exe tools\\tokenmap.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8  # noqa: E402

configure_utf8()

from spacingtest import scan  # noqa: E402

# ---------------------------------------------------------------- 词法
ELEM_RE = re.compile(r"^(\s*)([A-Z][A-Za-z0-9_.]*)\s*\{")
PROP_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_.]*)\s*:")
SPACE_RE = re.compile(r"Theme\.(scaled)?[Ss]pace\(\s*(-?\d+(?:\.\d+)?)\s*\)")
PX_RE = re.compile(r"Theme\.px\(\s*(-?\d+(?:\.\d+)?)\s*\)")

# 元素按「它在间距里扮演什么」归类
K_TEXT = {"Text", "Label"}
K_INPUT = {"TextField", "TextArea", "ComboBox", "SpinBox", "PawField"}
K_BUTTON = {"PawButton", "Button", "RoundButton", "ToolButton",
            "PawSwitch", "CheckBox", "Switch"}
K_BOX = {"Rectangle", "Item", "PawCard", "Image"}
K_LIST = {"Repeater", "ListView", "GridView"}
K_LAYOUT = {"ColumnLayout", "RowLayout", "GridLayout", "Flow",
            "Column", "Row"}


def norm(p: str) -> str:
    return p.replace("\\", "/")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _strip(line: str) -> str:
    line = re.sub(r"//.*$", "", line)
    return re.sub(r'"[^"]*"', '""', line)


def owner_of(lines: list[str], lineno: int) -> tuple[int, str] | None:
    """往上找最近的、形如 `Foo {` 的未闭合宿主（1-based 行号, 元素名）。"""
    level = 0
    for i in range(lineno - 1, -1, -1):
        if i >= len(lines):
            continue
        text = _strip(lines[i])
        close, open_ = text.count("}"), text.count("{")
        if level == 0 and open_ > 0 and close == 0:
            m = ELEM_RE.match(lines[i])
            return (i + 1, m.group(2)) if m else (i + 1, text.strip()[:24])
        level = max(level + close - open_, 0)
    return None


def children_of(lines: list[str], brace_line: int) -> list[str]:
    """`Foo {` 的**直接子元素**类型名（按缩进判层级）。"""
    if not (1 <= brace_line <= len(lines)):
        return []
    base = _indent(lines[brace_line - 1])
    out: list[str] = []
    for i in range(brace_line, len(lines)):
        raw = lines[i]
        if not raw.strip():
            continue
        if _indent(raw) <= base:
            break
        m = ELEM_RE.match(raw)
        if m:
            out.append(m.group(2))
    return out


def kinds_of(children: list[str]) -> set[str]:
    kinds: set[str] = set()
    for c in children:
        for name, tag in ((K_TEXT, "text"), (K_INPUT, "input"),
                          (K_BUTTON, "button"), (K_BOX, "box"),
                          (K_LIST, "list"), (K_LAYOUT, "layout")):
            if c in name:
                kinds.add(tag)
                break
        else:
            kinds.add("other")
    return kinds


# ---------------------------------------------------------------- 语义
def role_for_spacing(host: str, kinds: set[str],
                     children: list[str]) -> tuple[str, str]:
    """`spacing` 的语义。返回 (语义名, 置信度)。

    **先看布局方向** —— 这是第一版漏掉的关键：
    `ColumnLayout` 的 `spacing` 是垂直节奏，`RowLayout` 的是水平间距。
    """
    vertical = host == "ColumnLayout" or host == "Column"
    horizontal = host == "RowLayout" or host == "Row"
    if not (vertical or horizontal):
        return f"spacing@{host}", "low"

    dirname = "纵向" if vertical else "横向"
    pure = {k for k in kinds if k != "other"}

    if pure == {"text"}:
        # 纵向 = 段落堆叠；横向 = 两个文字并排
        return (f"段落堆叠（纵向）" if vertical else "文字并排（横向）"), "high"
    if pure == {"button"}:
        return "按钮组（横向）" if horizontal else "按钮堆叠（纵向）", "high"
    if pure == {"text", "input"}:
        return ("标签↔输入框（横向）" if horizontal
                else "表单堆叠（纵向）"), "high"
    if pure == {"box", "text"}:
        return ("卡片堆叠（纵向）" if vertical
                else "方块+文字（横向）"), "low"
    if pure == {"input"}:
        return f"输入控件{dirname}", "high"
    if "list" in pure:
        return f"列表容器{dirname}", "low"
    if "input" in pure:
        return f"含输入的{dirname}混合块", "low"
    if "button" in pure:
        return f"含按钮的{dirname}混合块", "low"
    if "text" in pure:
        return f"文字{dirname}混合块", "low"
    return f"{dirname}混合块", "low"


def role_for_box(prop: str, host: str) -> tuple[str, str]:
    """`anchors.*Margin` / `padding` 的语义 —— 由**宿主**决定。"""
    if prop == "anchors.margins":
        return ("四周内缩（疑似卡内边距）",
                "high" if host in ("ColumnLayout", "RowLayout", "Rectangle")
                else "low")
    if prop in ("anchors.leftMargin", "anchors.rightMargin"):
        return "左右单侧留白", "high"
    if prop in ("anchors.topMargin", "anchors.bottomMargin"):
        return "上下单侧偏移", "high"
    if prop == "padding":
        conf = "high" if host in ("Dialog", "Popup") else "low"
        return f"整体内边距@{host}", conf
    if prop in ("leftPadding", "rightPadding", "topPadding", "bottomPadding"):
        return f"单侧内边距@{host}", "high"
    return f"{prop}@{host}", "low"


# ---------------------------------------------------------------- 主流程
def build() -> list[dict]:
    counts, details = scan()
    cache: dict[str, list[str]] = {}
    for rel, _l, _c, _k, _v in details:
        r = norm(rel)
        if r not in cache:
            cache[r] = (ROOT / r).read_text(
                encoding="utf-8", errors="replace").split("\n")

    rows: list[dict] = []
    for rel, lineno, cat, kind, _v in details:
        if not kind.startswith("seam"):
            continue
        r = norm(rel)
        lines = cache[r]
        text = lines[lineno - 1] if lineno <= len(lines) else ""

        pm = PROP_RE.match(text)
        prop = pm.group(2) if pm else "?"

        sm = SPACE_RE.search(text)
        if sm:
            axis = "scale" if sm.group(1) else "density"
            value = sm.group(2)
        else:
            xm = PX_RE.search(text)
            if xm:
                axis, value = "scale", xm.group(1)
            else:
                axis, value = "?", "?"

        own = owner_of(lines, lineno)
        host = own[1] if own else "?"
        children = children_of(lines, own[0]) if own else []
        kids = kinds_of(children)

        if prop == "spacing":
            role, conf = role_for_spacing(host, kids, children)
        else:
            role, conf = role_for_box(prop, host)

        # 惰性：spacing 的子元素 ≤1 个 → 「之间」不存在
        inert = prop == "spacing" and len(children) <= 1
        # 小值/负值：`space()` 原值返回 → 永不缩放
        try:
            nval = float(value)
        except ValueError:
            nval = None
        never_scales = (axis == "density" and nval is not None
                        and abs(nval) <= 4)

        rows.append({
            "rel": r, "line": lineno, "cat": cat, "prop": prop,
            "value": value, "axis": axis, "role": role, "conf": conf,
            "host": host, "children": children, "inert": inert,
            "never_scales": never_scales, "src": text.strip(),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="语义 token 化盘点")
    ap.add_argument("--full", action="store_true", help="逐处明细")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    rows = build()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("=" * 98)
    print("一、总量与轴")
    print("=" * 98)
    axis_c = Counter((r["cat"], r["axis"]) for r in rows)
    for cat in ("spacing", "margin", "padding"):
        dens = axis_c.get((cat, "density"), 0)
        scal = axis_c.get((cat, "scale"), 0)
        print(f"  {cat:9s} seam {dens + scal:4d}　"
              f"密度轴 {dens:4d}（跟 density）　"
              f"**缩放轴 {scal}**（跟 uiScale）")
    print(f"\n  合计 {len(rows)} 处")
    print("  ⚠ 两个轴**无关** —— 缩放轴那几处只能继续走 `scaledSpace`/`px`，")
    print("    换成密度轴 token 会**静默换轴**（Codex 硬边界第 2 条）。")

    print()
    print("=" * 98)
    print("二、语义 × 值（`spacing`）")
    print("=" * 98)
    sp = [r for r in rows if r["prop"] == "spacing"]
    grp = Counter((r["role"], r["value"], r["conf"]) for r in sp)
    for (role, val, conf), n in sorted(
            grp.items(), key=lambda kv: (kv[0][2] != "high", -kv[1])):
        print(f"  {n:3d}  {role:28s} = {val:>3s}   [{conf}]")

    print()
    print("=" * 98)
    print("三、同一个数字跨几个语义（Codex 要的「无法安全合并」）")
    print("=" * 98)
    by_val: dict[str, set] = defaultdict(set)
    for r in sp:
        by_val[r["value"]].add(r["role"])
    for val in sorted(by_val, key=lambda v: -len(by_val[v])):
        roles = sorted(by_val[val])
        n = sum(1 for r in sp if r["value"] == val)
        mark = "**" if len(roles) > 1 else "  "
        print(f"  {mark}spacing = {val:>3s}（{n:2d} 处）跨 {len(roles)} 个语义"
              f"{'　← 不能机械合并' if len(roles) > 1 else ''}")
        if args.full or len(roles) > 1:
            for role in roles:
                print(f"        · {role}")

    print()
    print("=" * 98)
    print("四、三类「特殊项」（token 化要么无收益、要么必须避开）")
    print("=" * 98)
    inert = [r for r in rows if r["inert"]]
    small = [r for r in rows if r["never_scales"]]
    scale = [r for r in rows if r["axis"] == "scale"]
    print(f"  1. **惰性 spacing**（子元素 ≤1，「之间」不存在）　{len(inert)} 处")
    for r in inert:
        print(f"       {r['rel'].replace('pawpet/qml/PawPet/', '')}"
              f":{r['line']}  子 {len(r['children'])} 个")
    print(f"     ⚠ token 化它们**零风险但零收益** —— 不能拿来充候选数。")
    print()
    print(f"  2. **小值 / 负值**（`abs(N) <= denseFloor(4)` → 原值返回，"
          f"永不缩放）　{len(small)} 处")
    for r in sorted(small, key=lambda x: (x["rel"], x["line"])):
        print(f"       {r['rel'].replace('pawpet/qml/PawPet/', '')}"
              f":{r['line']}  = {r['value']}")
    print(f"     ⚠ 它们**计数上是进度、感知上没有变化** —— 单独一类，")
    print(f"       不算「可合并的语义 token」。")
    print()
    print(f"  3. **缩放轴**（`px` / `scaledSpace`）　{len(scale)} 处")
    for r in sorted(scale, key=lambda x: (x["rel"], x["line"])):
        print(f"       {r['rel'].replace('pawpet/qml/PawPet/', '')}"
              f":{r['line']}  {r['prop']} = {r['value']}")
    print(f"     ⚠ **必须留在缩放轴** —— 换成密度轴 token 就是静默换轴。")

    print()
    print("=" * 98)
    print("五、候选池：`(语义, 值)` 分组，值**保持不变**")
    print("=" * 98)
    print("  角色有多个值**不妨碍** token 化 —— 把 token 定义成")
    print("  **(角色, 尺寸) 对**即可（`dFormGap` / `dFormGapSm`），值不变。")
    print()
    pool = Counter((r["role"], r["value"], r["conf"]) for r in sp
                   if not r["inert"])
    high_total = 0
    for (role, val, conf), n in sorted(
            pool.items(), key=lambda kv: (kv[0][2] != "high",
                                          float(kv[0][1]))):
        if n < 2:
            continue
        if conf == "high":
            high_total += n
        print(f"  {n:3d}  {role:28s} = {val:>3s}   [{conf}]")
    print(f"\n  **high 置信度候选合计 {high_total} 处**（≤20，可作第一批）")

    if args.full:
        print()
        print("=" * 98)
        print("六、逐处明细")
        print("=" * 98)
        cur = None
        for r in sorted(rows, key=lambda x: (x["rel"], x["line"])):
            if r["rel"] != cur:
                cur = r["rel"]
                print(f"\n=== {cur} ===")
            flags = "".join((" [惰性]" if r["inert"] else "",
                             " [永不缩放]" if r["never_scales"] else "",
                             " [缩放轴]" if r["axis"] == "scale" else ""))
            print(f"  :{r['line']:<6d} {r['prop']:24s} "
                  f"{r['value']:>4s} ({r['axis']:7s}) "
                  f"{r['role']:28s} [{r['conf']}]{flags}")

    print()
    print("=" * 98)
    print("判读的边界（必须一起交出去）")
    print("=" * 98)
    print("  本工具的语义是**按「布局方向 + 子元素类型」自动判的**。")
    print("  第一版只用子元素、抽检 5 处**错了 3 处**；这一版加了布局方向。")
    print("  **它仍然会错。** 所以：")
    print("    · 它用于**分组统计**和**缩小人审范围**")
    print("    · **不能**当作最终映射表 —— 每一处都要人过一遍")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
