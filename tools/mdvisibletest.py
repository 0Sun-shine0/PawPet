r"""以**真实用户文本**为输入，断言「用户可见文字里没有 Markdown/HTML 标签」。

## 为什么有这个文件（Codex 第 14 轮第 3 节）

Codex 从我的走查报告里读到一行：

```
裁切 ×1: ⚠ <p style='margin:0 0 6
```

据此判断「走查截图暴露了 AI 页可见的原始片段」，并要求：
定位是错误消息、进度消息还是 Markdown 转换链路，
**补一个以真实用户文本为输入的回归断言**，再修复显示层。

## 定位结论（`⚠ <p ...` 不是屏幕上显示的东西）

  · 那一行来自我的报告 `_count_clipped()`，它当时记录的 label 是
    **`text` 属性**，而富文本控件的 `text` 属性**是 HTML 源码**。
  · `AiBubble.qml:380` 写的是 `text: "⚠ " + bubble.richText`，
    所以属性就是 `"⚠ <p style='margin:0 0 6px 0;'>…"`。
  · **把那张截图放大 4 倍看：屏幕上是 `⚠ 还没有配置模型`，
    正常中文，没有标签。**（`.cache/htmlverify/err_1.00.png`）
  · 用 `QTextDocument`（和 `QQuickText` 同一套解析器）解析
    `"⚠ " + html` 得到 123 字符的完整正文，**没有标签残留**。

> **所以「屏幕上显示原始 HTML」这个结论站不住 —— 是我的报告措辞造成的。**
> 报告已经修正为记录**解析后的可见文本**。

## 那这个文件断言什么

即便前提错了，**断言本身仍然有价值** —— 它守的是真实存在的转换链路：
真实用户文本（多行错误消息、Windows 路径、粗体、代码、列表、引用、
省略号）经 `to_qt_html` / `to_plain` 之后，**用户能看到的字符里
不该有标签**。

## 并且明确记录两条**潜在**风险（当前不可达）

  · `to_plain(<html>)` —— 把 HTML 喂给纯文本转换 → 标签**会**透传
  · **双层转换** `to_qt_html(to_qt_html(x))` —— 同上

`controller.py` 的三条路径（`_push` / `_restore` / `restoreSession`）
都是 `to_qt_html(text)`，**text 是原文**，所以**当前不可达**。
本文件把它们**当作探测器的判别力对照**：证明这个检测**抓得住**
真正的泄漏，而不是一条永远通过的断言。

用法：
    .venv\Scripts\python.exe tools\mdvisibletest.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from console import configure_utf8  # noqa: E402

configure_utf8()

os.environ["QT_QPA_PLATFORM"] = "windows"

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


# ============================================================ 真实样本
#
# 这些是**用户真的会看到**的文本形态，不是编出来的玩具字符串。
SAMPLES: list[tuple[str, str]] = [
    ("多行错误消息（含省略号）",
     "还没有配置模型 API Key。\n"
     "在下面的「模型设置」里填一个，或者把 key 写进 .env 的 "
     "OPENAI_API_KEY。\n"
     "任何 OpenAI 兼容的服务都可以：OpenAI、DeepSeek、Moonshot、"
     "本地 Ollama…"),

    ("带 Windows 路径",
     r"读不到 C:\Users\PC\AppData\Roaming\PawPet\pet_data.json，"
     "已回退到默认值。"),

    ("Markdown 粗体 + 行内代码",
     "配置完成。用 `Ctrl+Shift+Space` 唤起，"
     "**注意**要先把 key 填进 .env。"),

    ("无序列表",
     "- 待办：本机保存\n- 专注：本机保存\n- 提醒：本机保存"),

    ("有序列表 + 链接",
     "1. 打开 [控制台](https://platform.deepseek.com/api_keys)\n"
     "2. 复制 key\n3. 粘到下面的输入框"),

    ("引用块",
     "> 不配也能用：待办、专注、便签、提醒都是本机的。\n"
     "> 配了之后才多出「看屏幕、替你操作」的能力。"),

    ("代码块",
     "这样填：\n\n```\nOPENAI_API_KEY=sk-xxxx\n```\n\n保存后重启。"),

    ("含比较符号（不是标签）",
     "如果 3 < 5 且 a > b，就说明是真的运算符，不该被当成 HTML。"),

    ("含尖括号词",
     "按 <Enter> 确认，按 <Esc> 取消。"),

    ("含 HTML 实体",
     "界面里写的是 &lt;p&gt; 这种转义形式。"),

    ("单行很短", "出错了。"),
]

# 用户可见文字里**不该出现**的标签痕迹
FORBIDDEN = ["<p", "</", "<br", "<span", "<div", "style=", "<blockquote",
             "<pre", "<ul", "<ol", "<li>", "<b>", "<i>"]


def main() -> int:
    from PySide6.QtGui import QGuiApplication, QTextDocument

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.processEvents()

    from pawpet.ai.markdown import to_plain, to_qt_html

    print("Markdown 转换后用户可见文本的回归\n")

    def visible(html: str) -> str:
        doc = QTextDocument()
        doc.setHtml(html)
        return doc.toPlainText()

    # ---------------- 0. 抓一条**真的** lastError ----------------
    print("=== 从运行时抓真实 `lastError` ===")
    try:
        import shutil

        from pawpet.backend import Backend
        from pawpet.store import Store

        tmp = ROOT / ".cache" / "mdvisible"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        store = Store(tmp / "pet_data.json", tmp / "pet_data.backup.json")
        store.load()
        backend = Backend(store)
        ai = backend.property("ai")
        real = str(ai.property("lastError") or "") if ai is not None else ""
        if real:
            SAMPLES.insert(0, ("**运行时实际的 `lastError`**", real))
            print(f"  取到 {len(real)} 字符：{real[:50]!r}…")
        else:
            print("  （当前没有 lastError）")
    except Exception as exc:  # noqa: BLE001
        print(f"  取不到（{type(exc).__name__}: {exc}）")

    # ---------------- 1. `to_qt_html` 之后**用户看到的文字** ----------------
    print("\n=== `to_qt_html` 之后用户看到的文字里有没有标签 ===")
    for label, text in SAMPLES:
        vis = visible(to_qt_html(text))
        leaked = [f for f in FORBIDDEN if f in vis]
        check(f"{label}：可见文字里没有标签", not leaked,
              f"**泄漏 {leaked}**；前 80：{vis[:80]!r}")

        # 内容不能丢：原文第一个有意义的片段应当还在
        probe = next((seg.strip() for seg in text.replace("\n", " ").split()
                      if len(seg.strip()) >= 2
                      and seg.strip() not in ("-", "1.", "2.", "3.", ">",
                                              "```")), "")
        if probe:
            check(f"{label}：可见文字仍含原文片段",
                  probe in vis, f"找不到 {probe!r}；前 80：{vis[:80]!r}")

    # ---------------- 2. 交叉：`to_plain` ----------------
    print("\n=== 交叉检查：`to_plain`（纯文本场景）===")
    for label, text in SAMPLES:
        plain = to_plain(text)
        leaked = [f for f in FORBIDDEN if f in plain]
        check(f"{label}：`to_plain` 结果没有标签", not leaked,
              f"**泄漏 {leaked}**；前 80：{plain[:80]!r}")

    # ---------------- 3. 判别力对照 ----------------
    print("\n=== 判别力对照（证明上面的断言不是恒真）===")

    html = to_qt_html("你好世界")
    check("`to_qt_html` 的输出**确实含标签**（那是它的职责）",
          "<" in html and ">" in html,
          f"输出 {html!r} —— 若不含标签，上面那些断言就没有判别力")
    check("而解析后的可见文字就是「你好世界」",
          visible(html).strip() == "你好世界",
          f"实际 {visible(html)!r}")

    # 对照 A：把 HTML 当纯文本直接显示（就是「漏设 textFormat」的形态）
    fake = html
    check("对照 A：把 HTML 当纯文本时会检出标签",
          bool([f for f in FORBIDDEN if f in fake]),
          "一个都没命中 → 泄漏检测坏了，上面的断言全是恒真")

    # 对照 B：`to_plain(html)` —— **真实存在的泄漏路径**
    leak_b = to_plain(html)
    check("对照 B：`to_plain(<html>)` 会泄漏标签（检测抓得住）",
          bool([f for f in FORBIDDEN if f in leak_b]),
          "没抓住 → 检测坏了")

    # 对照 C：**双层转换**
    leak_c = visible(to_qt_html(html))
    check("对照 C：双层转换会泄漏标签（检测抓得住）",
          bool([f for f in FORBIDDEN if f in leak_c]),
          "没抓住 → 检测坏了")

    # ---------------- 4. 潜在风险是否可达 ----------------
    print("\n=== 潜在风险的可达性（查 `controller.py` 的三条路径）===")
    ctrl = (ROOT / "pawpet" / "ai" / "controller.py").read_text(
        encoding="utf-8")
    # 三条路径都应当是 `to_qt_html(text)`（text 是原文），不是 `to_qt_html(html)`
    bad = []
    for n, line in enumerate(ctrl.split("\n"), 1):
        stripped = line.strip()
        if "to_qt_html(" in stripped and "import" not in stripped:
            if "to_qt_html(html" in stripped.replace(" ", "") \
                    or "to_qt_html(item[\"html\"]" in stripped:
                bad.append(f"{n}: {stripped}")
    check("`controller.py` 里没有 `to_qt_html(<已有的 html>)`（双层转换）",
          not bad,
          f"发现 {bad} —— 那会让屏幕上出现标签")
    check("`controller.py` 里没有 `to_plain(<已有的 html>)`",
          not any("to_plain(html" in ln.replace(" ", "")
                  for ln in ctrl.split("\n")),
          "发现 `to_plain(html)` —— 那会让纯文本场景出现标签")

    print()
    print("  说明：对照 B / C 抓得住泄漏，但 `controller.py` 的三条路径")
    print("  （`_push` / 恢复 / `restoreSession`）用的都是 `to_qt_html(text)`，")
    print(f"  **text 是原文** —— 所以那两条风险**当前不可达**，只作为")
    print("  「检测有判别力」的证据记录。")

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
