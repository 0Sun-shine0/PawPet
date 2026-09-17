"""MCP 接线验证：**用真的 MCP server**跑一遍，确认工具真的到得了模型、调得动。

为什么需要这个：`MCPClient.as_openai_tools()` 和 `call_tool()` 原来
**没有任何地方调用** —— 界面显示「已连接，N 个工具」，但那 N 个工具
模型永远看不到、也调不到。mcpStatus 是绿的，功能是假的。
只测「连得上」抓不住这种问题，必须测「调得动」。

用法：
    .venv\\Scripts\\python.exe tools\\mcptest.py
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "mcptest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "mcptest"

PASSED = 0
FAILED: list[str] = []
_keep_alive: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


# --------------------------------------------------------------------------
# 一个最小的 MCP server（stdio，逐行 JSON-RPC）
# --------------------------------------------------------------------------
FAKE_SERVER = textwrap.dedent('''
    """测试用的最小 MCP server：两个工具，一个正常一个报错。"""
    import json, sys

    TOOLS = [
        {
            "name": "echo",
            "description": "把输入原样返回",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        {
            "name": "boom",
            "description": "故意报错的工具",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]

    def reply(rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        rid = req.get("id")
        if rid is None:
            continue                      # 通知，不回
        if method == "initialize":
            reply(rid, {"protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "fake", "version": "1"},
                        "capabilities": {"tools": {}}})
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                reply(rid, {"content": [{"type": "text",
                                         "text": "echo: " + str(args.get("text", ""))}]})
            elif name == "boom":
                reply(rid, {"isError": True,
                            "content": [{"type": "text", "text": "我就是要报错"}]})
            else:
                reply(rid, {"isError": True,
                            "content": [{"type": "text", "text": "没有这个工具"}]})
        else:
            reply(rid, error={"code": -32601, "message": "不认识 " + str(method)})
''')


def main() -> int:
    print("小爪 MCP 接线验证\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Basic")
    _keep_alive.append(QApplication.instance() or QApplication(sys.argv[:1]))

    server_py = SCRATCH / "fake_server.py"
    server_py.write_text(FAKE_SERVER, encoding="utf-8")

    from pawpet.ai.mcp import MCPClient
    from pawpet.ai.tools import TOOL_INDEX, ToolContext, openai_tools
    from pawpet.backend import Backend
    from pawpet.store import Store

    # ---------------------------------------------------------------- 一
    print("=== 一、MCPClient 本身能连、能列、能调 ===")
    client = MCPClient("fake", [sys.executable, str(server_py)], timeout=10)
    ok, message = client.start()
    check("连得上", ok, message)
    if not ok:
        print("\n连不上，后面的测试没法做")
        return 1
    check("列出了 2 个工具", len(client.tools) == 2, str(len(client.tools)))

    ok, text = client.call_tool("echo", {"text": "你好"})
    check("echo 调得动", ok and "你好" in text, text)
    ok, text = client.call_tool("boom", {})
    check("工具报错时 isError 被识别", not ok and "报错" in text, text)

    # ---------------------------------------------------------------- 二
    print("\n=== 二、工具真的进了给模型的清单（这是原来断掉的地方）===")
    plain = openai_tools()
    names = [item["function"]["name"] for item in plain]
    builtin_count = len(names)
    check("不带 extra 时只有内置工具", "mcp_fake_echo" not in names,
          str(builtin_count))

    merged = openai_tools(client.as_openai_tools())
    names = [item["function"]["name"] for item in merged]
    check("带上 extra 后 MCP 工具出现了", "mcp_fake_echo" in names, str(names[-4:]))
    check("带前缀，不会和内置重名",
          all(n in TOOL_INDEX or n.startswith("mcp_") for n in names),
          str([n for n in names if not n.startswith("mcp_") and n not in TOOL_INDEX]))
    check("工具数量 = 内置 + 2", len(merged) == builtin_count + 2,
          f"{builtin_count} + 2 vs {len(merged)}")
    echo_spec = next(i for i in merged if i["function"]["name"] == "mcp_fake_echo")
    check("参数 schema 透传过来了",
          "text" in (echo_spec["function"]["parameters"].get("properties") or {}),
          str(echo_spec["function"]["parameters"])[:80])

    # ---------------------------------------------------------------- 三
    print("\n=== 三、同名的外部工具不能顶掉内置工具 ===")
    clash = [{"type": "function", "function": {
        "name": "read_file", "description": "假的", "parameters": {},
    }}]
    guarded = openai_tools(clash)
    picked = next(i for i in guarded if i["function"]["name"] == "read_file")
    check("内置 read_file 没被顶掉",
          picked["function"]["description"] != "假的",
          picked["function"]["description"][:40])
    check("数量没变多", len(guarded) == builtin_count, str(len(guarded)))

    # ---------------------------------------------------------------- 四
    print("\n=== 四、ToolContext 能把 mcp_ 调用路由回去 ===")
    real = ROOT / "pet_data.json"
    store = Store(SCRATCH / "p.json", SCRATCH / "p.bak.json")
    store.load()
    # **关掉自动连接，这个用例要的是受控环境。**
    #
    # Backend 启动时会自动连上随包的 pawkit（8 个工具），那样下面
    # 「我注册的这个 client 被路由到了吗」就测不准了 —— 计数里混进了
    # 真实 server 的工具，断言变成对总数的猜测。
    #
    # 分工：这个脚本测**接线**（用假 server，环境受控）；
    # 「装了就能用」由 mcpwiringtest.py 负责（那里就该让真的连上）。
    store.settings["ai_mcp_enabled"] = False
    backend = Backend(store)

    from pawpet.ai.actions import AuditLog, DesktopActions
    from pawpet.ai.vision import ScreenCapture

    actions = DesktopActions(AuditLog())
    actions.level = "confirm"
    context = ToolContext(ScreenCapture(), actions, store, backend)
    backend.ai._mcp_clients.append(client)     # 假装是 controller 连上的

    check("context 看得到这个 client", len(context.mcp_clients()) == 1,
          str(len(context.mcp_clients())))
    check("mcp_tools() 能拼出工具", len(context.mcp_tools()) == 2,
          str(len(context.mcp_tools())))

    ok, text, _bundle = context.execute("mcp_fake_echo", {"text": "路由测试"})
    check("execute() 能路由到真 server", ok and "路由测试" in text, text)

    ok, text, _bundle = context.execute("mcp_fake_boom", {})
    check("MCP 工具报错会如实返回失败", not ok and "报错" in text, text)

    ok, text, _bundle = context.execute("mcp_nosuch_echo", {})
    check("server 不存在时给可读提示", not ok and "没有名为" in text, text)

    # 前缀匹配要取最长的（server 名有重叠时不能路由错）
    client_short = MCPClient("fake", [sys.executable, str(server_py)], timeout=10)
    client_short.name = "fa"        # 故意造一个前缀重叠的
    client_short.start()
    backend.ai._mcp_clients.append(client_short)
    ok, text, _bundle = context.execute("mcp_fake_echo", {"text": "最长匹配"})
    check("前缀重叠时仍然路由到对的那个（取最长前缀）",
          ok and "最长匹配" in text, text)
    client_short.stop()

    # ---------------------------------------------------------------- 五
    print("\n=== 五、没连接时不能假装有工具 ===")
    backend.ai._mcp_clients.clear()
    check("清空后 mcp_tools() 返回空", context.mcp_tools() == [],
          str(context.mcp_tools()))
    ok, text, _bundle = context.execute("mcp_fake_echo", {"text": "x"})
    check("调用时给「没连着」的提示", not ok and "没有名为" in text, text)

    # 断开的 client 不该贡献工具
    dead = MCPClient("dead", [sys.executable, str(server_py)], timeout=10)
    dead.start()
    dead.stop()
    backend.ai._mcp_clients.append(dead)
    check("已停掉的 client 不贡献工具", context.mcp_tools() == [],
          str(context.mcp_tools()))

    # ---------------------------------------------------------------- 六
    print("\n=== 六、内置工具不受影响 ===")
    # 用「至少」而不是「等于」：工具表是会增长的（加了定制配色那三个之后
    # 从 37 变成 40）。这条断言要守的是「MCP 的接线没有把内置工具弄丢或弄乱」，
    # 不是「内置工具永远恰好 N 个」—— 卡死具体数字只会让每次加工具都要改测试。
    check("内置工具都在（没被 MCP 的接线弄丢）", len(TOOL_INDEX) >= 37,
          str(len(TOOL_INDEX)))
    ok, text, _bundle = context.execute("screen_info", {})
    check("内置工具照常能调", ok, text[:60])
    ok, text, _bundle = context.execute("根本没有这个工具", {})
    check("不认识的名字给可读错误", not ok and "没有名为" in text, text)

    # ---------------------------------------------------------------- 七
    #
    # 用**项目自带的** pawkit server 再跑一遍。
    #
    # 前面那个 fake server 只证明「接线是对的」；pawkit 是真正要发给用户用的
    # 东西，495 行、8 个工具，而且**之前一次都没真连过**。实测发现它的
    # `time_until` 说明里写着能问「到 9:30 还有多久」，实现却只认完整日期 ——
    # 模型照着说明传参会被拒，然后跟用户说「这个工具不支持」。
    # 说明和实现不一致比少个功能更糟，因为模型会信说明。
    print("\n=== 七、项目自带的 pawkit 真连一遍 ===")
    pawkit = ROOT / "mcp_servers" / "pawkit.py"
    check("pawkit.py 存在", pawkit.exists(), "找不到 mcp_servers/pawkit.py")
    if pawkit.exists():
        pk = MCPClient("pawkit", [sys.executable, str(pawkit)],
                       cwd=str(ROOT), timeout=20)
        ok, message = pk.start()
        check("pawkit 连得上", ok, message)
        if ok:
            check("pawkit 提供了工具", len(pk.tools) >= 6, str(len(pk.tools)))
            names = [t["name"] for t in pk.tool_summaries()]
            print(f"     工具：{', '.join(names)}")

            # 每个工具都要能真的调一次 —— 光「列得出来」不算数，
            # 前面那个 fake server 已经证明「能列」和「能调」是两回事。
            cases = [
                ("now", {}),
                # 纯时间是这次修的 bug 的回归：说明里承诺支持，实现原来不认
                ("time_until", {"target": "18:00"}),
                ("time_until", {"target": "2026-10-01"}),
                ("time_until", {"target": "10-01"}),
                ("ts_convert", {"value": "1789541258"}),
                ("sys_status", {}),
                ("calc", {"expression": "2+3*4"}),
                ("gen_password", {"length": 12}),
                ("decide", {"mode": "pick", "options": ["甲", "乙", "丙"]}),
                ("decide", {"mode": "coin"}),
            ]
            for tool_name, args in cases:
                ok2, text = pk.call_tool(tool_name, args)
                label = f"{tool_name}({','.join(args) or '-'})"
                check(f"pawkit.{label} 调得动",
                      ok2 and bool((text or "").strip()), (text or "")[:70])

            ok2, text = pk.call_tool("file_hash",
                                     {"path": str(ROOT / "run_pawpet.py")})
            check("file_hash 能算本地文件", ok2 and "sha256" in text.lower(),
                  text[:60])

            pk.stop()

    client.stop()
    backend.shutdown()

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
