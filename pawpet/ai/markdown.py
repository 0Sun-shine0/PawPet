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

# 标题相对基准字号的倍率。
#
# **这里踩过一个坑：不能靠 <h1> 也不能靠 font-size:132%。**
#
# * 用 <h1>/<h2> 标签：Qt 的富文本会套上它自己的默认倍率（实测 h1 是
#   正文的 1.8 倍），在 320px 宽的聊天气泡里一行放不下几个字，很难看。
# * 用 <span style="font-size:132%">：**Qt 根本不认相对单位**。
#   实测 132%、1.32em 渲染出来的宽高和正文一模一样（44x23），
#   只有绝对单位 font-size:20px 才生效。
#
# 所以「相对倍率」这件事必须由我们来算成绝对像素：
# 正文基准 14px × 倍率 = 实际的 px 值，见 _heading_size()。
# 结论：markdown.py 里**只能写绝对字号**，写百分比是静默失效的。
HEADING_SCALE = {1: 132, 2: 120, 3: 110, 4: 104, 5: 100, 6: 100}

# 正文基准字号（px）。和 QML 的 Theme.fsBody 基准值保持一致，
# 改一边要记得改另一边 —— featuretest 里有一条断言盯着这个数。
BASE_FONT_PX = 14


def _heading_size(level: int, base_scale: int = 100) -> int:
    """把标题倍率算成**绝对像素**（Qt 只认绝对字号）。"""
    scaled = HEADING_SCALE.get(level, 100) * base_scale // 100
    return max(1, BASE_FONT_PX * scaled // 100)


# ---------------------------------------------------------------- 配色
# 这一组颜色是**深色主题时代**留下的，换粉白之后全部失效了：
# 代码块的 #d8cfe8 是浅紫，压在粉白气泡上几乎看不见（对比度 1.3:1，
# 等于把代码块藏起来了）。现在按浅色底重挑一遍，都保证 4.5:1 以上。
#
# 为什么写在这里而不是从 QML 的 Theme 取：这一段是 Python 侧拼 HTML 用的，
# 拿不到 QML 单例。代价是「改主题色要记得同步这里」——
# 所以值都集中在这四行，别散到字符串里去。
CODE_COLOR = "#c2410c"      # 行内代码：暖橙，浅底上够深
QUOTE_COLOR = "#6d5a7a"     # 引用：灰紫，比正文弱但读得清
LINK_COLOR = "#2563eb"      # 链接：蓝
CODE_BLOCK_COLOR = "#40325a"  # 代码块正文
RULE_COLOR = "#e6cfda"      # 分隔线：淡粉，别抢视线
TABLE_HEAD_BORDER = "#d9bccb"   # 表头下边框
TABLE_ROW_BORDER = "#f0dde6"    # 行下边框

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
                f"<pre style='margin:0 0 6px 0; color:{CODE_BLOCK_COLOR};'>"
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
            out.append(f"<hr style='color:{RULE_COLOR};'/>")
            index += 1
            continue

        # ---------------------------------------------------------- 标题
        heading = _heading_level(line)
        if heading is not None:
            flush_paragraph()
            close_list()
            flush_quote()
            level, title = heading
            size = _heading_size(level, base_scale)
            out.append(
                f"<p style='margin:8px 0 6px 0;'>"
                f"<span style='font-size:{size}px;'><b>{_inline(escape(title))}</b></span></p>"
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
                    f"<td style='border-bottom:1px solid {TABLE_HEAD_BORDER};'>"
                    f"<b>{_inline(escape(cell))}</b></td>"
                )
            table.append("</tr>")
            for row in rows:
                table.append("<tr>")
                for cell in row:
                    table.append(
                        f"<td style='border-bottom:1px solid {TABLE_ROW_BORDER};'>"
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
