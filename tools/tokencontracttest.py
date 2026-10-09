r"""语义 token 的**窄范围合同测试**（第一批：`densityLabelGap`）。

## 为什么需要它（Codex 第 16 轮第 4 节）

第一批把 `AiSettingsPanel.qml` 的 3 处 `Theme.space(8)` 换成了
`Theme.densityLabelGap`。**值一个都没变**，所以：

  · 逐像素比较**看不出来**接错线（换成另一个 token、甚至换成裸 `8`，
    在 `density=1` 下画面可能一模一样）
  · 扫描器也**看不出来**（`space(N)` 和命名 token 都是「接上了密度轴」）

**能出错的地方恰好落在两个工具的盲区里**：

  1. **轴接错了** —— `densityLabelGap` 若定义成 `scaledSpace(8)`，
     `density=1` 时值仍是 8，但 `uiScale=1.6` 时会变成 13
  2. **接线断了** —— 某个调用点写回 `Theme.space(8)` 或裸 `8`，
     画面照样对，但那处**不再跟 token 走**（以后调 token 它不动）

本文件就守这两件事。另外它顺带守住**定义本身**（值真的是 `space(8)`）。

## 它**不能**证明什么（Codex 要求写明，我完全同意）

**它证明不了「标签 ↔ 输入框」这个语义判断是对的。**

那是个**设计判断**，不是算术事实 —— 本文件再怎么加断言也测不出来。
语义由 Codex 第 16 轮逐处人工复核（三处形态逐字一致：`RowLayout` +
`Layout.preferredWidth: 72` 的标签 + 输入控件）。

> 换句话说：**这个测试通过 ≠ 这批评审通过。**
> 它只保证「轴对、值对、线接着」，不保证「角色分得对」。
> 这条边界写在输出里，免得以后有人把绿当成「语义也验过了」。

## 七节

  1. **定义**：`Theme.qml` 里真的是 `space(8)`（不是 `scaledSpace`、不是裸 8）
  2. **运行时值 × density**：`1.00/0.90/0.80/0.70` → `8 / 7 / 6 / 6`
  3. **不随 uiScale 变**：`uiScale = 0.80 / 1.60` 时值恒为 8
  4. **接线**：三个**真实调用点**用的是 `Theme.densityLabelGap`
     （读 QML 源码 + 按标签文本锚定，**不比较测试脚本自己的常量**）
  5. **判别力（注入）**：临时副本里把调用点改回 `space(8)` / 裸 `8` /
     改成另一个 token → 第 4 节必须报红
  6. **该文件里其余 `space(8)` 没被顺手改掉**（盘点证明 `8` 跨 12 个语义，
     「全文件替换」是错的做法）
  7. **扫描器口径**：命名 token 计入 `token` 而不是 `seam`，
     且**总数不变**（这是接线的第二种证据）

用法：
    .venv\Scripts\python.exe tools\tokencontracttest.py
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8  # noqa: E402

configure_utf8()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SCRATCH = ROOT / ".cache" / "tokencontract"
os.environ["PAWPET_HOME"] = str(SCRATCH)

TOKEN = "densityLabelGap"
SOURCE_VALUE = 8
CALL_SITE_FILE = "pawpet/qml/PawPet/AiSettingsPanel.qml"

# 三处的**语义锚**：标签文本。用文本锚定而不是行号 —— 行号会漂。
ANCHORS = ["接口地址", "选择模型", "执行步数"]

# Codex 第 16 轮点名的两个 uiScale
UI_SCALES = [0.80, 1.60]
DENSITIES = [1.00, 0.90, 0.80, 0.70]
# `space(8)` 在各档的期望值（JS 的 Math.round，.5 向 +∞）
EXPECTED_BY_DENSITY = [8, 7, 6, 6]

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


def js_round(x: float) -> int:
    """JS 的 `Math.round`（`.5` 向 +∞）—— Python 的 `round` 是银行家舍入。"""
    return math.floor(x + 0.5)


# ============================================================ 调用点检查
#
# **抽成函数**，因为它要被跑两次：一次对真源码（应当通过），
# 一次对**注入过的临时副本**（应当报红）—— 后者证明它有判别力。

SPACING_LINE = re.compile(r"^\s*spacing\s*:\s*(.+?)\s*$")


def caller_check(root: Path, rel: str,
                 anchors: list[str]) -> list[str]:
    """按标签文本锚定，检查每个锚上方最近的 `spacing:` 用的是哪个 token。

    返回**问题列表**（空 = 通过）。这样它既能当断言，也能被注入验证复用。

    为什么按文本锚定：行号会漂（这个项目里漂过好几次）。
    标签文本是**语义锚**，比行号稳。
    """
    problems: list[str] = []
    path = root / rel
    if not path.exists():
        return [f"{rel} 不存在"]

    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")

    for anchor in anchors:
        # 找这个标签
        idxs = [i for i, l in enumerate(lines) if f'text: "{anchor}"' in l]
        if len(idxs) != 1:
            problems.append(f"锚 `{anchor}` 出现 {len(idxs)} 次（应为 1 次）")
            continue
        at = idxs[0]

        # 往上找最近的 `spacing:` —— 它应当在同一个 RowLayout 里
        found = None
        for i in range(at, max(-1, at - 14), -1):
            m = SPACING_LINE.match(lines[i])
            if m:
                found = (i + 1, m.group(1))
                break
        if found is None:
            problems.append(f"锚 `{anchor}` 上方 14 行内找不到 `spacing:`")
            continue

        lineno, value = found
        want = f"Theme.{TOKEN}"
        if value != want:
            problems.append(
                f"锚 `{anchor}`（:{lineno}）的 spacing 是 `{value}`，"
                f"应当是 `{want}`")
    return problems


def count_plain_space8(root: Path, rel: str) -> int:
    """该文件里还剩几处 `spacing: Theme.space(8)`（未被 token 化的）。"""
    path = root / rel
    if not path.exists():
        return -1
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(1 for l in text.split("\n")
               if l.strip() == f"spacing: Theme.space({SOURCE_VALUE})")


def main() -> int:
    print("语义 token 合同测试（第一批：densityLabelGap）\n")

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])

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
        """走 QML 赋值（和产品同一条路径）。

        **每次赋值，包括 1.0** —— spacetest 踩过这个坑：只在 `≠1` 时设，
        会让上一节的 density 泄漏到下一节。
        """
        setter = QQmlComponent(engine)
        setter.setData(f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{
                Component.onCompleted: Theme.density = {value}
            }}
        """.encode("utf-8"),
            QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "tct_set.qml")))
        setter.create(engine.rootContext())
        app.processEvents()

    def read_token(expr: str):
        """建一个 probe 读表达式的值。每个配置**新建 probe**，不靠改属性触发重算。"""
        comp = QQmlComponent(engine)
        comp.setData(f"""
            import QtQuick
            import PawPet 1.0
            QtObject {{ property real v: {expr} }}
        """.encode("utf-8"),
            QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "tct_probe.qml")))
        obj = comp.create(engine.rootContext())
        if obj is None:
            errs = [e.toString() for e in comp.errors()]
            return None, errs
        return float(obj.property("v")), []

    try:
        # ============================================ 一、定义
        print("=== 一、`Theme.qml` 里的定义 ===")
        theme_src = (ROOT / "pawpet/qml/PawPet/Theme.qml").read_text(
            encoding="utf-8")

        # 界定「定义行」：`readonly property int densityLabelGap: <表达式>`
        def_re = re.compile(
            rf"readonly\s+property\s+int\s+{TOKEN}\s*:\s*(.+?)\s*$",
            re.MULTILINE)
        hits = def_re.findall(theme_src)
        check(f"`{TOKEN}` 有且只有一处定义", len(hits) == 1,
              f"找到 {len(hits)} 处")
        expr = hits[0] if hits else ""
        check(f"定义是 `space({SOURCE_VALUE})`（**密度轴**）",
              expr == f"space({SOURCE_VALUE})",
              f"实际 `{expr}` —— 若是 `scaledSpace` 就接错轴了")
        check("定义**不是** `scaledSpace` 或裸数字",
              "scaledSpace" not in expr and not expr.strip().isdigit(),
              f"实际 `{expr}`")

        # ============================================ 二、运行时值 × density
        print("\n=== 二、运行时值随 density 变 ===")
        set_scale(1.0)
        seen = []
        for d in DENSITIES:
            set_density(d)
            v, errs = read_token(f"Theme.{TOKEN}")
            if v is None:
                check(f"density={d:.2f} 能读到值", False, str(errs[:1]))
                continue
            seen.append(int(v))
        check(f"`{TOKEN}` 在 {DENSITIES} 下 = {EXPECTED_BY_DENSITY}",
              seen == EXPECTED_BY_DENSITY,
              f"实际 {seen}")
        check("四档**确实在变小**（不是恒定 —— 恒定说明没接密度轴）",
              len(set(seen)) > 1, f"实际 {seen}")

        # 逐档的算术也对一遍（不只对序列）
        for d, want in zip(DENSITIES, EXPECTED_BY_DENSITY):
            check(f"density={d:.2f}: space({SOURCE_VALUE}) = {want}",
                  js_round(SOURCE_VALUE * d) == want,
                  f"算式给了 {js_round(SOURCE_VALUE * d)}")

        # ============================================ 三、不随 uiScale 变
        print(f"\n=== 三、不随 uiScale 变（{UI_SCALES}）===")
        set_density(1.0)
        by_scale = []
        for s in UI_SCALES:
            set_scale(s)
            v, errs = read_token(f"Theme.{TOKEN}")
            by_scale.append(None if v is None else int(v))
        check(f"uiScale={UI_SCALES} 时 `{TOKEN}` 恒为 {SOURCE_VALUE}",
              by_scale == [SOURCE_VALUE] * len(UI_SCALES),
              f"实际 {by_scale} —— 变了说明接的是 `scaledSpace`")

        # 对照：同期 `gap`（缩放轴）**应当**跟着变 —— 证明上面那条不是恒真
        gap_by_scale = []
        for s in UI_SCALES:
            set_scale(s)
            v, _ = read_token("Theme.gap")
            gap_by_scale.append(None if v is None else int(v))
        check(f"对照：同一组 uiScale 下 `gap`（缩放轴）**确实在变**"
              f"（{gap_by_scale}）",
              len(set(gap_by_scale)) > 1,
              f"实际 {gap_by_scale} —— 若也不变，说明上一条断言没有判别力")
        set_scale(1.0)

        # ============================================ 四、接线（真实调用点）
        print("\n=== 四、三个**真实调用点**接了 token ===")
        problems = caller_check(ROOT, CALL_SITE_FILE, ANCHORS)
        check(f"{CALL_SITE_FILE} 的 3 处标签↔输入框都用了 "
              f"`Theme.{TOKEN}`", not problems,
              "；".join(problems))
        # 把每一处单独报出来（好看清是哪一处），也便于注入时报红定位
        for anchor in ANCHORS:
            one = caller_check(ROOT, CALL_SITE_FILE, [anchor])
            check(f"  · 锚 `{anchor}` 接的是 `Theme.{TOKEN}`", not one,
                  "；".join(one))

        # ============================================ 五、判别力（注入）
        print("\n=== 五、判别力：注入故障后第四节必须报红 ===")
        tmp_root = ROOT / ".cache" / "tokencontract-inject"
        rel = CALL_SITE_FILE
        src_lines = (ROOT / rel).read_text(
            encoding="utf-8").split("\n")

        def inject_and_expect_fail(label: str, new_value: str) -> None:
            """在临时副本里把**锚 1** 的 spacing 换成 new_value，跑检查。"""
            shutil.rmtree(tmp_root, ignore_errors=True)
            (tmp_root / Path(rel).parent).mkdir(parents=True, exist_ok=True)
            lines = list(src_lines)
            # 找锚 1 上方最近的 spacing 行
            at = next(i for i, l in enumerate(lines)
                      if f'text: "{ANCHORS[0]}"' in l)
            for i in range(at, max(-1, at - 14), -1):
                if SPACING_LINE.match(lines[i]):
                    indent = len(lines[i]) - len(lines[i].lstrip())
                    lines[i] = " " * indent + f"spacing: {new_value}"
                    break
            else:
                check(f"注入 `{new_value}`：找得到注入点", False, "找不到")
                return
            (tmp_root / rel).write_bytes("\n".join(lines).encode("utf-8"))

            got = caller_check(tmp_root, rel, [ANCHORS[0]])
            check(f"注入 `{new_value}` → 报红", bool(got),
                  "**没报红 —— 第四节的断言没有判别力**")
            if got:
                print(f"        报的是：{got[0][:88]}")

        inject_and_expect_fail("恢复成 Theme.space(8)", "Theme.space(8)")
        inject_and_expect_fail("恢复成裸 8", "8")
        inject_and_expect_fail("换成另一个 token",
                              "Theme.densityIconGap")
        # **对照**：原样复制，应当**通过** —— 证明上面的报红来自注入本身
        shutil.rmtree(tmp_root, ignore_errors=True)
        (tmp_root / Path(rel).parent).mkdir(parents=True, exist_ok=True)
        (tmp_root / rel).write_bytes(
            "\n".join(src_lines).encode("utf-8"))
        ok = caller_check(tmp_root, rel, [ANCHORS[0]])
        check("对照：原样副本**不报红**（证明报红来自注入，不是副本路径）",
              not ok, "；".join(ok))
        shutil.rmtree(tmp_root, ignore_errors=True)

        # ============================================ 六、没有顺手改别的
        print("\n=== 六、该文件里其余的 `space(8)` 没被顺手改掉 ===")
        left = count_plain_space8(ROOT, CALL_SITE_FILE)
        # 改之前 11 处，改了 3 处 → 应当剩 8 处
        check(f"`spacing: Theme.space({SOURCE_VALUE})` 还剩 8 处"
              f"（原 11 − 本批 3）", left == 8,
              f"实际 {left} 处 —— 数量不对说明「全文件替换」误伤了别的角色")
        print(f"        （这条守着盘点的结论：`spacing = 8` 跨 12 个语义，"
              f"整文件替换是错的）")

        token_uses = sum(
            1 for l in (ROOT / CALL_SITE_FILE).read_text(
                encoding="utf-8").split("\n")
            if l.strip() == f"spacing: Theme.{TOKEN}")
        check(f"该文件里 `Theme.{TOKEN}` 恰好 3 处", token_uses == 3,
              f"实际 {token_uses}")

        # ============================================ 七、扫描器口径
        print("\n=== 七、扫描器把它算成 token（不是 seam）===")
        from spacingtest import scan
        counts, details = scan()
        # `counts` 只按 (类别, 类型) 计数，**不含 token 名** ——
        # 所以要看明细里的 value 才知道是哪个 token。
        token_rows = [(rel, num) for rel, num, _cat, kind, value in details
                      if kind == "token" and TOKEN in value]
        check(f"扫描器把 `{TOKEN}` 归成 **token**（不是 seam）",
              len(token_rows) >= 3,
              f"只找到 {len(token_rows)} 处 —— 若归成 seam，棘轮会假性归零")
        print(f"        命中 {len(token_rows)} 处："
              f"{[f'{r.split(chr(47))[-1]}:{n}' for r, n in token_rows]}")
        # **对照**：它确实**不是** seam
        seam_rows = [(rel, num) for rel, num, _cat, kind, value in details
                     if kind == "seam" and TOKEN in value]
        check("它**不**出现在 seam 里（对照，防止口径含糊）",
              not seam_rows, f"有 {len(seam_rows)} 处被算成 seam")
        # seam 总数：3 处从 seam 升级成 token
        sp_seam = counts.get("spacing.seam.density", 0)
        print(f"        spacing 密度轴 seam 现在是 {sp_seam}"
              f"（批 1 之前是 58）")
        check("spacing 的密度轴 seam 少了 3 处（3 处升级成 token）",
              sp_seam == 55, f"实际 {sp_seam}")

    finally:
        # `QQmlEngine` 没有 `rootObjects()`（那是 `QQmlApplicationEngine` 的）。
        # 直接放掉 engine 就行。
        del engine
        shutil.rmtree(SCRATCH, ignore_errors=True)

    # ============================================ 边界声明
    print()
    print("=" * 86)
    print("⚠ 这个测试**证明不了**什么（Codex 第 16 轮要求写明）")
    print("=" * 86)
    print("  它证明：**轴对**（密度轴，不是缩放轴）、**值对**（8/7/6/6）、")
    print("          **线接着**（三个真实调用点用的就是这个 token）。")
    print()
    print("  它**证明不了**：「标签 ↔ 输入框」这个**语义判断本身**是对的。")
    print("  那是设计判断，不是算术事实 —— 再怎么加断言也测不出来。")
    print("  三处的语义由 Codex 第 16 轮逐处人工复核。")
    print()
    print("  > **这个测试通过 ≠ 这批评审通过。**")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
