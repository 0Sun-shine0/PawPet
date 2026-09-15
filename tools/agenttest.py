"""端到端联调：起一个假的 OpenAI 兼容服务，让 Agent 真的跑一遍完整流程。

这能验证真实代码路径：HTTP 请求 → 解析 tool_calls → 截图 → 执行 → 回灌结果
→ 再决策 → 最终回答。全程不联网、不花钱。

用法：
    .venv\\Scripts\\python.exe tools\\agenttest.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


# --------------------------------------------------------------------------
# 假模型服务器：按脚本依次返回工具调用
# --------------------------------------------------------------------------
class FakeModel:
    """记录收到的请求，并按预设脚本返回响应。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def next_reply(self, request_body: dict) -> dict:
        with self.lock:
            self.requests.append(request_body)
            if self.script:
                reply = self.script.pop(0)
            else:
                reply = {"content": "任务完成。"}
        return {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": request_body.get("model", "fake-model"),
            "choices": [{
                "index": 0,
                "message": reply,
                "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
    }


def make_server(model: FakeModel) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):    # 静音
            pass

        def do_GET(self):
            if self.path.endswith("/models"):
                payload = {"data": [{"id": "fake-vision-model"}, {"id": "fake-text-model"}]}
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            try:
                request_body = json.loads(raw)
            except json.JSONDecodeError:
                request_body = {}
            reply = model.next_reply(request_body)
            body = json.dumps(reply, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


# --------------------------------------------------------------------------
def main() -> int:
    import shutil

    print("小爪 Agent 端到端联调")

    from pawpet.ai.actions import LEVEL_CONFIRM, AuditLog, DesktopActions
    from pawpet.ai.agent import AgentRunner, StepEvent
    from pawpet.ai.client import AIClient
    from pawpet.ai.tools import ToolContext
    from pawpet.ai.vision import ScreenCapture
    from pawpet.store import Store

    scratch = ROOT / ".cache" / "agenttest"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()

    # ================================================== 场景一：完整链路
    print("\n=== 场景一：让 AI 看屏幕 + 记待办（只读工具，无需确认）===")

    # 连通性和列表用独立的服务，免得消耗掉正式跑批的脚本队列
    probe_model = FakeModel([])
    probe_server, probe_url = make_server(probe_model)
    try:
        probe_client = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                                base_url=probe_url, timeout=20)
        ok, message = probe_client.test_connection()
        check("客户端能连上服务", ok, message)

        ok, models = probe_client.list_models()
        check("能拉取模型列表", ok and "fake-vision-model" in models, str(models)[:60])
    finally:
        probe_server.shutdown()

    # 正式跑批：干净的脚本队列
    model = FakeModel([
        tool_call("c1", "screenshot", {"monitor": 1}),
        tool_call("c2", "add_task", {"text": "回复李总的邮件", "priority": 1}),
        {"content": "我看过屏幕了，已经帮你把「回复李总的邮件」加进待办，标为重要。"},
    ])
    server, base_url = make_server(model)
    print(f"  假模型服务：{base_url}")

    try:
        client = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                          base_url=base_url, timeout=20)

        capture = ScreenCapture()
        actions = DesktopActions(AuditLog())
        actions.level = LEVEL_CONFIRM
        context = ToolContext(capture, actions, store)

        events: list[StepEvent] = []
        approvals: list[str] = []
        statuses: list[str] = []

        class Recorder:
            def on_status(self, text): statuses.append(text)
            def on_event(self, event): events.append(event)
            def on_image(self, png, note): pass
            def on_finished(self, text): pass
            def on_error(self, text): events.append(StepEvent(kind="error", text=text))
            def request_approval(self, request):
                approvals.append(request.tool_name)
                return True

        recorder = Recorder()
        runner = AgentRunner(client, context, actions, recorder)
        final = runner.run("看看我屏幕上是什么，然后记一下要回复李总的邮件")

        check("跑完没有报错", not any(e.kind == "error" for e in events),
              str([e.text for e in events if e.kind == "error"]))
        check("拿到了最终回答", "待办" in final, final[:70])
        check("只读工具没触发审批", len(approvals) == 0, f"实际 {approvals}")

        tool_events = [e for e in events if e.kind == "tool"]
        check("执行了 2 个工具", len(tool_events) == 2,
              f"实际 {len(tool_events)}：{[e.tool for e in tool_events]}")
        check("截图工具带回了图片",
              any(e.tool == "screenshot" and len(e.image_png) > 1000 for e in tool_events),
              str([(e.tool, len(e.image_png)) for e in tool_events]))

        # 坐标说明必须随截图一起回灌给模型，这直接决定它点击准不准
        notes_sent = [
            part.get("text", "")
            for request in model.requests
            for message in (request.get("messages") or [])
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part.get("type") == "text"
        ]
        check("坐标说明随截图一起发给了模型",
              any("截图信息" in text for text in notes_sent),
              str([t[:50] for t in notes_sent if "截图" in t])[:120])
        check("待办真的写进数据了",
              any(t["text"] == "回复李总的邮件" for t in store.tasks),
              str([t["text"] for t in store.tasks]))
        check("优先级也带过去了",
              any(t["text"] == "回复李总的邮件" and t["priority"] == 1 for t in store.tasks))

        # 关键：截图必须真的作为图片消息发给了模型
        image_messages = 0
        for request in model.requests:
            for message in request.get("messages") or []:
                content = message.get("content")
                if isinstance(content, list) and any(
                        part.get("type") == "image_url" for part in content):
                    image_messages += 1
        check("截图作为图片消息回灌给了模型", image_messages >= 1, f"实际 {image_messages}")

        # 工具结果也要回灌
        tool_messages = sum(
            1 for request in model.requests
            for message in (request.get("messages") or [])
            if message.get("role") == "tool"
        )
        check("工具执行结果回灌给了模型", tool_messages >= 2, f"实际 {tool_messages}")

        # 每一次请求都要带工具定义，否则模型没法调工具
        check("每个请求都带了工具定义",
              all(request.get("tools") for request in model.requests),
              f"{len(model.requests)} 个请求，"
              f"{sum(1 for r in model.requests if not r.get('tools'))} 个缺 tools")
        check("工具定义数量与本地一致",
              all(len(request.get("tools") or []) >= 15 for request in model.requests))

        # ============================================ 场景二：需要确认的动作
        print("\n=== 场景二：点击操作 —— 用户逐次确认 ===")

        model2 = FakeModel([
            tool_call("d1", "click", {"x": 640, "y": 360}),
            {"content": "已经点好了。"},
        ])
        server2, base2 = make_server(model2)
        client2 = AIClient(api_key="sk-fake", model="fake", base_url=base2, timeout=20)

        store2 = Store(scratch / "s2.json", scratch / "s2.bak.json")
        store2.load()
        actions2 = DesktopActions(AuditLog())
        actions2.level = LEVEL_CONFIRM
        context2 = ToolContext(capture, actions2, store2)

        approvals2: list[dict] = []
        events2: list[StepEvent] = []

        class Recorder2:
            def on_status(self, text): pass
            def on_event(self, event): events2.append(event)
            def on_image(self, png, note): pass
            def on_finished(self, text): pass
            def on_error(self, text): events2.append(StepEvent(kind="error", text=text))
            def request_approval(self, request):
                approvals2.append({"tool": request.tool_name, "risk": request.risk,
                                   "summary": request.summary})
                return True

        # 先截一张图，让坐标换算有参照
        context2.last_shot = capture.grab(monitor=1 if len(capture.monitors()) > 1 else 0)
        runner2 = AgentRunner(client2, context2, actions2, Recorder2())
        runner2.run("帮我点一下屏幕中间")

        check("点击触发了审批请求", len(approvals2) == 1, f"实际 {len(approvals2)}")
        if approvals2:
            check("审批信息含工具名", approvals2[0]["tool"] == "click")
            check("审批信息标了风险等级", approvals2[0]["risk"] == "confirm")
            check("审批信息有人话说明", "点击" in approvals2[0]["summary"],
                  approvals2[0]["summary"])

        check("点击事件被记录为成功",
              any(e.kind == "tool" and e.tool == "click" and e.ok for e in events2))
        check("审计日志里有记录", len(actions2.audit.recent(10)) >= 1,
              f"实际 {len(actions2.audit.recent(10))}")
        check("审计记录了用户批准",
              any(a["approved"] == "user" for a in actions2.audit.recent(10)),
              str([a["approved"] for a in actions2.audit.recent(10)]))

        # ============================================ 场景三：用户拒绝
        print("\n=== 场景三：用户拒绝操作 ===")

        model3 = FakeModel([
            tool_call("e1", "run_command", {"command": "echo hello"}),
            {"content": "好的，我不执行了。"},
        ])
        server3, base3 = make_server(model3)
        client3 = AIClient(api_key="sk-fake", model="fake", base_url=base3, timeout=20)

        store3 = Store(scratch / "s3.json", scratch / "s3.bak.json")
        store3.load()
        actions3 = DesktopActions(AuditLog())
        context3 = ToolContext(capture, actions3, store3)
        events3: list[StepEvent] = []
        denied: list[str] = []

        class Recorder3:
            def on_status(self, text): pass
            def on_event(self, event): events3.append(event)
            def on_image(self, png, note): pass
            def on_finished(self, text): pass
            def on_error(self, text): events3.append(StepEvent(kind="error", text=text))
            def request_approval(self, request):
                denied.append(request.tool_name)
                return False

        runner3 = AgentRunner(client3, context3, actions3, Recorder3())
        runner3.run("执行一下 echo hello")

        check("高危命令请求了审批", denied == ["run_command"], f"实际 {denied}")
        check("拒绝后没有执行成功的事件",
              not any(e.kind == "tool" and e.ok for e in events3))
        check("拒绝被写进审计（denied）",
              any(a["approved"] == "denied" for a in actions3.audit.recent(10)),
              str([a["approved"] for a in actions3.audit.recent(10)]))
        check("模型收到了拒绝说明",
              any(message.get("role") == "tool" and "拒绝" in str(message.get("content"))
                  for request in model3.requests
                  for message in (request.get("messages") or [])),
              "模型历史里应包含拒绝原因")

        # ============================================ 场景四：HTTP 错误处理
        print("\n=== 场景四：服务端错误 ===")
        bad_client = AIClient(api_key="sk-fake", model="fake",
                              base_url="http://127.0.0.1:1/v1", timeout=3)
        ok, message = bad_client.test_connection()
        check("连接失败时给出可读错误", ok is False and len(message) > 5, message[:70])

        # 401 的提示
        class AuthHandler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                # 必须把请求体读完再回，否则客户端会碰到
                # ConnectionAbortedError（连接被重置），测不到 401 分支
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                body = b'{"error":{"message":"invalid api key"}}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        auth_server = HTTPServer(("127.0.0.1", 0), AuthHandler)
        threading.Thread(target=auth_server.serve_forever, daemon=True).start()
        auth_client = AIClient(api_key="sk-bad", model="m",
                               base_url=f"http://127.0.0.1:{auth_server.server_port}/v1")
        ok, message = auth_client.test_connection()
        check("401 错误提示了 Key 问题", ok is False and "Key" in message, message[:80])
        auth_server.shutdown()

        # ============================================ 场景五：界面元素工具
        # 这是 P0 功能的端到端验证：AI 是否真的能通过 UIA 拿到控件信息，
        # 而不是只会看图猜坐标
        print("\n=== 场景五：界面元素（UI Automation）工具 ===")

        model5 = FakeModel([
            tool_call("u1", "ui_windows", {}),
            tool_call("u2", "ui_element_at", {"x": 960, "y": 540}),
            tool_call("u3", "ui_focused", {}),
            {"content": "我看过界面上的控件了。"},
        ])
        server5, base5 = make_server(model5)
        client5 = AIClient(api_key="sk-fake", model="fake", base_url=base5, timeout=30)

        store5 = Store(scratch / "s5.json", scratch / "s5.bak.json")
        store5.load()
        actions5 = DesktopActions(AuditLog())
        context5 = ToolContext(capture, actions5, store5)

        events5: list[StepEvent] = []
        approvals5: list[str] = []

        class Recorder5:
            def on_status(self, text): pass
            def on_event(self, event): events5.append(event)
            def on_image(self, png, note): pass
            def on_finished(self, text): pass
            def on_error(self, text): events5.append(StepEvent(kind="error", text=text))
            def request_approval(self, request):
                approvals5.append(request.tool_name)
                return True

        runner5 = AgentRunner(client5, context5, actions5, Recorder5())
        runner5.run("看看当前有哪些窗口，屏幕中间是什么控件")

        ui_events = [e for e in events5 if e.kind == "tool" and e.tool.startswith("ui_")]
        check("AI 调用了界面元素工具", len(ui_events) >= 3,
              f"实际 {len(ui_events)} 个：{[e.tool for e in ui_events]}")
        check("界面元素查询不需要审批（只读）", len(approvals5) == 0,
              f"实际 {approvals5}")
        check("窗口列表返回了内容",
              any(e.tool == "ui_windows" and e.ok and "窗口" in (e.detail or "")
                  for e in ui_events),
              str([(e.tool, (e.detail or '')[:40]) for e in ui_events]))
        check("坐标查询返回了控件信息",
              any(e.tool == "ui_element_at" and e.ok
                  and ("控件" in (e.detail or "") or "没有读到" in (e.detail or ""))
                  for e in ui_events),
              str([(e.tool, (e.detail or '')[:50]) for e in ui_events]))
        check("界面工具没让 agent 卡死（全部及时返回）",
              all(e.seconds < 15 for e in ui_events),
              str([(e.tool, round(e.seconds, 1)) for e in ui_events]))

        # 关键：系统提示词里必须引导模型优先用 UIA 而不是猜坐标
        from pawpet.ai.agent import SYSTEM_PROMPT
        check("系统提示词要求优先用界面元素接口",
              "ui_element_at" in SYSTEM_PROMPT and "ui_controls" in SYSTEM_PROMPT,
              "提示词里没提到这些工具")
        check("系统提示词说明了控件读不到时怎么办",
              "读不到" in SYSTEM_PROMPT or "自绘" in SYSTEM_PROMPT)

        # 工具定义里必须带上这些
        from pawpet.ai.tools import TOOL_INDEX
        for name in ("ui_element_at", "ui_focused", "ui_windows",
                     "ui_controls", "ui_click", "ui_set_text"):
            check(f"工具 {name} 已注册", name in TOOL_INDEX)

    finally:
        server.shutdown()
        try:
            server2.shutdown()
            server3.shutdown()
        except NameError:
            pass
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
