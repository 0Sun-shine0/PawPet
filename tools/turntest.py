r"""复现并验证「问了新问题，却输出上一轮结果」这个 bug。

用户的描述：
    连问两句时，第二句**没有任何输出**，界面把**上一轮的结果**又显示了一遍。
    复现概率很高。

真实原因有两处（都在 controller 的轮次处理上）：

1. **自动记忆线程晚归。** 每轮结束都会起一个后台线程判断「这轮有没有
   值得记的」。它比主线程慢，用户马上追问第二句时，上一轮的线程才回来，
   弹一条「记住了新东西」—— 看起来就是「答非所问 / 上一轮的结果又出来了」。

2. **自动记忆拿错了文本。** `_start_learning` 原来回头去 `self._messages`
   里找「最后一条用户消息」当本轮内容。用户已经追问下一句时，找到的是
   **新问题** —— 于是拿新问题去学习上一轮的回复，记忆内容也是错的。

修法：给每轮编个号，跨线程回调都带着它回来，过期的直接丢掉；
并且把本轮用户原话由调用方传进去，不再回头去消息列表里找。

这个测试用假模型 + 精确控制时序来复现，而不是靠碰运气。

用法：
    .venv\\Scripts\\python.exe tools\\turntest.py
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "turn"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "turntest"

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


def main() -> int:
    print("小爪轮次串台 bug 复现与验证\n")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QCoreApplication

    from pawpet.ai import controller as controller_module
    from pawpet.ai.controller import AiController
    from pawpet.store import Store

    app = QCoreApplication.instance() or QCoreApplication([])

    def pump(seconds: float) -> None:
        """转事件循环 —— 跨线程排队的主线程槽要靠它才会执行。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    # ================================================== 一、轮次号机制
    print("=== 一、轮次号：过期回调必须被丢掉 ===")

    store = Store(SCRATCH / "a.json", SCRATCH / "a.bak.json")
    store.load()
    controller = AiController(store)
    try:
        check("初始轮次号是 0", controller._turn == 0, str(controller._turn))

        # 这里**不用 _start_worker** —— 那会真的起线程，线程自己也会
        # 回调 _apply_finished，把测试搅成一团（我第一次就是这么写的，
        # 结果测出来一堆假失败）。手动推进轮次状态，测的是纯机制。
        def begin_turn(text: str) -> int:
            controller._turn += 1
            controller._turn_user_text = text
            controller._running = True
            return controller._turn

        first_turn = begin_turn("第一句")
        check("发一句后轮次号变成 1", first_turn == 1, str(first_turn))
        check("记录了本轮用户原话",
              controller._turn_user_text == "第一句",
              controller._turn_user_text)

        controller._apply_finished("第一轮的回答", "", first_turn)
        pump(0.05)
        check("结束后 running 为假", not controller.running)

        second_turn = begin_turn("第二句")
        check("再发一句轮次号变成 2", second_turn == 2, str(second_turn))
        check("本轮用户原话更新了",
              controller._turn_user_text == "第二句",
              controller._turn_user_text)

        # 关键：拿**上一轮**的号回来的回调，必须无效
        check("第一轮的轮次号已经过期",
              not controller._is_current(first_turn))
        check("第二轮的轮次号是当前",
              controller._is_current(second_turn))

        before = len(controller.messages)
        # 模拟上一轮的自动记忆线程晚归
        controller._apply_learned("记住了新东西", {"learned": ["旧的"]},
                                  first_turn)
        pump(0.1)
        check("过期的自动学习回调不产生任何界面消息",
              len(controller.messages) == before,
              f"{before} -> {len(controller.messages)}")
        check("过期的自动学习也不会改写 lastLearned",
              controller.lastLearned.get("learned") != ["旧的"],
              str(controller.lastLearned))

        # 当前轮的则应该生效
        controller._apply_learned("记住了 1 条", {"learned": ["新的"]},
                                  second_turn)
        pump(0.1)
        check("当前轮的自动学习回调正常生效",
              controller.lastLearned.get("learned") == ["新的"],
              str(controller.lastLearned))

        # 过期的状态/事件也不能串进来
        controller._apply_status("上一轮的状态", first_turn)
        pump(0.05)
        check("过期的状态更新被丢掉", controller.status != "上一轮的状态",
              controller.status)
        controller._apply_status("当前轮的状态", second_turn)
        pump(0.05)
        check("当前轮的状态更新生效", controller.status == "当前轮的状态",
              controller.status)

        before = len(controller.messages)
        from pawpet.ai.agent import StepEvent
        controller._apply_event(
            StepEvent(kind="assistant", text="上一轮的回答"), first_turn)
        pump(0.05)
        check("过期的助手消息不会插进新一轮",
              len(controller.messages) == before,
              f"{before} -> {len(controller.messages)}")
        controller._apply_event(
            StepEvent(kind="assistant", text="第二句的回答"), second_turn)
        pump(0.05)
        check("当前轮的助手消息正常进来",
              len(controller.messages) == before + 1,
              f"{before} -> {len(controller.messages)}")

        # 过期轮次的 finished 不能再改状态（否则会把新一轮的 running 清掉）
        check("此时新一轮还在跑", controller.running, "前置条件不成立")
        controller._apply_finished("过期的回答", "", first_turn)
        pump(0.05)
        check("过期的 finished 不会把新一轮标记成结束",
              controller.running, "running 被上一轮清掉了")
    finally:
        controller.shutdown()

    # ================================================== 二、自动记忆拿对文本
    print("\n=== 二、自动记忆必须拿到**本轮**的用户原话 ===")

    from pawpet.ai.client import AIClient

    store2 = Store(SCRATCH / "b.json", SCRATCH / "b.bak.json")
    store2.load()
    controller2 = AiController(store2)
    captured: list[tuple[str, str]] = []

    try:
        def spy(user_text, assistant_text, turn=0):
            captured.append((user_text, assistant_text))
            # 不真的调模型：这一节测的是「文本对不对」，不是学习本身

        controller2._learn_worker = spy
        # 换掉 client：否则真实的 _learn_worker 会去连 .env 里的地址
        controller2._client = lambda: AIClient(api_key="", model="x",
                                               base_url="http://127.0.0.1:1")
        controller2._settings["ai_memory_enabled"] = True

        def begin_turn(text: str) -> int:
            controller2._turn += 1
            controller2._turn_user_text = text
            controller2._running = True
            return controller2._turn

        turn1 = begin_turn("我叫马靖凯")
        controller2._apply_finished("好的", "", turn1)
        pump(0.4)
        check("第一轮学到了正确的文本",
              captured and captured[0][0] == "我叫马靖凯", str(captured))
        check("连带把本轮的回答也传过去了",
              captured and captured[0][1] == "好的", str(captured))

        # 用户马上追问：新问题进来，轮次号变了
        captured.clear()
        turn2 = begin_turn("第二个问题")
        controller2._apply_finished("对第二个问题的回答", "", turn2)
        pump(0.4)

        check("第二轮学到的是第二轮的原话",
              captured and captured[-1][0] == "第二个问题", str(captured))
        check("没有把新问题当成上一轮的内容",
              all(text != "我叫马靖凯" for text, _ in captured), str(captured))

        # 最关键的一条：上一轮迟到的 finished 不该再触发学习
        captured.clear()
        controller2._apply_finished("迟到的上一轮回答", "", turn1)
        pump(0.4)
        check("过期轮次的 finished 不会重复触发学习",
              not captured, str(captured))
    finally:
        controller2.shutdown()

    # ================================================== 三、端到端：连问两句
    print("\n=== 三、端到端：连问两句，第二句必须有回答 ===")

    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    answers = ["第一句的回答", "第二句的回答"]

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
            self.rfile.read(length)
            with lock:
                text = answers.pop(0) if answers else "没有更多回答了"
            payload = json.dumps({
                "id": "x", "object": "chat.completion", "model": "fake",
                "choices": [{"index": 0, "message": {"role": "assistant",
                                                     "content": text},
                             "finish_reason": "stop"}],
                "usage": {},
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    lock = threading.Lock()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    store3 = Store(SCRATCH / "c.json", SCRATCH / "c.bak.json")
    store3.load()
    controller3 = AiController(store3)
    real_client = controller_module.AIClient

    def fake_client(*_args, **_kwargs):
        return real_client(api_key="sk-fake", model="fake", base_url=url,
                           timeout=15)

    controller_module.AIClient = fake_client
    try:
        controller3.actions.level = "full"

        controller3.send("第一句")
        for _ in range(60):
            pump(0.15)
            if not controller3.running:
                break
        pump(0.3)

        controller3.send("第二句")
        for _ in range(60):
            pump(0.15)
            if not controller3.running:
                break
        pump(0.5)

        texts = [(m.get("role"), (m.get("text") or "").strip())
                 for m in controller3.messages]
        print("    对话记录：")
        for role, text in texts:
            print(f"      [{role}] {text[:40]}")

        assistants = [text for role, text in texts if role == "assistant"]
        check("第二句得到了回答",
              any("第二句的回答" in text for text in assistants),
              str(assistants))
        check("第一句的回答也在",
              any("第一句的回答" in text for text in assistants),
              str(assistants))
        check("最后一条助手消息是第二句的回答（不是上一轮的）",
              assistants and "第二句的回答" in assistants[-1],
              assistants[-1] if assistants else "（没有助手消息）")
        check("两条用户消息都在",
              sum(1 for role, _ in texts if role == "user") == 2,
              str([role for role, _ in texts]))
        check("lastSummary 指向的是最新回答",
              "第二句的回答" in controller3.lastSummary,
              controller3.lastSummary[:40])
    finally:
        controller_module.AIClient = real_client
        controller3.shutdown()
        server.shutdown()

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
