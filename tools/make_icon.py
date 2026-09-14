r"""生成应用图标 build\pawpet.ico。

用和托盘图标同一套画法，保证 exe 图标、快捷方式图标、托盘图标看起来是同一个东西。
用 Qt 画好各尺寸再写成多分辨率 ico（Windows 会按需要挑合适的那张）。

用法：
    .venv\\Scripts\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = ROOT / "build" / "pawpet.ico"
PNG_OUT = ROOT / "build" / "pawpet.png"

# Windows 图标常用尺寸。16/32 用于任务栏和标题栏，256 用于大图标视图。
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def draw(size: int):
    """按给定尺寸画一只猫爪。和小爪的形象呼应：奶油底 + 橘色描边 + 爪印。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    unit = size / 32.0

    # 圆角方底，暖橘渐变
    from PySide6.QtGui import QLinearGradient

    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, QColor("#FFB877"))
    gradient.setColorAt(1.0, QColor("#F0913F"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    radius = size * 0.28
    painter.drawRoundedRect(0, 0, size, size, radius, radius)

    # 深色爪印
    painter.setBrush(QColor("#3A2A20"))
    # 掌垫
    painter.drawEllipse(int(9.5 * unit), int(14.5 * unit),
                        int(13 * unit), int(11 * unit))
    # 四个脚趾
    for cx, cy, r in ((7.0, 10.0, 2.5), (13.0, 7.4, 2.7), (19.0, 7.4, 2.7), (25.0, 10.0, 2.5)):
        painter.drawEllipse(int((cx - r) * unit), int((cy - r) * unit),
                            int(2 * r * unit), int(2 * r * unit))

    painter.end()
    return pixmap


def pixmap_to_png_bytes(pixmap) -> bytes:
    """把 QPixmap 编码成 PNG 字节。"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(array)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    """按 ICO 格式打包多张 PNG。

    ICO 结构：
        6 字节头 (保留 0, 类型 1, 数量 n)
        每张 16 字节目录项
        然后是各张图片的数据
    从 Vista 起 ICO 里可以直接放 PNG，所以不用转成 BMP。
    """
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)

    directory = b""
    offset = 6 + 16 * count
    for size, data in images:
        # 256 要写成 0（字段只有一个字节）
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            width, height,      # 宽高
            0,                  # 调色板数量
            0,                  # 保留
            1,                  # 颜色平面
            32,                 # 位深
            len(data),          # 数据大小
            offset,             # 数据偏移
        )
        offset += len(data)

    return header + directory + b"".join(data for _size, data in images)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    images: list[tuple[int, bytes]] = []
    for size in SIZES:
        pixmap = draw(size)
        images.append((size, pixmap_to_png_bytes(pixmap)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build_ico(images))

    # 顺便存一张 256 的 PNG，给文档和商店页面用
    draw(256).save(str(PNG_OUT), "PNG")

    print(f"已生成 {OUT}  ({OUT.stat().st_size / 1024:.1f} KB, {len(SIZES)} 种尺寸)")
    print(f"已生成 {PNG_OUT}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
