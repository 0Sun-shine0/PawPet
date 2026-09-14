"""把程序本体压成 payload.zip。

单独一步，因为压缩要花时间，而且改安装界面时不需要重新压一遍。

压缩策略：
  * ZIP_DEFLATED + compresslevel=9。不用 LZMA 是因为 Windows 自带的
    解压和 Python 的 zipfile 对 LZMA 支持都不够普遍，兼容性优先。
  * 跳过 .pyc / __pycache__：它们在打包后没用，还占体积。
  * Qt 的 DLL 已经压过了，再压收益很低，但为了体积还是压一下。

用法：
    .venv\\Scripts\\python.exe tools/make_payload.py
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "dist" / "PawPet"
PAYLOAD = ROOT / "build" / "payload.zip"

# 这些没必要塞进安装包
SKIP_DIRS = {"__pycache__", ".cache", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".pdb", ".log"}


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main() -> int:
    if not APP_DIR.exists():
        print(f"[XX] 找不到 {APP_DIR}，先跑 tools/build.py")
        return 1

    exe = APP_DIR / "PawPet.exe"
    if not exe.exists():
        print(f"[XX] 找不到 {exe}")
        return 1

    print("压缩程序本体…")
    PAYLOAD.parent.mkdir(parents=True, exist_ok=True)
    if PAYLOAD.exists():
        PAYLOAD.unlink()

    total_raw = 0
    written = 0
    skipped = 0

    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for item in sorted(APP_DIR.rglob("*")):
            if not item.is_file():
                continue
            relative = item.relative_to(APP_DIR)
            if any(part in SKIP_DIRS for part in relative.parts):
                skipped += 1
                continue
            if item.suffix.lower() in SKIP_SUFFIXES:
                skipped += 1
                continue

            try:
                size = item.stat().st_size
            except OSError:
                continue

            bundle.write(item, relative.as_posix())
            total_raw += size
            written += 1
            if written % 300 == 0:
                print(f"  {written} 个文件，原始 {human(total_raw)}", flush=True)

    packed = PAYLOAD.stat().st_size
    ratio = (packed / total_raw * 100) if total_raw else 0

    print(f"\n  [ok] {PAYLOAD}")
    print(f"       文件数  : {written}（跳过 {skipped}）")
    print(f"       压缩前  : {human(total_raw)}")
    print(f"       压缩后  : {human(packed)}（{ratio:.1f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
