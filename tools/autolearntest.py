r"""自动记忆自测。

这个功能的失败方式和别的不同：
* 学不到东西 → 只是没效果，用户能忍
* **学到脏东西 → 每一轮提示词都被污染**，用户会开始不信任它
* **学到敏感内容 → 持续泄露**（记忆会进每一轮请求）

所以测试重点全在「不该记的有没有被挡住」，而不是「能不能记」。

用法：
    .venv\Scripts\python.exe tools\autolearntest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "autolearn"
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


class FakeClient:
    """假模型：固定返回一段文本，并记录收到的请求。"""

    def __init__(self, text="", fail=None):
        self.text = text
        self.fail = fail
        self.calls: list[list[dict]] = []

    @property
    def configured(self) -> bool:
        return True

    def chat(self, messages, tools=None, temperature=0.2, max_tokens=1600):
        self.calls.append(messages)
        if self.fail is not None:
            raise self.fail
        return type("Reply", (), {"text": self.text})()


class UnconfiguredClient(FakeClient):
    @property
    def configured(self) -> bool:
        return False


def main() -> int:
    print("小爪自动记忆自测\n")

    from pawpet.ai.autolearn import (
        MAX_PER_TURN,
        LearnResult,
        _looks_sensitive,
        learn_from_turn,
        parse_items,
        should_extract,
    )
    from pawpet.ai.client import AiError
    from pawpet.ai.memory import (
        KIND_AUTO,
        KIND_RANK,
        KIND_TOLD,
        KIND_TOOL,
        Memory,
        MemoryBook,
        count_auto,
        format_for_prompt,
    )
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # ================================================== 一、成本闸门
    print("=== 一、本地信号闸门（决定要不要花钱问模型）===")

    must_extract = [
        "以后回答都用中文",
        "我习惯用 WPS 打开表格",
        "别再用 Markdown 表格了",
        "帮我记住我用双屏",
        "我喜欢结论先行的回答方式，细节放后面",
        "我在公司用的是 Windows 11，家里是 Mac，两边环境不一样",
    ]
    for text in must_extract:
        check(f"该问：{text[:22]}", should_extract(text), "被挡掉了，会漏记")

    must_skip = [
        "打开记事本",
        "看下报错",
        "点击那个按钮",
        "他有点忙",
        "",
        "截图看看",
    ]
    for text in must_skip:
        check(f"不该问：{text[:22] or '(空)'}", not should_extract(text),
              "白花钱问了一次")

    # ================================================== 二、解析
    print("\n=== 二、解析模型输出（不能因为格式问题崩掉）===")

    good = parse_items('{"items": [{"text": "他习惯用 WPS", "category": "workflow",'
                       ' "confidence": 2}]}')
    check("正常 JSON 能解析", len(good) == 1 and good[0]["text"] == "他习惯用 WPS",
          str(good))

    fenced = parse_items('```json\n{"items": [{"text": "他喜欢深色主题"}]}\n```')
    check("带围栏也能解析", len(fenced) == 1, str(fenced))

    chatty = parse_items('好的，我分析了一下：\n{"items": [{"text": "他叫马靖凯"}]}\n'
                         '以上就是我的判断。')
    check("夹在废话里也能抠出来", len(chatty) == 1, str(chatty))

    check("空字符串返回空", parse_items("") == [])
    check("乱码返回空而不是抛异常", parse_items("这不是 JSON") == [])
    check("items 不是列表返回空", parse_items('{"items": "abc"}') == [])
    check("条目不是字典会被跳过", parse_items('{"items": [null, 5, "x"]}') == [])
    check("没有 text 的条目被跳过", parse_items('{"items": [{"category": "x"}]}') == [])
    check("空 items 返回空", parse_items('{"items": []}') == [])

    long_text = parse_items('{"items": [{"text": "' + "很" * 300 + '"}]}')
    check("超长文本被截断",
          bool(long_text) and len(long_text[0]["text"]) <= 120,
          str(len(long_text[0]["text"]) if long_text else 0))

    # ================================================== 三、敏感内容
    print("\n=== 三、敏感内容：宁可误杀也不能漏 ===")

    sensitive = [
        "他的密码是 abc123",
        "记下这个 API key: sk-abcdefg",
        "他的身份证号是 320xxx",
        "银行卡号 6222 xxxx",
        "token 是 ghp_xxxxx",
        "-----BEGIN RSA PRIVATE KEY-----",
        "他的支付密码",
    ]
    for text in sensitive:
        check(f"挡住：{text[:24]}", _looks_sensitive(text), "放行了，会持续泄露")

    harmless = [
        "他习惯用 WPS",
        "他喜欢简洁的回答",
        "他在用 Windows 11",
    ]
    for text in harmless:
        check(f"不误杀：{text[:24]}", not _looks_sensitive(text), "误杀了")

    # ================================================== 四、端到端
    print("\n=== 四、走完整流程：学到的东西真的进记忆 ===")

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    book = MemoryBook(store)

    client = FakeClient('{"items": ['
                        '{"text": "他习惯用 WPS 而不是 Office", "category": "workflow",'
                        ' "confidence": 3}]}')
    result = learn_from_turn(client, book, "我平时都用 WPS 的，别给我说 Office",
                             "好的，记住了。")
    check("真的去问了模型", result.asked, result.reason)
    check("学到了 1 条", len(result.learned) == 1, str(result.learned))
    check("结果里 changed 为真", result.changed)

    memory = book.load()
    check("确实写进了记忆",
          any("WPS" in f.text for f in memory.facts),
          str([f.text for f in memory.facts]))
    check("标成了自己学的",
          all(f.kind == KIND_AUTO for f in memory.facts),
          str([(f.text, f.kind) for f in memory.facts]))
    check("置信度被采纳",
          any(f.confidence == 3 for f in memory.facts),
          str([f.confidence for f in memory.facts]))

    rendered = format_for_prompt(book.load())
    check("学到的内容进了系统提示词", "WPS" in rendered, rendered[:120])

    # ================================================== 五、去重
    print("\n=== 五、同一件事不会记两遍 ===")

    again = FakeClient('{"items": [{"text": "他习惯用 WPS 而不是 Office。",'
                       ' "category": "workflow", "confidence": 2}]}')
    before = len(book.load().facts)
    result2 = learn_from_turn(again, book, "我习惯用 WPS 啊", "知道了")
    after = len(book.load().facts)
    check("重复的不会新增", after == before, f"{before} -> {after}")
    check("被记为更新而不是新记",
          len(result2.learned) == 0 and len(result2.updated) == 1,
          f"learned={result2.learned} updated={result2.updated}")

    # ================================================== 六、说反了要能改
    print("\n=== 六、前后矛盾时删掉旧记忆 ===")

    switched = FakeClient('{"items": [{"text": "他现在改用 Office 了",'
                          ' "category": "workflow", "confidence": 3,'
                          ' "forget": "他习惯用 WPS 而不是 Office"}]}')
    result3 = learn_from_turn(switched, book, "我现在都用 Office 了，改用 Office",
                              "好的")
    memory3 = book.load()
    check("旧记忆被删掉", len(result3.forgotten) == 1, str(result3.forgotten))
    check("新记忆记上了",
          any("Office" in f.text for f in memory3.facts),
          str([f.text for f in memory3.facts]))
    check("删的是旧的那条",
          not any("他习惯用 WPS 而不是" in f.text for f in memory3.facts),
          str([f.text for f in memory3.facts]))

    # ================================================== 七、上限
    print("\n=== 七、一轮最多记几条 ===")

    many = "".join(
        f'{{"text": "第 {index} 条偏好内容", "category": "preference",'
        f' "confidence": 2}},'
        for index in range(8)
    )
    flood = FakeClient('{"items": [' + many.rstrip(",") + "]}")
    result4 = learn_from_turn(flood, book, "我有很多偏好要说，第一第二第三第四",
                              "好")
    check(f"最多只记 {MAX_PER_TURN} 条",
          len(result4.learned) <= MAX_PER_TURN, str(len(result4.learned)))
    check("多出来的被记为忽略", len(result4.skipped) >= 1, str(result4.skipped))

    # ================================================== 八、失败必须静默
    print("\n=== 八、出错时不能影响主流程 ===")

    failing = FakeClient(fail=AiError("接口炸了"))
    result5 = learn_from_turn(failing, book, "以后都用深色主题，记住")
    check("请求失败不抛异常", isinstance(result5, LearnResult))
    check("失败时 changed 为假", not result5.changed)
    check("失败时给了原因", bool(result5.reason), result5.reason[:50])
    check("失败时 asked 被置回假", not result5.asked)

    weird = FakeClient(fail=ValueError("奇怪的错误"))
    result6 = learn_from_turn(weird, book, "以后都用深色主题，记住")
    check("非 AiError 的异常也被吞掉", isinstance(result6, LearnResult))
    check("异常时也不改记忆", not result6.changed)

    unconfigured = UnconfiguredClient("{}")
    result7 = learn_from_turn(unconfigured, book, "以后都用深色主题，记住")
    check("没配模型时直接跳过不发请求",
          not result7.asked and not unconfigured.calls,
          f"asked={result7.asked} calls={len(unconfigured.calls)}")

    quiet = FakeClient('{"items": []}')
    result8 = learn_from_turn(quiet, book, "打开记事本")
    check("没有信号时不发请求", not quiet.calls and not result8.asked,
          f"calls={len(quiet.calls)}")
    check("跳过时说明了原因", "信号" in result8.reason, result8.reason)

    forced = FakeClient('{"items": []}')
    learn_from_turn(forced, book, "打开记事本", force=True)
    check("force=True 时绕过闸门", len(forced.calls) == 1, str(len(forced.calls)))

    # ================================================== 九、成本控制
    print("\n=== 九、请求本身的成本控制 ===")

    cost_client = FakeClient('{"items": []}')
    learn_from_turn(cost_client, book, "我习惯用双屏，左边放资料", "好的")
    sent = cost_client.calls[0]
    check("只发两条消息（system + user）", len(sent) == 2, str(len(sent)))
    check("第一条是 system", sent[0]["role"] == "system", sent[0]["role"])
    check("不带截图/tool 消息（省 token）",
          all(m.get("role") in ("system", "user") for m in sent),
          str([m.get("role") for m in sent]))
    check("告诉模型它在维护长期记忆",
          "长期" in sent[0]["content"] or "记忆" in sent[0]["content"])
    check("提示词里明确禁止记敏感内容",
          "绝对不要记" in sent[0]["content"] or "敏感" in sent[0]["content"])
    check("提示词里说明了判别标准（换一天还用得上吗）",
          "换一天" in sent[0]["content"], "缺少判别标准")

    body = sent[1]["content"]
    check("带上了用户说的话", "双屏" in body, body[:80])
    check("带上了小爪的回复", "好的" in body, body[:80])

    known_client = FakeClient('{"items": []}')
    learn_from_turn(known_client, book, "我习惯用三屏了", "好")
    known_body = known_client.calls[0][1]["content"]
    check("带上了已有记忆（用来发现说反了）",
          "Office" in known_body or "偏好" in known_body, known_body[:140])

    # ================================================== 十、来源标记
    print("\n=== 十、来源标记与合并规则 ===")

    check("told 比 auto 优先级高",
          KIND_RANK[KIND_TOLD] > KIND_RANK[KIND_AUTO], str(KIND_RANK))
    check("auto 比 tool 优先级高",
          KIND_RANK[KIND_AUTO] > KIND_RANK[KIND_TOOL], str(KIND_RANK))

    merge = Memory()
    merge.add_fact("他喜欢深色主题", "preference", 2, kind=KIND_TOLD)
    merge.add_fact("他喜欢深色主题", "preference", 2, kind=KIND_AUTO)
    check("用户教的不会被自动学习降级",
          merge.facts[0].kind == KIND_TOLD, merge.facts[0].kind)

    merge2 = Memory()
    merge2.add_fact("他喜欢浅色主题", "preference", 2, kind=KIND_AUTO)
    merge2.add_fact("他喜欢浅色主题", "preference", 2, kind=KIND_TOOL)
    check("自动学到的不会被降级",
          merge2.facts[0].kind == KIND_AUTO, merge2.facts[0].kind)

    old = Memory.from_dict({"facts": [{"text": "旧数据没有 kind 字段"}]})
    check("老数据没 kind 时默认成 tool",
          old.facts[0].kind == KIND_TOOL, old.facts[0].kind)
    bad = Memory.from_dict({"facts": [{"text": "x", "kind": "不认识的类型"}]})
    check("非法 kind 被纠正", bad.facts[0].kind == KIND_TOOL, bad.facts[0].kind)

    # ================================================== 十一、面板
    print("\n=== 十一、界面能看到自己学了什么 ===")

    from pawpet.ai.controller import AiController

    store2 = Store(SCRATCH / "auto.json", SCRATCH / "auto.backup.json")
    store2.load()
    controller = AiController(store2)
    try:
        check("初始没有自己学的", controller.autoMemoryCount == 0,
              str(controller.autoMemoryCount))
        check("初始状态文案是引导性的",
              "自己" in controller.autoLearnStatus
              or "还没有" in controller.autoLearnStatus,
              controller.autoLearnStatus[:40])

        MemoryBook(store2).add_fact("他习惯用 WPS", "workflow", 2, kind=KIND_AUTO)
        MemoryBook(store2).add_fact("他叫马靖凯", "identity", 3, kind=KIND_TOLD)
        controller.refreshMemory()

        check("能数出自己学了几条", controller.autoMemoryCount == 1,
              str(controller.autoMemoryCount))
        check("总数还是 2", controller.memoryCount == 2,
              str(controller.memoryCount))
        check("摘要里标出了哪条是自己学的",
              "自己学的" in controller.memorySummary,
              controller.memorySummary[:200])
        check("count_auto 和控制器一致",
              count_auto(controller._memory()) == controller.autoMemoryCount)
    finally:
        controller.shutdown()

    # ================================================== 十二、后台接线
    print("\n=== 十二、控制器后台触发（真的会自己学）===")

    import threading
    import time as _time

    from PySide6.QtCore import QCoreApplication

    from pawpet.ai import controller as controller_module

    # 需要一个 QApplication/QCoreApplication：
    # _learn_worker 在**工作线程**里 emit 信号，Qt 会把它排队送到主线程的
    # 事件循环 —— 脚本里没有事件循环，槽就永远不执行。
    # 真实应用里 QApplication 一直在跑，所以没这个问题。
    app = QCoreApplication.instance() or QCoreApplication([])

    store3 = Store(SCRATCH / "wired.json", SCRATCH / "wired.backup.json")
    store3.load()
    wired = AiController(store3)

    learned_client = FakeClient('{"items": [{"text": "他习惯把窗口放左边",'
                                ' "category": "workflow", "confidence": 2}]}')
    real_client_class = controller_module.AIClient
    controller_module.AIClient = lambda *a, **k: learned_client

    fired = threading.Event()

    def spy(summary, detail):
        fired.set()

    # 订阅主线程上的对外广播（learnedSomething）。
    #
    # 之前我是去连内部的 _learnedIn（线程桥）并替换 _apply_learned —— 那样
    # 测不出来：从工作线程 emit 的信号，只有连到 QObject 的槽才会被 Qt
    # 排队送进主线程；连到普通 Python 函数时，在真实应用里由 QML 的事件
    # 循环驱动，在没有事件循环的脚本里就静默丢掉。
    # 连 learnedSomething（_apply_learned 在主线程 emit）才是稳定的观测点。
    wired.learnedSomething.connect(spy)

    try:
        wired.memoryEnabled = True
        wired._push("user", "我平时都把窗口放左边，右边留给参考")
        wired._start_learning("好的，明白了。")

        # 一边等，一边转事件循环 —— 没有事件循环，跨线程排队的槽不会执行
        deadline = _time.time() + 10
        while not fired.is_set() and _time.time() < deadline:
            app.processEvents()
            _time.sleep(0.05)
        _time.sleep(0.1)
        app.processEvents()

        memory = MemoryBook(store3).load()
        check("后台线程真的学了东西",
              any("左边" in f.text for f in memory.facts),
              str([f.text for f in memory.facts]))
        check("学到的标成了 auto",
              all(f.kind == KIND_AUTO for f in memory.facts),
              str([(f.text, f.kind) for f in memory.facts]))
        check("通知了界面（广播被触发）", fired.is_set(),
              "没有收到 learnedSomething")
        check("lastLearned 有内容",
              bool(wired.lastLearned.get("learned")),
              str(wired.lastLearned))
    finally:
        controller_module.AIClient = real_client_class
        wired.shutdown()

    # 关掉开关就不该学
    store4 = Store(SCRATCH / "off.json", SCRATCH / "off.backup.json")
    store4.load()
    off = AiController(store4)
    controller_module.AIClient = lambda *a, **k: FakeClient(
        '{"items": [{"text": "不该被记下来", "category": "other"}]}')
    try:
        off.memoryEnabled = False
        off._push("user", "我习惯用双屏，以后都这样")
        off._apply_finished("好的", "")
        _time.sleep(0.5)
        check("关掉记忆开关后不会自动学",
              not MemoryBook(store4).load().facts,
              str([f.text for f in MemoryBook(store4).load().facts]))
    finally:
        controller_module.AIClient = real_client_class
        off.shutdown()

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
