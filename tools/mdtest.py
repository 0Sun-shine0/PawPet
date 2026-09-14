"""Markdown 转换器自测。

重点验证两件事：
1. 常见的模型输出能被正确转换，不残留 ** # ` 这类记号。
2. 渲染出来的确实是 Qt 能认的富文本（用 QTextDocument 真解析一遍）。

用法：
    .venv\\Scripts\\python.exe tools\\mdtest.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


# 真实的模型输出样本：就是用户截图里那种
SAMPLE = """现在你的屏幕上有这些内容：

**1. 背景是一个 WPS Office 文字文档（占据大部分屏幕）**

- 打开了好几个标签页：`AI模板`、`售后服务运营规范2026.pdf`、`新员工入职指引及培训计划`
- 正文是一份《新员工入职指引及培训计划》，能看到「5.3 第三阶段：软件安装」「5.6 第六阶段：交接培训」
- 底部状态栏：第 11/13 页，字数 4864，缩放 100%

**2. 屏幕中间浮动着一个「小爪工作台」窗口**

- 左侧菜单：今日 / 专注 / 待办 / 便签 / 提醒 / AI 操作 / 设置

> 需要我帮你做什么吗？

参考链接：[OpenAI 文档](https://platform.openai.com/docs)

```python
def hello():
    return "world"
```

| 项目 | 状态 |
|------|------|
| 文档 | 已打开 |
| 窗口 | 悬浮中 |

~~这段是废弃的~~

---

结束。
"""


def main() -> int:
    print("Markdown 转换器自测")

    from pawpet.ai.markdown import to_plain, to_qt_html

    # ------------------------------------------------------------ 基本转换
    print("\n=== 行内标记 ===")
    cases = [
        ("**粗体**", "<b>", "加粗"),
        ("*斜体*", "<i>", "斜体"),
        ("__也粗__", "<b>", "下划线加粗"),
        ("`code`", "<code", "行内代码"),
        ("~~删掉~~", "<s>", "删除线"),
        ("[文字](https://a.com)", "<a href", "链接"),
        ("普通文字", "普通文字", "纯文本原样"),
    ]
    for source, expect, label in cases:
        result = to_qt_html(source)
        check(f"{label}", expect in result, f"{source!r} -> {result!r}")

    print("\n=== 块级元素 ===")
    block_cases = [
        ("# 大标题", "<span", "一级标题"),
        ("## 二级", "<span", "二级标题"),
        ("- 项目一\n- 项目二", "<ul", "无序列表"),
        ("1. 第一\n2. 第二", "<ol", "有序列表"),
        ("> 引用内容", "<blockquote", "引用"),
        ("---", "<hr", "分隔线"),
        ("```\ncode\n```", "<pre", "代码块"),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "<table", "表格"),
    ]
    for source, expect, label in block_cases:
        result = to_qt_html(source)
        check(label, expect in result, f"{expect} 不在 {result[:90]!r}")

    # ------------------------------------------------------- 不残留记号
    print("\n=== 真实样本：转换后不应残留 Markdown 记号 ===")
    converted = to_qt_html(SAMPLE)

    leftovers = {
        "**": "加粗记号",
        "##": "标题记号",
        "~~": "删除线记号",
        "|---": "表格分隔行",
    }
    for token, label in leftovers.items():
        check(f"没有残留 {label} {token!r}", token not in converted,
              f"位置 {converted.find(token)}")

    # 列表的 "- " 应该变成 <li>
    check("列表符号已转成 li", "<li>" in converted)
    check("代码块已转成 pre", "<pre" in converted)
    check("引用已转成 blockquote", "<blockquote" in converted)
    check("链接已转成 a", '<a href="https://platform.openai.com/docs"' in converted)
    check("分隔线已转成 hr", "<hr" in converted)
    check("表格已转成 table", "<table" in converted)

    # ------------------------------------------------------- HTML 转义
    print("\n=== 转义（防止命令输出破坏渲染）===")
    check("尖括号被转义", "&lt;script&gt;" in to_qt_html("<script>alert(1)</script>"))
    check("& 被转义", "&amp;" in to_qt_html("a & b"))
    check("代码里的 HTML 也被转义",
          "&lt;div&gt;" in to_qt_html("`<div>`"),
          to_qt_html("`<div>`"))

    # --------------------------------------------- Qt 真的能解析这些 HTML
    print("\n=== 用 QTextDocument 验证 Qt 能解析 ===")
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    doc = QTextDocument()
    doc.setHtml(converted)
    plain = doc.toPlainText()

    check("Qt 能解析转换结果（不报错）", len(plain) > 100, f"纯文本长度 {len(plain)}")
    check("解析后保留了正文", "WPS Office" in plain)
    check("解析后保留了列表项文字", "售后服务运营规范" in plain)
    check("解析后**不**包含星号", "**" not in plain, str(plain[:120]))
    check("解析后不包含井号标题", "##" not in plain)
    check("解析后链接文字还在", "OpenAI 文档" in plain)
    check("行数合理（说明块级结构被识别）", plain.count("\n") >= 8,
          f"实际 {plain.count(chr(10))} 行")

    # 富文本确实产生了格式，而不是纯文本套壳
    fmt_ranges = 0
    block = doc.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and (
                fragment.charFormat().fontWeight() > 50
                or fragment.charFormat().fontItalic()
            ):
                fmt_ranges += 1
            iterator += 1
        block = block.next()
    check("存在粗体或斜体格式（说明不是纯文本套壳）", fmt_ranges > 0,
          f"格式片段 {fmt_ranges} 个")

    print("\n=== 纯文本版本 ===")
    plain_version = to_plain(SAMPLE)
    for token in ("**", "##", "~~", "|---"):
        check(f"纯文本无 {token!r}", token not in plain_version)
    check("纯文本保留正文", "WPS Office" in plain_version)
    check("纯文本把列表变成 ·", "·" in plain_version or "-" not in plain_version)

    print("\n=== 边界情况 ===")
    check("空字符串", to_qt_html("") == "")
    check("None 安全", to_qt_html(None) == "")
    check("纯空白", to_qt_html("   ").strip() in ("", "<p style='margin:0 0 6px 0;'></p>"))
    check("没有换行的长文本", "<p" in to_qt_html("一句话"))
    check("未闭合的加粗不会崩", isinstance(to_qt_html("**没关"), str))
    check("未闭合的代码块不会崩", isinstance(to_qt_html("```\ncode"), str))
    check("只有列表符号", isinstance(to_qt_html("- \n- "), str))
    check("数学式不被误判成斜体", "<i>" not in to_qt_html("3 * 4 * 5"),
          to_qt_html("3 * 4 * 5"))
    check("下划线变量名不被误判", "<i>" not in to_qt_html("my_var_name"),
          to_qt_html("my_var_name"))

    del app

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
