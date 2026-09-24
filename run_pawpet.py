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


def safe_print(message: str = "", **_kwargs: object) -> None:
    """Write diagnostics without letting a broken stdout hide the real error."""
    text = f"{message}\n"
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            stream.write(text)
            stream.flush()
            return
        except (AttributeError, OSError, UnicodeError, ValueError):
            pass
    try:
        os.write(1, text.encode("utf-8", errors="backslashreplace"))
    except (OSError, ValueError):
        pass


def _ensure_stdio() -> str:
    """给窗口程序补上 stdin/stdout。失败返回原因，成功返回空串。

    打包后是 GUI 子系统程序（console=False），PyInstaller 会把
    `sys.stdout` / `sys.stdin` 设成 None。但 MCP 走的就是 stdio ——
    父进程是用管道启动我们的，fd 0/1 其实是好的，只是 Python 没把它
    包成文本流。这里补上。

    不补的话，`send()` 第一次 `sys.stdout.write` 就是
    `AttributeError: 'NoneType' object has no attribute 'write'`，
    而且发生在协议层 —— 客户端只会看到「连上了但没有工具」。
    """
    import os

    for name, fd, mode in (("stdout", 1, "w"), ("stdin", 0, "r")):
        stream = getattr(sys, name, None)
        if stream is not None:
            continue
        try:
            setattr(sys, name, os.fdopen(os.dup(fd), mode,
                                         encoding="utf-8",
                                         buffering=1 if mode == "w" else -1))
        except OSError as exc:
            return f"{name} 打不开：{exc}"
    return ""


def run_mcp_server(name: str) -> int:
    """把自己当成一个 MCP server 跑起来（`--mcp-server <名字>`）。

    **为什么需要这个模式。** stdio MCP 的客户端要 spawn 一个子进程，
    而打包之后**没有 python.exe** —— 配置里写
    `["python", "mcp_servers/pawkit.py"]` 会直接「找不到可执行文件」。
    所以让 exe 自己兼任 server：`PawPet.exe --mcp-server pawkit`。

    开发模式下 `sys.executable` 是 venv 的 python，同样能跑这个入口 ——
    于是**同一份配置在两种环境下都对**，不需要按环境写两套。

    注意这个分支在**创建任何 Qt 对象和抢单实例互斥体之前**返回：
    主程序正开着的时候，另一个 `--mcp-server` 进程必须能起来
    （它就是被主程序 spawn 的）。
    """
    problem = _ensure_stdio()
    if problem:
        print(f"[mcp] 无法启动 {name}：{problem}", file=sys.stderr)
        return 1

    try:
        from pawpet.ai.mcp import server_path
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp] 读不到 server 位置：{exc}", file=sys.stderr)
        return 1

    path = server_path(name)
    if path is None:
        print(f"[mcp] 没有内置的 server：{name}", file=sys.stderr)
        return 1

    # 直接跑那个脚本（它本来就是 `if __name__ == "__main__"` 的结构）。
    # 用 runpy 而不是 import：不挂进 sys.modules，退出时也不用清理全局状态。
    import runpy

    sys.argv = [str(path)]
    try:
        runpy.run_path(str(path), run_name="__main__")
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[mcp] {name} 异常退出：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    return 0


def selfcheck() -> int:
    """把关键模块**真的 import 一遍**，报告结果，然后退出。

    为什么需要：PyInstaller 靠静态分析找模块，而项目里有不少
    「函数内部的 import」（controller 里的 `from .providers import ...`、
    backend 里的 `from . import theme`）。这类导入一旦没被收进包里，
    **打包阶段完全看不出来** —— 要等用户点到那个功能的那一刻才炸。

    所以打包后跑一次这个：`PawPet.exe --selfcheck`。
    它逐个 import 并打印，缺哪个一眼就看到。
    """
    print = safe_print
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
    # --mcp-server <名字>：把自己当成 MCP server 跑。
    #
    # **必须排在所有东西前面**（早于 load_env、早于 Qt、早于单实例互斥体）：
    # 这个进程是被主程序 spawn 出来的子进程，主程序正开着 ——
    # 去抢互斥体的话会「检测到已有实例」然后安静退出，
    # 表现出来就是 MCP「连上了但一个工具都没有」。
    if "--mcp-server" in sys.argv:
        index = sys.argv.index("--mcp-server")
        target = sys.argv[index + 1] if index + 1 < len(sys.argv) else ""
        if not target:
            print("[mcp] --mcp-server 后面要跟 server 名字", file=sys.stderr)
            return 1
        return run_mcp_server(target)

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
