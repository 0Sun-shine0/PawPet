r"""端到端验证这一批新功能：现成任务、停下来问人、做完交代、边做边说。

featuretest 测的是纯逻辑；这里拉起**真的控制器 + 真的 Qt 事件循环 +
假的模型服务**，验证跨线程那一段真的走得通：

* 模型调 ask_user 之后，工作线程是不是真的停住等在主线程回答上
* 回答有没有真的回到模型手里（不是只更新了界面）
* 界面上的 pendingApproval / hasPendingQuestion 对不对
* 现成任务点下去发出去的到底是什么
* 收尾交代有没有落到那条助手消息上

为什么要单独测：这几个功能全部跨线程（工作线程阻塞、主线程弹卡片、
再唤醒工作线程）。单测纯逻辑是看不出「信号参数对不上」这种错的。

用法：
    .venv\\Scripts\\python.exe tools\\asktest.py
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

SCRATCH = ROOT / ".cache" / "asktest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "asktest"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
# 假模型：按队列依次吐响应
# --------------------------------------------------------------------------
class ScriptedModel:
    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def reply(self, body: dict) -> dict:
        with self.lock:
            self.requests.append(body)
            if self.script:
                message = self.script.pop(0)
            else:
                message = {"role": "assistant", "content": "兜底回答"}
        return {
            "id": "chatcmpl-fake", "object": "chat.completion", "model": "fake",
            "choices": [{"index": 0, "message": message,
                         "finish_reason":
                             "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "role": "assistant", "content": None,
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
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
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def main() -> int:
    print("小爪新功能端到端（控制器 + 线程桥）\n")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QCoreApplication

    from pawpet.ai import controller as controller_module
    from pawpet.ai.controller import AiController
    from pawpet.store import Store

    app = QCoreApplication.instance() or QCoreApplication([])

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    def wait_idle(controller, seconds: float = 40.0) -> bool:
        deadline = time.time() + seconds
        while controller.running and time.time() < deadline:
            pump(0.05)
        pump(0.3)
        return not controller.running

    real_client = controller_module.AIClient

    def wire(url: str):
        def fake(*_a, **_k):
            return real_client(api_key="sk-fake", model="fake", base_url=url,
                               timeout=15)
        controller_module.AIClient = fake

    # ==================================================================
    # 一、停下来问人：整条链路
    # ==================================================================
    print("=== 一、拿不准就停一下问 ===")

    model = ScriptedModel([
        tool_call("c1", "ask_user", {
            "question": "「下载」里有两个文件夹看着都能放，你要哪个？",
            "options": ["放「学习」", "放「工作」", "你帮我定"],
        }),
        {"role": "assistant", "content": "好，那我放到「学习」下面了。"},
    ])
    server, url = make_server(model)

    store = Store(SCRATCH / "a.json", SCRATCH / "a.bak.json")
    store.load()
    controller = AiController(store)
    wire(url)
    try:
        controller.actions.level = "full"
        controller.memoryEnabled = False
        controller.send("帮我整理下载文件夹")

        # 等它停在问题上
        deadline = time.time() + 30
        while not controller.hasPendingQuestion and time.time() < deadline:
            pump(0.05)

        check("工作线程停下来等了", controller.hasPendingQuestion,
              "pendingApproval = " + str(controller.pendingApproval))
        check("问题文字传到了界面",
              "都能放" in (controller.pendingApproval.get("summary") or ""),
              str(controller.pendingApproval.get("summary"))[:50])
        check("选项传到了界面",
              controller.pendingApproval.get("options") ==
              ["放「学习」", "放「工作」", "你帮我定"],
              str(controller.pendingApproval.get("options")))
        check("提问不会被当成普通审批",
              controller.hasPendingApproval and controller.hasPendingQuestion)
        check("提问期间还在运行（没被当成结束）", controller.running)

        # 回答 → 工作线程应该被唤醒，继续跑完
        controller.answerPending("放「学习」")
        pump(0.2)
        check("回答后卡片收起", not controller.hasPendingQuestion)
        check("回答后任务继续跑完", wait_idle(controller))

        # 回答必须真的进了模型上下文，否则「问了等于没问」
        tool_messages = [m for m in controller.runner.messages
                         if m.get("role") == "tool"]
        joined = " ".join(str(m.get("content") or "") for m in tool_messages)
        check("回答回到了模型手里", "放「学习」" in joined, joined[:120])
        check("模型按回答继续给结果",
              any("学习" in (m.get("text") or "")
                  for m in controller.messages if m["role"] == "assistant"),
              str([m.get("text") for m in controller.messages])[:120])

        # 对话里能看到「问了什么 / 答了什么」，用户回看时不会一头雾水
        texts = [m.get("text") or "" for m in controller.messages]
        check("对话里留了提问记录",
              any("都能放" in t for t in texts), str(texts)[:150])
        check("对话里留了回答记录",
              any("你回答" in t and "学习" in t for t in texts), str(texts)[:150])
    finally:
        controller_module.AIClient = real_client
        controller.shutdown()
        server.shutdown()

    # ==================================================================
    # 二、用户不回答
    # ==================================================================
    print("\n=== 二、用户不回答时不能当成默认同意 ===")

    model2 = ScriptedModel([
        tool_call("c1", "ask_user", {"question": "覆盖原来的文件吗？"}),
        {"role": "assistant", "content": "你没回我，那我就先不动了。"},
    ])
    server2, url2 = make_server(model2)
    store2 = Store(SCRATCH / "b.json", SCRATCH / "b.bak.json")
    store2.load()
    controller2 = AiController(store2)
    wire(url2)
    try:
        controller2.actions.level = "full"
        controller2.memoryEnabled = False
        controller2.send("把那个文件重写一遍")

        deadline = time.time() + 30
        while not controller2.hasPendingQuestion and time.time() < deadline:
            pump(0.05)
        check("停下来问了", controller2.hasPendingQuestion)

        # 直接不理它，按停止 —— 用户走开的场景
        controller2.stop()
        pump(0.3)
        check("停止后不再挂着问题", not controller2.hasPendingQuestion)

        notes = " ".join(
            str(m.get("content") or "") for m in controller2.runner.messages
            if m.get("role") == "tool")
        check("明确告诉模型「不要假设同意」", "不要假设" in notes, notes[:120])
    finally:
        controller_module.AIClient = real_client
        controller2.shutdown()
        server2.shutdown()

    # ==================================================================
    # 三、现成任务
    # ==================================================================
    print("\n=== 三、现成任务点一下就跑 ===")

    model3 = ScriptedModel([
        {"role": "assistant", "content": "好的，开始整理。"},
    ])
    server3, url3 = make_server(model3)
    store3 = Store(SCRATCH / "c.json", SCRATCH / "c.bak.json")
    store3.load()
    controller3 = AiController(store3)
    wire(url3)
    try:
        controller3.actions.level = "full"
        controller3.memoryEnabled = False

        groups = controller3.taskGroups
        check("界面能拿到分组", len(groups) == 3, str(len(groups)))
        check("分组能直接喂给 QML（是 dict）",
              all(isinstance(g, dict) for g in groups))
        check("每条任务字段齐全",
              all(all(k in item for k in ("key", "label", "hint", "prompt"))
                  for g in groups for item in g["items"]))

        toasts: list[tuple] = []
        controller3.toastRequested.connect(lambda a, b: toasts.append((a, b)))

        key = groups[0]["items"][0]["key"]
        controller3.runTemplate(key)
        check("点了就开始跑", controller3.running)
        check("先留了一条「现成任务」的说明",
              any("现成任务" in (m.get("text") or "")
                  for m in controller3.messages), str(controller3.messages)[:150])
        user_texts = [m.get("text") or "" for m in controller3.messages
                      if m.get("role") == "user"]
        check("发出去的是模板里的完整提示词", bool(user_texts), str(user_texts)[:80])
        check("提示词不是空的占位文本",
              user_texts and len(user_texts[0]) > 20, str(user_texts)[:80])
        wait_idle(controller3)

        # 不存在的 key 不能把状态搞乱
        before = len(controller3.messages)
        controller3.runTemplate("根本没有这条")
        pump(0.2)
        check("无效 key 被安全忽略", len(controller3.messages) == before,
              f"{before} → {len(controller3.messages)}")

        # 跑的时候再点一次：应该提示而不是排队
        toasts.clear()
        controller3.send("再干一件事")
        pump(0.05)
        controller3.runTemplate(key)
        check("正在跑的时候点模板会提示", bool(toasts), str(toasts))
    finally:
        controller_module.AIClient = real_client
        controller3.shutdown()
        server3.shutdown()

    # ==================================================================
    # 四、做完有交代 + 边做边说
    # ==================================================================
    print("\n=== 四、做完有交代 / 边做边说 ===")

    target = SCRATCH / "产出" / "report.txt"
    model4 = ScriptedModel([
        tool_call("c1", "write_file", {
            "path": str(target), "content": "汇总结果\n第一项\n第二项",
        }),
        {"role": "assistant", "content": "写好了。"},
    ])
    server4, url4 = make_server(model4)
    store4 = Store(SCRATCH / "d.json", SCRATCH / "d.bak.json")
    store4.load()
    controller4 = AiController(store4)
    wire(url4)
    try:
        controller4.actions.level = "full"
        controller4.memoryEnabled = False

        steps_seen: list[int] = []
        controller4.stepsChanged.connect(
            lambda: steps_seen.append(len(controller4.stepTimeline)))

        controller4.send("把这些写成一个文件")
        wait_idle(controller4)

        assistants = [m for m in controller4.messages if m["role"] == "assistant"]
        check("有助手消息", bool(assistants))
        report = assistants[-1].get("report") if assistants else ""
        check("助手消息带了收尾交代", bool(report), repr(report)[:80])
        check("交代说了做了几件事", "个操作" in (report or ""), repr(report)[:80])
        check("交代说了文件在哪", str(target) in (report or ""), repr(report)[:100])
        check("交代说了能不能撤回",
              "撤回" in (report or "") or "恢复" in (report or ""),
              repr(report)[:100])
        check("文件真的写出来了", target.exists(), str(target))
        check("写出来的内容对", target.exists()
              and "汇总结果" in target.read_text(encoding="utf-8", errors="replace"))

        check("边做边说：时间线真的更新过", bool(steps_seen), str(steps_seen))
        check("时间线里是写文件这一步",
              any("写文件" in step for step in controller4.stepTimeline),
              str(controller4.stepTimeline))

        # 纯问答不该出现「这一轮做了 0 个操作」这种傻话
        model5 = ScriptedModel([
            {"role": "assistant", "content": "这段报错的意思是磁盘满了。"},
        ])
        server5, url5 = make_server(model5)
        store5 = Store(SCRATCH / "e.json", SCRATCH / "e.bak.json")
        store5.load()
        controller5 = AiController(store5)
        wire(url5)
        try:
            controller5.actions.level = "full"
            controller5.memoryEnabled = False
            controller5.send("这段报错什么意思")
            wait_idle(controller5)
            assistants5 = [m for m in controller5.messages
                           if m["role"] == "assistant"]
            report5 = assistants5[-1].get("report") if assistants5 else ""
            check("纯问答不硬凑交代", not report5, repr(report5)[:60])
            check("但回答本身还在",
                  assistants5 and "磁盘" in (assistants5[-1].get("text") or ""),
                  str(assistants5)[:80])
        finally:
            controller_module.AIClient = real_client
            controller5.shutdown()
            server5.shutdown()
    finally:
        controller_module.AIClient = real_client
        controller4.shutdown()
        server4.shutdown()

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
