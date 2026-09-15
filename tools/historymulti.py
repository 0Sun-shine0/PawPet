r"""检查「同一次会话里发第二条指令」时历史会不会变脏。

这条路径和单轮不同：controller 会用 _build_history() 重建历史，
再交给一个新的 AgentRunner。做成独立脚本是因为它跑得比较久，
不适合塞进主测试套件。

用法：
    .venv\Scripts\python.exe tools\historymulti.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "historymulti"
os.environ["PAWPET_HOME"] = str(SCRATCH)

from pawpet.ai.agent import MAX_HISTORY_MESSAGES, AgentRunner  # noqa: E402

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


def validate(messages: list[dict]) -> str:
    """按 OpenAI 的规矩检查：tool 消息必须能找到对应的 tool_call。"""
    seen: set[str] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if call.get("id"):
                    seen.add(call["id"])
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if not call_id or call_id not in seen:
                return (f"第 {index} 条 tool(tool_call_id={call_id}) "
                        f"找不到对应的 tool call")
    return ""


class FakeCall:
    def __init__(self, call_id, name="ui_windows"):
        self.id = call_id
        self.name = name
        self.arguments = {}
        self.raw_arguments = "{}"


class FakeReply:
    def __init__(self, text="", calls=None):
        self.text = text
        self.tool_calls = calls or []

    @property
    def wants_tools(self):
        return bool(self.tool_calls)


class ScriptedClient:
    """每次请求都调 tools_per_step 个工具，直到 max_calls 用光。"""

    def __init__(self, max_calls=12, tools_per_step=1):
        self.max_calls = max_calls
        self.tools_per_step = tools_per_step
        self.issued = 0
        self.seen: list[list[dict]] = []
        self.problems: list[tuple[int, str]] = []

    def chat(self, messages, tools=None):
        self.seen.append([dict(m) for m in messages])
        error = validate(messages)
        if error:
            self.problems.append((len(self.seen), error))
        if self.issued >= self.max_calls:
            return FakeReply(text="做完了。")
        calls = []
        for _ in range(self.tools_per_step):
            self.issued += 1
            calls.append(FakeCall(f"call_{self.issued:03d}"))
        return FakeReply(calls=calls)


class Callbacks:
    def on_status(self, t): pass
    def on_event(self, e): pass
    def on_image(self, p, n): pass
    def on_finished(self, t): pass
    def on_error(self, t): pass
    def request_approval(self, r): return True


def main() -> int:
    print("多轮会话的历史合法性\n")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from pawpet.ai.actions import AuditLog, DesktopActions
    from pawpet.ai.agent import system_prompt
    from pawpet.ai.tools import ToolContext
    from pawpet.ai.vision import ScreenCapture
    from pawpet.store import Store

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()

    for turn in range(1, 7):
        client = ScriptedClient(max_calls=12)
        actions = DesktopActions(AuditLog())
        actions.level = "full"
        context = ToolContext(ScreenCapture(), actions, store)
        runner = AgentRunner(client, context, actions, Callbacks(), max_steps=30)

        # 模拟 controller：第二条指令时历史是重建出来的
        if turn > 1:
            runner.messages = [
                {"role": "system", "content": system_prompt(30, "")},
                {"role": "user", "content": f"第 {turn - 1} 轮的话"},
                {"role": "assistant", "content": "好的，做完了。"},
            ]

        runner.run(f"第 {turn} 轮：开始干活")

        peak = max(len(s) for s in client.seen) if client.seen else 0
        check(f"第 {turn} 轮历史合法（{len(client.seen)} 次请求，峰值 {peak} 条）",
              not client.problems,
              str(client.problems[:1]))

    # 极长会话：把裁剪逼到边界上
    client = ScriptedClient(max_calls=200)
    actions = DesktopActions(AuditLog())
    actions.level = "full"
    context = ToolContext(ScreenCapture(), actions, store)
    runner = AgentRunner(client, context, actions, Callbacks(), max_steps=120)
    runner.run("跑很久")
    peak = max(len(s) for s in client.seen) if client.seen else 0
    check(f"超长会话历史合法（{len(client.seen)} 次请求，峰值 {peak} 条，"
          f"上限 {MAX_HISTORY_MESSAGES}）",
          not client.problems, str(client.problems[:1]))
    check("峰值没有超过上限", peak <= MAX_HISTORY_MESSAGES, str(peak))

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
