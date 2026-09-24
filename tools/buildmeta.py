"""构建产物的可验证元数据。

源码版不依赖这份清单；打包时由 ``tools/build.py`` 生成，随 exe 一起带走。
这样即使版本号没有变化，也能判断运行的包是不是当前源码构建出来的。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path


SOURCE_GLOBS = (
    "run_pawpet.py",
    "pawpet/**/*.py",
    "pawpet/**/*.qml",
    "pawpet/**/qmldir",
    "mcp_servers/**/*.py",
    "build/pawpet.spec",
    "build/使用说明.md",
)


def source_files(root: Path) -> list[Path]:
    """返回会进入主程序产物的源码文件，顺序固定且不重复。"""
    found: set[Path] = set()
    for pattern in SOURCE_GLOBS:
        for path in root.glob(pattern):
            if path.is_file():
                found.add(path.resolve())
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def source_fingerprint(root: Path) -> str:
    """对源码路径和内容做短 SHA-256 指纹。"""
    digest = hashlib.sha256()
    for path in source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def source_latest_mtime_ns(root: Path) -> int:
    """返回参与构建的源码中最新的修改时间。"""
    files = source_files(root)
    return max((path.stat().st_mtime_ns for path in files), default=0)


def make_manifest(root: Path, version: str) -> dict:
    """生成一份可写入随包清单的字典。"""
    built_at_ns = time.time_ns()
    fingerprint = source_fingerprint(root)
    return {
        "schema": 1,
        "version": str(version),
        "build_id": f"{built_at_ns:x}-{fingerprint}",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built_at_ns": built_at_ns,
        "source_fingerprint": fingerprint,
        "source_latest_mtime_ns": source_latest_mtime_ns(root),
        "source_file_count": len(source_files(root)),
    }


def write_manifest(path: Path, root: Path, version: str) -> dict:
    """生成并写入构建清单，返回写入的内容。"""
    manifest = make_manifest(root, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
