r"""复现并验证「No tool call found for tool output with call_id ...」这个 400。

背景：OpenAI 兼容接口的硬规矩是 role="tool" 的消息必须紧跟在
「带 tool_calls 的 assistant」后面，tool_call_id 要对得上。
历史裁剪如果从中间一刀切，就会把 assistant 砍掉、把它的 tool 结果
留在开头 —— 下一次请求直接被拒。

这个测试用真接口的校验规则去检查历史，而不是只看「没抛异常」。

用法：
    .venv\Scripts\python.exe tools\historytest.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
    """按 OpenAI 的规矩检查历史。返回错误说明，合法则返回空串。

    这条规则就是那个 400 的来源，所以这里直接照它实现。
    """
    seen: set[str] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "system":
            if index != 0:
                return f"第 {index} 条是 system，但它不在开头"
            continue
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                call_id = call.get("id")
                if not call_id:
                    return f"第 {index} 条的 tool_call 没有 id"
                seen.add(call_id)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not call_id:
                return f"第 {index} 条 tool 消息没有 tool_call_id"
            if call_id not in seen:
                return (f"第 {index} 条的 tool_call_id={call_id} "
                        f"找不到对应的 tool call（这就是那个 400）")
            continue
        if role == "user":
            continue
        return f"第 {index} 条的角色不认识：{role}"
    return ""


class FakeReply:
    def __init__(self, text="", calls=None):
        self.text = text
        self.tool_calls = calls or []

    @property
    def wants_tools(self):
        return bool(self.tool_calls)


class FakeCall:
    def __init__(self, call_id, name="ui_windows", arguments="{}"):
        self.id = call_id
        self.name = name
        self.arguments = {}
        self.raw_arguments = arguments


class FakeClient:
    """按脚本回放，并把每次收到的历史记下来。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools=None):
        # 深拷一份，免得后面被改
        self.seen.append([dict(m) for m in messages])
        if self.script:
            return self.script.pop(0)
        return FakeReply(text="做完了。")


def make_runner(script, **kwargs):
    from pawpet.ai.agent import AgentRunner

    class Ctx:
        pass

    class Actions:
        def clear_stop(self):
            pass

        def request_stop(self):
            pass

        def blocked(self, _risk):
            return False

    class Callbacks:
        def on_status(self, text):
            pass

        def on_event(self, event):
            pass

        def on_image(self, png, note):
            pass

        def on_finished(self, text):
            pass

        def on_error(self, text):
            pass

        def request_approval(self, request):
            return False

    client = FakeClient(script)
    runner = AgentRunner(client, Ctx(), Actions(), Callbacks(), **kwargs)
    return runner, client


def main() -> int:
    print("工具消息历史合法性测试\n")

    from pawpet.ai.agent import MAX_HISTORY_MESSAGES, AgentRunner  # noqa: F401

    # ============================================ 一、先造出那个真实的坏历史
    print("=== 一、复现：裁剪落在 tool 消息上 ===")

    def alternating(pairs: int, tail_user: bool = False) -> list[dict]:
        """造一段**真实形状**的历史：assistant(tool_calls) 和 tool 成对交替。

        一开始我把所有 assistant 写在一起、所有 tool 写在一起，那是现实中
        不会出现的形状（工具结果必然紧跟它那一条 assistant）。
        用真形状才能验证真问题。
        """
        out: list[dict] = [{"role": "system", "content": "系统"}]
        for index in range(pairs):
            out.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"call_{index}", "type": "function",
                                "function": {"name": "ui_windows", "arguments": "{}"}}],
            })
            out.append({"role": "tool", "tool_call_id": f"call_{index}",
                        "content": f"结果 {index}"})
        if tail_user:
            out.append({"role": "user", "content": "再说一句"})
        return out

    messages = alternating(30, tail_user=True)
    print(f"  构造出 {len(messages)} 条历史（上限 {MAX_HISTORY_MESSAGES}）")

    runner, _client = make_runner([])
    runner.messages = [dict(m) for m in messages]

    # 先看看「老写法」（直接从尾部截）会切到哪里
    naive_cut = len(messages) - (MAX_HISTORY_MESSAGES - 1)
    naive = messages[:1] + messages[naive_cut:]
    naive_role = messages[naive_cut].get("role")
    naive_error = validate(naive)
    print(f"  老写法切在第 {naive_cut} 条（role={naive_role}）")
    print(f"  这份形状下老写法：{naive_error or '居然合法'}")
    print("  （如实记录：交替形状下切点往往落在 assistant 上，老写法也能过。")
    print("    所以这个测试重点是**保证非法历史一定发不出去**，")
    print("    而不是声称复现了线上那个 400。）")

    # 明确构造一个「切在 tool 上」的输入，验证这层保护真的有用
    orphan_case = [{"role": "system", "content": "s"}]
    for index in range(40):
        orphan_case.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"t{index}", "type": "function",
                            "function": {"name": "x", "arguments": "{}"}}],
        })
        orphan_case.append({"role": "tool", "tool_call_id": f"t{index}",
                            "content": "y"})
    # 手工删掉中间一条 assistant，制造出真正的孤儿
    del orphan_case[41]
    check("构造出的输入确实是非法历史（有孤儿 tool）",
          bool(validate(orphan_case)), "构造失败")
    r0, _ = make_runner([])
    r0.messages = [dict(m) for m in orphan_case]
    r0._repair_tool_messages()
    check("兜底之后变合法（这就是这层保护的价值）",
          not validate(r0.messages), validate(r0.messages))

    # ============================================ 二、新写法必须合法
    print("\n=== 二、新写法：裁剪后历史必须合法 ===")

    runner._trim_history()
    after = validate(runner.messages)
    check("裁剪后历史合法", not after, after)
    check("裁剪后条数没超上限",
          len(runner.messages) <= MAX_HISTORY_MESSAGES,
          f"实际 {len(runner.messages)}")
    check("system 还在开头",
          runner.messages and runner.messages[0].get("role") == "system")
    check("第一条不是 tool 消息",
          len(runner.messages) > 1 and runner.messages[1].get("role") != "tool",
          runner.messages[1].get("role") if len(runner.messages) > 1 else "（只有 system）")
    check("裁剪保留了对话内容（不是把历史清空了）",
          len(runner.messages) > 10, f"只剩 {len(runner.messages)} 条")

    # ============================================ 三、各种边界形状
    print("\n=== 三、边界形状 ===")

    shapes = {
        "全是孤儿 tool（最坏情况）": (
            [{"role": "system", "content": "s"}]
            + [{"role": "tool", "tool_call_id": f"c{i}", "content": "x"}
               for i in range(100)]
        ),
        "正好等于上限": (
            [{"role": "system", "content": "s"}]
            + [{"role": "user", "content": f"u{i}"}
               for i in range(MAX_HISTORY_MESSAGES - 1)]
        ),
        "比上限多一条": (
            [{"role": "system", "content": "s"}]
            + [{"role": "user", "content": f"u{i}"}
               for i in range(MAX_HISTORY_MESSAGES)]
        ),
        "只有 system": [{"role": "system", "content": "s"}],
        "空历史": [],
        "真实交替且很长": alternating(50, tail_user=True),
        "真实交替结尾就是 tool": alternating(50),
    }

    for label, shape in shapes.items():
        r, _ = make_runner([])
        r.messages = [dict(m) for m in shape]
        try:
            r._trim_history()
            # 裁剪本身不负责修孤儿（那是 _repair_tool_messages 的事），
            # 所以这里只要求：不超限、system 在开头、不抛异常。
            ok = (len(r.messages) <= MAX_HISTORY_MESSAGES
                  and (not r.messages or r.messages[0].get("role") == "system"))
            check(f"{label} -> 不超限且 system 在开头", ok,
                  f"{len(r.messages)} 条")
        except Exception as exc:  # noqa: BLE001
            check(f"{label} -> 不抛异常", False, f"{type(exc).__name__}: {exc}")

    print("\n  —— 真实的交替历史，裁剪+兜底之后必须完全合法 ——")
    for pairs, tail in ((50, True), (50, False), (28, True), (31, True)):
        r, _ = make_runner([])
        r.messages = [dict(m) for m in alternating(pairs, tail)]
        r._trim_history()
        r._repair_tool_messages()
        error = validate(r.messages)
        check(f"{pairs} 对（tail_user={tail}）裁剪后合法", not error,
              error or f"{len(r.messages)} 条")

    # ============================================ 四、兜底修复
    print("\n=== 四、_repair_tool_messages 兜底 ===")

    broken = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_call_id": "孤儿A", "content": "没有前文"},
        {"role": "user", "content": "用户"},
        {"role": "tool", "tool_call_id": "孤儿B", "content": "也没有前文"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "好的", "type": "function",
                         "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "好的", "content": "这个有前文"},
    ]
    r, _ = make_runner([])
    r.messages = [dict(m) for m in broken]
    dropped = r._repair_tool_messages()
    check("丢掉了 2 条孤儿 tool 消息", dropped == 2, f"实际 {dropped}")
    check("修复后历史合法", not validate(r.messages), validate(r.messages))
    check("有前文的那条 tool 消息被留下",
          any(m.get("tool_call_id") == "好的" for m in r.messages),
          str([m.get("tool_call_id") for m in r.messages]))
    check("user 和 assistant 都没被误删",
          any(m.get("role") == "user" for m in r.messages)
          and any(m.get("role") == "assistant" for m in r.messages))

    r2, _ = make_runner([])
    r2.messages = [{"role": "system", "content": "s"},
                   {"role": "user", "content": "u"}]
    check("干净历史不会被改动", r2._repair_tool_messages() == 0)

    # ============================================ 五、真跑一轮，检查每次请求
    print("\n=== 五、真跑一轮：每次发给模型的历史都必须合法 ===")

    # 造一段已经很长、且边界敏感的历史，再让 agent 真的跑几步
    long_history: list[dict] = [{"role": "system", "content": "s"}]
    for index in range(35):
        long_history.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"old_{index}", "type": "function",
                            "function": {"name": "ui_windows", "arguments": "{}"}}],
        })
        long_history.append({"role": "tool", "tool_call_id": f"old_{index}",
                             "content": f"旧结果 {index}"})

    script = [FakeReply(calls=[FakeCall("new_1")]),
              FakeReply(calls=[FakeCall("new_2")]),
              FakeReply(text="好了。")]
    runner3, client3 = make_runner(script, max_steps=8)
    runner3.messages = [dict(m) for m in long_history]

    class _Ctx:
        pass

    # _run_one 需要 context/actions，这里用一个能返回失败的最小实现，
    # 重点是历史合法性，不是工具真的执行成功
    runner3.context = type("C", (), {})()
    runner3._run_one = lambda call, call_id: (
        runner3._record_tool(call_id, call.name, "（测试里不真执行）", ok=True) or None
    )

    runner3.run("继续")

    check("确实发出了请求", len(client3.seen) >= 2, f"{len(client3.seen)} 次")
    bad = []
    for index, seen in enumerate(client3.seen):
        error = validate(seen)
        if error:
            bad.append(f"第 {index + 1} 次请求：{error}")
    check("每次发出去的历史都合法（不会 400）", not bad, "; ".join(bad[:3]))
    check("每次请求都带了工具定义或至少结构完整",
          all(isinstance(m, dict) for m in client3.seen[-1]),
          "结构异常")

    # ============================================ 收尾
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
