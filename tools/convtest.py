r"""对话持久化回归。

用户要的是「关掉小爪再打开，之前问过的还在」——这决定了这个测试必须
**真的重启进程**，不能只在同一个进程里 load/save 一遍。后者证明不了
「下次启动读得回来」（而那恰恰是唯一重要的事）。

所以做法是：起子进程 → 造对话 → 退出 → 再起一个子进程 → 检查还在不在。

另外盯住几个容易做错的点：
  * `image` 不能落盘：它指向 `.cache/ai/screen.png`，一个**每次截图都被
    覆盖**的固定路径。存下来的话，历史消息会显示「当前」截图而不是当时的，
    比不显示更糟。
  * `html` 不落盘但加载时要重算，否则历史消息的排版全丢。
  * 退出前必须把节流中的改动补写，否则「问完就关」的那一段会丢。

用法：
    .venv\\Scripts\\python.exe tools\\convtest.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "convtest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "convtest"

PASSED = 0
FAILED: list[str] = []

DATA = SCRATCH / "data"
CONV = DATA / "conversations.json"


def clear_conversation_files() -> None:
    CONV.unlink(missing_ok=True)
    CONV.with_suffix(".jsonl").unlink(missing_ok=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def child_env(tag: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PAWPET_HOME"] = str(DATA)
    env["PAWPET_INSTANCE_SUFFIX"] = f"convtest-{tag}"
    return env


def run_snippet(name: str, body: str, timeout: int = 180):
    """在子进程里跑一段脚本（子进程 = 一次真实的启动）。"""
    script = SCRATCH / f"{name}.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from pawpet.backend import Backend\n"
        "from pawpet.store import Store\n"
        "app = QApplication(sys.argv[:1])\n"
        f"store = Store(Path({str(DATA / 'pet.json')!r}), "
        f"Path({str(DATA / 'pet.bak.json')!r}))\n"
        "store.load()\n"
        "backend = Backend(store)\n"
        "ai = backend.ai\n"
        + textwrap.dedent(body)
        + "\nbackend.shutdown()\nprint('CHILD_DONE')\n",
        encoding="utf-8")
    return subprocess.run([sys.executable, str(script), name],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=child_env(name), timeout=timeout)


def main() -> int:
    print("小爪对话持久化回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)

    from pawpet.ai import history as hm

    # ================================================================ 一
    print("=== 一、存储层（纯逻辑，不起 Qt）===")
    check("初始没有文件", not CONV.exists())
    check("读不存在的文件返回空", hm.load(CONV) == [])

    CONV.write_text("{ 这不是 json", encoding="utf-8")
    check("文件坏了返回空而不是抛异常", hm.load(CONV) == [])
    clear_conversation_files()

    session = hm.new_session([
        {"role": "user", "text": "帮我整理下载文件夹"},
        {"role": "assistant", "text": "好的"},
    ])
    check("会话有 id", bool(session.id), session.id)
    check("标题取自第一条用户消息",
          session.title == "帮我整理下载文件夹", session.title)
    check("长标题会截断",
          len(hm.make_title([{"role": "user", "text": "一" * 200}]))
          <= hm.TITLE_CHARS)
    compacted = hm._trim_message({
        "role": "tool", "text": "x" * 5000, "detail": "y" * 3000,
        "html": "<b>不要存</b>",
    })
    check("历史正文有长度上限", len(compacted["text"]) <= 1600)
    check("历史细节有长度上限", len(compacted["detail"]) <= 1200)

    hm.save(CONV, [session])
    back = hm.load(CONV)
    check("能存能读", len(back) == 1 and back[0].id == session.id, str(len(back)))

    journal = hm.journal_path(CONV)
    journal.unlink(missing_ok=True)
    updated = hm.Session(
        id=session.id,
        started=session.started,
        updated=session.updated + 1,
        title=session.title,
        messages=session.messages + [{"role": "assistant", "text": "追加的一条"}],
    )
    ok, message = hm.append_session(CONV, updated)
    check("追加会话不要求重写快照", ok, message)
    check("追加日志可恢复最新会话",
          hm.load(CONV)[0].messages[-1].get("text") == "追加的一条")
    check("追加日志文件生成", journal.exists())

    # ---- 哪些字段不能落盘 ----
    print("\n  image / html 不该落盘：")
    hm.save(CONV, [hm.new_session([
        {"role": "tool", "text": "看屏幕", "image": "file:///C:/x/screen.png",
         "html": "<b>看屏幕</b>", "detail": "细节", "seconds": 1.5},
    ])])
    raw = json.loads(CONV.read_text(encoding="utf-8"))
    msg = raw["sessions"][0]["messages"][0]
    check("image 没落盘（存了会显示错图，比不显示更糟）",
          "image" not in msg, str(msg))
    check("html 没落盘（可从 text 重算，存两份文件大一倍）",
          "html" not in msg, str(msg))
    check("detail 留着（回看时有用）", msg.get("detail") == "细节", str(msg))
    check("seconds 留着", msg.get("seconds") == 1.5, str(msg))

    # ---- 容量控制 ----
    print("\n  容量控制：")
    many = [hm.Session(id=f"s{i}", started=i, updated=i, title=f"会话{i}",
                       messages=[{"role": "user", "text": f"第{i}段"}])
            for i in range(hm.MAX_SESSIONS + 20)]
    hm.save(CONV, many)
    kept = hm.load(CONV)
    check(f"会话数截到 {hm.MAX_SESSIONS}", len(kept) == hm.MAX_SESSIONS,
          str(len(kept)))
    check("留下的是最新的", kept[0].title == f"会话{hm.MAX_SESSIONS + 19}",
          kept[0].title)

    long_one = hm.Session(id="long", started=1, updated=1, title="很长",
                          messages=[{"role": "user", "text": f"第{i}条"}
                                    for i in range(hm.SESSION_MAX_MESSAGES + 50)])
    hm.save(CONV, [long_one])
    kept = hm.load(CONV)
    check(f"单会话截到 {hm.SESSION_MAX_MESSAGES} 条",
          len(kept[0].messages) == hm.SESSION_MAX_MESSAGES,
          str(len(kept[0].messages)))
    check("留下的是最后那些（不是最前面）",
          kept[0].messages[-1]["text"] == f"第{hm.SESSION_MAX_MESSAGES + 49}条",
          kept[0].messages[-1]["text"])

    probe = hm.Session(id="p", started=1, updated=1, title="t",
                       messages=[{"role": "user", "text": f"x{i}"}
                                 for i in range(hm.SESSION_MAX_MESSAGES + 30)])
    before = len(probe.messages)
    hm.save(CONV, [probe])
    check("save 不会把调用方手里的消息截短（它是个读操作）",
          len(probe.messages) == before, f"{before} → {len(probe.messages)}")

    # ================================================================ 二
    print("\n=== 二、跨进程重启（重点）===")
    clear_conversation_files()

    first = run_snippet("a_write", '''
ai._push("user", "帮我整理一下下载文件夹")
ai._push("tool", "看文件夹 D:\\\\下载", tool="list_dir", ok=True,
         detail="共 43 项", seconds=0.2)
ai._push("assistant", "整理好了，分成了四类。",
         report="这一轮执行了 3 个操作。文件位置：D:\\\\下载。")
ai._flush_history()
''')
    check("第一个进程跑完", "CHILD_DONE" in (first.stdout or ""),
          ((first.stdout or "") + (first.stderr or ""))[-260:])
    check("对话文件被创建", CONV.exists(),
          f"目录内容 {[p.name for p in DATA.rglob('*')][:8]}")

    if CONV.exists():
        raw = json.loads(CONV.read_text(encoding="utf-8"))
        sessions = raw.get("sessions") or []
        check("存了 1 段会话", len(sessions) == 1, str(len(sessions)))
        if sessions:
            roles = [m.get("role") for m in sessions[0]["messages"]]
            check("用户那句话在",
                  any("下载文件夹" in (m.get("text") or "")
                      for m in sessions[0]["messages"]), str(roles))
            check("工具卡片也在（回看时要看到当时调了什么）",
                  "tool" in roles, str(roles))
            check("收尾交代也在",
                  any(m.get("report") for m in sessions[0]["messages"]))

    # ---- 关键：新进程能读回来 ----
    second = run_snippet("b_read", '''
print("RESTORED", ai.restored)
print("COUNT", ai.messageCount)
print("SESSIONS", ai.conversationCount)
for m in ai.messages:
    print("MSG", m.get("role"), "|", (m.get("text") or "")[:36],
          "| html:", len(m.get("html") or ""))
''')
    out = second.stdout or ""
    print("  --- 第二个进程 ---")
    for line in out.strip().splitlines()[:10]:
        print(f"    {line}")
    print("  --- 结束 ---")

    check("重启后标记为「接着上次」", "RESTORED True" in out, out[:200])
    check("重启后消息还在", "COUNT 0" not in out and "COUNT" in out, out[:200])
    check("重启后能列出会话", "SESSIONS 1" in out, out[:200])
    # 只检查 **assistant** 那一行有 html。
    # 不能断言「输出里没有 html: 0」—— user/tool 消息本来就该是 0
    # （只有 assistant / error 才转富文本）。第一版就是这么写错的。
    assistant_line = next((ln for ln in out.splitlines()
                           if ln.startswith("MSG assistant")), "")
    check("重启后 assistant 的 html 被重算出来了",
          "html: 0" not in assistant_line and "html:" in assistant_line,
          assistant_line or "（没有 assistant 行）")
    check("重启后工具卡片还在（不只是文字对话）",
          "MSG tool" in out, out[:300])

    # **问候语不能进对话记录。** 它是「当前状态提示」，每次启动都会重新
    # 生成 —— 存下来的话重启几次就堆满「已就绪：…」「还没有配置模型…」，
    # 把用户真正问过的东西淹掉。
    if CONV.exists():
        blob = CONV.read_text(encoding="utf-8")
        check("问候语没被存进对话记录（否则每次重启堆一条）",
              "已就绪" not in blob and "还没有配置模型" not in blob,
              "问候语被持久化了")
        check("问候语累积检查：文件里不含「接着上次的对话」",
              "接着上次的对话" not in blob, "启动提示被存下来了")
        raw = json.loads(blob)
        stored = raw["sessions"][0]["messages"]
        check("存下来的只该是真实对话（user/assistant/tool）",
              all(m.get("role") in ("user", "assistant", "tool")
                  for m in stored),
              str([m.get("role") for m in stored]))

    # ================================================================ 三
    print("\n=== 三、退出时补写（问完就关也不丢）===")
    clear_conversation_files()
    # 故意不调 _flush_history：模拟「问完最后一句话，两秒内就关掉」。
    # 节流定时器还没到点，只有 shutdown() 的兜底能救这一条。
    third = run_snippet("c_noflush", '''
ai._push("user", "这条是问完就关的")
ai._push("assistant", "好的。")
''')
    check("这个进程故意没调 _flush_history",
          "CHILD_DONE" in (third.stdout or ""), (third.stdout or "")[-150:])
    if CONV.exists():
        blob = CONV.read_text(encoding="utf-8")
        check("shutdown() 的兜底把它写下来了", "问完就关" in blob, blob[:200])
    else:
        check("shutdown() 的兜底把对话写下来了", False,
              "文件根本没生成 —— 退出时没补写")

    # ================================================================ 四
    print("\n=== 四、会话切换与删除 ===")
    fourth = run_snippet("d_switch", '''
before = ai.conversationCount
print("BEFORE", before)

# 开新对话：旧那段要归档，段数 +1，界面清空
ai.newConversation()
ai._push("user", "第二段：查一下日志")
ai._push("assistant", "查到了三条报错。")
ai._flush_history()
print("AFTER_NEW", ai.conversationCount)
print("CURRENT_MSGS", ai.messageCount)

# 切回最旧那段
listing = ai.conversations
print("LISTING", [c["title"] for c in listing])
oldest = listing[-1] if listing else None
if oldest:
    ai.openConversation(oldest["id"])
    print("OPENED", oldest["title"])
    print("OPENED_MSGS", ai.messageCount)
    print("OPENED_FIRST", (ai.messages[0].get("text") or "")[:30] if ai.messages else "")

# 删一段
listing = ai.conversations
if len(listing) >= 2:
    ai.deleteConversation(listing[-1]["id"])
    print("AFTER_DELETE", ai.conversationCount)
''')
    out4 = fourth.stdout or ""
    print("  --- 第四个进程 ---")
    for line in out4.strip().splitlines()[:12]:
        print(f"    {line}")
    print("  --- 结束 ---")

    check("开新对话后段数 +1", "AFTER_NEW 2" in out4, out4[:260])
    # 3 条 = 新对话的开场提示 + 我 push 的 user + assistant。
    # 关键是**旧内容不在里面**（下面那条断言）。
    check("新对话之后界面是干净的新内容", "CURRENT_MSGS 3" in out4, out4[:260])
    check("新对话之后看不到上一段的内容",
          "CURRENT_MSGS" in out4 and "问完就关" not in out4.split("CURRENT_MSGS")[1]
          .split("\n")[0], out4[:260])
    check("能列出两段", "LISTING" in out4 and "问完就关" in out4, out4[:260])
    check("能切回旧会话", "OPENED" in out4, out4[:260])
    check("切回后消息回来了", "OPENED_MSGS 0" not in out4
          and "OPENED_MSGS" in out4, out4[:260])
    check("切回后内容对得上", "OPENED_FIRST 这条是问完就关的" in out4, out4[:300])
    check("能删掉一段", "AFTER_DELETE 1" in out4, out4[:260])

    # ================================================================ 五
    print("\n=== 五、清空不会静默抹掉历史 ===")
    fifth = run_snippet("e_clear", '''
ai._push("user", "清空前的一句话")
ai._push("assistant", "好的。")
ai._flush_history()
count_before = ai.conversationCount
ai.clear()
print("COUNT_BEFORE", count_before)
print("AFTER_CLEAR_MSGS", ai.messageCount)
print("AFTER_CLEAR_SESSIONS", ai.conversationCount)
''')
    out5 = fifth.stdout or ""
    check("清空后界面空了", "AFTER_CLEAR_MSGS 0" in out5, out5[:260])
    check("清空后那段进历史了（不是无声抹掉）",
          "AFTER_CLEAR_SESSIONS" in out5 and "AFTER_CLEAR_SESSIONS 1" in out5,
          out5[:260])

    # ================================================================ 六
    print("\n=== 六、界面接了这些接口 ===")
    qml = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "pawpet" / "qml" / "PawPet" / "page" / "AiPage.qml",
            ROOT / "pawpet" / "qml" / "PawPet" / "AiHistoryPanel.qml",
            ROOT / "pawpet" / "qml" / "PawPet" / "AiSettingsPanel.qml",
        )
    )
    for api in ("newConversation()", "openConversation(", "deleteConversation(",
                "clearHistory()", "backend.ai.conversations",
                "backend.ai.historyFolder()", "page.showHistory"):
        check(f"QML 调了 {api}", api in qml, "界面没接")

    # ================================================================ 七
    print("\n=== 七、其它测试套件的隔离 ===")
    # 对话持久化会引入一类**隐蔽的测试污染**：`conversations.json` 落在
    # `store.path.parent`，所以同一个脚本里建两个 Store（都指向同一个
    # SCRATCH 根）就会共享对话文件 —— 第二个用例会「恢复」出第一个的
    # 对话，断言看到多余消息。
    #
    # 这不是功能坏了（它就该记住），是测试没隔离。已经手工修了 asktest
    # 和 finishtest；这里加一条自动检查，防止以后再写新测试时踩进去。
    import re as _re

    polluted: list[str] = []
    for path in sorted((ROOT / "tools").glob("*.py")):
        if path.name in ("convtest.py", "historytest.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "AiController(" not in text and "Backend(" not in text:
            continue
        # 找同一目录下的多个 Store(...) 调用
        dirs = _re.findall(r'Store\(\s*([A-Z_]+)\s*/', text)
        if len(dirs) != len(set(dirs)) or len(dirs) < 2:
            continue
        # 允许用子目录：Store(SCRATCH / "case_a" / ...)
        subdirs = _re.findall(r'Store\(\s*[A-Z_]+\s*/\s*"[^"]+"\s*/', text)
        if len(subdirs) < len(dirs):
            polluted.append(f"{path.name}（{len(dirs)} 个 Store 用同一个根目录）")

    if polluted:
        for item in polluted:
            print(f"    {item}")
    check("没有其它套件共享数据目录（会互相污染对话）", not polluted,
          "; ".join(polluted))

    # ================================================================ 八
    print("\n=== 八、隐私：不该进仓库 ===")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check("conversations.json 在 .gitignore 里（含用户的对话内容，仓库是公开的）",
          "conversations.json" in ignore, "私人数据")

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
