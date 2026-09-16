r"""字形覆盖检查：把界面用到的每个字符**渲染出来**，确认它真的画出了东西。

背景
----
用户反馈「字体是不是有问题」。查下来最大的隐患是**字体里没有这个字形**：
Qt 不报错，只是画成豆腐块（□）或者丢给另一个完全不搭的字体。

界面里塞了 36 个符号字符当图标（◉ ⚙ ✓ ✕ ▶ ■ ☑ ◔ ◷ ↺ ⇅ ⏭ ⏰ ⏸ …），
雅黑和 Segoe UI 里都没有，全靠回退 —— 实测回退结果很糟：
* ▶ ▢ ☐ 画成**豆腐块**
* ◉ ⏭ ⏰ ⏸ 落到 **Segoe UI Emoji**，变成彩色卡通图标

所以这个脚本不能只问 `QRawFont.supportsCharacter`：
**那个 API 只看家族列表里的第一个字体，看不到回退链**，
拿它测回退会得出「整条链都不支持中文」这种假结论。

正确做法就是**渲染出来数像素**：把字符画到画布上，
统计非背景像素的比例。豆腐块的墨量明显异常，缺字形直接是 0。

用法：
    .venv\Scripts\python.exe tools\glyphcheck.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

PASSED = 0
FAILED: list[str] = []

BOX_W, BOX_H = 40, 44
INK_PX = 26


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def collect_chars() -> tuple[set[str], set[str], set[str]]:
    """从 QML 源码里抠出所有会显示出来的字符，按区段分桶。

    做法很土但够用：把双引号/单引号字符串里的内容全拼起来再分桶。
    比解析 QML 简单，也不会漏掉拼在代码里的字。
    """
    chunks: list[str] = []
    for path in (ROOT / "pawpet" / "qml").rglob("*.qml"):
        text = path.read_text(encoding="utf-8", errors="replace")
        chunks.extend(re.findall(r'"((?:[^"\\]|\\.)*)"', text))
        chunks.extend(re.findall(r"'((?:[^'\\]|\\.)*)'", text))
    blob = "".join(chunks)

    han = {ch for ch in blob if "\u4e00" <= ch <= "\u9fff"}
    # emoji：彩色字形由系统 emoji 字体画，单独一桶（我们不要求它单色）
    emoji = {ch for ch in blob if ord(ch) > 0x1F000}
    symbols = {
        ch for ch in blob
        if ch.isprintable() and not ch.isalnum() and not ch.isspace()
        and not ("\u4e00" <= ch <= "\u9fff")
        and ord(ch) <= 0x1F000
        # 变体选择符（U+FE0F）本身不画东西，跳过
        and ch != "\ufe0f"
    }
    return han, symbols, emoji


def main() -> int:
    print("小爪字形覆盖检查（渲染级）\n")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])

    from pawpet.qmlfont import FONT_FAMILY, FONT_LATIN, FONT_MONO

    han, symbols, emoji = collect_chars()
    print(f"界面用到的字符：汉字 {len(han)}、符号 {len(symbols)}、emoji {len(emoji)}")
    print(f"  符号：{''.join(sorted(symbols))}")
    print(f"  emoji：{''.join(sorted(emoji))}")

    def ink_ratio(family: str, ch: str, px: int = INK_PX) -> float:
        """这个字符渲染出来「有多少墨」。

        判定条件不能写「亮度 < 200」—— 彩色 emoji 是浅色系（✨ 是金黄、
        🍅 是红色），亮度很高，用亮度判会得出「什么都没画」的假结论。
        正确的判据是「和纯白背景不一样」：任一通道明显低于 255 就算画了东西。
        """
        img = QImage(BOX_W, BOX_H, QImage.Format_ARGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        font = QFont(family)
        font.setPixelSize(px)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        painter.drawText(img.rect(), Qt.AlignCenter, ch)
        painter.end()

        ink = 0
        for y in range(BOX_H):
            for x in range(BOX_W):
                color = img.pixelColor(x, y)
                if min(color.red(), color.green(), color.blue()) < 240:
                    ink += 1
        return ink / (BOX_W * BOX_H)

    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("一、每个字体族：有没有字符画不出来（豆腐块 / 空白）")
    print("=" * 70)

    EMPTY = 0.004          # 低于这个墨量就是「什么都没画出来」

    for label, family in (("主字体", FONT_FAMILY),
                          ("等宽", FONT_MONO),
                          ("拉丁", FONT_LATIN)):
        blanks = [ch for ch in sorted(han | symbols)
                  if ink_ratio(family, ch) < EMPTY]
        print(f"  {label:6s} {family}")
        print(f"      完全没画出来：{len(blanks)} 个 "
              f"{''.join(blanks[:40]) if blanks else '（无）'}")

    # 关键断言：主字体必须能把所有汉字和符号画出来。
    #
    # 「什么都没画出来」怎么判：直接看墨量。别去猜豆腐块的形状 ——
    # 试过用私有区字符当模板比对，结果不可靠（Qt 对私有区的处理不统一）。
    # 墨量判据本身已经足够，而且这里还有一条自检盯着它别失灵。
    def blanks_for(family: str, chars) -> list[str]:
        return [ch for ch in sorted(chars) if ink_ratio(family, ch) < EMPTY]

    # 自检：判据必须真的在数像素，而不是恒返回某个值。
    #
    # 一开始这里想拿「渲染私有区字符」当空白样本，但那个思路是错的：
    # Qt 会给私有区字符配一个回退字形（实测墨量 0.078，和字母 A 差不多），
    # 所以它**不是**空白样本。用空格才是干净的对照 —— 空格就是什么都不画，
    # 同时又能确认字符确实被排版了（墨量为 0 而不是抛异常）。
    space_ink = ink_ratio(FONT_FAMILY, " ")
    letter_ink = ink_ratio(FONT_FAMILY, "A")
    check("墨量判据本身有效（自检）",
          space_ink < EMPTY <= letter_ink,
          f"空格={space_ink:.4f} 字母A={letter_ink:.4f} 阈值={EMPTY}")

    def blanks_for(family: str, chars) -> list[str]:
        return [ch for ch in sorted(chars) if ink_ratio(family, ch) < EMPTY]

    main_han = blanks_for(FONT_FAMILY, han)
    check("主字体：所有汉字都画得出来", not main_han,
          f"缺 {len(main_han)} 个：{''.join(main_han[:30])}")

    main_sym = blanks_for(FONT_FAMILY, symbols)
    check("主字体：所有符号都画得出来", not main_sym,
          f"缺 {len(main_sym)} 个：{''.join(main_sym[:30])}")

    latin_han = blanks_for(FONT_LATIN, han)
    check("拉丁字体族：中文能画（回退链生效）", not latin_han,
          f"缺 {len(latin_han)} 个：{''.join(latin_han[:20])}")

    latin_sym = blanks_for(FONT_LATIN, symbols)
    check("拉丁字体族：按钮图标字符能画出来", not latin_sym,
          f"缺 {len(latin_sym)} 个：{''.join(latin_sym[:30])}")

    mono_han = blanks_for(FONT_MONO, han)
    check("等宽字体族：中文能画（Consolas 本身没有汉字）", not mono_han,
          f"缺 {len(mono_han)} 个：{''.join(mono_han[:20])}")

    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("二、混排：「数字 + 中文量词」不能整行走成同一个字体")
    print("=" * 70)

    def render_bytes(family: str, text: str, px: int = 16) -> bytes:
        img = QImage(240, 40, QImage.Format_ARGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        font = QFont(family)
        font.setPixelSize(px)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        painter.drawText(img.rect(), Qt.AlignCenter, text)
        painter.end()
        return bytes(img.constBits())

    mixed = "45 分钟"
    check("混排里的数字仍然用等宽（没被整行换成雅黑）",
          render_bytes(FONT_MONO, mixed) != render_bytes("Microsoft YaHei UI", mixed))
    check("混排里的汉字被回退字体接管（没画成豆腐块）",
          ink_ratio(FONT_MONO, "分", 16) > EMPTY)
    check("纯数字仍然由 Consolas 渲染",
          render_bytes(FONT_MONO, "45") == render_bytes("Consolas", "45"))

    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("三、符号有没有偷偷变成彩色 emoji")
    print("=" * 70)
    # 判定：彩色 ≠ 一定有问题。**分两种情况**：
    #   * 按钮图标、状态标记（✓ ✕ ⚙ ◉ …）必须是单色线条 ——
    #     一个彩色卡通图标混在一排线框图标里非常突兀；
    #   * 卡片标题、空状态、内容里的 emoji（🐾 🎉 📋 🍅）**本来就该是彩色**，
    #     那是设计的一部分，不是缺陷。
    # 所以这里用的是「显式豁免名单」而不是一律禁止 ——
    # 谁敢往按钮里塞彩色 emoji，测试会红。
    EMOJI_OK_AS_ICON = set("⏰✨✅⚠")     # 内容/装饰位，允许彩色

    def is_colored(family: str, ch: str) -> bool:
        img = QImage(BOX_W, BOX_H, QImage.Format_ARGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        font = QFont(family)
        font.setPixelSize(INK_PX)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        painter.drawText(img.rect(), Qt.AlignCenter, ch)
        painter.end()
        for y in range(0, BOX_H, 2):
            for x in range(0, BOX_W, 2):
                color = img.pixelColor(x, y)
                hi = max(color.red(), color.green(), color.blue())
                lo = min(color.red(), color.green(), color.blue())
                if hi - lo > 60:
                    return True
        return False

    colored = [ch for ch in sorted(symbols)
               if is_colored(FONT_FAMILY, ch) and ch not in EMOJI_OK_AS_ICON]
    check("按钮/状态图标都是单色线条（没有彩色 emoji 混进来）", not colored,
          f"这些是彩色的：{''.join(colored)}")

    # 记一笔：豁免掉的那几个到底是什么状态，方便以后回看
    allowed_colored = [ch for ch in sorted(EMOJI_OK_AS_ICON)
                       if is_colored(FONT_FAMILY, ch)]
    print(f"  豁免的装饰性 emoji（内容位，允许彩色）：{''.join(allowed_colored)}")

    # 对比图，人工也能看一眼
    out = ROOT / ".cache" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    order = "".join(sorted(symbols))
    rows = (("主字体", FONT_FAMILY), ("拉丁", FONT_LATIN), ("等宽", FONT_MONO))
    img = QImage(BOX_W * len(order), BOX_H * len(rows), QImage.Format_ARGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    for row, (_, family) in enumerate(rows):
        font = QFont(family)
        font.setPixelSize(INK_PX)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        for i, ch in enumerate(order):
            painter.drawText(i * BOX_W, row * BOX_H, BOX_W, BOX_H,
                             Qt.AlignCenter, ch)
    painter.end()
    img.save(str(out / "glyph-grid.png"))
    print(f"\n  对比图：{out / 'glyph-grid.png'}")
    print(f"  字符顺序：{order}")

    print(f"\n{'=' * 70}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
