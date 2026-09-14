"""对比 offscreen 与 windows 平台下的字体数据库，找出文字变方块的原因。"""
from __future__ import annotations

import os
import sys

platform = sys.argv[1] if len(sys.argv) > 1 else "offscreen"
os.environ["QT_QPA_PLATFORM"] = platform

from PySide6.QtGui import QFontDatabase, QFont, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv[:1])
    print(f"=== platform = {platform} ===")

    families = QFontDatabase.families()
    print(f"字体族数量：{len(families)}")
    for probe in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Consolas", "SimSun"):
        print(f"  {probe:22s} 存在={probe in families}")

    if families:
        print("  前 12 个：" + ", ".join(families[:12]))

    for family in ("Microsoft YaHei UI", "Segoe UI"):
        font = QFont(family, 13)
        metrics = QFontMetrics(font)
        info = metrics.fontDx
        width_zh = metrics.horizontalAdvance("小爪助手")
        width_en = metrics.horizontalAdvance("PawPet")
        print(f"  {family:22s} 中文宽={width_zh:4d} 英文宽={width_en:4d}")
        del info

    print(f"实际用于 13pt 的字体：{QFont('Microsoft YaHei UI', 13).family()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
