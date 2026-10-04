r"""批次专用的**显式清单守卫**：10 对「值不同」的边距必须两侧同步。

## 为什么有这个文件（Codex 第 12 轮第 1 节）

它明确说：

> 我不要求现在改变 `spacingtest.py` 的全局配对口径，也不要求实现一个
> 依赖启发式方向词匹配的通用守卫；那会扩大测试口径并重新引入错配风险。
> 但批次 3 的 10 对值不同边距不能只靠人工看提交信息。

**这不是通用配对建模，而是 10 对已审计对象的窄范围回归守卫。**

## 它和 `pairsafe.py` 的分工

| 工具 | 管什么 |
|---|---|
| `pairsafe.py` | **值**在四个 `density` 档位下会不会**塌陷**（纯算术，不看源码） |
| `pairguard.py`（本文件） | **源码**里这两侧的写法是否**同步**（读真实 QML） |

两件事，两个工具。**不能互相替代。**

## 清单是**显式**的，不做启发式配对

那 10 对是**人工审计出来**并写死在这里的（文件 + 行号 + 两侧属性 + 原始值）。
它不靠「方向词替换」去找配对 —— 那条路已经错过一次
（`anchors.rightMargin` 的 `right` 被换成 `left`，错配到别的属性上，
导致 2 处单侧漏统计）。

## 阶段常量

批 3b **还没做**时，这 10 对两侧都是**裸数字**；批 3b 之后两侧都该是
`Theme.space(原始值)`。`PHASE` 记录当前处于哪个阶段：

  · `"3b-pending"` —— 允许两侧都是裸数字（但要**一致**）
  · `"done"`       —— 要求两侧都必须是 `Theme.space(原始值)`

**无论哪个阶段，「只迁一侧」都会失败** —— 那正是这个守卫存在的理由。

⚠️ 批 3b 完成后要**手动把这个常量改成 `"done"`**。
故意不做成自动的：那一步本身是一次确认。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 根目录可被环境变量覆盖 —— **为了能在「临时副本」上跑**。
#
# Codex 第 12 轮要求「用临时副本注入『只迁一侧』的故障，确认守卫实际变红，
# 再还原」。在真文件上注入再还原也能验，但**副本更干净**：
# 万一脚本在中途挂了，真源码不会被留在半改状态。
#
# 副本必须保持相对路径结构（`pawpet/qml/...`），守卫按 `ROOT / rel` 读。
ROOT = Path(os.environ.get("PAIRGUARD_ROOT")
            or Path(__file__).resolve().parent.parent)

# ============================================================ 阶段
# 批 3b 完成后改成 "done"。
PHASE = "3b-pending"

# ============================================================ 显式清单
# (文件, 行号, 左侧属性, 左侧原值, 右侧属性, 右侧原值)
#
# 这 10 对来自 E7 的成对审计（`margin` 里**值不同**的那些）。
# 行号是**清单写下时**的位置，用于精确定位；行号漂移会被单独报出来。
PAIRS: list[tuple[str, int, str, int, str, int]] = [
    ("pawpet/qml/PawPet/AiHistoryPanel.qml", 90,
     "anchors.leftMargin", 10, "anchors.rightMargin", 8),
    ("pawpet/qml/PawPet/CollapsibleSection.qml", 36,
     "anchors.leftMargin", 12, "anchors.rightMargin", 8),
    ("pawpet/qml/PawPet/Dashboard.qml", 151,
     "anchors.leftMargin", 16, "anchors.rightMargin", 8),
    ("pawpet/qml/PawPet/Dashboard.qml", 309,
     "anchors.leftMargin", 14, "anchors.rightMargin", 12),
    ("pawpet/qml/PawPet/page/AiPage.qml", 749,
     "anchors.leftMargin", 12, "anchors.rightMargin", 10),
    ("pawpet/qml/PawPet/page/NotesPage.qml", 268,
     "anchors.leftMargin", 12, "anchors.rightMargin", 8),
    ("pawpet/qml/PawPet/page/RemindersPage.qml", 937,
     "anchors.leftMargin", 12, "anchors.rightMargin", 8),
    ("pawpet/qml/PawPet/page/TasksPage.qml", 149,
     "anchors.leftMargin", 10, "anchors.rightMargin", 38),
    ("pawpet/qml/PawPet/page/TasksPage.qml", 266,
     "anchors.leftMargin", 10, "anchors.rightMargin", 8),
    ("pawpet/qml/PawPet/page/TasksPage.qml", 674,
     "anchors.leftMargin", 12, "anchors.rightMargin", 8),
]

# 允许相邻的行距（左右两侧可能隔一两行）
MAX_GAP = 6

# ============================================================ Popup 成组（批 3a）
#
# Codex 第 12 轮第 2 节：
#
# > Popup 的 `padding` 和 `implicitHeight` 必须一起验证，**不能只让扫描器的
# > `padding literal` 归零而留下未接密度轴的 `+ 8`**。
#
# 为什么**必须**单独查：扫描器只看得见 `padding:` 属性，
# `implicitHeight: contentItem.implicitHeight + Theme.space(8)` 里那个
# `+ 8` 是**表达式内的数字**，扫描器**看不到**。
# 所以「`padding literal = 0`」**不能证明** Popup 接上了密度轴。
#
# `(文件, 行号, 属性, 表达式片段, 期望的 Theme.space 值)`
POPUP_GROUPS: list[tuple[str, int, str, str, int, int]] = [
    # (文件, padding 行, 属性, 表达式行, padding 原值, +N 原值)
    ("pawpet/qml/PawPet/page/FocusPage.qml", 236,
     "padding", 235, 4, 8),
    ("pawpet/qml/PawPet/page/RemindersPage.qml", 198,
     "padding", 197, 4, 8),
]

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


def read_lines(rel: str) -> list[str] | None:
    p = ROOT / rel
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace").split("\n")


def parse_side(line: str, prop: str) -> tuple[str, str] | None:
    """从一行里解析出 `(形态, 数字)`。

    形态：`raw`（裸数字）或 `spaced`（`Theme.space(N)`）。
    解析不出、或属性名不符，返回 None。
    """
    # `属性: 值`
    m = re.match(rf"^\s*{re.escape(prop)}\s*:\s*(.+?)\s*$", line)
    if not m:
        return None
    value = m.group(1).strip()
    ms = re.fullmatch(r"Theme\.space\(\s*(-?\d+(?:\.\d+)?)\s*\)", value)
    if ms:
        return "spaced", ms.group(1)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return "raw", value
    return None


def find_prop_line(lines: list[str], prop: str) -> int | None:
    """全文件找这个属性的**唯一**一行（用于诊断行号漂移）。"""
    hits = [i for i, ln in enumerate(lines, 1)
            if re.match(rf"^\s*{re.escape(prop)}\s*:", ln)]
    return hits[0] if len(hits) == 1 else None


def main() -> int:
    print(f"成对边距同步守卫（显式清单 {len(PAIRS)} 对，阶段 {PHASE}）\n")

    for rel, line, lprop, lval, rprop, rval in PAIRS:
        lines = read_lines(rel)
        if lines is None:
            check(f"{rel} 存在", False, "文件不存在")
            continue

        tag = f"{Path(rel).name}:{line}"

        # ---- 定位左侧 ----
        left_line = lines[line - 1] if line <= len(lines) else ""
        left = parse_side(left_line, lprop)
        if left is None:
            # 诊断：是行号漂移还是属性真的没了？
            moved = find_prop_line(lines, lprop)
            if moved and moved != line:
                check(f"{tag} 左侧 `{lprop}` 位置", False,
                      f"行号漂移：清单说 {line}，实际在 {moved}"
                      f"  → **清单该更新**")
            elif len(lines) < line:
                check(f"{tag} 左侧 `{lprop}` 位置", False,
                      f"行号 {line} 越界（文件只有 {len(lines)} 行）")
            else:
                check(f"{tag} 左侧 `{lprop}` 位置", False,
                      f"第 {line} 行不是 `{lprop}: ...`：{left_line.strip()!r}")
            continue

        # ---- 定位右侧（允许隔 MAX_GAP 行）----
        right = None
        right_at = None
        for off in range(1, MAX_GAP + 1):
            idx = line - 1 + off
            if idx >= len(lines):
                break
            got = parse_side(lines[idx], rprop)
            if got is not None:
                right = got
                right_at = idx + 1
                break
        if right is None:
            check(f"{tag} 右侧 `{rprop}` 存在（±{MAX_GAP} 行内）", False,
                  "找不到")
            continue

        # ---- 1. 值必须等于清单里的原始值 ----
        check(f"{tag} 左侧值 = {lval}（{left[0]}）",
              left[1] == str(lval), f"实际 {left[1]}")
        check(f"{tag} 右侧值 = {rval}（{right[0]}）",
              right[1] == str(rval), f"实际 {right[1]}")

        # ---- 2. **两侧形态必须一致** —— 「只迁一侧」在这里被抓住 ----
        check(f"{tag} 两侧形态一致（左 {left[0]} / 右 {right[0]}）",
              left[0] == right[0],
              f"**只迁了一侧**：左侧 {left[0]}、右侧 {right[0]}"
              f"（{lprop} / {rprop}）")

        # ---- 3. 阶段要求 ----
        if PHASE == "done":
            check(f"{tag} 两侧都已接入 Theme.space（阶段 done）",
                  left[0] == "spaced" and right[0] == "spaced",
                  f"还有裸数字：左 {left[0]} / 右 {right[0]}")

    # ================================================ Popup 成组（批 3a）
    print()
    print("=" * 74)
    print("Popup 的 `padding` 与 `implicitHeight + N` 必须**成组**")
    print("=" * 74)
    print("  为什么单独查：扫描器只看得见 `padding:` 属性，")
    print("  `implicitHeight: contentItem.implicitHeight + Theme.space(8)`")
    print("  里那个 `+ 8` 是**表达式内的数字**，扫描器**看不到**。")
    print("  所以「`padding literal = 0`」不能证明 Popup 接上了密度轴。")
    print()
    for rel, pad_line, prop, expr_line, pad_val, plus_val in POPUP_GROUPS:
        lines = read_lines(rel)
        name = Path(rel).name
        if lines is None:
            check(f"{name} 存在", False, "文件不存在")
            continue

        # ——— `padding: Theme.space(原值)` ———
        pad_got = parse_side(lines[pad_line - 1]
                             if pad_line <= len(lines) else "", prop)
        check(f"{name}:{pad_line} `{prop}: Theme.space({pad_val})`",
              pad_got is not None and pad_got[0] == "spaced"
              and pad_got[1] == str(pad_val),
              f"实际 {pad_got}")

        # ——— `implicitHeight: ... + Theme.space(N)` ———
        #
        # 这里不能用 `parse_side`（它是给 `属性: 值` 用的），
        # 要匹配「表达式 + Theme.space(N)」形态。
        expr_line_text = lines[expr_line - 1] if expr_line <= len(lines) else ""
        m = re.search(
            r"implicitHeight\s*:\s*.+?\+\s*Theme\.space\(\s*(\d+)\s*\)",
            expr_line_text)
        check(f"{name}:{expr_line} `implicitHeight: ... + Theme.space("
              f"{plus_val})`",
              m is not None and m.group(1) == str(plus_val),
              f"实际 {expr_line_text.strip()!r}")

        # ——— **两行必须相邻**（成组）———
        check(f"{name} 这两行相邻（{pad_line} / {expr_line}）",
              abs(pad_line - expr_line) <= 2,
              f"相距 {abs(pad_line - expr_line)} 行 —— 可能不是同一处")

    print()
    print("=" * 74)
    print("清单完整性")
    print("=" * 74)
    check(f"成对清单恰好 10 对", len(PAIRS) == 10,
          f"实际 {len(PAIRS)} 对 —— 批 3 的范围是 10 对")
    # 同一文件里不该有两个相同行号
    seen = {(rel, line) for rel, line, *_ in PAIRS}
    check("清单里没有重复项", len(seen) == len(PAIRS))

    print()
    print("=" * 74)
    print("为什么这个守卫**不是**通用配对守卫（Codex 第 12 轮的边界）")
    print("=" * 74)
    print("  · 清单是**人工审计**出来的 10 对，写死在 `PAIRS` 里")
    print("  · 不靠「方向词替换」找配对 —— 那条路错过一次")
    print("    （`anchors.rightMargin` 的 `right` 换成 `left`，")
    print("     错配到别的属性上，导致 2 处单侧漏统计）")
    print("  · 它**只**覆盖这 10 对，不声称能发现清单外的「只迁一侧」")
    print()
    print("  ⚠ `spacingtest.py` 的 `PAIR_GUARD_IMPLEMENTED = False` 仍然成立 ——")
    print("    扫描器**全局**没有配对守卫，本文件只是**批次专用**的窄范围守卫。")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
