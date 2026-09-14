"""AI 模块自测：验证屏幕捕获、坐标换算、动作安全策略、Agent 循环。

不联网、不需要 API Key —— 用一个假的客户端把整条链路跑通。
用法：
    .venv\\Scripts\\python.exe tools\\aitest.py
"""

from __future__ import annotations

import os
import sys
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


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    print("小爪 AI 模块自测")

    from pawpet.ai.actions import (
        LEVEL_AUTO,
        LEVEL_CONFIRM,
        LEVEL_FULL,
        LEVEL_READ_ONLY,
        AuditLog,
        DesktopActions,
        Risk,
    )
    from pawpet.ai.agent import AgentRunner, StepEvent
    from pawpet.ai.client import AIClient, ChatReply, ToolCall
    from pawpet.ai.tools import TOOLS, TOOL_INDEX, ToolContext, openai_tools
    from pawpet.ai.vision import ScreenCapture, downscale_png
    from pawpet.store import Store

    # ------------------------------------------------------------ 屏幕捕获
    section("屏幕捕获与坐标换算")
    capture = ScreenCapture()
    check("捕获模块可用", capture.available, capture.status())

    monitors = capture.monitors()
    check("能枚举显示器", len(monitors) >= 1, f"实际 {len(monitors)}")

    shot = capture.grab(monitor=1 if len(monitors) > 1 else 0, with_preview=True)
    check("截图成功", shot.ok, shot.message)
    if shot.ok:
        print(f"       真实 {shot.width}x{shot.height} → 模型 {shot.model_width}x{shot.model_height}"
              f"（缩放 {shot.scale_x:.2f}）")
        check("模型图不超过上限", max(shot.model_width, shot.model_height) <= 1400,
              f"实际 {max(shot.model_width, shot.model_height)}")
        check("生成了 JPEG", len(shot.model_jpeg) > 1000, f"{len(shot.model_jpeg)} 字节")
        check("生成了 PNG 预览", len(shot.preview_png) > 1000, f"{len(shot.preview_png)} 字节")
        check("data URL 格式正确", shot.data_url().startswith("data:image/jpeg;base64,"))

        # 坐标换算必须可逆，否则点击会全部偏掉
        mid_x = shot.model_width // 2
        mid_y = shot.model_height // 2
        real_x, real_y = shot.to_screen(mid_x, mid_y)
        back_x, back_y = shot.to_model(real_x, real_y)
        check("坐标换算可逆（模型→真实→模型）",
              abs(back_x - mid_x) <= 1 and abs(back_y - mid_y) <= 1,
              f"{mid_x},{mid_y} → {real_x},{real_y} → {back_x},{back_y}")
        check("换算后的真实坐标在屏幕内",
              0 <= real_x < shot.width and 0 <= real_y < shot.height,
              f"({real_x}, {real_y}) 屏幕 {shot.width}x{shot.height}")

        small = downscale_png(shot.preview_png, 400)
        check("预览缩略图变小了", len(small) < len(shot.preview_png),
              f"{len(shot.preview_png)} → {len(small)}")

    # ------------------------------------------------------------ 安全策略
    section("动作安全策略")
    actions = DesktopActions(AuditLog())
    check("桌面控制可用", actions.available, actions.status())

    actions.level = LEVEL_READ_ONLY
    check("只读模式下拦截点击", actions.blocked(Risk.CONFIRM) is True)
    check("只读模式下拦截命令", actions.blocked(Risk.DANGER) is True)
    check("只读模式下放行截图", actions.blocked(Risk.READ) is False)

    actions.level = LEVEL_CONFIRM
    check("逐步确认下点击要审批", actions.needs_approval(Risk.CONFIRM) is True)
    check("逐步确认下命令要审批", actions.needs_approval(Risk.DANGER) is True)
    check("逐步确认下截图不要审批", actions.needs_approval(Risk.READ) is False)

    actions.level = LEVEL_AUTO
    check("自动模式下点击免审批", actions.needs_approval(Risk.CONFIRM) is False)
    check("自动模式下命令仍需审批", actions.needs_approval(Risk.DANGER) is True)

    actions.level = LEVEL_FULL
    check("完全自动下命令免审批", actions.needs_approval(Risk.DANGER) is False)
    actions.level = LEVEL_CONFIRM

    # ------------------------------------------------------------ 审计
    section("审计日志")
    audit = AuditLog(limit=5)
    for i in range(7):
        audit.add("click", f"第 {i} 次", Risk.CONFIRM, "user")
    check("审计有上限", len(audit.recent(100)) == 5, f"实际 {len(audit.recent(100))}")
    check("审计保留最新", audit.recent(1)[0]["detail"] == "第 6 次")

    # ------------------------------------------------------------ 工具集
    section("工具集")
    names = [t.name for t in TOOLS]
    check("工具数量合理", len(names) >= 15, f"实际 {len(names)}")
    for required in ("screenshot", "click", "type_text", "press_keys", "add_task", "run_command"):
        check(f"包含 {required}", required in names)
    check("工具名唯一", len(names) == len(set(names)))

    converted = openai_tools()
    check("可转成 OpenAI 格式", len(converted) == len(TOOLS))
    check("转出的格式有 function 字段", all("function" in item for item in converted))
    check("每个工具都有描述",
          all(item["function"]["description"] for item in converted))
    check("每个工具都有 parameters",
          all(item["function"]["parameters"].get("type") == "object" for item in converted))

    # ------------------------------------------------------- 危险命令拦截
    section("危险命令拦截")
    for bad in ("format c:", "diskpart", "shutdown /s", "vssadmin delete shadows"):
        result = actions.run_command(bad)
        check(f"拒绝 {bad!r}", result.ok is False, result.message[:40])
    result = actions.open_app("format c:")
    check("拒绝 open_app 里的危险命令", result.ok is False)

    # ------------------------------------------------------- 剪贴板往返
    section("剪贴板与中文输入")
    ok = actions.set_clipboard("小爪测试文本 ✓")
    check("写入剪贴板", ok)
    if ok:
        text = actions.clipboard_text()
        check("读回剪贴板一致", text == "小爪测试文本 ✓", f"实际 {text!r}")

    # ------------------------------------------------------- 工具执行器
    section("工具执行器")
    import shutil

    scratch = ROOT / ".cache" / "aitest"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()

    capture2 = ScreenCapture()
    actions2 = DesktopActions(AuditLog())
    context = ToolContext(capture2, actions2, store)
    context.last_shot = capture2.grab(monitor=1 if len(capture2.monitors()) > 1 else 0)

    ok, text, bundle = context.execute("screenshot", {"monitor": 1})
    check("screenshot 工具可用", ok and bundle is not None, text[:60])
    if bundle:
        check("截图包里有预览图", len(bundle.get("preview") or b"") > 1000)
        check("截图包里有 data URL",
              str(bundle.get("data_url") or "").startswith("data:image/jpeg;base64,"))
        check("截图包里带坐标说明", "截图信息" in str(bundle.get("note") or ""),
              str(bundle.get("note"))[:60])

    ok, text, _ = context.execute("screen_info", {})
    check("screen_info 返回信息", ok and "真实分辨率" in text, text[:60])

    ok, text, _ = context.execute("list_windows", {})
    check("list_windows 可用", ok, text[:60])

    ok, text, _ = context.execute("add_task", {"text": "AI 加的待办", "priority": 1})
    check("add_task 写进数据", ok and len(store.tasks) == 1, text[:60])
    check("待办优先级正确", store.tasks[0]["priority"] == 1 if store.tasks else False)

    ok, text, _ = context.execute("add_reminder", {"title": "喝水", "time": "10:30"})
    check("add_reminder 写进数据", ok and len(store.reminders) == 1, text[:60])

    ok, text, _ = context.execute("save_note", {"title": "AI 便签", "text": "内容"})
    check("save_note 写进数据", ok and any(n["title"] == "AI 便签" for n in store.notes),
          text[:60])

    ok, text, _ = context.execute("不存在的工具", {})
    check("未知工具被拒绝", ok is False)

    # 坐标映射：模型坐标要能落到真实屏幕内
    if context.last_shot is not None:
        s = context.last_shot
        sx, sy = context.to_screen(s.model_width // 2, s.model_height // 2)
        check("工具层坐标映射在屏幕内",
              0 <= sx < s.width and 0 <= sy < s.height, f"({sx}, {sy})")
        # 故意给一个超出截图的坐标（像是直接给了真实坐标），不应崩
        sx2, sy2 = context.to_screen(s.width - 2, s.height - 2)
        check("超大坐标被夹回屏幕内",
              0 <= sx2 < s.width and 0 <= sy2 < s.height, f"({sx2}, {sy2})")

    # ------------------------------------------------------------ 假客户端
    section("Agent 循环（用假客户端）")

    class FakeClient:
        """按预设脚本依次返回工具调用，最后给一段总结。"""

        def __init__(self, script: list[ChatReply]) -> None:
            self.script = list(script)
            self.calls = 0

        @property
        def configured(self) -> bool:
            return True

        def chat(self, messages, tools=None, **kwargs) -> ChatReply:
            self.calls += 1
            if self.script:
                return self.script.pop(0)
            return ChatReply(text="全部完成。")

    class Recorder:
        def __init__(self, approve: bool = True) -> None:
            self.events: list[StepEvent] = []
            self.approvals: list[str] = []
            self.statuses: list[str] = []
            self.final = ""
            self.errors: list[str] = []
            self.approve = approve

        # 对应 AgentCallbacks 的接口
        def on_status(self, text): self.statuses.append(text)
        def on_event(self, event): self.events.append(event)
        def on_image(self, png, note): pass
        def on_finished(self, text): self.final = text
        def on_error(self, text): self.errors.append(text)
        def request_approval(self, request):
            self.approvals.append(request.tool_name)
            return self.approve

    # 场景一：只读工具直接执行，不需要审批
    store2 = Store(scratch / "s2.json", scratch / "s2.bak.json")
    store2.load()
    actions3 = DesktopActions(AuditLog())
    ctx3 = ToolContext(capture2, actions3, store2)
    recorder = Recorder()

    script = [
        ChatReply(tool_calls=[ToolCall(id="c1", name="add_task",
                                       arguments={"text": "写周报"},
                                       raw_arguments='{"text":"写周报"}')]),
        ChatReply(text="已经帮你把「写周报」加进待办了。"),
    ]
    runner = AgentRunner(FakeClient(script), ctx3, actions3, recorder)
    final = runner.run("帮我记一下要写周报")
    check("只读工具无需审批", len(recorder.approvals) == 0, f"实际 {recorder.approvals}")
    check("待办确实写入了", len(store2.tasks) == 1, f"实际 {len(store2.tasks)}")
    check("返回了最终回答", "待办" in final, final[:50])
    check("产生了工具事件", any(e.kind == "tool" for e in recorder.events))
    check("没有报错", not recorder.errors, str(recorder.errors))

    # 场景二：需要审批的动作 —— 用户拒绝
    store3 = Store(scratch / "s3.json", scratch / "s3.bak.json")
    store3.load()
    actions4 = DesktopActions(AuditLog())
    ctx4 = ToolContext(capture2, actions4, store3)
    denier = Recorder(approve=False)
    script2 = [
        ChatReply(tool_calls=[ToolCall(id="c2", name="click",
                                       arguments={"x": 10, "y": 10},
                                       raw_arguments='{"x":10,"y":10}')]),
        ChatReply(text="好的，我不点了。"),
    ]
    runner2 = AgentRunner(FakeClient(script2), ctx4, actions4, denier)
    runner2.run("点一下那里")
    check("点击请求了审批", denier.approvals == ["click"], f"实际 {denier.approvals}")
    check("拒绝后没有产生成功的工具事件",
          not any(e.kind == "tool" and e.ok for e in denier.events))
    check("拒绝被记录成 error 事件", any(e.kind == "error" for e in denier.events))

    # 场景三：只读模式直接拦住需要动键鼠的动作
    store4 = Store(scratch / "s4.json", scratch / "s4.bak.json")
    store4.load()
    actions5 = DesktopActions(AuditLog())
    actions5.level = LEVEL_READ_ONLY
    ctx5 = ToolContext(capture2, actions5, store4)
    blocked_rec = Recorder()
    script3 = [
        ChatReply(tool_calls=[ToolCall(id="c3", name="type_text",
                                       arguments={"text": "hello"},
                                       raw_arguments='{"text":"hello"}')]),
        ChatReply(text="只读模式不能输入。"),
    ]
    runner3 = AgentRunner(FakeClient(script3), ctx5, actions5, blocked_rec)
    runner3.run("打字")
    check("只读模式没有请求审批", len(blocked_rec.approvals) == 0)
    check("只读模式产生了拦截事件",
          any(e.kind == "error" and "拦截" in e.text for e in blocked_rec.events),
          str([e.text for e in blocked_rec.events]))

    # 场景四：步数上限
    store5 = Store(scratch / "s5.json", scratch / "s5.bak.json")
    store5.load()
    actions6 = DesktopActions(AuditLog())
    ctx6 = ToolContext(capture2, actions6, store5)
    looper = Recorder()
    forever = [ChatReply(tool_calls=[ToolCall(id=f"c{i}", name="screen_info",
                                              arguments={}, raw_arguments="{}")])
               for i in range(60)]
    runner4 = AgentRunner(FakeClient(forever), ctx6, actions6, looper)
    runner4.run("无限循环测试")
    tool_events = [e for e in looper.events if e.kind == "tool"]
    check("步数上限生效（<=20 步）", len(tool_events) <= 20, f"实际 {len(tool_events)} 步")
    check("达到上限会说明", any("上限" in s for s in looper.statuses),
          str(looper.statuses[-2:]))

    # 场景五：客户端报错要优雅处理
    class BrokenClient:
        @property
        def configured(self): return True

        def chat(self, *args, **kwargs):
            from pawpet.ai.client import AiError

            raise AiError("模拟的网络故障")

    store6 = Store(scratch / "s6.json", scratch / "s6.bak.json")
    store6.load()
    ctx7 = ToolContext(capture2, DesktopActions(AuditLog()), store6)
    broken = Recorder()
    runner5 = AgentRunner(BrokenClient(), ctx7, actions6, broken)
    runner5.run("会失败")
    check("客户端异常被捕获", len(broken.errors) == 1, str(broken.errors))
    check("错误信息可读", "模拟的网络故障" in broken.errors[0])

    # ------------------------------------------------------------ 历史裁剪
    section("对话历史裁剪")
    store7 = Store(scratch / "s7.json", scratch / "s7.bak.json")
    store7.load()
    ctx8 = ToolContext(capture2, DesktopActions(AuditLog()), store7)
    runner6 = AgentRunner(FakeClient([]), ctx8, actions6, Recorder())
    for i in range(80):
        runner6.messages.append({"role": "user", "content": f"消息 {i}"})
    runner6._trim_history()
    check("历史被裁剪到上限内", len(runner6.messages) <= 60, f"实际 {len(runner6.messages)}")
    check("system 提示保留", runner6.messages[0]["role"] == "system")

    # 图片也要被裁掉
    runner6.messages = [{"role": "system", "content": "s"}]
    for i in range(5):
        runner6.messages.append({
            "role": "user",
            "content": [{"type": "text", "text": f"图 {i}"},
                        {"type": "image_url", "image_url": {"url": "data:x"}}],
        })
    runner6._trim_history()
    remaining = sum(
        1 for m in runner6.messages
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    )
    check("只保留最近 2 张截图", remaining == 2, f"实际 {remaining}")

    # ------------------------------------------------------------ 客户端
    section("API 客户端")
    client = AIClient(api_key="", model="test-model", base_url="https://example.com/v1")
    check("未配置时 configured=False", client.configured is False)
    check("描述信息可读", "test-model" in client.describe())

    ok, message = client.test_connection()
    check("未配置时测试连接给出提示", ok is False and "API Key" in message, message)

    configured = AIClient(api_key="sk-fake", model="m", base_url="https://127.0.0.1:1/v1",
                          timeout=3)
    check("配置后 configured=True", configured.configured is True)
    ok, message = configured.test_connection()
    check("连不上时报错可读", ok is False and len(message) > 0, message[:80])

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
