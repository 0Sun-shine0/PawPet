"""最小 MCP stdio 客户端：连接一个本地 MCP server，列出工具并调用工具。"""
from __future__ import annotations
import json, subprocess, threading

class MCPStdioClient:
    def __init__(self, command: list[str]): self.command, self.proc, self._id = command, None, 0
    def start(self):
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        return self._request("initialize", {"protocolVersion":"2024-11-05", "capabilities":{}, "clientInfo":{"name":"paw-pet","version":"0.1"}})
    def _request(self, method, params):
        if not self.proc: return {"ok":False,"error":"MCP 未启动"}
        self._id += 1; msg={"jsonrpc":"2.0","id":self._id,"method":method,"params":params}
        self.proc.stdin.write(json.dumps(msg)+"\n"); self.proc.stdin.flush()
        line=self.proc.stdout.readline()
        return json.loads(line) if line else {"ok":False,"error":"MCP server 未返回结果"}
    def list_tools(self): return self._request("tools/list", {})
    def call_tool(self, name, arguments=None): return self._request("tools/call", {"name":name,"arguments":arguments or {}})
    def stop(self):
        if self.proc and self.proc.poll() is None: self.proc.terminate()
