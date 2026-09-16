"""字体族定义 —— **唯一来源**。

为什么放 Python 而不是只写在 Theme.qml 里
---------------------------------------
同一套字体名有三个地方要用：

1. `pawpet/qml/PawPet/Theme.qml` —— QML 界面读它；
2. `pawpet/ai/markdown.py` —— 模型回答的 Markdown 转 HTML 时要标等宽；
3. `tools/glyphcheck.py` / `tools/qmlcheck.py` —— 测试要检查字形覆盖。

写两遍必然会漂移（改了 QML 忘了 Python，测试就在验证一个不存在的字体），
所以这里定义一次，QML 侧通过 Backend 暴露的同名属性读，
测试直接 import 这里。谁要改字体，改这一个文件。

字体回退链为什么必须写
--------------------
Qt 的 `family` 支持逗号分隔的家族列表，是从左到右的回退链。
不写回退链的话，字体缺字形时 Qt 会**静默**挑一个别的字体：

* `Consolas` 里没有汉字。界面上「45 分钟」「未配置」这类
  「数字 + 中文量词」的混排，汉字会被丢给 SimSun 一类的衬线体 ——
  同一行里两种字形、两种基线，看起来就是「字体怪怪的」。
* `Segoe UI` 里没有汉字，也没有一堆几何符号（◉ ☑ ✕ ⚙）。
  按钮上的图标字符会各自回退到不同字体，字重和大小都不一致。

实测回退链不会改变排版宽度（「45 分钟」59px → 59px），
所以补上它不会把已有布局挤歪。
"""

from __future__ import annotations

# 中文字体：Windows 上「Microsoft YaHei UI」是雅黑的 UI 变体，
# 字面比普通雅黑略窄、更适合界面。系统没有时会回退到雅黑。
FONT_FAMILY = (
    "Microsoft YaHei UI, Microsoft YaHei, Segoe UI Symbol, Segoe UI"
)

# 等宽：给数字、路径、命令回显、时钟用。
FONT_MONO = (
    "Consolas, Microsoft YaHei UI, Microsoft YaHei, Segoe UI Symbol"
)

# 拉丁：给按钮上的图标字符用（✓ ◉ ✕ ⚙ ↗ ■ ▶ 这些）。
# 必须排上 Segoe UI Symbol —— 见下面「符号字形」那一段。
FONT_LATIN = (
    "Segoe UI, Segoe UI Symbol, Microsoft YaHei UI, Microsoft YaHei"
)

ALL_FAMILIES = {
    "font": FONT_FAMILY,
    "fontMono": FONT_MONO,
    "fontLatin": FONT_LATIN,
}


# ---------------------------------------------------------------------------
#  关于「Segoe UI Symbol」为什么必须显式写进去
# ---------------------------------------------------------------------------
# 界面上用了 36 个符号字符（◉ ⚙ ✓ ✕ ▶ ■ ☑ ◔ ◷ ↺ ⇅ ⏭ ⏰ ⏸ …）当图标。
# 这些字符雅黑和 Segoe UI 里**都没有**，靠 Qt 的自动回退去凑。
#
# 实测自动回退的结果是不可接受的：有的字符落到了 **Segoe UI Emoji**，
# 于是 ◉ 变成彩色 emoji、⏭⏰⏸ 变成蓝色方块和闹钟 —— 在粉白界面里
# 突然冒出几个彩色卡通图标，非常突兀；更糟的是 ▶ ▢ ☐ 这类直接画成
# **豆腐块（□）**，也就是缺字形。
#
# 显式排上 Segoe UI Symbol 之后，这 36 个字符全部变成单色线条字形，
# 风格和界面一致（对比图见 .cache/shots/glyph-compare.png）。
#
# 顺序有讲究：Segoe UI 在前（拉丁正文用它），Symbol 紧随其后专管符号，
# 雅黑垫底管中文。emoji（🐾 🎉 📋）不受影响，仍然由系统 emoji 字体渲染 ——
# 那是我们想要的，emoji 就该是彩色的。
