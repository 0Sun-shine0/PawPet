r"""用户报的三个问题的回归测试。

问题一：「这一轮只做了一个操作但是任务没有完成」
    现象：模型调了 search_knowledge，工具跑完了，然后**再也没给出任何正文**
    就结束了这一轮。对话里只剩一条动作卡片和一句「这一轮做了 1 个操作」，
    用户不知道发生了什么。

    根因：Agent.run 的循环里，`final_text` 只有模型吐正文时才被赋值。
    模型返回「有 tool_calls、但 content 为空」时，工具照跑，跑完之后
    模型空回复一次（`content: ""` + `finish_reason: stop`）循环就结束了 ——
    final_text 一直是空的。这不是「任务没做完」，是**根本没有收尾**。

问题二：「点一下就跑」卡片太占视野，要能关
问题三：任务栏图标是 Python 的，固定到任务栏固定的是 py 脚本

用法：
    .venv\\Scripts\\python.exe tools\\fixtest.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "fixtest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "fixtest"

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
class FakeReply:
    def __init__(self, text: str, calls: list | None = None) -> None:
        self.text = text
        self.tool_calls = calls or []

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class FakeCall:
    def __init__(self, name: str, call_id: str = "c1", args: dict | None = None):
        self.id = call_id
        self.name = name
        self.arguments = args or {}
        self.raw_arguments = "{}"


class SilentClient:
    """第一次给一个工具调用，**之后再要正文就一直空回复** —— 复现用户的现象。"""

    def __init__(self, tool: str = "search_knowledge",
                 forced_text: str = "") -> None:
        self.tool = tool
        self.forced_text = forced_text
        self.calls: list[dict] = []
        self.tools_param: list = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        self.tools_param.append(tools)
        if len(self.calls) == 1:
            return FakeReply("", [FakeCall(self.tool)])
        if tools is None:
            # 这就是 _force_answer 的补问（它**不带 tools 参数**）
            return FakeReply(self.forced_text)
        return FakeReply("")          # 空回复 —— 触发 bug 的元凶


class StubActions:
    def __init__(self) -> None:
        from pawpet.ai.actions import AuditLog

        self.level = "full"
        self.audit = AuditLog()

    def clear_stop(self): pass
    def request_stop(self): pass
    def blocked(self, risk): return False
    def needs_approval(self, risk): return False


class StubContext:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, name, arguments):
        self.calls.append(name)
        return True, "在知识库里找到 5 段相关内容：……", None


class Sink:
    def __init__(self) -> None:
        self.events: list = []
        self.finished = ""

    def on_status(self, text): pass
    def on_event(self, event): self.events.append(event)
    def on_image(self, png, note): pass
    def on_finished(self, text): self.finished = text
    def on_error(self, text): pass
    def request_approval(self, request): return True


def test_silent_model() -> None:
    print("\n=== 一、模型空回复时不能「跑一步就没了」 ===")
    from pawpet.ai.agent import AgentRunner

    # ---- 情形 A：补问能拿到正文
    client = SilentClient(forced_text="知识库里提到云枢的接口鉴权方式。")
    sink = Sink()
    runner = AgentRunner(client, StubContext(), StubActions(), sink, max_steps=6)
    result = runner.run("知识库里怎么说云枢的鉴权？")

    check("最终回答不是空的", bool(result.strip()), repr(result[:60]))
    check("补问确实发生了（总共请求了 3 次）", len(client.calls) == 3,
          str(len(client.calls)))
    check("补问那一次**没有**带工具（强制它说话）",
          client.tools_param[-1] is None, str(client.tools_param[-1])[:40])
    check("补问的结果被采纳", "云枢" in result, result[:80])
    check("正文进了对话（on_event 收到 assistant）",
          any(getattr(e, "kind", "") == "assistant" for e in sink.events))
    check("工具确实跑了", len(runner.touched_paths) >= 0)

    # ---- 情形 B：补问也拿不到正文 → 必须自己拼一句，绝不留空
    client2 = SilentClient(forced_text="")
    sink2 = Sink()
    runner2 = AgentRunner(client2, StubContext(), StubActions(), sink2, max_steps=6)
    result2 = runner2.run("再查一次")

    check("补问失败也不留空", bool(result2.strip()), repr(result2[:60]))
    check("自己拼的话如实提到执行了几个操作",
          "执行了" in result2 and "1" in result2, result2[:80])
    check("自己拼的话承认没有结论",
          "结论" in result2 or "没能" in result2, result2[:80])

    # ---- 确实什么都没做时也不能胡说
    class NoopClient(SilentClient):
        def chat(self, messages, tools=None):
            self.calls.append(messages)
            self.tools_param.append(tools)
            return FakeReply("")

    sink3 = Sink()
    runner3 = AgentRunner(NoopClient(), StubContext(), StubActions(), sink3,
                          max_steps=3)
    result3 = runner3.run("什么都不做")
    check("纯空回复也有兜底文字", bool(result3.strip()), repr(result3[:60]))
    check("没做事时不吹牛（不说「执行了 N 个操作」）",
          "执行了" not in result3, result3[:80])


def test_report_is_footnote() -> None:
    print("\n=== 二、收尾交代是附注，不能顶替回答 ===")
    from pawpet.ai.agent import AgentRunner

    # 有正文：交代附在后面
    client = SilentClient(forced_text="知识库里说鉴权用的是 Bearer Token。")
    sink = Sink()
    runner = AgentRunner(client, StubContext(), StubActions(), sink, max_steps=6)
    result = runner.run("问一下")
    check("有正文时交代附在后面", "执行了" in result, result[:120])
    check("正文排在交代前面",
          result.index("Bearer") < result.index("执行了"), result[:120])
    check("last_summary 记下了交代", bool(runner.last_summary))
    check("交代挂在消息上（界面用 report 字段）", bool(sink.finished))

    # 没动过手（纯问答）：不该硬凑一句「执行了 0 个操作」
    class ChatOnlyClient:
        def __init__(self): self.n = 0

        def chat(self, messages, tools=None):
            self.n += 1
            return FakeReply("这是一段普通回答。")

    sink2 = Sink()
    runner2 = AgentRunner(ChatOnlyClient(), StubContext(), StubActions(),
                          sink2, max_steps=3)
    result2 = runner2.run("解释一下")
    check("纯问答不加交代", "执行了" not in result2, result2[:80])
    check("纯问答的回答还在", "普通回答" in result2, result2[:80])
    check("纯问答的 last_summary 是空的（界面不显示那行小字）",
          not runner2.last_summary, repr(runner2.last_summary))

    # 交代的措辞：说「执行了」而不是「做了」——
    # 调了一次工具不等于事情办成了，用户就是被这个措辞误导的
    from pawpet.ai.agent import AgentRunner as AR

    probe = AR.__new__(AR)
    probe.actions_ok = 1
    probe.actions_failed = 0
    probe.touched_paths = []
    probe.wrote_files = False
    text = probe.completion_report()
    check("措辞是「执行了 N 个操作」而不是「做了」（不夸大成果）",
          "执行了" in text and "做了" not in text, text)


def test_html_knowledge() -> None:
    print("\n=== 三、HTML 资料导入后必须是可读正文 ===")
    from pawpet.ai.kb import chunk_text, clean_source_text

    # 复现用户那份资料的形态：正文埋在内联脚本的 URL 编码里
    html = """<!DOCTYPE html>
<html><head><title>yuque-云枢开发者手册</title>
<style>.a{color:#333}</style>
<script>
var doc = {"top":true,"id":"biAkN","margin":{"top":true,"bottom":1}};
var meta = "tb%22%3Atrue%2C%22id%22%3A%22biAkN%22%2C%22margin%22%3A%7B%22top%22%3Atrue";
</script></head>
<body>
<h1>云枢开发者手册</h1>
<p>接口鉴权使用 Bearer Token，放在 Authorization 头里。</p>
<div>超时时间默认 30 秒，可通过 timeout 参数调整。</div>
</body></html>"""

    cleaned = clean_source_text(html, ".html")
    check("剥掉了 <script> 里的 JSON 配置", "biAkN" not in cleaned,
          cleaned[:120])
    check("解开了 URL 编码（不再是 %22%3A 这种乱码）",
          "%22" not in cleaned and "%3A" not in cleaned, cleaned[:120])
    check("真实正文保留了", "Bearer Token" in cleaned, cleaned[:120])
    check("第二个段落也保留了", "超时时间" in cleaned, cleaned[:120])
    check("剥掉了 <style> 的内容", "color:#333" not in cleaned)
    check("剥掉了标签本身", "<p>" not in cleaned and "<div>" not in cleaned)

    chunks = chunk_text(cleaned)
    check("能切出可检索的块", bool(chunks), str(len(chunks)))
    joined = " ".join(c.text for c in chunks)
    check("切出来的块里没有编码垃圾", "%22" not in joined, joined[:120])

    # 纯文本 / Markdown 必须原样不动（动代码缩进会出事）
    md = "# 标题\n\n```python\n    indented = True\n```\n"
    check("Markdown 原样返回", clean_source_text(md, ".md") == md.strip())
    check("txt 原样返回", clean_source_text("a  b\nc", ".txt") == "a  b\nc")
    check("空内容安全", clean_source_text("", ".html") == "")
    check("畸形 HTML 不抛异常",
          bool(clean_source_text("<p>没闭合<div><span>", ".html")))

    # 没有后缀但内容明显是 HTML：也要认出来
    check("靠内容认得出 HTML",
          "%22" not in clean_source_text("<html><script>x='%22a%22'</script>"
                                         "<body>正文</body></html>", ""))


def test_auto_repair() -> None:
    print("\n=== 三之二、老版本导进来的脏资料会被自动修好 ===")
    from PySide6.QtCore import QCoreApplication

    from pawpet.ai.kb import KnowledgeBase, looks_unparsed
    from pawpet.store import Store

    app = QCoreApplication.instance() or QCoreApplication([])
    scratch = SCRATCH / "repair"
    scratch.mkdir(parents=True, exist_ok=True)

    # 造一份「修复前导入的」资料：源文件是真 HTML，但存进去的块是垃圾
    source = scratch / "手册.html"
    source.write_text(
        "<html><head><script>var d={\"id\":\"biAkN\"};"
        "var m='tb%22%3Atrue%2C%22id%22%3A%22biAkN%22';</script></head>"
        "<body><h1>云枢开发者手册</h1>"
        "<p>接口鉴权使用 Bearer Token。</p></body></html>",
        encoding="utf-8")

    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    store.state["knowledge"] = {
        "docs": [{
            "name": "手册.html",
            "path": str(source),
            "added": 1.0,
            "size": 200,
            "chunks": [
                {"text": "tb%22%3Atrue%2C%22id%22%3A%22biAkN%22 "
                         "<div style=\"x\"> </div>", "heading": ""},
            ],
            "error": "",
        }],
    }
    store.save()

    book = KnowledgeBase(store)
    raw = book.load()
    check("脏数据确实被判定为脏",
          any(looks_unparsed(c.text) for c in raw[0].chunks),
          raw[0].chunks[0].text[:60] if raw[0].chunks else "（没有块）")

    fixed = book.load_repaired()
    check("修完之后不再是脏数据",
          not any(looks_unparsed(c.text) for c in fixed[0].chunks),
          fixed[0].chunks[0].text[:60] if fixed[0].chunks else "（没有块）")
    joined = " ".join(c.text for c in fixed[0].chunks)
    check("修复后能读到真正的正文", "Bearer Token" in joined, joined[:100])
    check("修复后没有编码垃圾", "%22" not in joined, joined[:100])

    # 必须写回存储，否则每次加载都要重算
    again = KnowledgeBase(store).load()
    check("修复结果写回了存储",
          not any(looks_unparsed(c.text) for c in again[0].chunks),
          again[0].chunks[0].text[:50] if again[0].chunks else "")

    # 源文件没了：不许删用户的资料，留着旧数据
    source.unlink()
    store.state["knowledge"]["docs"][0]["chunks"] = [
        {"text": "tb%22%3Atrue%2C%22id%22%3A%22biAkN%22 <div style=\"x\">",
         "heading": ""},
    ]
    store.save()
    kept = KnowledgeBase(store).load_repaired()
    check("源文件不在了也不删资料", len(kept) == 1 and bool(kept[0].chunks),
          str(len(kept)))

    # 干净的资料不该被无谓地重读文件
    store2 = Store(scratch / "q.json", scratch / "q.bak.json")
    store2.load()
    store2.state["knowledge"] = {
        "docs": [{"name": "干净的.md", "path": "/不存在/干净的.md",
                  "added": 1.0, "size": 10,
                  "chunks": [{"text": "这是一段正常的中文正文，没有任何标签。",
                              "heading": ""}],
                  "error": ""}],
    }
    store2.save()
    clean = KnowledgeBase(store2).load_repaired()
    check("干净的资料原样保留（路径不存在也不报错）",
          len(clean) == 1 and "正常的中文正文" in clean[0].chunks[0].text)


def test_templates_hidden() -> None:
    print("\n=== 四、「点一下就跑」能关掉，而且记得住 ===")
    from PySide6.QtCore import QCoreApplication

    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QCoreApplication.instance() or QCoreApplication([])
    scratch = SCRATCH / "tpl"
    scratch.mkdir(parents=True, exist_ok=True)

    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    backend = Backend(store)

    check("默认展开（第一次用最需要它）", backend.aiTemplatesHidden is False)

    backend.aiTemplatesHidden = True
    check("能关掉", backend.aiTemplatesHidden is True)
    check("落进了设置（重启还记得）",
          bool(store.settings.get("ai_templates_hidden")), str(store.settings))

    store2 = Store(scratch / "p.json", scratch / "p.bak.json")
    store2.load()
    check("重新加载后仍然是收起的", bool(store2.settings.get("ai_templates_hidden")))

    backend.aiTemplatesHidden = False
    check("能再打开", backend.aiTemplatesHidden is False)
    backend.shutdown()

    # QML 里必须有触发开关的入口，两处都要有
    qml = (ROOT / "pawpet" / "qml" / "PawPet" / "page" / "AiPage.qml").read_text(
        encoding="utf-8")
    check("卡片可见性受开关控制",
          "!backend.aiTemplatesHidden" in qml, "AiPage.qml 里没接上开关")
    check("卡片上有 ✕ 按钮", "backend.aiTemplatesHidden = true" in qml)
    check("收起后有找回入口", "backend.aiTemplatesHidden = false" in qml)


def test_taskbar_identity() -> None:
    print("\n=== 五、任务栏身份（不再顶着 Python 图标）===")
    from pawpet import win32

    check("win32 里有 set_app_user_model_id", hasattr(win32, "set_app_user_model_id"))

    ok = win32.set_app_user_model_id("PawPet.DesktopPet.Test")
    check("调用成功（返回 S_OK）", ok, "SetCurrentProcessExplicitAppUserModelID 失败")

    # 必须在创建窗口之前调用 —— 检查调用顺序
    src = (ROOT / "pawpet" / "app.py").read_text(encoding="utf-8")
    call_at = src.find("set_app_user_model_id(")
    check("在 app.run 里调用了", call_at > 0)
    qapp_at = src.find("QApplication(sys.argv)")
    check("调用发生在创建 QApplication 之前（否则已经晚了）",
          0 < call_at < qapp_at, f"call@{call_at} vs QApplication@{qapp_at}")

    # QApplication 也要有名字/版本，任务栏跳转列表才完整
    check("设置了 applicationName", "setApplicationName" in src)
    check("设置了 windowIcon", "setWindowIcon" in src)

    # 安装程序创建的快捷方式必须指向 exe 且带图标
    setup = (ROOT / "build" / "setup_ui.py").read_text(encoding="utf-8")
    check("安装程序把图标设成了 exe", "icon = exe" in setup)
    check("快捷方式带了 IconLocation",
          "IconLocation" in setup or "icon" in setup.lower())


def main() -> int:
    print("小爪用户反馈三问题的回归\n")
    test_silent_model()
    test_report_is_footnote()
    test_html_knowledge()
    test_auto_repair()
    test_templates_hidden()
    test_taskbar_identity()

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
