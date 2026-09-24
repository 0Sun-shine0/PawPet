"""开发工具统一使用 UTF-8；兼容没有标准流的窗口程序和测试替身。"""

import os
import sys


def configure_utf8() -> None:
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
