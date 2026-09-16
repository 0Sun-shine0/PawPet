"""新功能回归：现成任务模板、停下来问人、做完交代、失败说人话、边做边说。

这些功能都是「用户能直接看到」的那一层，所以测试也盯着用户能看到的结果：
* 模板点下去到底发出去什么话
* ask_user 停了之后回答有没有真的回到模型手里
* 交代里有没有「做了几件、文件在哪、能不能撤回」这三件事
* 失败时会不会把错误码甩到用户脸上
* 进度时间线在跑的时候是不是真的在更新

用法：
    .venv\\Scripts\\python.exe tools\\featuretest.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
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


# ==========================================================================
def test_templates() -> None:
    print("\n=== 现成任务模板 ===")
    from pawpet.ai.tasks import TEMPLATES, all_templates, find, groups

    items = all_templates()
    check("模板数量够铺一屏", len(items) >= 10, str(len(items)))
    check("每条都有 key/label/hint/prompt",
          all(all(item.get(k) for k in ("key", "label", "hint", "prompt"))
              for item in items))
    keys = [item["key"] for item in items]
    check("key 不重复", len(keys) == len(set(keys)))
    check("找不到的 key 返回 None", find("没有这条") is None)
    check("能找到已有的 key", find(keys[0]) is not None)

    # 分人群：三个组都要有内容，不然切换标签会看到空白
    buckets = groups()
    check("分成三组", len(buckets) == 3, str(len(buckets)))
    check("每组都有任务", all(len(g["items"]) >= 3 for g in buckets),
          str([len(g["items"]) for g in buckets]))
    check("组有中文名和图标",
          all(g["label"] and g["icon"] for g in buckets))

    # 提示词里不能留占位符 —— 用户点了还得自己补，那还不如直接打字
    for item in items:
        check(f"「{item['label']}」没有花括号占位符",
              "{" not in item["prompt"] and "}" not in item["prompt"])
        # 「交付什么」必须写清楚，否则模型干完就沉默了
        check(f"「{item['label']}」说了做完怎么交代",
              any(word in item["prompt"] for word in
                  ("告诉我", "发给我", "念给我", "说明", "列出", "交代")),
              item["prompt"][:40])

    # 图片里点名的三个例子
    labels = " ".join(item["label"] for item in items)
    check("有「整理这个文件夹」", "整理" in labels)
    check("有「截图转文字」", "截图" in labels and "文字" in labels)
    check("有「按部门汇总」", "汇总" in labels)
    # 分人群里点名的
    check("学生组有找文件/转格式/压作业", any(
        g["key"] == "student" and len(g["items"]) >= 4 for g in buckets))
    check("上班党有搬数据/填表/做汇总",
          any(k in labels for k in ("搬数据",)) and "填" in labels)

    check("模板是不可变的 tuple 来源", isinstance(TEMPLATES, tuple))


def test_ask_user() -> None:
    print("\n=== 拿不准就停一下问 ===")
    from pawpet.ai.agent import AgentRunner
    from pawpet.ai.tools import TOOL_INDEX, openai_tools

    check("ask_user 注册进工具表", "ask_user" in TOOL_INDEX)

    names = [item["function"]["name"] for item in openai_tools()]
    check("ask_user 出现在给模型的清单里", "ask_user" in names)
    schema = TOOL_INDEX["ask_user"].parameters
    check("question 是必填", schema.get("required") == ["question"],
          str(schema.get("required")))
    check("options 是列表", schema["properties"]["options"]["type"] == "array")
    check("ask_user 是只读风险（不该触发审批）",
          TOOL_INDEX["ask_user"].risk == "read")

    runner = AgentRunner.__new__(AgentRunner)
    from pawpet.ai.agent import AgentCallbacks
    from pawpet.ai.advisor import Advisor
    from pawpet.ai.batch import BatchItem  # noqa: F401  (确认依赖可用)

    asked: list[tuple[str, list]] = []
    recorded: list[tuple] = []
    events: list = []

    class Cb(AgentCallbacks):
        def on_event(self, event): events.append(event)
        def on_status(self, text): pass

        def ask_user(self, question, options):
            asked.append((question, list(options)))
            return "放到 D:\\作业\\2024 下面"

    runner.callbacks = Cb()
    runner.advisor = Advisor()
    runner.actions_ok = 0
    runner.actions_failed = 0
    runner.touched_paths = []
    runner.wrote_files = False
    runner.last_error = ""
    runner._record_tool = lambda cid, name, text, ok: recorded.append((name, text, ok))

    class FakeCall:
        name = "ask_user"
        arguments = {"question": "作业存到哪个文件夹？",
                     "options": ["桌面", "D:\\作业", "你帮我定"]}

    runner._ask_user(FakeCall(), "call_1")
    check("问题抛给了用户", len(asked) == 1, str(asked))
    check("问题文字原样传过去", asked and asked[0][0] == "作业存到哪个文件夹？")
    check("选项最多 4 个", asked and len(asked[0][1]) == 3)
    check("回答回填给了模型",
          recorded and "D:\\作业\\2024" in recorded[0][1], str(recorded))
    check("回答算一次有效操作", runner.actions_ok == 1)
    check("界面上能看到问了什么",
          any("作业存到哪个文件夹" in (e.text or "") for e in events))

    # 用户没回答：绝不能当成「默认同意」
    recorded.clear()
    runner.callbacks.ask_user = lambda q, o: ""
    runner._ask_user(FakeCall(), "call_2")
    note = recorded[0][1] if recorded else ""
    check("没回答时告诉模型「不要假设同意」", "不要假设" in note, note[:50])
    check("没回答时算失败", recorded and recorded[0][2] is False)

    # 空问题：不该浪费一次用户交互
    recorded.clear()
    class EmptyCall:
        name = "ask_user"
        arguments = {"question": "   "}
    runner._ask_user(EmptyCall(), "call_3")
    check("空问题被拦下", recorded and recorded[0][2] is False)


def test_completion_report() -> None:
    print("\n=== 做完有交代 ===")
    from pawpet.ai.agent import AgentRunner

    def fresh():
        runner = AgentRunner.__new__(AgentRunner)
        runner.actions_ok = 0
        runner.actions_failed = 0
        runner.touched_paths = []
        runner.wrote_files = False
        return runner

    runner = fresh()
    check("什么都没做时不编交代", runner.completion_report() == "")

    runner.actions_ok = 3
    report = runner.completion_report()
    check("说了做了几件事", "3 个操作" in report, report)

    runner.touched_paths = ["D:\\作业\\数学.docx"]
    runner.wrote_files = True
    report = runner.completion_report()
    check("说了文件在哪", "D:\\作业\\数学.docx" in report, report)
    check("说了能不能撤回", "撤回" in report or "恢复" in report, report)

    # 只读的路径不算「产出」，但要说清「没改动」
    runner2 = fresh()
    runner2.actions_ok = 1
    runner2.touched_paths = ["D:\\x.txt"]
    runner2.wrote_files = False
    report2 = runner2.completion_report()
    check("只读时说清没改动", "没有改动" in report2, report2)

    # 路径太多时截断，别把气泡撑爆
    runner3 = fresh()
    runner3.actions_ok = 6
    runner3.wrote_files = True
    runner3.touched_paths = [f"D:\\f{i}.txt" for i in range(6)]
    report3 = runner3.completion_report()
    check("路径多时截断并给出总数", "等 6 处" in report3, report3)

    # 失败也要如实说
    runner4 = fresh()
    runner4.actions_ok = 2
    runner4.actions_failed = 1
    report4 = runner4.completion_report()
    check("有失败时如实说", "没成功" in report4, report4)

    # 写文件才会被记进产出
    runner5 = fresh()
    runner5._record_tool = lambda *a, **k: None
    runner5._note_path("write_file", {"path": "D:\\a.txt"}, "已写入", True)
    check("write_file 记成产出", runner5.wrote_files and "D:\\a.txt" in runner5.touched_paths)
    runner5._note_path("read_file", {"path": "D:\\b.txt"}, "内容", True)
    check("read_file 不算产出", not runner5.wrote_files
          or "D:\\b.txt" in runner5.touched_paths)
    runner5.wrote_files = False
    runner5.touched_paths = []
    runner5._note_path("write_file", {"path": "D:\\c.txt"}, "失败", False)
    check("失败的文件操作不记账", runner5.touched_paths == [])


def test_failure_wording() -> None:
    print("\n=== 失败说人话 ===")
    from pawpet.ai.advisor import (
        KIND_BAD_ARGUMENT,
        KIND_DENIED,
        KIND_MISSING_DEP,
        KIND_NOT_FOUND,
        KIND_PERMISSION,
        KIND_TIMEOUT,
        KIND_TRANSIENT,
        KIND_UNKNOWN,
        user_facing_failure,
    )

    kinds = [KIND_PERMISSION, KIND_DENIED, KIND_MISSING_DEP, KIND_TIMEOUT,
             KIND_NOT_FOUND, KIND_TRANSIENT, KIND_BAD_ARGUMENT, KIND_UNKNOWN]
    for kind in kinds:
        text = user_facing_failure(kind)
        check(f"{kind} 有专门的说法", bool(text))
        # 用户看不懂的技术词一个都不能出现
        for bad in ("Error", "Traceback", "Exception", "0x", "tool", "_", "**"):
            check(f"{kind} 文案里没有「{bad}」", bad not in text, text[:50])
        check(f"{kind} 文案说了用户该干什么",
              any(word in text for word in
                  ("你", "我", "告诉", "等着", "接着", "换")), text[:50])

    # 缺依赖要把具体组件名挑出来（这个信息对用户有用）
    text = user_facing_failure(KIND_MISSING_DEP, "ImportError: No module named 'comtypes'")
    check("缺依赖时点名了具体组件", "comtypes" in text, text)
    # 但别的类型不能把原始错误贴出来
    text2 = user_facing_failure(KIND_TIMEOUT, "UIAutomationCore FindAll timeout after 5s")
    check("超时不贴原始错误", "UIAutomationCore" not in text2, text2)

    check("认不出的类型也有兜底", bool(user_facing_failure("no_such_kind")))


def test_prompt_sections() -> None:
    print("\n=== 提示词里的新规矩 ===")
    from pawpet.ai.agent import SYSTEM_PROMPT

    check("写了「拿不准就停一下问」", "拿不准就停一下问" in SYSTEM_PROMPT)
    check("点名了 ask_user", "ask_user" in SYSTEM_PROMPT)
    check("写了「做完了要交代」", "做完了要交代" in SYSTEM_PROMPT)
    check("交代要包含文件位置", "东西在哪" in SYSTEM_PROMPT)
    check("交代要包含能不能撤回", "能不能撤回" in SYSTEM_PROMPT)
    check("强调了别什么都问", "什么都来问我" in SYSTEM_PROMPT)
    check("安全边界没被弄丢", "不要删除文件" in SYSTEM_PROMPT)
    check("回答格式那段还在", "回答格式（重要）" in SYSTEM_PROMPT)
    # 系统提示词不能出现重复段落（上次改的时候差点复制了一遍）
    check("回答格式只出现一次", SYSTEM_PROMPT.count("回答格式（重要）") == 1)
    check("安全与边界只出现一次", SYSTEM_PROMPT.count("安全与边界：") == 1)


def test_bubble_text() -> None:
    print("\n=== 气泡正文不出现 Markdown ===")
    from pawpet.backend import Backend

    cases = [
        ("**已经整理好了**", "已经整理好了"),
        ("# 标题\n正文", "标题；正文"),
        ("- 第一项\n- 第二项", "· 第一项；· 第二项"),
        ("看这里 `code` 就行", "看这里 code 就行"),
        ("> 引用一句", "引用一句"),
    ]
    for raw, expect in cases:
        got = Backend._bubble_text(raw)
        check(f"「{raw[:14]}」→ 无记号", got == expect, f"得到 {got!r}")

    # 换行必须压成一段 —— 气泡高度是算出来的，多行会把宠物顶歪
    multi = Backend._bubble_text("第一行\n第二行\n第三行")
    check("多行压成一段", "\n" not in multi, repr(multi))
    check("用分号接起来", multi == "第一行；第二行；第三行", multi)

    # 超长截断，不能把气泡撑到屏幕外
    long_text = "很长的内容" * 200
    got = Backend._bubble_text(long_text)
    check("超长会截断", len(got) <= 260, str(len(got)))
    check("截断处有省略号", got.endswith("…"))

    check("空字符串安全", Backend._bubble_text("") == "")
    check("None 安全", Backend._bubble_text(None) == "")  # type: ignore[arg-type]


def test_step_timeline() -> None:
    print("\n=== 边做边说 ===")
    from pawpet.ai.agent import StepEvent
    from pawpet.ai.controller import AiController

    check("时间线最多留 4 步（多了顶不住）",
          AiController.MAX_STEPS_SHOWN == 4, str(AiController.MAX_STEPS_SHOWN))

    class Signal:
        def __init__(self):
            self.count = 0

        def emit(self):
            self.count += 1

    class Fake:
        MAX_STEPS_SHOWN = AiController.MAX_STEPS_SHOWN
        _note_step = AiController._note_step

        def __init__(self):
            self._steps = []
            self.stepsChanged = Signal()

    fake = Fake()

    fake._note_step(StepEvent(kind="tool", tool="click",
                              text="点击 (640, 360) · 单击"))
    check("整步被记进时间线", fake._steps == ["点击 (640, 360) · 单击"], str(fake._steps))

    fake._note_step(StepEvent(kind="tool", tool="screenshot", text="看屏幕（显示器 1）"))
    check("截图不进时间线", len(fake._steps) == 1, str(fake._steps))

    fake._note_step(StepEvent(kind="tool", tool="click",
                              text="点击 (640, 360) · 单击"))
    check("同一步不重复记", len(fake._steps) == 1, str(fake._steps))

    for i in range(6):
        fake._note_step(StepEvent(kind="tool", tool="click", text=f"第 {i} 步"))
    check("只留最近几步", len(fake._steps) == AiController.MAX_STEPS_SHOWN,
          str(fake._steps))
    check("留下的是最新的", fake._steps[-1] == "第 5 步")
    check("时间线变化会通知界面", fake.stepsChanged.count >= 3,
          str(fake.stepsChanged.count))


def test_fonts() -> None:
    print("\n=== 字体：唯一来源 + 回退链 ===")
    from pawpet.qmlfont import ALL_FAMILIES, FONT_FAMILY, FONT_LATIN, FONT_MONO

    # 字体名的唯一来源必须是 qmlfont.py。QML 通过 Backend 读它。
    # 以前 Theme.qml 和 Python 各写一份，漂移了不报错 —— 只是界面上
    # 悄悄用回旧字体。
    for key, value in ALL_FAMILIES.items():
        check(f"{key} 有值", bool(value), repr(value))
        # 回退链必须带逗号。少了它就是「只指定一个字体」，
        # 缺字形时 Qt 会静默挑别的 —— 中文会落到衬线体，
        # 符号会变成彩色 emoji 或豆腐块。这是用户实际报过的问题。
        check(f"{key} 是回退链（含逗号）", "," in value, value)

    # 每个族都必须能覆盖中文和界面里的符号图标
    for key, value in ALL_FAMILIES.items():
        lowered = value.lower()
        check(f"{key} 带中文回退", "yahei" in lowered or "雅黑" in lowered, value)

    check("等宽族把中文排在 Consolas 之后（数字仍走等宽）",
          FONT_MONO.strip().lower().startswith("consolas"), FONT_MONO)
    check("符号回退显式写了 Segoe UI Symbol（否则会落成彩色 emoji/豆腐块）",
          "segoe ui symbol" in FONT_MONO.lower()
          and "segoe ui symbol" in FONT_LATIN.lower()
          and "segoe ui symbol" in FONT_FAMILY.lower(),
          f"{FONT_FAMILY} | {FONT_MONO} | {FONT_LATIN}")

    # Theme.qml 必须从 backend 读，不能自己写死
    theme = (ROOT / "pawpet" / "qml" / "PawPet" / "Theme.qml").read_text(
        encoding="utf-8")
    for prop, backend_prop in (("font", "backend.fontFamily"),
                               ("fontMono", "backend.fontFamilyMono"),
                               ("fontLatin", "backend.fontFamilyLatin")):
        check(f"Theme.{prop} 从 backend 读", backend_prop in theme,
              f"Theme.qml 里没找到 {backend_prop}")

    # markdown.py 的标题字号：**Qt 不认相对单位**，
    # 132% / 1.32em 实测渲染出来和正文一样大（静默失效）。
    # 所以只允许绝对的 px。
    from pawpet.ai.markdown import BASE_FONT_PX, HEADING_SCALE, _heading_size, to_qt_html

    html = to_qt_html("# 一级\n## 二级\n正文")
    check("Markdown 标题用绝对字号", "font-size:18px" in html, html[:140])
    check("Markdown 里不再出现百分比字号", "%" not in html.split("<b>")[0],
          html[:140])
    check("一级标题比正文大", _heading_size(1) > BASE_FONT_PX,
          f"{_heading_size(1)} vs {BASE_FONT_PX}")
    check("二级标题比一级小", _heading_size(2) < _heading_size(1))
    check("标题倍率和 HEADING_SCALE 对得上",
          _heading_size(1) == BASE_FONT_PX * HEADING_SCALE[1] // 100)
    # 基准字号必须和 QML 的 Theme.fsBody 基准一致（都是 14）
    check("标题基准字号和 Theme.fsBody 基准一致（14）", BASE_FONT_PX == 14,
          str(BASE_FONT_PX))
    check("Theme.qml 的 fsBody 基准也是 14", "fsBody:   px(14)" in theme,
          "Theme.qml 里 fsBody 的基准值变了，markdown.py 的 BASE_FONT_PX 要跟着改")

    # Markdown 的配色必须能在浅色气泡上读出来
    from pawpet.ai import markdown as md

    def luminance(hex_color: str) -> float:
        value = hex_color.lstrip("#")
        r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for name in ("CODE_COLOR", "QUOTE_COLOR", "LINK_COLOR", "CODE_BLOCK_COLOR"):
        color = getattr(md, name)
        check(f"{name} 是深色（浅底上读得清）", luminance(color) < 0.55,
              f"{color} 亮度 {luminance(color):.2f} —— 压在粉白气泡上会看不见")

    check("分隔线颜色是浅色（别抢视线）",
          luminance(md.RULE_COLOR) > 0.7, md.RULE_COLOR)


def main() -> int:
    print("小爪新功能回归")
    test_templates()
    test_ask_user()
    test_completion_report()
    test_failure_wording()
    test_prompt_sections()
    test_bubble_text()
    test_step_timeline()
    test_fonts()

    print("\n" + "=" * 52)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
