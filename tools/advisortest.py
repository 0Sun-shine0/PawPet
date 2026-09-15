r"""失败自纠自测。

这个功能的重点是**区分「该重试」和「该换路」**：
* 判错成「该重试」→ 模型把步数全烧在同一个失败动作上（就是原来的毛病）
* 判错成「该换路」→ 本来改个参数就成的事被放弃

另外要验证止损真的拦得住 —— 光靠提示词是劝不住模型重复的。

用法：
    .venv\Scripts\python.exe tools\advisortest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Actions 桩要用到审计日志，模块级就得可导入
from pawpet.ai.actions import AuditLog  # noqa: E402

SCRATCH = ROOT / ".cache" / "advisor"
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
        return FakeReply(text="停下来了。")


class Recorder:
    def __init__(self):
        self.events = []
        self.statuses = []

    def on_status(self, text):
        self.statuses.append(text)

    def on_event(self, event):
        self.events.append(event)

    def on_image(self, png, note):
        pass

    def on_finished(self, text):
        self.finished = text

    def on_error(self, text):
        self.events.append(type("E", (), {"kind": "error", "text": text,
                                          "tool": "", "risk": "", "ok": False,
                                          "detail": "", "seconds": 0.0,
                                          "recovery": ""})())

    def request_approval(self, request):
        return True


class Actions:
    def __init__(self):
        self.level = "full"
        # _run_one 每次执行都会写审计日志，所以桩里必须有 audit。
        # （先前漏了它，结果整轮直接抛 AttributeError，测试看起来像
        #   「工具没失败」，其实是根本没跑起来。）
        self.audit = AuditLog()

    def clear_stop(self):
        pass

    def request_stop(self):
        pass

    def blocked(self, risk):
        return False

    def needs_approval(self, risk):
        return False


def main() -> int:
    print("小爪失败自纠自测\n")

    from pawpet.ai.advisor import (
        SAME_ACTION_BLOCK,
        SAME_ACTION_WARN,
        Advice,
        Advisor,
        KIND_BAD_ARGUMENT,
        KIND_DENIED,
        KIND_MISSING_DEP,
        KIND_NOT_FOUND,
        KIND_PERMISSION,
        KIND_TIMEOUT,
        KIND_TRANSIENT,
        KIND_UNKNOWN,
        action_key,
        classify,
    )

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # ================================================== 一、分类
    print("=== 一、失败分类 ===")

    cases = [
        ("UI Automation 查询超时（>5s），已放弃（可能有无响应的程序）", KIND_TIMEOUT),
        ("读控件超时", KIND_TIMEOUT),
        ("The operation timed out", KIND_TIMEOUT),
        ("窗口「记事本」里没有读到控件", KIND_NOT_FOUND),
        ("没找到标题含「X」的窗口", KIND_NOT_FOUND),
        ("没有这个文件：D:\\a.txt", KIND_NOT_FOUND),
        ("未知工具：foo", KIND_NOT_FOUND),
        ("当前是「只读」模式，不允许执行会改变状态的动作（write_file）。", KIND_PERMISSION),
        ("已被安全策略拦截", KIND_PERMISSION),
        ("用户拒绝了这个操作，请换一种方式", KIND_DENIED),
        ("需要指定窗口标题的一部分", KIND_BAD_ARGUMENT),
        ("参数 x 不能为空", KIND_BAD_ARGUMENT),
        ("没有安装 opencv", KIND_MISSING_DEP),
        ("No module named 'cv2'", KIND_MISSING_DEP),
        ("窗口正在变化，稍后重试", KIND_TRANSIENT),
        ("", KIND_UNKNOWN),
        ("某种从没见过的问题", KIND_UNKNOWN),
    ]
    for text, want in cases:
        got = classify(text)
        check(f"分类 {want:12s} <- {text[:34] or '(空)'}", got == want,
              f"实际 {got}")

    # ================================================== 二、只该重试两类
    print("\n=== 二、只有两类值得原样重试 ===")

    advisor = Advisor()
    retryable_cases = {
        KIND_BAD_ARGUMENT: True,
        KIND_TRANSIENT: True,
        KIND_TIMEOUT: False,
        KIND_NOT_FOUND: False,
        KIND_PERMISSION: False,
        KIND_DENIED: False,
        KIND_MISSING_DEP: False,
        KIND_UNKNOWN: False,
    }
    samples = {
        KIND_BAD_ARGUMENT: "参数 x 不能为空",
        KIND_TRANSIENT: "窗口正在变化，稍后重试",
        KIND_TIMEOUT: "读控件超时",
        KIND_NOT_FOUND: "没找到窗口",
        KIND_PERMISSION: "只读模式下不允许",
        KIND_DENIED: "用户拒绝了",
        KIND_MISSING_DEP: "没有安装 cv2",
        KIND_UNKNOWN: "说不清的问题",
    }
    for kind, want in retryable_cases.items():
        fresh = Advisor()
        advice = fresh.after_failure("t", {"a": 1}, samples[kind])
        check(f"{kind:12s} retryable={advice.retryable}（期望 {want}）",
              advice.retryable == want, f"实际 {advice.retryable}")

    # ================================================== 三、建议要可执行
    print("\n=== 三、建议必须能直接照着做 ===")

    for kind, marker in [
        (KIND_TIMEOUT, "截图"),
        (KIND_NOT_FOUND, "重新确认"),
        (KIND_PERMISSION, "调高"),
        (KIND_DENIED, "不要换个说法"),
        (KIND_BAD_ARGUMENT, "值得改对再试"),
    ]:
        advice = Advisor().after_failure("t", {}, samples[kind])
        check(f"{kind:12s} 的建议里包含关键动作「{marker}」",
              marker in advice.hint, advice.hint[:60])

    timeout_advice = Advisor().after_failure("ui_controls", {}, samples[KIND_TIMEOUT])
    check("超时建议里明确说了「不要再重试」",
          "不要再重试" in timeout_advice.hint or "不要重试" in timeout_advice.hint,
          timeout_advice.hint[:60])
    check("超时建议给了替代工具（screenshot / ui_click）",
          "screenshot" in timeout_advice.hint and "ui_click" in timeout_advice.hint)

    denied_advice = Advisor().after_failure("click", {}, samples[KIND_DENIED])
    check("被拒绝时标记为需要问用户", denied_advice.escalate)
    perm_advice = Advisor().after_failure("write_file", {}, samples[KIND_PERMISSION])
    check("被权限拦下时标记为需要问用户", perm_advice.escalate)
    check("超时不算需要问用户", not timeout_advice.escalate)

    # 每条建议都得有标签，界面要显示
    for kind, sample in samples.items():
        advice = Advisor().after_failure("t", {}, sample)
        check(f"{kind:12s} 有给界面看的标签", bool(advice.label), "标签为空")

    # ================================================== 四、重复检测
    print("\n=== 四、同一个动作反复失败 ===")

    repeated = Advisor()
    args = {"window": "记事本", "name": "保存"}
    first = repeated.after_failure("ui_click", args, "读控件超时")
    check("第一次失败不警告", "已经失败" not in first.hint, first.hint[-80:])

    second = repeated.after_failure("ui_click", args, "读控件超时")
    check(f"第 {SAME_ACTION_WARN} 次失败开始警告",
          "已经失败" in second.hint, second.hint[-120:])
    check("警告后不再允许原样重试", not second.retryable)

    third = repeated.after_failure("ui_click", args, "读控件超时")
    check("计数正确", third.repeats == 3, str(third.repeats))

    # 参数顺序不同要算同一个动作
    key_a = action_key("ui_click", {"a": 1, "b": 2})
    key_b = action_key("ui_click", {"b": 2, "a": 1})
    check("参数顺序不同算同一个动作", key_a == key_b, f"{key_a} vs {key_b}")
    check("工具不同算不同动作",
          action_key("ui_click", {"a": 1}) != action_key("ui_set_text", {"a": 1}))

    # 不同的动作互不干扰
    mixed = Advisor()
    mixed.after_failure("ui_click", {"x": 1}, "读控件超时")
    other = mixed.after_failure("ui_click", {"x": 2}, "读控件超时")
    check("参数不同时不误判成重复", other.repeats == 1, str(other.repeats))

    # 警告不刷屏：同一个动作重复失败，那条「已经失败 N 次」只该出现一次
    # （工具累计那条是另一条文案，用「这条路累计」区分开）
    spam = Advisor()
    hints = []
    for _ in range(6):
        hints.append(spam.after_failure("ui_click", args, "读控件超时").hint)
    action_warnings = sum(1 for hint in hints if "已经失败" in hint)
    tool_warnings = sum(1 for hint in hints if "这条路累计" in hint)
    check("重复警告只出现一次（不刷屏）", action_warnings == 1,
          f"出现了 {action_warnings} 次")
    check("工具级警告也只出现一次", tool_warnings == 1,
          f"出现了 {tool_warnings} 次")
    # 计数是单独放在 tally 里的（agent 会把它拼到【恢复建议】末尾），
    # 不在 hint 里 —— 所以这里查 tally，不查 hint。
    check("每次都带上计数，模型知道自己在第几次",
          all("（这一步已失败" in advice.tally for advice in
              [spam.after_failure("ui_click", args, "读控件超时")
               for _ in range(3)]),
          "tally 缺失")

    # ================================================== 五、止损
    print("\n=== 五、止损：拦掉注定失败的调用 ===")

    stopper = Advisor()
    for _ in range(SAME_ACTION_BLOCK - 1):
        stopper.after_failure("ui_click", args, "读控件超时")

    check(f"失败 {SAME_ACTION_BLOCK - 1} 次时还不拦",
          stopper.before_call("ui_click", args) is None)

    stopper.after_failure("ui_click", args, "读控件超时")
    blocked = stopper.before_call("ui_click", args)
    check(f"失败 {SAME_ACTION_BLOCK} 次后被拦下", blocked is not None)
    check("拦截建议里标了 blocked", blocked is not None and blocked.blocked)
    check("拦截建议里说了试了几次",
          blocked is not None and str(SAME_ACTION_BLOCK) in blocked.hint,
          blocked.hint[:80] if blocked else "")
    check("拦截建议里让模型换做法",
          blocked is not None and "换" in blocked.hint,
          blocked.hint[-80:] if blocked else "")
    check("拦截建议里不再允许重试",
          blocked is not None and not blocked.retryable)

    check("换个参数就不拦",
          stopper.before_call("ui_click", {"不一样": 1}) is None)
    check("换个工具也不拦",
          stopper.before_call("ui_set_text", args) is None)

    # 同一个工具整体不通
    tool_level = Advisor()
    advices = [
        tool_level.after_failure("ui_controls", {"w": index}, "读控件超时")
        for index in range(6)
    ]
    check("同一工具累计失败会提示整体换路",
          any("这条路累计" in advice.hint for advice in advices),
          str([a.hint[-40:] for a in advices[-1:]]))
    check("累计计数出现在 tally 里",
          any("累计失败" in advice.tally for advice in advices),
          str([a.tally for a in advices[-2:]]))

    # ================================================== 六、reset
    print("\n=== 六、任务之间互不干扰 ===")

    resetter = Advisor()
    for _ in range(SAME_ACTION_BLOCK):
        resetter.after_failure("ui_click", args, "读控件超时")
    check("reset 前会被拦", resetter.before_call("ui_click", args) is not None)
    resetter.reset()
    check("reset 后不再拦", resetter.before_call("ui_click", args) is None)
    check("reset 后计数归零", resetter.count_key(action_key("ui_click", args)) == 0)
    check("reset 后没有失败记录", not resetter.failures)

    # ================================================== 七、概览
    print("\n=== 七、失败概览 ===")

    summarize = Advisor()
    check("没失败时概览为空", summarize.summary() == "", summarize.summary())
    summarize.after_failure("ui_click", {"a": 1}, "超时")
    summarize.after_failure("ui_click", {"a": 2}, "超时")
    summarize.after_failure("screenshot", {}, "没找到")
    text = summarize.summary()
    check("概览里有次数", "3" in text, text)
    check("概览按次数排序（ui_click 在前）",
          text.index("ui_click") < text.index("screenshot"), text)

    # ================================================== 八、Agent 集成
    print("\n=== 八、Agent 集成：建议真的回给了模型 ===")

    from pawpet.ai.agent import SYSTEM_PROMPT, AgentRunner

    runner = AgentRunner(FakeClient([]), object(), Actions(), Recorder())
    # 直接造一个必然失败的调用：未知工具
    runner._record_tool("c1", "nope", "未知工具：nope", ok=False)
    check("未知工具会被记进历史", any(
        m.get("role") == "tool" for m in runner.messages))

    # 用真实工具走一遍失败路径（ui_controls 传不存在的窗口 → not_found）
    from pawpet.ai.tools import ToolContext
    from pawpet.ai.vision import ScreenCapture
    from pawpet.store import Store

    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()
    actions = Actions()
    context = ToolContext(ScreenCapture(), actions, store)
    recorder = Recorder()
    # 注意：窗口名要用真实窗口里**绝不可能出现**的串。
    # 我先前用了纯中文的假名字，结果被 find_window 的模糊匹配命中了
    # （它按子串找窗口，中文很容易撞上），于是工具并没有失败。
    MISSING = "ZZQQ_no_such_window_9931"
    client = FakeClient([
        FakeReply(calls=[FakeCall("c1", "ui_controls", {"window": MISSING})]),
        FakeReply(text="做不了。"),
    ])
    runner2 = AgentRunner(client, context, actions, recorder, max_steps=5)
    runner2.run("试试看")

    tool_events = [e for e in recorder.events
                   if getattr(e, "kind", "") == "tool" and not e.ok]
    check("失败事件被记录", len(tool_events) >= 1, str(len(tool_events)))
    check("失败事件带了 recovery 标签",
          bool(tool_events) and bool(tool_events[0].recovery),
          tool_events[0].recovery if tool_events else "（没有事件）")

    tool_messages = [m for m in runner2.messages if m.get("role") == "tool"]
    check("回给模型的消息里有【恢复建议】",
          any("恢复建议" in (m.get("content") or "") for m in tool_messages),
          str([(m.get("content") or "")[:60] for m in tool_messages]))
    check("建议里包含具体下一步",
          any(("重新确认" in (m.get("content") or "")
               or "换" in (m.get("content") or ""))
              for m in tool_messages),
          "建议不够具体")

    # ================================================== 九、止损在 Agent 里生效
    print("\n=== 九、止损在 Agent 里真的拦住 ===")

    repeat_script = [
        FakeReply(calls=[FakeCall(f"c{i}", "ui_controls", {"window": MISSING})])
        for i in range(6)
    ]
    repeat_script.append(FakeReply(text="换不了，停。"))
    recorder3 = Recorder()
    client3 = FakeClient(repeat_script)
    runner3 = AgentRunner(client3, context, actions, recorder3, max_steps=8)
    runner3.run("一直试")

    blocked_events = [e for e in recorder3.events
                      if getattr(e, "text", "") == "已拦截重复失败的操作"]
    check("重复失败后出现了拦截事件", len(blocked_events) >= 1,
          f"拦截 {len(blocked_events)} 次")
    check("拦截事件也带 recovery",
          bool(blocked_events) and bool(blocked_events[0].recovery),
          blocked_events[0].recovery if blocked_events else "")
    check("拦截不会一直发生（说明调用确实被拦了）",
          len(blocked_events) >= 1)

    blocked_messages = [
        m for m in runner3.messages
        if m.get("role") == "tool" and "没有执行" in (m.get("content") or "")
    ]
    check("模型收到「这次没有执行」的说明", len(blocked_messages) >= 1,
          str(len(blocked_messages)))

    # ================================================== 十、任务之间清空
    print("\n=== 十、新任务不会带着旧失败记录 ===")

    recorder4 = Recorder()
    client4 = FakeClient([FakeReply(text="好。")])
    runner4 = AgentRunner(client4, context, actions, recorder4, max_steps=3)
    runner4.advisor.after_failure("ui_click", {"a": 1}, "超时")
    runner4.run("新任务")
    check("run() 会清空上一轮的失败记录",
          not runner4.advisor.failures or
          all(f.tool != "ui_click" or f.detail != "超时"
              for f in runner4.advisor.failures),
          str([(f.tool, f.detail) for f in runner4.advisor.failures]))

    # ================================================== 十一、提示词
    print("\n=== 十一、系统提示词 ===")

    check("提示词里有「失败之后怎么办」",
          "失败之后怎么办" in SYSTEM_PROMPT, "缺少这一节")
    check("提示词里区分了「只该重试哪种」",
          "值得原样重试" in SYSTEM_PROMPT, "没说清什么才值得重试")
    check("提示词里说了超时要改用截图",
          "截图" in SYSTEM_PROMPT and "重试一百次" in SYSTEM_PROMPT)
    check("提示词里说了会被直接拦掉",
          "直接拦掉" in SYSTEM_PROMPT, "没提前告知止损")
    check("提示词禁止把没做成说成做成了",
          "做成了" in SYSTEM_PROMPT, "缺少这条约束")

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
