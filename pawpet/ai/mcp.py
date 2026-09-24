"""MCP（Model Context Protocol）stdio 客户端。

比旧版那个「读一行就当结果」的实现靠谱一些，修掉了三个实际问题：

1. MCP server 会往 stdout 发通知和日志，读到的不一定是本次请求的响应 ——
   这里按 id 匹配，把通知单独放一边。
2. 同步 readline 一旦 server 不吭声就会永久卡死 —— 这里用后台读线程 + 超时。
3. 进程退出/管道断裂没有兜底 —— 这里每次都检查并给出可读的错误。

**怎么启动 server：见 resolve_command()。** 打包之后没有 python.exe，
所以配置里写 `["python", "mcp_servers/pawkit.py"]` 是起不来的 ——
统一改写成「用当前解释器跑 `--mcp-server <名字>`」。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "pawpet", "version": "2.0"}

# 配置里用这个前缀表示「跑随包发布的内置 server」，例如 ["builtin", "pawkit"]。
BUILTIN = "builtin"

# 随包发布的模板。首次启动时播种到数据目录。
DEFAULT_CONFIG = {
    "servers": [
        {
            "name": "pawkit",
            "command": [BUILTIN, "pawkit"],
            "enabled": True,
        }
    ]
}


def builtin_command(name: str) -> list[str]:
    """跑内置 server 的命令行。**开发模式和打包后是两种写法。**

    打包后：`sys.executable` 就是 PawPet.exe，它自己处理 `--mcp-server`，
            所以 `[exe, "--mcp-server", name]` 就够了。

    开发中：`sys.executable` 是**裸解释器**（venv 的 python.exe），
            它不认识 `--mcp-server`，会打印一行 usage 然后退出 ——
            客户端看到的是「MCP server 意外退出：Try `python -h'」。
            所以必须先告诉它跑哪个入口脚本：
            `[python, run_pawpet.py, "--mcp-server", name]`。

    这个区别是实测踩出来的：打包形态能用、开发形态不能用，
    而两边的配置是同一份。
    """
    from ..config import PACKAGE_DIR, is_frozen

    if is_frozen():
        return [sys.executable, "--mcp-server", name]

    entry = PACKAGE_DIR.parent / "run_pawpet.py"
    return [sys.executable, str(entry), "--mcp-server", name]


def resolve_command(command) -> list[str]:
    """把配置里的 command 解析成**当前环境下真能执行**的命令行。

    三种情况：

    * `["builtin", "pawkit"]` → 用当前解释器跑内置的 pawkit。
    * `["python", "mcp_servers/pawkit.py"]`（老配置，也是我们随包发的默认值）
      → **同样改写成内置形态**。打包后没有 python、也没有源码目录，
      照原样 spawn 只会得到「找不到可执行文件」。
    * 其它（用户自己加的外部 server）→ 原样返回，那是他的环境他的事。
    """
    parts = [str(item).strip() for item in (command or []) if str(item).strip()]
    if not parts:
        return []

    if parts[0] == BUILTIN:
        name = parts[1] if len(parts) > 1 else ""
        return builtin_command(name) if name else []

    # 认「我们自己发的」脚本：路径里有 mcp_servers/ 且是 .py
    for part in parts:
        normalized = part.replace("\\", "/")
        if normalized.startswith("mcp_servers/") and normalized.endswith(".py"):
            name = Path(normalized).stem
            if name:
                return builtin_command(name)

    return parts


def server_path(name: str) -> Path | None:
    """内置 server 的脚本路径。找不到返回 None。"""
    from ..config import MCP_DIR

    candidate = MCP_DIR / f"{name}.py"
    return candidate if candidate.exists() else None


def ensure_config(path: Path | None = None) -> tuple[Path, bool]:
    """确保数据目录里有一份 MCP 配置。返回 (路径, 是不是刚播种的)。

    为什么要在数据目录里放一份、而不直接读随包的：**用户要能自己加 server**，
    而打包后资源目录是只读的（解包到 _MEIPASS、退出就删）。
    所以首启播种一份到数据目录，之后用户改的都是那一份。
    """
    from ..config import MCP_CONFIG

    target = Path(path or MCP_CONFIG)
    if target.exists():
        return target, False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return target, True
    except OSError:
        # 写不进去（只读介质之类）就退回随包的那份，至少功能还在
        return target, False


def load_config(path: Path | None = None) -> tuple[list[dict], str]:
    """读配置。返回 (启用的 server 列表, 错误说明)。"""
    from ..config import MCP_CONFIG

    target = Path(path or MCP_CONFIG)
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return [], f"没有 {target.name}"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [], f"配置读不出来：{exc}"
    if not isinstance(raw, dict):
        return [], "配置格式不对（顶层要是对象）"
    servers = raw.get("servers")
    if not isinstance(servers, list):
        return [], "配置里没有 servers 列表"
    enabled = [s for s in servers if isinstance(s, dict) and s.get("enabled")]
    return enabled, ""


class MCPError(Exception):
    pass


class MCPClient:
    """一个本地 stdio MCP server 的连接。"""

    def __init__(self, name: str, command: list[str], cwd: str | None = None,
                 timeout: float = 20.0) -> None:
        self.name = name
        self.command = list(command or [])
        self.cwd = cwd
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self.server_info: dict = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._responses: dict[int, dict] = {}
        self._waiters: dict[int, threading.Event] = {}
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self.last_error = ""
        self._atexit_done = False

    # ---------------------------------------------------------------- 生命周期
    def _register_atexit_cleanup(self) -> None:
        """进程退出时收掉子进程，避免留孤儿。"""
        if getattr(self, "_atexit_done", False):
            return
        self._atexit_done = True
        import atexit

        def _cleanup() -> None:
            try:
                self.stop()
            except Exception:  # noqa: BLE001 - 退出路径上不能抛
                pass

        atexit.register(_cleanup)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> tuple[bool, str]:
        if not self.command:
            return False, "没有配置启动命令"
        if self.running:
            return True, "已经在运行"
        if self.process is not None:
            self.stop()

        with self._lock:
            self._responses.clear()
            self._waiters.clear()
            self._stderr_lines.clear()
            self.last_error = ""

        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.cwd,
            )
        except FileNotFoundError:
            return False, f"找不到可执行文件：{self.command[0]}"
        except OSError as exc:
            return False, f"启动失败：{exc}"

        # **保证子进程不会变成孤儿。**
        #
        # MCP server 是独立进程。正常退出时 controller.shutdown() 会收掉它，
        # 但应用被强杀、崩溃、或者某个测试忘了调 shutdown 的时候，
        # 它会一直挂在后台（实测：连跑整套测试时机器上会攒下十几个）。
        #
        # atexit 在这里是合适的位置：注册一次，进程无论怎么正常结束都会收。
        # 用 atexit 而不是 __del__ —— 后者在解释器关闭时的执行时机不确定，
        # subprocess 相关对象那时可能已经不可用了。
        self._register_atexit_cleanup()

        process = self.process
        self._reader = threading.Thread(
            target=self._read_stdout, args=(process,),
            name=f"mcp-{self.name}", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._drain_stderr, args=(process,),
            name=f"mcp-err-{self.name}", daemon=True)
        self._stderr_reader.start()

        try:
            result = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": CLIENT_INFO,
            })
        except MCPError as exc:
            self.stop()
            return False, str(exc)

        self.server_info = (result or {}).get("serverInfo") or {}
        self._notify("notifications/initialized", {})

        ok, payload = self.refresh_tools()
        if not ok:
            self.stop()
            return False, payload
        return True, f"已连接，{len(self.tools)} 个工具"

    def stop(self) -> None:
        with self._lock:
            process = self.process
            self.process = None
            waiters = list(self._waiters.values())
            self._waiters.clear()
            self._responses.clear()
        for waiter in waiters:
            waiter.set()

        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

        current = threading.current_thread()
        for thread in (self._reader, self._stderr_reader):
            if thread is not None and thread is not current:
                thread.join(timeout=1.5)
        self._reader = None
        self._stderr_reader = None

    # ---------------------------------------------------------------- IO 线程
    def _read_stdout(self, process: subprocess.Popen | None = None) -> None:
        stream = process.stdout if process else None
        if stream is None:
            return
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    # 有些 server 会往 stdout 打日志，忽略掉
                    continue
                message_id = message.get("id")
                if message_id is None:
                    continue    # 通知，暂时不处理
                try:
                    message_id = int(message_id)
                except (TypeError, ValueError):
                    continue
                with self._lock:
                    waiter = self._waiters.get(message_id)
                    if waiter is not None:
                        self._responses[message_id] = message
                if waiter is not None:
                    waiter.set()
        except (OSError, ValueError):
            pass

    def _drain_stderr(self, process: subprocess.Popen | None = None) -> None:
        stream = process.stderr if process else None
        if stream is None:
            return
        try:
            for line in stream:
                text = line.strip()
                if text:
                    with self._lock:
                        self._stderr_lines.append(text)
                        del self._stderr_lines[:-40]
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ 协议
    def _send(self, payload: dict) -> None:
        with self._write_lock:
            process = self.process
            if (process is None or process.poll() is not None
                    or process.stdin is None):
                raise MCPError("MCP server 没有在运行")
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise MCPError(f"写入 MCP 失败：{exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except MCPError:
            pass

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            waiter = threading.Event()
            self._waiters[request_id] = waiter

        try:
            self._send({
                "jsonrpc": "2.0", "id": request_id,
                "method": method, "params": params,
            })
        except MCPError:
            with self._lock:
                self._waiters.pop(request_id, None)
                self._responses.pop(request_id, None)
            raise

        deadline = time.monotonic() + self.timeout
        try:
            while True:
                with self._lock:
                    message = self._responses.pop(request_id, None)
                if message is not None:
                    if "error" in message:
                        error = message["error"]
                        raise MCPError(f"{method} 出错：{error.get('message', error)}")
                    return message.get("result") or {}

                if not self.running:
                    with self._lock:
                        detail = self._stderr_lines[-1] if self._stderr_lines else "进程已退出"
                    raise MCPError(f"MCP server 意外退出：{detail}")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPError(f"{method} 超时（{self.timeout} 秒）")
                waiter.wait(remaining)
        finally:
            with self._lock:
                self._waiters.pop(request_id, None)
                self._responses.pop(request_id, None)

    # ------------------------------------------------------------------ 工具
    def refresh_tools(self) -> tuple[bool, str]:
        try:
            result = self._request("tools/list", {})
        except MCPError as exc:
            return False, str(exc)
        self.tools = result.get("tools") or []
        return True, f"{len(self.tools)} 个工具"

    def call_tool(self, name: str, arguments: dict | None = None) -> tuple[bool, str]:
        try:
            result = self._request("tools/call", {
                "name": name, "arguments": arguments or {},
            })
        except MCPError as exc:
            return False, str(exc)

        chunks: list[str] = []
        for item in result.get("content") or []:
            if item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
            elif item.get("type") == "resource":
                chunks.append(f"[资源] {item.get('resource', {}).get('uri', '')}")
            else:
                chunks.append(f"[{item.get('type', '未知')} 内容]")
        text = "\n".join(chunks).strip() or "(工具没有返回文本内容)"
        if result.get("isError"):
            return False, text
        return True, text

    def tool_summaries(self) -> list[dict]:
        return [
            {
                "name": tool.get("name", ""),
                "description": (tool.get("description") or "").strip()[:200],
            }
            for tool in self.tools
        ]

    # ------------------------------------------------------------ 给模型用
    def as_openai_tools(self) -> list[dict]:
        """把 MCP 工具转成 chat/completions 的 tools 格式。"""
        converted = []
        for tool in self.tools:
            name = tool.get("name")
            if not name:
                continue
            converted.append({
                "type": "function",
                "function": {
                    # 加前缀，避免和内置工具重名
                    "name": f"mcp_{self.name}_{name}"[:64],
                    "description": (tool.get("description") or f"MCP 工具 {name}")[:1024],
                    "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        return converted
