"""小爪助手启动入口。

无论从 .cmd、快捷方式、IDE、注册表自启动还是打包后的 exe，走的都是这个文件，
所以 .env 的加载和路径解析只在这里做一次。

打包成窗口程序后没有控制台（sys.stdout 是 None），所有错误都要靠弹窗告诉用户，
不然程序就是「双击没反应」，没法排查。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 源码运行时把项目根目录加进 sys.path；打包后不需要（PyInstaller 已经处理好）
if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def show_error(title: str, message: str) -> None:
    """尽量把错误显示出来：能打印就打印，不能就弹窗。"""
    try:
        if sys.stdout is not None:
            print(f"[{title}] {message}")
    except (OSError, ValueError):
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        from pawpet.config import load_env

        load_env()
    except Exception as exc:  # noqa: BLE001
        show_error("小爪助手 · 启动失败", f"读取配置失败：\n{exc}")
        return 1

    try:
        from pawpet.app import run
    except ImportError as exc:
        missing = getattr(exc, "name", None) or str(exc)
        message = (
            f"缺少运行依赖：{missing}\n\n"
            "如果你是从源码运行，请在项目目录下执行：\n"
            "    .venv\\Scripts\\python.exe -m pip install -r requirements.txt\n"
        )
        show_error("小爪助手 · 启动失败", message)
        return 1
    except Exception as exc:  # noqa: BLE001
        import traceback

        show_error("小爪助手 · 启动失败",
                   f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-1200:]}")
        return 1

    try:
        return run()
    except Exception as exc:  # noqa: BLE001
        import traceback

        detail = traceback.format_exc()
        show_error(
            "小爪助手 · 运行时出错",
            f"{type(exc).__name__}: {exc}\n\n{detail[-1500:]}",
        )
        return 1


if __name__ == "__main__":
    os.environ.setdefault("PAWPET_LAUNCHED", "1")
    sys.exit(main())
