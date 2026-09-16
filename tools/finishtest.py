r"""复现并验证「任务没有完成就结束」这个 bug。

用户的描述：
    跑了一堆步骤（写脚本、执行命令、读文件…），然后**什么都没有了** ——
    不知道该继续还是重来。

查下来是两个 bug 叠在一起：

1. **最终回答从来没进对话。** `_apply_finished` 只发了个「任务结束」的
   瞬时提示，没有把它 `_push` 进消息列表。模型如果整轮都在调工具、
   没吐过正文（多步任务很常见），收尾文字就会被算出来然后**丢掉**。

2. **步数上限只发状态提示，不留在对话里。** 原来那段「我已经执行了 N 步」
   只走了 `on_status`（瞬时状态），用户看不到。

这两条加在一起，用户看到的就是「跑了很多步 → 空白 → 结束」。

用法：
    .venv\\Scripts\\python.exe tools\\finishtest.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "finish"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "finishtest"

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


class ScriptedModel:
    """永远只回工具调用，从不吐正文 —— 复现用户那种多步任务。"""

    def __init__(self, tool_name: str = "ui_windows", limit: int = 500):
        self.tool_name = tool_name
        self.limit = limit
        self.count = 0

    def reply(self, body: dict) -> dict:
        self.count += 1
        if self.count > self.limit:
            return {
                "id": "x", "object": "chat.completion", "model": "fake",
                "choices": [{"index": 0,
                             "message": {"role": "assistant",
                                         "content": "兜底回答"},
                             "finish_reason": "stop"}],
                "usage": {},
            }
        return {
            "id": "x", "object": "chat.completion", "model": "fake",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": f"c{self.count}", "type": "function",
                        "function": {"name": self.tool_name, "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }


def make_server(model: ScriptedModel):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"data": [{"id": "fake"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {}
            payload = json.dumps(model.reply(body), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> int:
    print("小爪「任务没完成就结束」复现与验证\n")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QCoreApplication

    from pawpet.ai import controller as controller_module
    from pawpet.ai.agent import AgentRunner
    from pawpet.ai.controller import AiController
    from pawpet.store import Store

    app = QCoreApplication.instance() or QCoreApplication([])

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    # ================================================== 一、Agent 层
    print("=== 一、Agent：顶到步数上限必须产出收尾文字 ===")

    from pawpet.ai.agent import StepEvent

    class Actions:
        def __init__(self):
            self.level = "full"
            from pawpet.ai.actions import AuditLog
            self.audit = AuditLog()

        def clear_stop(self): pass
        def request_stop(self): pass
        def blocked(self, risk): return False
        def needs_approval(self, risk): return False

    class ProbeContext:
        def __init__(self): self.calls = []

        def execute(self, name, arguments):
            self.calls.append(name)
            return True, f"{name} 完成", None

    class Client:
        def __init__(self, steps, tool="ui_windows"):
            self.steps = steps
            self.tool = tool
            self.count = 0

        def chat(self, messages, tools=None):
            self.count += 1
            call = type("C", (), {
                "id": f"c{self.count}", "name": self.tool,
                "arguments": {}, "raw_arguments": "{}"})()
            reply = type("R", (), {"text": "", "tool_calls": [call]})()
            reply.wants_tools = True
            return reply

    class Sink:
        def __init__(self): self.events = []
        def on_status(self, t): pass
        def on_event(self, e): self.events.append(e)
        def on_image(self, p, n): pass
        def on_finished(self, t): self.finished = t
        def on_error(self, t): pass
        def request_approval(self, r): return True

    context = ProbeContext()
    sink = Sink()
    runner = AgentRunner(Client(500), context, Actions(), sink, max_steps=5)
    result = runner.run("干个活")

    check("顶到上限时会返回非空文字", bool(result.strip()),
          repr(result[:60]))
    check("收尾文字说明了是步数上限",
          "步" in result and ("上限" in result or "步" in result),
          result[:80])
    check("收尾文字告诉用户可以继续",
          "继续" in result, result[:80])
    check("确实只跑了 5 步", len(context.calls) == 5, str(len(context.calls)))

    # 有中间正文时，收尾文字要附在后面而不是顶掉它
    class ChattyClient(Client):
        def chat(self, messages, tools=None):
            self.count += 1
            call = type("C", (), {
                "id": f"c{self.count}", "name": self.tool,
                "arguments": {}, "raw_arguments": "{}"})()
            reply = type("R", (), {"text": "我先看一下。",
                                   "tool_calls": [call]})()
            reply.wants_tools = True
            return reply

    sink2 = Sink()
    runner2 = AgentRunner(ChattyClient(500), ProbeContext(), Actions(),
                          sink2, max_steps=3)
    result2 = runner2.run("干个活")
    check("中间正文被保留", "我先看一下" in result2, result2[:80])
    check("收尾说明也附上了", "上限" in result2, result2[-80:])

    # 没顶到上限时**不该**出现这段话
    class ShortClient(Client):
        def chat(self, messages, tools=None):
            self.count += 1
            if self.count >= 2:
                reply = type("R", (), {"text": "做完了。", "tool_calls": []})()
                reply.wants_tools = False
                return reply
            call = type("C", (), {
                "id": f"c{self.count}", "name": self.tool,
                "arguments": {}, "raw_arguments": "{}"})()
            reply = type("R", (), {"text": "", "tool_calls": [call]})()
            reply.wants_tools = True
            return reply

    sink3 = Sink()
    runner3 = AgentRunner(ShortClient(10), ProbeContext(), Actions(),
                          sink3, max_steps=10)
    result3 = runner3.run("小事")
    check("没顶到上限时不出现上限提示",
          "上限" not in result3, result3[:80])
    check("正常收尾文字还在", "做完了" in result3, result3[:60])

    # ================================================== 二、控制器层
    print("\n=== 二、控制器：最终回答必须进对话 ===")

    model = ScriptedModel(tool_name="ui_windows", limit=500)
    server, url = make_server(model)

    store = Store(SCRATCH / "a.json", SCRATCH / "a.bak.json")
    store.load()
    controller = AiController(store)
    real_client = controller_module.AIClient

    def fake_client(*_args, **_kwargs):
        return real_client(api_key="sk-fake", model="fake", base_url=url,
                           timeout=15)

    controller_module.AIClient = fake_client
    try:
        controller.actions.level = "full"
        controller.maxSteps = 6
        controller.memoryEnabled = False

        controller.send("帮我干个复杂活")

        deadline = time.time() + 60
        while controller.running and time.time() < deadline:
            pump(0.1)
        pump(0.5)

        texts = [(m.get("role"), (m.get("text") or "").strip())
                 for m in controller.messages]
        print("    对话记录：")
        for role, text in texts[-6:]:
            print(f"      [{role}] {text[:54]}")

        assistants = [text for role, text in texts if role == "assistant"]
        check("对话里**有**助手消息（不再是一片空白）",
              bool(assistants), "一条助手消息都没有 —— 就是用户看到的症状")
        check("最后一条助手消息说明了为什么停",
              assistants and "上限" in assistants[-1],
              assistants[-1][:80] if assistants else "（没有）")
        check("告诉用户可以继续",
              assistants and "继续" in assistants[-1],
              assistants[-1][:80] if assistants else "（没有）")
        check("工具确实跑了（不是没执行就结束）",
              sum(1 for role, _ in texts if role == "tool") >= 3,
              str(sum(1 for role, _ in texts if role == "tool")))
    finally:
        controller_module.AIClient = real_client
        controller.shutdown()
        server.shutdown()

    # ================================================== 三、不重复
    print("\n=== 三、正常回答不会因为这次修复被推两遍 ===")

    model2 = ScriptedModel(tool_name="ui_windows", limit=0)
    server2, url2 = make_server(model2)

    store2 = Store(SCRATCH / "b.json", SCRATCH / "b.bak.json")
    store2.load()
    controller2 = AiController(store2)

    def fake2(*_a, **_k):
        return real_client(api_key="sk-fake", model="fake", base_url=url2,
                           timeout=15)

    controller_module.AIClient = fake2
    try:
        controller2.actions.level = "full"
        controller2.memoryEnabled = False
        controller2.send("简单问题")

        deadline = time.time() + 40
        while controller2.running and time.time() < deadline:
            pump(0.1)
        pump(0.5)

        assistants = [m.get("text", "") for m in controller2.messages
                      if m.get("role") == "assistant"]
        print(f"    助手消息 {len(assistants)} 条：{assistants}")
        check("普通回答只出现一次", len(assistants) == 1,
              f"出现了 {len(assistants)} 次")
        check("内容正确", assistants and "兜底回答" in assistants[0],
              str(assistants))
    finally:
        controller_module.AIClient = real_client
        controller2.shutdown()
        server2.shutdown()

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
