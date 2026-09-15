r"""批量操作 + 合并确认自测。

合并确认这件事有个很微妙的失败方式：**它减少了询问次数，但绝不能
减少信息量**。测错方向的话，用户会变成「闭着眼睛点全部允许」——
那比逐个确认更不安全。

所以测试重点：
* 清单是完整的（用户批的就是将要执行的）
* 高危操作**不参与合并**（不能藏在批量的第 7 条里）
* 没明确同意的按拒绝处理（fail closed）
* 减少的确认次数要真的减少（否则这个功能没意义）

用法：
    .venv\Scripts\python.exe tools\batchtest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai.actions import AuditLog  # noqa: E402

SCRATCH = ROOT / ".cache" / "batch"
os.environ["PAWPET_HOME"] = str(SCRATCH)

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


class FakeCall:
    def __init__(self, call_id, name, arguments=None):
        self.id = call_id
        self.name = name
        self.arguments = arguments or {}
        self.raw_arguments = "{}"


class FakeReply:
    def __init__(self, text="", calls=None):
        self.text = text
        self.tool_calls = calls or []

    @property
    def wants_tools(self):
        return bool(self.tool_calls)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append([dict(m) for m in messages])
        if self.script:
            return self.script.pop(0)
        return FakeReply(text="好了。")


class Actions:
    """可以指定哪些操作需要确认的桩。"""

    def __init__(self, level="confirm"):
        self.level = level
        self.audit = AuditLog()
        self.executed: list[str] = []

    def clear_stop(self):
        pass

    def request_stop(self):
        pass

    def blocked(self, risk):
        return False

    def needs_approval(self, risk):
        if risk == "read":
            return False
        if risk == "confirm":
            return self.level in ("read_only", "confirm")
        return self.level != "full"


class Recorder:
    """记录回调，并可按脚本决定批准哪些操作。"""

    def __init__(self, approve=True, batch_decisions=None):
        self.events = []
        self.approve = approve
        self.batch_decisions = batch_decisions
        self.single_calls = 0
        self.batch_calls = 0
        self.last_batch: list = []

    def on_status(self, text):
        pass

    def on_event(self, event):
        self.events.append(event)

    def on_image(self, png, note):
        pass

    def on_finished(self, text):
        self.finished = text

    def on_error(self, text):
        pass

    def request_approval(self, request):
        self.single_calls += 1
        return self.approve

    def request_approval_batch(self, items):
        self.batch_calls += 1
        self.last_batch = list(items)
        if self.batch_decisions is not None:
            return dict(self.batch_decisions)
        return {item.index: self.approve for item in items}

    def executed_tools(self):
        return [e.tool for e in self.events
                if getattr(e, "kind", "") == "tool"]


class ProbeContext:
    """假装执行工具，记录执行了什么。"""

    def __init__(self):
        self.calls: list[str] = []

    def execute(self, name, arguments):
        self.calls.append(name)
        return True, f"{name} 完成", None


def main() -> int:
    print("小爪批量操作 + 合并确认自测\n")

    from pawpet.ai.batch import (
        MAX_BATCH,
        MAX_LISTED,
        BatchItem,
        build_summary,
        build_title,
        collect,
        should_merge,
    )

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # ================================================== 一、清单完整性
    print("=== 一、清单必须完整（用户批的就是要执行的）===")

    items = [
        BatchItem(index=1, tool="ui_click", summary="点击「保存」"),
        BatchItem(index=2, tool="ui_set_text", summary="输入「周报」"),
        BatchItem(index=3, tool="press_keys", summary="按 Enter"),
    ]
    summary = build_summary(items)
    for item in items:
        check(f"清单里有「{item.summary}」", item.summary in summary, summary)
    check("清单带序号", "1." in summary and "3." in summary, summary)

    many = [BatchItem(index=i, tool="t", summary=f"第 {i} 个操作")
            for i in range(1, 12)]
    folded = build_summary(many)
    check(f"太长时只列前 {MAX_LISTED} 条",
          folded.count(".") >= MAX_LISTED, folded)
    check("折叠时说清还有多少个",
          f"还有 {len(many) - MAX_LISTED} 个" in folded, folded)
    check("折叠不丢数量信息（用户知道一共几个）",
          str(len(many) - MAX_LISTED) in folded, folded)

    check("标题说明是几个操作", "3" in build_title(items), build_title(items))
    check("单个时的标题也通顺",
          "1" in build_title(items[:1]), build_title(items[:1]))

    # ================================================== 二、fail closed
    print("\n=== 二、没明确同意的一律按拒绝 ===")

    decided = collect(items, {1: True, 2: True, 3: True})
    check("全同意时 approved 有 3 个", len(decided.approved) == 3,
          str(decided.approved))
    check("全同意时 denied 为空", not decided.denied)

    partial = collect(items, {1: True, 3: True})
    check("只同意 1、3 时 approved=[1,3]",
          partial.approved == [1, 3], str(partial.approved))
    check("没表态的第 2 条算拒绝",
          2 in partial.denied, str(partial.denied))

    nothing = collect(items, {})
    check("什么都没表态时全部拒绝",
          len(nothing.denied) == 3 and not nothing.approved, str(nothing.denied))

    explicit_deny = collect(items, {1: False, 2: False, 3: False})
    check("明确拒绝时 approved 为空", not explicit_deny.approved)
    check("all_denied 为真", explicit_deny.all_denied)
    check("全同意时 all_approved 为真", decided.all_approved)

    timed = collect(items, {1: True}, timeout=True)
    check("超时被标出来", timed.timed_out)
    check("超时后没表态的算拒绝", 2 in timed.denied and 3 in timed.denied)

    # ================================================== 三、什么时候合并
    print("\n=== 三、什么时候才合并 ===")

    check("一个操作不包装成批次", not should_merge(items[:1]))
    check("两个以上才合并", should_merge(items[:2]))
    check("三个也合并", should_merge(items))

    # ================================================== 四、Agent 集成
    print("\n=== 四、Agent：多个操作只问一次 ===")

    from pawpet.ai.agent import AgentRunner

    store = None
    from pawpet.store import Store

    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()

    actions = Actions(level="confirm")
    context = ProbeContext()

    # 一轮里三个需要确认的操作
    script = [
        FakeReply(calls=[
            FakeCall("c1", "ui_click", {"name": "保存"}),
            FakeCall("c2", "ui_set_text", {"name": "文件名", "text": "周报"}),
            FakeCall("c3", "press_keys", {"keys": "enter"}),
        ]),
        FakeReply(text="做完了。"),
    ]
    recorder = Recorder(approve=True)
    runner = AgentRunner(FakeClient(script), context, actions, recorder, max_steps=4)
    runner.run("保存一下")

    check("三个操作只弹了一次确认", recorder.batch_calls == 1,
          f"batch={recorder.batch_calls} single={recorder.single_calls}")
    check("没有退化成逐个确认", recorder.single_calls == 0,
          str(recorder.single_calls))
    check("批次里就是那三个操作", len(recorder.last_batch) == 3,
          str([i.summary for i in recorder.last_batch]))
    check("三个都真的执行了", len(context.calls) == 3, str(context.calls))
    check("执行顺序和模型给的一致",
          context.calls == ["ui_click", "ui_set_text", "press_keys"],
          str(context.calls))

    # ================================================== 五、部分拒绝
    print("\n=== 五、部分拒绝时只跳过被拒的 ===")

    actions5 = Actions(level="confirm")
    context5 = ProbeContext()
    script5 = [
        FakeReply(calls=[
            FakeCall("c1", "ui_click", {"name": "保存"}),
            FakeCall("c2", "ui_set_text", {"name": "文件名", "text": "周报"}),
            FakeCall("c3", "press_keys", {"keys": "enter"}),
        ]),
        FakeReply(text="好。"),
    ]
    recorder5 = Recorder(batch_decisions={1: True, 2: False, 3: True})
    runner5 = AgentRunner(FakeClient(script5), context5, actions5, recorder5, max_steps=4)
    runner5.run("保存")

    check("被拒的那个没有执行",
          "ui_set_text" not in context5.calls, str(context5.calls))
    check("其余两个照常执行",
          context5.calls == ["ui_click", "press_keys"], str(context5.calls))
    check("只弹了一次确认", recorder5.batch_calls == 1, str(recorder5.batch_calls))

    tool_messages = [m for m in runner5.messages if m.get("role") == "tool"]
    denied_msgs = [m for m in tool_messages
                   if "拒绝" in (m.get("content") or "")]
    check("模型收到了拒绝说明", len(denied_msgs) == 1, str(len(denied_msgs)))
    check("拒绝说明里让它别换说法重试",
          any("不要换个说法" in (m.get("content") or "") for m in denied_msgs),
          str([(m.get("content") or "")[:60] for m in denied_msgs]))
    notes = [m for m in runner5.messages
             if m.get("role") == "user" and "[系统]" in (m.get("content") or "")]
    check("明确告诉模型这一批的结果",
          any("被拒绝" in (m.get("content") or "") for m in notes),
          str([(m.get("content") or "")[:80] for m in notes]))

    # ================================================== 六、全部拒绝
    print("\n=== 六、全部拒绝 ===")

    actions6 = Actions(level="confirm")
    context6 = ProbeContext()
    script6 = [
        FakeReply(calls=[
            FakeCall("c1", "ui_click", {"name": "保存"}),
            FakeCall("c2", "press_keys", {"keys": "enter"}),
        ]),
        FakeReply(text="好。"),
    ]
    recorder6 = Recorder(approve=False)
    runner6 = AgentRunner(FakeClient(script6), context6, actions6, recorder6, max_steps=4)
    runner6.run("保存")
    check("全拒时一个都没执行", not context6.calls, str(context6.calls))
    check("仍然只问了一次", recorder6.batch_calls == 1, str(recorder6.batch_calls))

    # ================================================== 七、高危不合并
    print("\n=== 七、高危操作绝不合并（不能藏在批量里）===")

    from pawpet.ai.tools import TOOL_INDEX

    danger_names = [name for name, spec in TOOL_INDEX.items()
                    if spec.risk == "danger"]
    check("确实有 danger 级工具（不是空测）", bool(danger_names),
          str(danger_names))

    if danger_names:
        danger_tool = danger_names[0]
        actions7 = Actions(level="confirm")
        context7 = ProbeContext()
        script7 = [
            FakeReply(calls=[
                FakeCall("c1", "ui_click", {"name": "保存"}),
                FakeCall("c2", danger_tool, {"command": "echo hi"}),
            ]),
            FakeReply(text="好。"),
        ]
        recorder7 = Recorder(approve=True)
        runner7 = AgentRunner(FakeClient(script7), context7, actions7,
                              recorder7, max_steps=4)
        runner7.run("试试")

        check("高危操作走的是单独确认", recorder7.single_calls >= 1,
              f"single={recorder7.single_calls}")
        check("批次里没有高危操作",
              all(item.risk != "danger" for item in recorder7.last_batch),
              str([(i.tool, i.risk) for i in recorder7.last_batch]))
        check("高危和非高危操作都执行了",
              len(context7.calls) == 2, str(context7.calls))

    # ================================================== 八、单个操作不乱包装
    print("\n=== 八、只有一个操作时不包装 ===")

    actions8 = Actions(level="confirm")
    context8 = ProbeContext()
    script8 = [
        FakeReply(calls=[FakeCall("c1", "ui_click", {"name": "保存"})]),
        FakeReply(text="好。"),
    ]
    recorder8 = Recorder(approve=True)
    runner8 = AgentRunner(FakeClient(script8), context8, actions8, recorder8, max_steps=3)
    runner8.run("点击保存")
    check("一个操作走普通确认", recorder8.single_calls == 1,
          f"single={recorder8.single_calls}")
    check("一个操作不生成批次", recorder8.batch_calls == 0,
          f"batch={recorder8.batch_calls}")

    # ================================================== 九、只读操作不问
    print("\n=== 九、只读操作不参与确认 ===")

    actions9 = Actions(level="confirm")
    context9 = ProbeContext()
    script9 = [
        FakeReply(calls=[
            FakeCall("c1", "ui_windows", {}),
            FakeCall("c2", "ui_click", {"name": "保存"}),
            FakeCall("c3", "type_text", {"text": "周报"}),
        ]),
        FakeReply(text="好。"),
    ]
    recorder9 = Recorder(approve=True)
    runner9 = AgentRunner(FakeClient(script9), context9, actions9, recorder9, max_steps=3)
    runner9.run("看看再点")
    check("只读操作不进批次（批次里只有那两个写操作）",
          [i.tool for i in recorder9.last_batch] == ["ui_click", "type_text"],
          str([i.tool for i in recorder9.last_batch]))
    check("只问了一次（没有被只读操作额外打扰）",
          recorder9.single_calls + recorder9.batch_calls == 1,
          f"single={recorder9.single_calls} batch={recorder9.batch_calls}")
    check("三个操作都执行了", len(context9.calls) == 3, str(context9.calls))
    check("只读操作先执行（顺序没被打乱）",
          context9.calls[0] == "ui_windows", str(context9.calls))

    # ================================================== 十、自动模式下不问
    print("\n=== 十、权限够高时不问 ===")

    actions10 = Actions(level="full")
    context10 = ProbeContext()
    script10 = [
        FakeReply(calls=[
            FakeCall("c1", "ui_click", {"name": "保存"}),
            FakeCall("c2", "press_keys", {"keys": "enter"}),
        ]),
        FakeReply(text="好。"),
    ]
    recorder10 = Recorder(approve=True)
    runner10 = AgentRunner(FakeClient(script10), context10, actions10,
                           recorder10, max_steps=3)
    runner10.run("保存")
    check("完全自动时一次都不问",
          recorder10.single_calls == 0 and recorder10.batch_calls == 0,
          f"single={recorder10.single_calls} batch={recorder10.batch_calls}")
    check("但操作照常执行", len(context10.calls) == 2, str(context10.calls))

    # ================================================== 十一、审计
    print("\n=== 十一、审计日志记得下每条 ===")

    audit = actions5.audit.recent(20)
    entries = {entry["action"]: entry["approved"] for entry in audit}
    check("被允许的记成 user",
          entries.get("ui_click") == "user", str(entries))
    check("被拒绝的记成 denied",
          entries.get("ui_set_text") == "denied", str(entries))
    check("批次里每条都有记录",
          "ui_click" in entries and "ui_set_text" in entries
          and "press_keys" in entries, str(list(entries)))

    # ================================================== 十二、截图开关
    print("\n=== 十二、自动截图开关真的生效（原来是个死设置）===")

    from pawpet.ai.agent import AgentRunner as Runner

    look_context = ProbeContext()
    runner_a = Runner(FakeClient([FakeReply(text="好。")]), look_context,
                      Actions(level="full"), Recorder(), max_steps=2)
    runner_a.auto_screenshot = True
    runner_a.run("看看屏幕")
    check("开关打开时会自动截一张",
          "screenshot" in look_context.calls, str(look_context.calls))

    look_context2 = ProbeContext()
    runner_b = Runner(FakeClient([FakeReply(text="好。")]), look_context2,
                      Actions(level="full"), Recorder(), max_steps=2)
    runner_b.auto_screenshot = False
    runner_b.run("看看屏幕")
    check("开关关掉时不截",
          "screenshot" not in look_context2.calls, str(look_context2.calls))

    # ================================================== 十三、提示词
    print("\n=== 十三、提示词教模型批量 ===")

    from pawpet.ai.agent import SYSTEM_PROMPT

    check("提示词里说了要批量做", "批量做" in SYSTEM_PROMPT)
    check("提示词里说了截图很贵（几万 token）",
          "几万 token" in SYSTEM_PROMPT, "没说明成本")
    check("提示词里给了判断标准（要不要用上一步的结果）",
          "需不需要用到上一步的结果" in SYSTEM_PROMPT)
    check("提示词里说了会合成一次确认",
          "一次" in SYSTEM_PROMPT and "确认" in SYSTEM_PROMPT)
    check("提示词里说明了合并上限", "6" in SYSTEM_PROMPT, "没说上限")
    check("提示词里说了高危不合并",
          "危险操作不参与合并" in SYSTEM_PROMPT or "高危" in SYSTEM_PROMPT,
          "没说明高危例外")

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
