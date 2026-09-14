"""裁剪并放大截图的一块区域，方便用肉眼确认细节。"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) < 6:
        print("用法：crop.py <图片> <x> <y> <w> <h> [放大倍数] [输出名]")
        return 1
    src = Path(sys.argv[1])
    if not src.is_absolute():
        src = ROOT / src
    x, y, w, h = (int(v) for v in sys.argv[2:6])
    scale = int(sys.argv[6]) if len(sys.argv) > 6 else 2
    out_name = sys.argv[7] if len(sys.argv) > 7 else f"{src.stem}_crop.png"

    image = QImage(str(src))
    if image.isNull():
        print(f"读不到图片：{src}")
        return 1
    rect = QRect(x, y, w, h).intersected(image.rect())
    crop = image.copy(rect)
    if scale > 1:
        crop = crop.scaled(crop.width() * scale, crop.height() * scale,
                           Qt.KeepAspectRatio, Qt.FastTransformation)
    out = ROOT / ".cache" / "preview" / out_name
    crop.save(str(out))
    print(f"{out}  ({crop.width()}x{crop.height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
