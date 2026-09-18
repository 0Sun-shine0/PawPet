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
        # 坐标查询的**三种诚实结果都算通过**：
        #   读到控件 / 那里没东西 / 读不动而及时放弃
        #
        # 为什么接受「超时」：这里查的是 (960, 540) —— 屏幕正中央。
        # 那儿是哪个程序就查哪个，而某些程序的 UIA 提供程序响应很慢
        # （用户桌面上开着十几个窗口时尤其明显）。超时之后放弃正是
        # uia.py 的设计：**AI 永远不会因为一个坏窗口彻底卡死**。
        #
        # 原来只认前两种，于是断言退化成「用户的桌面恰好配合」——
        # 实测同一个用例 8 分钟前还是绿的、之后就红了，而代码一行没动。
        # 真正该守住的不变量是下一条：所有调用都要及时返回，不许卡死。
        check("坐标查询给出了结果（三种诚实结果都算）",
              any(e.tool == "ui_element_at" and
                  ("控件" in (e.detail or "")
                   or "没有读到" in (e.detail or "")
                   or "超时" in (e.detail or ""))
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

        # ============================================ 场景六：每轮步数上限
        print("\n=== 场景六：每轮最多执行步数 ===")

        from pawpet.ai.agent import (
            DEFAULT_MAX_STEPS,
            MAX_MAX_STEPS,
            MIN_MAX_STEPS,
            STEP_PRESETS,
            clamp_max_steps,
            system_prompt,
        )

        check("默认步数是 20", DEFAULT_MAX_STEPS == 20, str(DEFAULT_MAX_STEPS))
        check("合法值原样返回", clamp_max_steps(30) == 30, str(clamp_max_steps(30)))
        check("低于下限被抬到 5", clamp_max_steps(1) == MIN_MAX_STEPS,
              str(clamp_max_steps(1)))
        check("高于上限被压到 100", clamp_max_steps(9999) == MAX_MAX_STEPS,
              str(clamp_max_steps(9999)))
        check("脏值退回默认值",
              clamp_max_steps(None) == DEFAULT_MAX_STEPS
              and clamp_max_steps("abc") == DEFAULT_MAX_STEPS
              and clamp_max_steps(12.7) == 12,
              str([clamp_max_steps(None), clamp_max_steps("abc"), clamp_max_steps(12.7)]))
        check("下拉选项都在合法范围内且递增",
              all(MIN_MAX_STEPS <= v <= MAX_MAX_STEPS for v in STEP_PRESETS)
              and list(STEP_PRESETS) == sorted(STEP_PRESETS)
              and DEFAULT_MAX_STEPS in STEP_PRESETS,
              str(STEP_PRESETS))
        check("提示词里写明了本轮预算",
              "7 步" in system_prompt(7), system_prompt(7)[-90:])
        from pawpet.ai.agent import SYSTEM_PROMPT as BASE_PROMPT
        check("基础提示词没有被预算句污染", "步数预算" not in BASE_PROMPT)

        # 让假模型永远只回工具调用，逼 agent 顶到上限
        endless_model = FakeModel([tool_call(f"n{i}", "ui_windows", {})
                                   for i in range(200)])
        server4, url4 = make_server(endless_model)
        try:
            client4 = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                               base_url=url4, timeout=20)
            actions4 = DesktopActions(AuditLog())
            actions4.level = LEVEL_CONFIRM
            context4 = ToolContext(ScreenCapture(), actions4, store)

            statuses4: list[str] = []
            events4: list[StepEvent] = []

            class Recorder4:
                def on_status(self, text): statuses4.append(text)
                def on_event(self, event): events4.append(event)
                def on_image(self, png, note): pass
                def on_finished(self, text): pass
                def on_error(self, text): events4.append(StepEvent(kind="error", text=text))
                def request_approval(self, request): return True

            runner4 = AgentRunner(client4, context4, actions4, Recorder4(), max_steps=5)
            final4 = runner4.run("一直查窗口列表别停")

            check("runner 记住了设置的上限", runner4.max_steps == 5,
                  str(runner4.max_steps))
            check("真的在第 5 步停下（刚好 5 次请求）", len(endless_model.requests) == 5,
                  f"实际 {len(endless_model.requests)} 次")
            check("停止原因报的是 5 步而不是写死的 20",
                  any("5 步" in s for s in statuses4), str(statuses4[-3:]))
            check("最终回答里带上了真实步数", "5 步" in final4, final4[:80])
            check("没跑满默认的 20 步", len(endless_model.requests) < DEFAULT_MAX_STEPS,
                  str(len(endless_model.requests)))

            # 换个大一点的预算，确认上限真的会跟着变
            endless_model2 = FakeModel([tool_call(f"m{i}", "ui_windows", {})
                                        for i in range(200)])
            server5, url5 = make_server(endless_model2)
            try:
                client5 = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                                   base_url=url5, timeout=20)

                class Recorder5b:
                    def on_status(self, text): pass
                    def on_event(self, event): pass
                    def on_image(self, png, note): pass
                    def on_finished(self, text): pass
                    def on_error(self, text): pass
                    def request_approval(self, request): return True

                runner5b = AgentRunner(client5, context4, actions4, Recorder5b(),
                                       max_steps=50)
                runner5b.run("一直查窗口列表别停")
                check("上限调大后确实跑得更远（50 次请求）",
                      len(endless_model2.requests) == 50,
                      f"实际 {len(endless_model2.requests)} 次")
            finally:
                server5.shutdown()
        finally:
            server4.shutdown()

        # ============================================ 场景七：设置链路
        print("\n=== 场景七：设置从界面到 Agent 是通的 ===")

        check("store 里带了这个设置项",
              "ai_max_steps" in store.settings, str(sorted(store.settings)[:6]))
        check("新装用户拿到默认值 20",
              store.settings.get("ai_max_steps") == DEFAULT_MAX_STEPS,
              str(store.settings.get("ai_max_steps")))

        from pawpet.ai.controller import AiController

        controller = AiController(store)
        try:
            check("控制器暴露了 maxSteps", controller.maxSteps == 20,
                  str(controller.maxSteps))
            check("选项列表非空且带 label",
                  len(controller.stepOptions) >= 3
                  and all(o.get("label") for o in controller.stepOptions),
                  str(controller.stepOptions)[:90])
            check("提示文案里有范围和一步的含义",
                  "一步" in controller.maxStepsHint and "100" in controller.maxStepsHint,
                  controller.maxStepsHint[:80])

            controller.maxSteps = 50
            check("改设置能生效", controller.maxSteps == 50, str(controller.maxSteps))
            check("改设置会落盘", store.settings.get("ai_max_steps") == 50,
                  str(store.settings.get("ai_max_steps")))
            controller.maxSteps = 9999
            check("越界值会被夹住", controller.maxSteps == MAX_MAX_STEPS,
                  str(controller.maxSteps))

            check("历史里的系统提示词也带上了预算",
                  f"{MAX_MAX_STEPS} 步" in controller._build_history()[0]["content"],
                  controller._build_history()[0]["content"][-60:])
        finally:
            controller.maxSteps = DEFAULT_MAX_STEPS
            controller.shutdown()
            store.save()

        # ============================================ 场景八：跨会话记忆
        print("\n=== 场景八：跨会话记忆 ===")

        from pawpet.ai.memory import Memory, MemoryBook, format_for_prompt

        # 先塞一条「上次会话」留下的记忆
        book = MemoryBook(store)
        book.add_fact("他一直用 WPS，不要给他推荐 Office", "workflow", 3)

        # 第一轮：模型记住一件新事
        mem_model = FakeModel([
            tool_call("m1", "remember", {
                "text": "他喜欢回答控制在三行以内",
                "category": "preference",
                "confidence": 3,
            }),
            {"content": "好，我记住了。"},
        ])
        mem_server, mem_url = make_server(mem_model)
        try:
            mem_client = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                                  base_url=mem_url, timeout=20)
            mem_actions = DesktopActions(AuditLog())
            mem_actions.level = LEVEL_CONFIRM
            mem_context = ToolContext(ScreenCapture(), mem_actions, store)

            mem_events: list[StepEvent] = []

            class MemRecorder:
                def on_status(self, text): pass
                def on_event(self, event): mem_events.append(event)
                def on_image(self, png, note): pass
                def on_finished(self, text): pass
                def on_error(self, text): mem_events.append(StepEvent(kind="error", text=text))
                def request_approval(self, request): return True

            runner_m = AgentRunner(mem_client, mem_context, mem_actions, MemRecorder(),
                                   max_steps=5, memory_text=format_for_prompt(book.load()))
            runner_m.run("以后回答短一点")

            remembered = Memory.from_dict(store.memory)
            check("模型通过 remember 记下了新东西",
                  any("三行以内" in f.text for f in remembered.facts),
                  str([f.text for f in remembered.facts]))
            check("记住的内容带上了置信度",
                  any("三行以内" in f.text and f.confidence == 3
                      for f in remembered.facts),
                  str([(f.text, f.confidence) for f in remembered.facts]))
            check("分类也存下来了",
                  any("三行以内" in f.text and f.category == "preference"
                      for f in remembered.facts),
                  str([(f.text, f.category) for f in remembered.facts]))

            # 关键：第一次请求的系统提示词里就该有「上一次」的记忆
            first_prompt = mem_model.requests[0]["messages"][0]["content"]
            check("上一次会话的记忆进了系统提示词",
                  "WPS" in first_prompt, first_prompt[-200:])
            check("提示词里带上了使用记忆的规矩",
                  "remember" in first_prompt and "跨会话记忆" in first_prompt)
            check("记忆放在 system 消息里而不是历史里",
                  mem_model.requests[0]["messages"][0]["role"] == "system")

            # 记忆工具是只读风险，不该弹审批
            remember_events = [e for e in mem_events
                               if e.kind == "tool" and e.tool == "remember"]
            check("remember 不需要审批（只读写自己的数据）",
                  len(remember_events) == 1 and remember_events[0].ok,
                  str([(e.tool, e.ok) for e in mem_events]))
        finally:
            mem_server.shutdown()

        # 第二轮：新开的 runner 必须看到上一轮记住的内容（跨会话的核心）
        again = format_for_prompt(MemoryBook(store).load())
        check("新一轮渲染出的记忆包含刚记住的内容",
              "三行以内" in again, again[:200])
        check("也还包含更早的那条", "WPS" in again, again[:200])

        # 忘掉
        forget_model = FakeModel([
            tool_call("f1", "forget_memory", {"text": "三行以内"}),
            {"content": "已经忘掉了。"},
        ])
        forget_server, forget_url = make_server(forget_model)
        try:
            forget_client = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                                     base_url=forget_url, timeout=20)
            forget_actions = DesktopActions(AuditLog())
            forget_actions.level = LEVEL_CONFIRM
            forget_context = ToolContext(ScreenCapture(), forget_actions, store)

            class ForgetRecorder:
                def on_status(self, text): pass
                def on_event(self, event): pass
                def on_image(self, png, note): pass
                def on_finished(self, text): pass
                def on_error(self, text): pass
                def request_approval(self, request): return True

            runner_f = AgentRunner(forget_client, forget_context, forget_actions,
                                   ForgetRecorder(), max_steps=5,
                                   memory_text=format_for_prompt(book.load()))
            runner_f.run("别记着那条了")
            after_forget = Memory.from_dict(store.memory)
            check("forget_memory 真的删掉了",
                  not any("三行以内" in f.text for f in after_forget.facts),
                  str([f.text for f in after_forget.facts]))
            check("别的记忆没被误删",
                  any("WPS" in f.text for f in after_forget.facts),
                  str([f.text for f in after_forget.facts]))
        finally:
            forget_server.shutdown()

        # 任务进度：写进去、读出来
        task_model = FakeModel([
            tool_call("t1", "note_task_state", {
                "text": "给客户做报价单", "status": "open", "next_step": "先确认税率",
            }),
            {"content": "记下了，下次接着做。"},
        ])
        task_server, task_url = make_server(task_model)
        try:
            task_client = AIClient(api_key="sk-fake-key", model="fake-vision-model",
                                   base_url=task_url, timeout=20)
            task_actions = DesktopActions(AuditLog())
            task_actions.level = LEVEL_CONFIRM
            task_context = ToolContext(ScreenCapture(), task_actions, store)

            class TaskRecorder:
                def on_status(self, text): pass
                def on_event(self, event): pass
                def on_image(self, png, note): pass
                def on_finished(self, text): pass
                def on_error(self, text): pass
                def request_approval(self, request): return True

            runner_t = AgentRunner(task_client, task_context, task_actions,
                                   TaskRecorder(), max_steps=5, memory_text="")
            runner_t.run("这个报价单先放着，回头弄")

            progress = format_for_prompt(MemoryBook(store).load())
            check("任务进度进了下次的提示词",
                  "报价单" in progress and "税率" in progress, progress[:300])
            check("提示词里说明了这是没做完的事",
                  "没做完" in progress, progress[:300])
        finally:
            task_server.shutdown()

        # ================================ 场景九：P0 —— 完全自动档也免不了
        #
        # 原来那条漏洞链：造草稿是 READ（不问）→ 安装是 CONFIRM（full 下不问）
        # → 调用时合成 CONFIRM（full 下不问）→ 子进程跑任意 Python。
        # 全程一张卡片都没有，而 install_extension 的说明里明明写着
        # 「会弹一张卡片让用户确认」。承诺和实现对不上，就是缺陷。
        #
        # 修法是把这条链路整段挂到 Risk.CRITICAL 上，而 CRITICAL 在
        # needs_approval 的第一行就 return True —— 任何档位都拦不住它。
        # 这一组就是在**真实 runner** 上验证那件事，不只是验常量。
        print("\n=== 场景九：完全自动档下「装工具 / 跑自定义代码」仍然要弹卡片 ===")

        from pawpet.ai.actions import LEVEL_FULL
        from pawpet.ai.extensions import (
            LEVEL_CODE,
            Extension,
            load_all as load_ext,
            save_all as save_ext,
        )

        full_store = Store(scratch / "full.json", scratch / "full.bak.json")
        full_store.load()
        # extensions.json 的路径是从 store.path 的**父目录**推的，
        # 所以这套数据天然落在 scratch 里，不会碰到用户的真工具。
        full_actions = DesktopActions(AuditLog())
        full_actions.level = LEVEL_FULL          # ← 最宽松的一档，正是漏过 P0 的那档
        full_context = ToolContext(ScreenCapture(), full_actions, full_store)

        asked: list[dict] = []
        full_events: list[StepEvent] = []

        class FullRecorder:
            def on_status(self, text): pass
            def on_event(self, event): full_events.append(event)
            def on_image(self, png, note): pass
            def on_finished(self, text): pass
            def on_error(self, text):
                full_events.append(StepEvent(kind="error", text=text))
            def request_approval(self, request):
                asked.append({"tool": request.tool_name, "risk": request.risk})
                # 一律拒绝：这样不会有任何东西真的被装上或执行。
                # 这一组验的就是「有没有问」，不是「装上之后怎么样」。
                return False

        book = scratch / "extensions.json"

        # --- 5.1 安装本身必须问
        install_model = FakeModel([
            tool_call("g1", "install_extension", {"draft": {
                "name": "p0_probe", "title": "P0 探针", "description": "回归用",
                "level": "recipe",
                "steps": [{"op": "list_dir", "path": "{p}"}],
            }}),
            {"content": "好，先放着。"},
        ])
        install_server, install_url = make_server(install_model)
        try:
            install_client = AIClient(api_key="sk-fake", model="fake",
                                      base_url=install_url, timeout=20)
            runner_full = AgentRunner(install_client, full_context, full_actions,
                                      FullRecorder(), max_steps=4)
            runner_full.run("给我造一个整理目录的工具并装上")

            check("full 档下 install_extension 弹了卡片",
                  [a["tool"] for a in asked] == ["install_extension"],
                  str(asked))
            check("卡片上的级别是 critical（不是 confirm）",
                  bool(asked) and asked[0]["risk"] == "critical", str(asked))
            check("拒绝之后没有落盘",
                  not book.exists()
                  or "p0_probe" not in [e.name for e in load_ext(book)],
                  str([e.name for e in load_ext(book)]) if book.exists() else "文件不存在")
        finally:
            install_server.shutdown()

        # --- 5.2 已经装好的 code 档工具，调用时也必须问
        #
        # 这一条是闭环的另一半：光把「安装」挂上 CRITICAL 还不够，
        # 运行那段代码同样得问 —— 否则用户装了之后，
        # 每次「完全自动」跑它都是一次静默的任意代码执行。
        save_ext(book, [Extension(
            name="p0_coder", title="P0 代码工具", description="回归用",
            level=LEVEL_CODE, code="print('这段代码不该被执行到')",
            approved=True,
        )])

        asked.clear()
        full_events.clear()
        code_model = FakeModel([
            tool_call("g2", "ext_p0_coder", {}),
            {"content": "好。"},
        ])
        code_server, code_url = make_server(code_model)
        try:
            code_client = AIClient(api_key="sk-fake", model="fake",
                                   base_url=code_url, timeout=20)
            runner_code = AgentRunner(code_client, full_context, full_actions,
                                      FullRecorder(), max_steps=4)
            runner_code.run("跑一下我那个 P0 代码工具")

            check("full 档下调用 code 档自定义工具弹了卡片",
                  [a["tool"] for a in asked] == ["ext_p0_coder"], str(asked))
            check("卡片上的级别是 critical",
                  bool(asked) and asked[0]["risk"] == "critical", str(asked))
            check("拒绝之后那段代码没被执行",
                  not any("不该被执行到" in e.text for e in full_events),
                  str([e.text[:40] for e in full_events]))
        finally:
            code_server.shutdown()

        # --- 5.3 对照：note / recipe 档在 full 下**不该**被拦
        #
        # 没有这一条，5.1/5.2 就可能被「把所有自定义工具都升级成 CRITICAL」
        # 这种偷懒改法骗过 —— 那样用户每调一次自己的小工具都要点一次卡片，
        # 最后他还是会把权限开到 full 并且学会闭眼点「允许」。
        asked.clear()
        save_ext(book, [Extension(
            name="p0_reader", title="P0 只读工具", description="回归用",
            level="recipe", steps=[{"op": "list_dir", "path": "{p}"}],
            approved=True,
        )])
        recipe_model = FakeModel([
            tool_call("g3", "ext_p0_reader", {"p": str(scratch)}),
            {"content": "好。"},
        ])
        recipe_server, recipe_url = make_server(recipe_model)
        try:
            recipe_client = AIClient(api_key="sk-fake", model="fake",
                                     base_url=recipe_url, timeout=20)
            runner_recipe = AgentRunner(recipe_client, full_context, full_actions,
                                        FullRecorder(), max_steps=4)
            runner_recipe.run("列一下那个目录")

            check("full 档下 recipe 档自定义工具不弹卡片（别把所有东西都升级）",
                  asked == [], str(asked))
        finally:
            recipe_server.shutdown()

        # --- 5.4 审计日志要如实记下「谁批的」
        #
        # 上面两次都被拒了，日志里应该是 denied —— 如果哪一天有人把
        # CRITICAL 的判断挪回后面，这里会先于用户发现。
        log = full_actions.audit.recent(50)
        denied = [e for e in log if e["approved"] == "denied"]
        check("被拒的高危操作在审计里记成 denied",
              len(denied) >= 2, str([(e["action"], e["approved"]) for e in log]))

        # 关掉记忆之后，一个字都不该进提示词
        silent = format_for_prompt(Memory())
        check("空记忆渲染成空串（不注入空壳）", silent == "", repr(silent))

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
