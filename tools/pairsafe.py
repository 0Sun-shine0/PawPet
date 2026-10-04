r"""塌陷检测：成对边距在四个 density 下会不会**变成相等**。

## Codex 第 11 轮第 3 条

「`density=1.00` 与迁移前保持视觉等价；`0.90/0.80/0.70` 下边距单调收紧
**且不塌陷**」。

「塌陷」= 成对的两个值在某个 density 下**变成同一个数** ——
那就说明原本刻意的左右/上下差异被密度缩放抹平了。

## 为什么现在要**全量**做，而不只查本批

批 3 要做「值不同的 10 对」，而**塌陷风险正好在那 10 对上**
（本批的 10 对值相同，恒不塌陷）。所以这个工具的用途是：
  · 现在：确认本批 10 对（值相同）在任何档位都不塌陷
  · 批 3：确认那 10 对（值不同）也不塌陷
它同时覆盖「本批已迁的」和「批 3 待迁的」，因为**值都是确定的、与是否迁移无关**。

## 判据与它的边界

对每一对 (a, b)，算四个 density 下的实际值，检查 `space(a,d) != space(b,d)`。

**注意这不代表「视觉上有区别」** —— 差 1px 在屏幕上基本看不出来。
它只保证「没有变成完全相等」。**这一点要写清楚，不要夸大。**
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\pet")
sys.path.insert(0, str(ROOT / "tools"))

from spacingtest import scan  # noqa: E402

FLOOR = 4
# Codex 第 6 轮定的四档
DENSITIES = [1.00, 0.90, 0.80, 0.70]


def js_round(x: float) -> int:
    """JS 的 `Math.round`（`.5` 向 +∞），不是 Python 的 `round`。"""
    return math.floor(x + 0.5)


def space(value: float, density: float) -> float:
    return value if abs(value) <= FLOOR else js_round(value * density)


def norm(p: str) -> str:
    return p.replace("\\", "/")


counts, details = scan()
RAW: dict[str, list[str]] = {}
for rel, _l, _c, _k, _v in details:
    r = norm(rel)
    if r not in RAW:
        RAW[r] = (ROOT / r).read_text(encoding="utf-8",
                                      errors="replace").splitlines()

PROP_RE = re.compile(r"^\s*([A-Za-z_.]+)\s*:\s*(.+?)\s*$")

# 收集全部**当前还是 literal** 的单侧边距（本批迁完后剩 25+2 处，
# 其中大部分是批 3 的；已经迁成 Theme.space 的要回读其值）
entries = []
for rel, line, cat, kind, raw in details:
    if cat not in ("margin", "padding"):
        continue
    if kind not in ("literal", "seam"):
        continue
    r = norm(rel)
    src = RAW[r]
    text = src[line - 1] if 0 < line <= len(src) else ""
    m = PROP_RE.match(text)
    prop = m.group(1) if m else "?"
    if prop in ("anchors.margins", "padding"):
        continue
    # 值：literal 就是 raw；seam 从 `Theme.space(N)` 里抠
    v = raw.strip()
    sm = re.search(r"Theme\.space\(\s*(-?\d+(?:\.\d+)?)\s*\)", v)
    if sm:
        v = sm.group(1)
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", v):
        continue
    entries.append({"rel": r, "line": line, "prop": prop, "value": float(v)})


def counterpart(prop: str) -> str | None:
    for a, b in (("left", "right"), ("right", "left"),
                 ("top", "bottom"), ("bottom", "top")):
        idx = prop.lower().find(a)
        if idx >= 0:
            return prop[:idx] + b + prop[idx + len(a):]
    return None


grouped = sorted(entries, key=lambda x: (x["rel"], x["line"]))
used: set = set()
pairs = []
for it in grouped:
    key = (it["rel"], it["line"])
    if key in used:
        continue
    want = counterpart(it["prop"])
    partner = None
    if want:
        for other in grouped:
            okey = (other["rel"], other["line"])
            if okey == key or okey in used or other["rel"] != it["rel"]:
                continue
            if other["prop"] == want and abs(other["line"] - it["line"]) <= 6:
                partner = other
                break
    used.add(key)
    if partner:
        used.add((partner["rel"], partner["line"]))
        pairs.append((it, partner))

print(f"=== 找到 {len(pairs)} 对成对边距，检查四个档位是否塌陷 ===\n")

# **「塌陷」只对「原本值不同」的配对有意义。**
#
# 值相同的配对（`10 / 10`）本来就应该相等 —— 那是**对称**，
# 不是塌陷。第一版没区分，把 10 对对称的也算成「塌陷」，
# 报了 48 项失败（每对 × 4 个档位）—— **判据搞错了**。
#
# 真正要查的是：**原本刻意不等的两个值，会不会被密度缩放抹平成相等。**
same_pairs = [(a, b) for a, b in pairs if a["value"] == b["value"]]
diff_pairs = [(a, b) for a, b in pairs if a["value"] != b["value"]]
print(f"  值相同（对称）：{len(same_pairs)} 对 —— 恒相等，**不算塌陷**")
print(f"  值不同（要查塌陷）：{len(diff_pairs)} 对\n")

print(f"  {'配对（值不同）':46s} " + " ".join(f"{d:>9.2f}" for d in DENSITIES))
print("  " + "-" * 90)
collapse = []
for a, b in diff_pairs:
    cells = []
    for d in DENSITIES:
        va, vb = space(a["value"], d), space(b["value"], d)
        mark = "!" if va == vb else " "
        cells.append(f"{va:g}/{vb:g}{mark}")
        if va == vb:
            collapse.append((a, b, d))
    tag = f"{a['rel'].split('/')[-1]}:{a['line']}~{b['line']}"
    print(f"  {tag:46s} " + " ".join(f"{c:>9s}" for c in cells))
print("\n  图例：`左/右`，`!` = 变成相等（**塌陷**）")

print("\n" + "=" * 90)
print("判读")
print("=" * 90)
if collapse:
    print(f"  **有 {len(collapse)} 对会塌陷**：")
    for a, b, d in collapse:
        print(f"    {a['rel']}:{a['line']}~{b['line']}  "
              f"（原本 {a['value']:g} vs {b['value']:g}）在 density={d} 时相等")
    raise SystemExit(1)
print(f"  ✓ {len(diff_pairs)} 对「值不同」的在四个档位下**都不塌陷**")
print(f"    （另有 {len(same_pairs)} 对「值相同」的恒相等 —— 那是对称，正常）")

# 最薄的那对有多薄
thin = []
for a, b in diff_pairs:
    for d in DENSITIES:
        va, vb = space(a["value"], d), space(b["value"], d)
        thin.append((abs(va - vb), a, b, d))
thin.sort(key=lambda x: x[0])
print(f"\n  最薄的 3 对（差值最小）：")
for diff, a, b, d in thin[:3]:
    print(f"    {a['rel'].split('/')[-1]}:{a['line']}~{b['line']}  "
          f"density={d}: {space(a['value'], d):g} vs "
          f"{space(b['value'], d):g}（差 {diff:g}px）")

print()
print("  ⚠ **「不塌陷」不等于「视觉上分得开」。** 差 1px 在屏幕上看不出来。")
print("     这条断言只保证「没有变成完全相等」，别把它读成「层级保住了」。")

