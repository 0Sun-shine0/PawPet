"""MCP（Model Context Protocol）stdio 客户端。

比旧版那个「读一行就当结果」的实现靠谱一些，修掉了三个实际问题：

1. MCP server 会往 stdout 发通知和日志，读到的不一定是本次请求的响应 ——
   这里按 id 匹配，把通知单独放一边。
2. 同步 readline 一旦 server 不吭声就会永久卡死 —— 这里用后台读线程 + 超时。
3. 进程退出/管道断裂没有兜底 —— 这里每次都检查并给出可读的错误。
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "pawpet", "version": "2.0"}


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
        self._event = threading.Event()
        self._reader: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self.last_error = ""

    # ---------------------------------------------------------------- 生命周期
    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> tuple[bool, str]:
        if not self.command:
            return False, "没有配置启动命令"
        if self.running:
            return True, "已经在运行"

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

        self._reader = threading.Thread(target=self._read_stdout, name=f"mcp-{self.name}", daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, name=f"mcp-err-{self.name}", daemon=True).start()

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
            return False, payload
        return True, f"已连接，{len(self.tools)} 个工具"

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        except OSError:
            pass

    # ---------------------------------------------------------------- IO 线程
    def _read_stdout(self) -> None:
        stream = self.process.stdout if self.process else None
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
                with self._lock:
                    self._responses[int(message_id)] = message
                self._event.set()
        except (OSError, ValueError):
            pass

    def _drain_stderr(self) -> None:
        stream = self.process.stderr if self.process else None
        if stream is None:
            return
        try:
            for line in stream:
                text = line.strip()
                if text:
                    self._stderr_lines.append(text)
                    del self._stderr_lines[:-40]
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ 协议
    def _send(self, payload: dict) -> None:
        if not self.running or self.process is None or self.process.stdin is None:
            raise MCPError("MCP server 没有在运行")
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"写入 MCP 失败：{exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except MCPError:
            pass

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._event.clear()
        self._send({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        })

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            with self._lock:
                message = self._responses.pop(request_id, None)
            if message is not None:
                if "error" in message:
                    error = message["error"]
                    raise MCPError(f"{method} 出错：{error.get('message', error)}")
                return message.get("result") or {}

            if not self.running:
                detail = self._stderr_lines[-1] if self._stderr_lines else "进程已退出"
                raise MCPError(f"MCP server 意外退出：{detail}")

            self._event.wait(0.15)
            self._event.clear()

        raise MCPError(f"{method} 超时（{self.timeout} 秒）")

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
