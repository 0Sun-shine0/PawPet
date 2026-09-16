r"""知识库自测。

这个功能有两个容易做错的地方：

1. **变成「全塞进提示词」** —— 那样导几份文档就把上下文撑爆，
   而且每轮都要重发。所以测试会盯住「目录必须小、正文必须靠检索」。
2. **检索不准** —— 明明导入过却搜不到，用户会以为功能坏了。
   所以要用真实提问方式（自然语言）验证能命中。

用法：
    .venv\Scripts\python.exe tools\kbtest.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "kb"
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


def write(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def main() -> int:
    print("小爪知识库自测\n")

    from pawpet.ai import kb as kbmod
    from pawpet.ai.kb import (
        CATALOG_BUDGET,
        MAX_TOTAL_CHARS,
        Chunk,
        KnowledgeBase,
        catalog_text,
        chunk_text,
        describe,
        import_file,
        import_folder,
        merge,
        search,
    )
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    docs_dir = SCRATCH / "资料"
    write(docs_dir / "产品说明.md", """# 小爪助手 产品说明

小爪助手是一个桌面宠物，顺带把专注、待办、便签、提醒都做了。

## 定价

基础版免费，含专注计时和待办。

## 专业版

专业版每月 30 元，包含全部 AI 操作功能。

## 系统要求

需要 Windows 10 或更高版本。
""")
    write(docs_dir / "常见问题.txt", """问：支持多显示器吗？
答：支持。截图时可以选择要抓哪个显示器。

问：数据会上传吗？
答：不会。所有数据只存在你自己电脑上。
""")
    write(docs_dir / "skill.py", """def greet(name):
    \"\"\"打个招呼。\"\"\"
    return f"你好 {name}"
""")
    write(docs_dir / "notes.csv", "name,score\n张三,90\n李四,85\n")
    write(docs_dir / "手册.pdf", "%PDF-1.4 fake")
    write(docs_dir / "旧文档.docx", "PK fake")

    sub = docs_dir / "子目录"
    write(sub / "深层.md", "# 深层资料\n\n这段内容在子目录里。")

    # ================================================== 一、切块
    print("=== 一、切块：按标题切，不劈断话 ===")

    # 注意：过短的相邻小节会被**合并**（MERGE_BELOW_CHARS），
    # 所以测试要用真实长度的正文，否则看到的是合并后的结果而不是切块结果。
    body_one = "内容一。" * 60      # 240 字，超过合并阈值
    body_two = "内容二。" * 60
    chunks = chunk_text(f"# 标题一\n\n{body_one}\n\n# 标题二\n\n{body_two}")
    check("按标题切成多块", len(chunks) == 2, str(len(chunks)))
    check("标题被记下来了",
          chunks[0].heading == "标题一", chunks[0].heading)
    check("内容跟着对的标题走",
          "内容二" in chunks[1].text and chunks[1].heading == "标题二",
          str(chunks[1].heading))

    # 短小节**不能丢**（这是踩过的坑：原来直接扔掉，用户的文档凭空少几节）
    short = chunk_text("## 定价\n\n基础版免费。\n\n## 专业版\n\n每月 30 元。")
    check("短小节不会被丢掉",
          any("30 元" in c.text for c in short), "短内容被扔了！")
    check("短小节被并成一块", len(short) == 1, str(len(short)))
    check("合并后标题都留着",
          "定价" in short[0].heading and "专业版" in short[0].heading,
          short[0].heading)

    check("空文本切出空", chunk_text("") == [])
    check("纯空白切出空", chunk_text("   \n\n  ") == [])

    # 超长无标题的，按大小切
    long_text = "\n\n".join(f"第 {i} 段内容。" * 20 for i in range(20))
    many = chunk_text(long_text)
    check("超长文本文按大小切成多块", len(many) > 1, str(len(many)))
    check("每块不超过上限",
          all(len(c.text) <= kbmod.CHUNK_CHARS + 50 for c in many),
          str([len(c.text) for c in many][:5]))

    # ================================================== 二、导入
    print("\n=== 二、导入 ===")

    docs, notes = import_folder(str(docs_dir), [])
    names = {doc.name for doc in docs}
    check("导入了 md", "产品说明.md" in names, str(sorted(names)))
    check("导入了 txt", "常见问题.txt" in names)
    check("导入了代码", "skill.py" in names)
    check("导入了 csv", "notes.csv" in names)
    check("默认不递归（子目录没进来）", "深层.md" not in names, str(sorted(names)))
    check("PDF 被跳过并给了说明",
          any("PDF" in note for note in notes), str(notes))
    check("docx 被跳过并给了说明",
          any("Word" in note for note in notes), str(notes))

    deep, _ = import_folder(str(docs_dir), [], recursive=True)
    check("recursive=true 时子目录也进来",
          any(doc.name == "深层.md" for doc in deep),
          str(sorted(doc.name for doc in deep)))

    single, message = import_file(str(docs_dir / "产品说明.md"), [])
    check("能导入单个文件", single is not None, message)
    check("单个文件也有块", single is not None and single.chunk_count >= 1)

    missing, message = import_file(str(SCRATCH / "没有这个.md"), [])
    check("不存在的文件给出可读提示",
          missing is None and "没有这个文件" in message, message)

    pdf, message = import_file(str(docs_dir / "手册.pdf"), [])
    check("PDF 明确拒绝并给替代方案",
          pdf is None and "另存为" in message, message)

    # ================================================== 三、检索
    print("\n=== 三、检索：用真实提问方式能命中 ===")

    cases = [
        ("专业版多少钱", "产品说明.md", "30 元"),
        # 注意：「怎么收费的」**测不了** —— 文档里写的是「定价」，
        # 关键词检索跨不过同义词（那需要向量检索，不在这个零依赖项目的范围内）。
        # 所以用例都用文档里真实出现过的说法，别拿它测做不到的事。
        ("专业版价格", "产品说明.md", "30"),
        ("定价", "产品说明.md", "基础版"),
        ("支持多显示器吗", "常见问题.txt", "显示器"),
        ("数据会上传吗", "常见问题.txt", "不会"),
        ("需要什么系统", "产品说明.md", "Windows"),
    ]
    for query, want_doc, want_text in cases:
        hits = search(docs, query, limit=4)
        hit = any(h.doc == want_doc and want_text in h.text for h in hits)
        check(f"「{query}」-> 命中 {want_doc} 里的「{want_text}」",
              hit, str([(h.doc, h.text[:24]) for h in hits]))

    hits = search(docs, "greet 函数", limit=4)
    check("能搜到代码", any("greet" in h.text for h in hits),
          str([h.doc for h in hits]))

    hits = search(docs, "张三多少分", limit=4)
    check("能搜到 csv 内容", any("张三" in h.text for h in hits),
          str([h.doc for h in hits]))

    check("无关的词返回空", search(docs, "量子计算机", limit=4) == [])
    check("空查询返回空", search(docs, "", limit=4) == [])

    # 上限
    many_hits = search(docs, "小爪", limit=99)
    check(f"limit 被夹到 {kbmod.MAX_HITS} 以内",
          len(many_hits) <= kbmod.MAX_HITS, str(len(many_hits)))

    total = sum(len(h.text) for h in search(docs, "小爪", limit=8))
    check("一次检索的总字数受控",
          total <= MAX_TOTAL_CHARS + 400, f"实际 {total}")

    # ================================================== 四、目录必须小
    print("\n=== 四、注入提示词的目录必须很小 ===")

    catalog = catalog_text(docs)
    check("目录里列出了资料名", "产品说明.md" in catalog, catalog[:90])
    check("目录里有块数", "块" in catalog)
    check("目录里**没有**正文",
          "每月 30 元" not in catalog and "多显示器" not in catalog,
          "正文漏进目录了，这会让每轮请求都变大")
    check(f"目录很短（< {CATALOG_BUDGET + 300} 字）",
          len(catalog) < CATALOG_BUDGET + 300, f"实际 {len(catalog)}")
    check("提到了检索工具名（和真实工具名一致）",
          "search_knowledge" in catalog, catalog[-140:])

    # 塞很多资料，目录也不能爆
    flood = [kbmod.Doc(name=f"资料{i}.md", path=f"/x/{i}.md",
                       chunks=[Chunk(text="内容" * 100, heading="h")],
                       added=float(i))
             for i in range(300)]
    flood_catalog = catalog_text(flood)
    check("资料很多时目录被截断",
          len(flood_catalog) < CATALOG_BUDGET + 400, f"实际 {len(flood_catalog)}")
    check("截断时说明了还有多少", "还有" in flood_catalog, flood_catalog[-120:])

    check("空知识库返回空串（不注入空壳）", catalog_text([]) == "")
    check("只有失败记录时也返回空串",
          catalog_text([kbmod.Doc(name="x.pdf", error="不支持")]) == "")

    # ================================================== 五、持久化与容错
    print("\n=== 五、持久化与脏数据容错 ===")

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    book = KnowledgeBase(store)
    check("新库是空的", book.load() == [])

    book.add(docs)
    reopened = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    reopened.load()
    reloaded = KnowledgeBase(reopened).load()
    check("重开还在", len(reloaded) == len(docs),
          f"{len(docs)} -> {len(reloaded)}")
    check("块数也还在",
          sum(d.chunk_count for d in reloaded) == sum(d.chunk_count for d in docs))

    # 同名替换，不是追加
    updated, _ = import_file(str(docs_dir / "产品说明.md"), reloaded)
    merged = merge(reloaded, [updated])
    same_name = [d for d in merged if d.name == "产品说明.md"]
    check("同名资料是替换而不是追加", len(same_name) == 1, str(len(same_name)))

    # 脏数据
    check("None 能解析", kbmod.Doc.from_dict(None).name == "")
    check("字符串能解析", kbmod.Doc.from_dict("x").name == "")
    dirty = kbmod.Doc.from_dict({
        "name": "脏.md", "path": "/x",
        "chunks": [None, 42, {"text": ""}, {"text": "好的内容", "heading": 5}],
        "added": "不是数字", "size": "也不是",
    })
    check("坏块被跳过", len(dirty.chunks) == 1, str(len(dirty.chunks)))
    check("坏的 heading 变成字符串",
          dirty.chunks[0].heading == "5", repr(dirty.chunks[0].heading))
    check("坏的时间戳不炸", dirty.added == 0.0, str(dirty.added))

    check("describe 空库时给引导", "导入" in describe([]), describe([])[:60])
    check("describe 有资料时列出来",
          "产品说明.md" in describe(reloaded), describe(reloaded)[:80])

    # ================================================== 六、工具接线
    print("\n=== 六、工具接线 ===")

    from pawpet.ai.actions import AuditLog, DesktopActions
    from pawpet.ai.tools import TOOL_INDEX, ToolContext
    from pawpet.ai.vision import ScreenCapture

    for name, want_risk in (("import_knowledge", "confirm"),
                            ("search_knowledge", "read"),
                            ("list_knowledge", "read"),
                            ("forget_knowledge", "confirm")):
        spec = TOOL_INDEX.get(name)
        check(f"工具 {name} 已注册（risk={want_risk}）",
              spec is not None and spec.risk == want_risk,
              spec.risk if spec else "未注册")

    actions = DesktopActions(AuditLog())
    actions.level = "full"
    context = ToolContext(ScreenCapture(), actions, store)

    ok, message, _ = context.execute("list_knowledge", {})
    check("list_knowledge 能列出资料", ok and "产品说明.md" in message,
          message[:80])

    ok, message, _ = context.execute("search_knowledge",
                                     {"query": "专业版多少钱"})
    check("search_knowledge 能检索到", ok and "30 元" in message, message[:100])
    check("检索结果带出处", "产品说明.md" in message, message[:100])

    ok, message, _ = context.execute("search_knowledge", {"query": "完全没有的词"})
    check("搜不到时说明没找到并列出已有资料",
          ok and "没有" in message and "产品说明" in message, message[:100])

    ok, message, _ = context.execute("import_knowledge",
                                     {"path": str(sub), "recursive": True})
    check("import_knowledge 能导入目录", ok and "已导入" in message,
          message[:100])

    ok, message, _ = context.execute("forget_knowledge", {"name": "notes.csv"})
    check("forget_knowledge 能移除", ok and "移除" in message, message[:80])

    ok, message, _ = context.execute("forget_knowledge", {"name": "不存在的资料"})
    check("移除不存在的资料不报错", ok and "没有" in message, message[:60])

    # 空库时检索要给引导而不是报错
    empty_store = Store(SCRATCH / "empty.json", SCRATCH / "empty.bak.json")
    empty_store.load()
    empty_actions = DesktopActions(AuditLog())
    empty_actions.level = "full"
    empty_context = ToolContext(ScreenCapture(), empty_actions, empty_store)
    ok, message, _ = empty_context.execute("search_knowledge", {"query": "任何"})
    check("空库检索给引导而不是报错", ok and "空" in message, message[:80])

    # ================================================== 七、控制器与提示词
    print("\n=== 七、控制器接线与提示词 ===")

    from pawpet.ai.controller import AiController
    from pawpet.ai.agent import system_prompt

    store2 = Store(SCRATCH / "c.json", SCRATCH / "c.bak.json")
    store2.load()
    controller = AiController(store2)
    try:
        check("空库时 knowledgeCount 为 0", controller.knowledgeCount == 0,
              str(controller.knowledgeCount))
        controller.importKnowledge(str(docs_dir))
        check("通过控制器能导入", controller.knowledgeCount >= 4,
              str(controller.knowledgeCount))
        check("块数统计有值", controller.knowledgeChunks > 0,
              str(controller.knowledgeChunks))
        check("摘要里有资料名", "产品说明.md" in controller.knowledgeSummary,
              controller.knowledgeSummary[:80])

        history = controller._build_history()
        prompt = history[0]["content"]
        check("系统提示词里有知识库目录",
              "【知识库】" in prompt and "产品说明.md" in prompt,
              prompt[-200:])
        check("目录里没有正文",
              "每月 30 元" not in prompt, "正文漏进提示词了")
        check("提示词里的工具名和真实工具一致",
              "search_knowledge" in prompt)

        result = controller.searchKnowledge("专业版多少钱")
        check("界面上的试查能用", "30 元" in result or "产品说明" in result,
              result[:80])

        controller.forgetKnowledge("产品说明.md")
        check("界面能移除单份", "产品说明.md" not in controller.knowledgeSummary,
              controller.knowledgeSummary[:80])
        controller.clearKnowledge()
        check("界面能清空", controller.knowledgeCount == 0,
              str(controller.knowledgeCount))
    finally:
        controller.shutdown()

    # system_prompt 的行为
    check("没有知识库时提示词不含那一节",
          "【知识库】" not in system_prompt(20, "", ""))
    check("有知识库时才注入",
          "【知识库】" in system_prompt(20, "", "【知识库】\n- a.md"))
    check("记忆和知识库能同时注入",
          "记忆" in system_prompt(20, "【跨会话记忆】x", "【知识库】y")
          and "知识库" in system_prompt(20, "【跨会话记忆】x", "【知识库】y"))

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
