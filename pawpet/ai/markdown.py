"""把模型输出的 Markdown 转成 Qt 富文本能正确显示的 HTML。

为什么需要这个：
    模型几乎一定会输出 Markdown（`**加粗**`、`- 列表`、`# 标题`）。
    这些记号在纯文本控件里会原样显示，用户看到的就是一堆星号和井号，
    非常难读。所以这里把常用的 Markdown 子集转成 Qt 支持的富文本标签。

Qt 的富文本只支持 HTML 的一个子集（见 Qt 文档 "Supported HTML Subset"），
所以这里刻意只用它确实支持的标签：
    b i s u code pre br p ul ol li blockquote hr a font span table
不支持的东西（比如 CSS 盒模型、复杂表格样式）一律避开。

对外三个函数：
    to_qt_html(text)  给 QML 的 Text 用（textFormat: Text.RichText）
    to_plain(text)    给托盘提示、通知标题这类只能放纯文本的地方用
    render(text, fmt) 调试用：把转换结果打印出来看
"""

from __future__ import annotations

import html
import re

# 标题相对基准字号的倍率。直接用 <h1> 在 Qt 里会大得离谱，
# 所以改成 span + font-size（单位用 %，相对于 Text 自己的字号）。
HEADING_SCALE = {1: 132, 2: 120, 3: 110, 4: 104, 5: 100, 6: 100}

# 行内代码的配色：偏暖的橙，和整体主题一致
CODE_COLOR = "#ffb98a"
QUOTE_COLOR = "#a99fc4"
LINK_COLOR = "#8fb8ff"

_ESCAPES = [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")]

# 用于把代码片段临时挖出来，避免被其它规则改写
_PLACEHOLDER = "\x00CODE{}\x00"


def escape(text: str) -> str:
    for needle, replacement in _ESCAPES:
        text = text.replace(needle, replacement)
    return text


def _inline(text: str) -> str:
    """处理行内标记。调用前必须已经做过 HTML 转义。"""
    codes: list[str] = []

    def stash(match: re.Match) -> str:
        codes.append(match.group(1))
        return _PLACEHOLDER.format(len(codes) - 1)

    # 1) 行内代码先挖出来，免得里面的 * 和 _ 被当成强调
    text = re.sub(r"`([^`\n]+)`", stash, text)

    # 2) 链接 [文字](地址)。地址里可能有 &，已经转义过了，直接用
    text = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
        lambda m: f'<a href="{m.group(2)}" style="color:{LINK_COLOR};">{m.group(1)}</a>',
        text,
    )

    # 3) 加粗。** 和 __ 都要处理，且必须排在斜体前面
    text = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"__(?!\s)(.+?)(?<!\s)__", r"<b>\1</b>", text, flags=re.S)

    # 4) 斜体。用 * 时要求两侧不是空格，避免把 "3 * 4 * 5" 变成斜体
    text = re.sub(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", text)

    # 5) 删除线
    text = re.sub(r"~~(?!\s)(.+?)(?<!\s)~~", r"<s>\1</s>", text, flags=re.S)

    # 6) 裸链接自动变成可点的链接
    text = re.sub(
        r"(?<![\"'>=])(https?://[^\s<>\"]+)",
        lambda m: f'<a href="{m.group(1)}" style="color:{LINK_COLOR};">{m.group(1)}</a>',
        text,
    )

    # 7) 把行内代码放回去
    for index, code in enumerate(codes):
        text = text.replace(
            _PLACEHOLDER.format(index),
            f'<code style="color:{CODE_COLOR};">{code}</code>',
        )
    return text


def _is_hr(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    return bool(re.fullmatch(r"(?:\s*[-*_]){3,}\s*", stripped))


def _heading_level(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2)


def _list_match(line: str):
    match = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
    if not match:
        return None
    indent = len(match.group(1).replace("\t", "    "))
    ordered = match.group(2)[0].isdigit()
    return indent, ordered, match.group(3)


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped or "-" not in stripped:
        return False
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", stripped)) and "---" in stripped.replace(" ", "")


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def to_qt_html(text: str, base_scale: int = 100) -> str:
    """把 Markdown 转成 Qt 富文本 HTML。

    base_scale 用来整体缩放标题，一般不用改。
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    out: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None      # "ul" | "ol"
    quote: list[str] = []
    code_block: list[str] = []
    in_code = False
    code_lang = ""

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p style='margin:0 0 6px 0;'>" + "<br/>".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = None

    def flush_quote() -> None:
        if quote:
            body = "<br/>".join(quote)
            out.append(
                f"<blockquote style='color:{QUOTE_COLOR}; margin:0 0 6px 14px;'>"
                f"{body}</blockquote>"
            )
            quote.clear()

    def flush_code() -> None:
        if code_block:
            body = "<br/>".join(code_block)
            out.append(
                "<pre style='margin:0 0 6px 0; color:#d8cfe8;'>"
                f"{body}</pre>"
            )
            code_block.clear()

    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]

        # ---------------------------------------------------------- 代码块
        fence = re.match(r"^\s*```+\s*(\S*)\s*$", line)
        if fence:
            if in_code:
                flush_code()
                in_code = False
                code_lang = ""
            else:
                flush_paragraph()
                close_list()
                flush_quote()
                in_code = True
                code_lang = fence.group(1)
            index += 1
            continue

        if in_code:
            code_block.append(escape(line) if line.strip() else "&nbsp;")
            index += 1
            continue

        # ---------------------------------------------------------- 空行
        if not line.strip():
            flush_paragraph()
            close_list()
            flush_quote()
            index += 1
            continue

        # ---------------------------------------------------------- 分隔线
        if _is_hr(line):
            flush_paragraph()
            close_list()
            flush_quote()
            out.append("<hr style='color:#3a3352;'/>")
            index += 1
            continue

        # ---------------------------------------------------------- 标题
        heading = _heading_level(line)
        if heading is not None:
            flush_paragraph()
            close_list()
            flush_quote()
            level, title = heading
            size = HEADING_SCALE.get(level, 100) * base_scale // 100
            out.append(
                f"<p style='margin:8px 0 6px 0;'>"
                f"<span style='font-size:{size}%;'><b>{_inline(escape(title))}</b></span></p>"
            )
            index += 1
            continue

        # ---------------------------------------------------------- 引用
        if re.match(r"^\s*>", line):
            flush_paragraph()
            close_list()
            quote.append(_inline(escape(re.sub(r"^\s*>\s?", "", line))))
            index += 1
            continue

        # ---------------------------------------------------------- 表格
        if "|" in line and index + 1 < total and _is_table_separator(lines[index + 1]):
            flush_paragraph()
            close_list()
            flush_quote()
            header = _split_row(line)
            index += 2
            rows: list[list[str]] = []
            while index < total and "|" in lines[index] and lines[index].strip():
                rows.append(_split_row(lines[index]))
                index += 1

            table = ["<table cellspacing='0' cellpadding='4' width='100%'>"]
            table.append("<tr>")
            for cell in header:
                table.append(
                    "<td style='border-bottom:1px solid #4a4162;'>"
                    f"<b>{_inline(escape(cell))}</b></td>"
                )
            table.append("</tr>")
            for row in rows:
                table.append("<tr>")
                for cell in row:
                    table.append(
                        "<td style='border-bottom:1px solid #2b2540;'>"
                        f"{_inline(escape(cell))}</td>"
                    )
                table.append("</tr>")
            table.append("</table>")
            out.append("".join(table))
            continue

        # ---------------------------------------------------------- 列表
        item = _list_match(line)
        if item is not None:
            flush_paragraph()
            flush_quote()
            _indent, ordered, content = item
            wanted = "ol" if ordered else "ul"
            if list_kind != wanted:
                close_list()
                out.append(f"<{wanted} style='margin:2px 0 6px 16px;'>")
                list_kind = wanted
            out.append(f"<li>{_inline(escape(content))}</li>")
            index += 1
            continue

        # ---------------------------------------------------------- 普通段落
        close_list()
        flush_quote()
        paragraph.append(_inline(escape(line)))
        index += 1

    flush_code()
    flush_paragraph()
    close_list()
    flush_quote()

    return "".join(out)


# --------------------------------------------------------------------------
# 纯文本版本：托盘提示、系统通知、剪贴板这些地方不能带标签
# --------------------------------------------------------------------------
def to_plain(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 去掉代码块围栏但保留内容
    text = re.sub(r"^\s*```+\s*\S*\s*$", "", text, flags=re.M)
    # 标题、引用、列表符号
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "· ", text, flags=re.M)
    text = re.sub(r"^\s*(\d+)[.)]\s+", r"\1. ", text, flags=re.M)
    text = re.sub(r"^\s*(?:\s*[-*_]){3,}\s*$", "", text, flags=re.M)
    # 行内标记
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(?!\s)(.+?)(?<!\s)__", r"\1", text, flags=re.S)
    text = re.sub(r"~~(?!\s)(.+?)(?<!\s)~~", r"\1", text, flags=re.S)
    text = re.sub(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])", r"\1", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", text)
    # 表格竖线
    text = re.sub(r"^\s*\|(.+)\|\s*$", lambda m: "  ".join(
        cell.strip() for cell in m.group(1).split("|") if cell.strip()), text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
