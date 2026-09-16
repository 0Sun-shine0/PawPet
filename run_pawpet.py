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


def selfcheck() -> int:
    """把关键模块**真的 import 一遍**，报告结果，然后退出。

    为什么需要：PyInstaller 靠静态分析找模块，而项目里有不少
    「函数内部的 import」（controller 里的 `from .providers import ...`、
    backend 里的 `from . import theme`）。这类导入一旦没被收进包里，
    **打包阶段完全看不出来** —— 要等用户点到那个功能的那一刻才炸。

    所以打包后跑一次这个：`PawPet.exe --selfcheck`。
    它逐个 import 并打印，缺哪个一眼就看到。
    """
    targets = [
        ("核心配置", "pawpet.config"),
        ("界面后端", "pawpet.backend"),
        ("主题配色", "pawpet.theme"),
        ("字体定义", "pawpet.qmlfont"),
        ("AI 控制器", "pawpet.ai.controller"),
        ("AI 主循环", "pawpet.ai.agent"),
        ("工具表", "pawpet.ai.tools"),
        ("模型服务商", "pawpet.ai.providers"),
        ("自定义工具", "pawpet.ai.extensions"),
        ("跨会话记忆", "pawpet.ai.memory"),
        ("知识库", "pawpet.ai.kb"),
        ("失败自纠", "pawpet.ai.advisor"),
        ("MCP 客户端", "pawpet.ai.mcp"),
        ("Markdown", "pawpet.ai.markdown"),
    ]
    failed: list[str] = []
    for label, module in targets:
        try:
            __import__(module)
            print(f"[ok] {label} ({module})", flush=True)
        except Exception as exc:  # noqa: BLE001 - 这里就是要抓所有错
            failed.append(f"{label} ({module}): {type(exc).__name__}: {exc}")
            print(f"[XX] {label} ({module}): {exc}", flush=True)

    # QML 资源（打包后是只读资源，路径解析和开发模式不一样）
    try:
        from pawpet.config import QML_DIR

        qml_count = len(list(QML_DIR.rglob("*.qml"))) if QML_DIR.exists() else 0
        if qml_count >= 15:
            print(f"[ok] QML 资源：{qml_count} 个文件", flush=True)
        else:
            failed.append(f"QML 资源只有 {qml_count} 个（目录 {QML_DIR}）")
            print(f"[XX] QML 资源只有 {qml_count} 个（目录 {QML_DIR}）", flush=True)
    except Exception as exc:  # noqa: BLE001
        failed.append(f"QML 资源路径解析失败：{exc}")
        print(f"[XX] QML 资源路径解析失败：{exc}", flush=True)

    print("")
    if failed:
        print(f"[XX] 自检失败 {len(failed)} 项：")
        for item in failed:
            print(f"     · {item}")
        # 给自动化测试用的**纯 ASCII** 标记。
        #
        # 为什么不用中文：打包后的 exe 是窗口程序，stdout 的编码跟着系统
        # 区域设置走（中文 Windows 上是 GBK），而调用方一般按 UTF-8 解码 ——
        # 中文会变成乱码，测试就找不到这句话了（实测踩过：
        # 「自检通过」被读成 `�Լ�ͨ��`，断言失败，但功能其实是好的）。
        # ASCII 标记不受编码影响。
        print(f"SELFCHECK FAILED: {len(failed)}")
        return 1
    print(f"[ok] 自检通过：{len(targets)} 个模块 + QML 资源全部就位")
    print(f"SELFCHECK OK: {len(targets)} modules")
    return 0


def main() -> int:
    # --selfcheck：只做导入自检，不启动界面。给打包后的冒烟测试用。
    if "--selfcheck" in sys.argv:
        try:
            from pawpet.config import load_env

            load_env()
        except Exception:  # noqa: BLE001 - 自检阶段读不到 .env 不算失败
            pass
        return selfcheck()

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
