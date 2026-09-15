"""跨会话记忆自测。

重点验证三件事：
  1. **不会长歪** —— 去重、上限淘汰、脏数据容错
  2. **不会白烧 token** —— 提示词预算真的生效，没记忆时一个字都不加
  3. **真的跨会话** —— 写盘、重开、内容还在（含 BOM 情况）

用法：
    .venv\\Scripts\\python.exe tools\\memtest.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "memtest"
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


def main() -> int:
    print("小爪跨会话记忆自测\n")

    from pawpet.ai.memory import (
        CONFIDENCE_HIGH,
        CONFIDENCE_LOW,
        MAX_FACTS,
        MAX_TASKS,
        PROMPT_BUDGET,
        Memory,
        MemoryBook,
        format_for_prompt,
        normalize,
    )
    from pawpet.ai.agent import SYSTEM_PROMPT, system_prompt
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # ================================================== 一、规范化与去重
    print("=== 一、去重：同一件事不会记两遍 ===")

    check("空白被折叠", normalize("  a   b  ") == "a b", normalize("  a   b  "))
    check("首尾标点被去掉", normalize("他喜欢简洁。") == "他喜欢简洁",
          normalize("他喜欢简洁。"))
    check("中文逗号也算标点", normalize("，你好，") == "你好", normalize("，你好，"))
    check("大小写归一", normalize("Use WPS") == "use wps", normalize("Use WPS"))
    check("空串是空串", normalize("") == "" and normalize(None) == "")

    # 关键：不能把语义相反的两句判成同一条
    a = normalize("喜欢 A 不喜欢 B")
    b = normalize("不喜欢 A 喜欢 B")
    check("语义不同的两句不会被误判成重复", a != b, f"{a!r} vs {b!r}")

    m = Memory()
    _, created1 = m.add_fact("他习惯用 WPS 而不是 Office", "workflow", 2)
    _, created2 = m.add_fact("他习惯用 WPS 而不是 Office。", "workflow", 3)
    check("第一次是新建", created1 is True)
    check("换个标点算同一条，不新增", created2 is False and len(m.facts) == 1,
          f"created={created2} facts={len(m.facts)}")
    check("重复时置信度取高的", m.facts[0].confidence == 3,
          str(m.facts[0].confidence))

    m.add_fact("他习惯用 WPS 而不是 Office 这个办公软件", "workflow", 2)
    check("重复时保留信息更多的那句", "办公软件" in m.facts[0].text, m.facts[0].text)

    # 更啰嗦但信息更少的那句不该覆盖掉信息更多的那句
    m.add_fact("他习惯用 WPS", "workflow", 2)
    check("啰嗦的短句不会覆盖信息更多的长句",
          "办公软件" in m.facts[0].text, m.facts[0].text)

    # ================================================== 二、上限与淘汰
    print("\n=== 二、上限：不会无限长大 ===")

    big = Memory()
    for index in range(MAX_FACTS + 30):
        # 置信度递增，这样被淘汰的必然是最早的那些
        big.add_fact(f"第 {index} 条测试记忆", "other", 1 + (index % 3))
    check(f"条目数被压到上限 {MAX_FACTS}", len(big.facts) == MAX_FACTS,
          f"实际 {len(big.facts)}")

    low = Memory()
    low.add_fact("这条不确定", "other", CONFIDENCE_LOW)
    for index in range(MAX_FACTS):
        low.add_fact(f"很确定的第 {index} 条", "other", CONFIDENCE_HIGH)
    check("淘汰时先扔低置信度的",
          all("不确定" not in f.text for f in low.facts),
          str([f.text for f in low.facts if "不确定" in f.text]))

    maxed = Memory()
    open_count = 0
    for index in range(MAX_TASKS + 6):
        status = "open" if index < 6 else "done"
        open_count += 1 if status == "open" else 0
        maxed.note_task(f"任务 {index}", status)
    check(f"任务数被压到上限 {MAX_TASKS}", len(maxed.tasks) == MAX_TASKS,
          f"实际 {len(maxed.tasks)}")
    # 关键：没做完的绝不能因为超额被丢掉，那是「下次接着做」的唯一线索
    kept_open = sum(1 for t in maxed.tasks if t.status != "done")
    check("超额时优先保住没做完的任务", kept_open == open_count,
          f"保住 {kept_open} 条未完成，原本有 {open_count} 条")

    # ================================================== 三、忘记
    print("\n=== 三、忘记：改得掉 ===")

    forget_me = Memory()
    forget_me.add_fact("他用双屏", "environment", 2)
    removed = forget_me.forget("他用双屏")
    check("精确匹配能删掉", removed is not None and len(forget_me.facts) == 0,
          f"removed={removed}")

    forget_me.add_fact("他的显示器是 27 寸 4K", "environment", 2)
    removed2 = forget_me.forget("27 寸")
    check("只给片段也能删掉", removed2 is not None, f"removed={removed2}")

    check("删不存在的不报错", forget_me.forget("根本没记过") is None)
    check("删空串不报错", forget_me.forget("") is None)

    # ================================================== 四、叫法映射
    print("\n=== 四、叫法映射 ===")

    alias_mem = Memory()
    alias_mem.add_alias("那个表格", "WPS 表格")
    alias_mem.add_alias("那个表格", "WPS 表格")
    check("同一个叫法只存一条", len(alias_mem.aliases) == 1,
          str(len(alias_mem.aliases)))
    check("重复时累计使用次数", alias_mem.aliases[0].used == 2,
          str(alias_mem.aliases[0].used))
    check("能换算成真实标题", alias_mem.resolve_alias("那个表格") == "WPS 表格",
          alias_mem.resolve_alias("那个表格"))
    check("子串也能命中", alias_mem.resolve_alias("帮我打开那个表格") == "WPS 表格",
          alias_mem.resolve_alias("帮我打开那个表格"))
    check("查不到就原样返回", alias_mem.resolve_alias("没听过的词") == "没听过的词",
          alias_mem.resolve_alias("没听过的词"))

    # ================================================== 五、任务进度
    print("\n=== 五、任务进度 ===")

    task_mem = Memory()
    task_mem.note_task("整理客户名单", "open", "把重复项去掉")
    task_mem.note_task("整理客户名单", "open", "按地区分组")
    check("同一件事只留一条", len(task_mem.tasks) == 1, str(len(task_mem.tasks)))
    check("下一步会被更新", task_mem.tasks[0].next_step == "按地区分组",
          task_mem.tasks[0].next_step)
    check("未完成的算待办", len(task_mem.open_tasks()) == 1)

    task_mem.note_task("整理客户名单", "done")
    check("标记完成后不再算待办", len(task_mem.open_tasks()) == 0,
          str([t.status for t in task_mem.tasks]))

    task_mem.note_task("另一个活儿", "乱七八糟的状态")
    check("非法状态退回 open", task_mem.tasks[-1].status == "open",
          task_mem.tasks[-1].status)

    # ================================================== 六、提示词注入
    print("\n=== 六、注入提示词：预算与边界 ===")

    empty_mem = Memory()
    check("没记忆时一个字都不加", format_for_prompt(empty_mem) == "",
          repr(format_for_prompt(empty_mem)))
    check("没记忆时 system_prompt 不变",
          system_prompt(20) == system_prompt(20, ""),
          "空记忆不该改变提示词")

    small = Memory()
    small.add_fact("他喜欢简洁的回答", "preference", 3)
    rendered = format_for_prompt(small)
    check("有记忆时会带出内容", "简洁" in rendered, rendered[:80])
    check("带上了使用规矩", "remember" in rendered, rendered[-120:])
    check("确定的事会被标出来", "确定" in rendered, rendered[:200])

    combined = system_prompt(20, rendered)
    check("记忆拼进了系统提示词", "简洁" in combined and "步数预算" in combined)
    check("基础提示词没被改坏", combined.startswith(SYSTEM_PROMPT[:40]),
          combined[:40])

    # 预算：塞一大堆，看总长度有没有被按住
    flood = Memory()
    for index in range(200):
        flood.add_fact(f"这是第 {index} 条很长的记忆内容，用来测试预算控制是否生效",
                       "other", 2)
    flood_rendered = format_for_prompt(flood)
    # 预算只管正文，使用规矩那段是固定开销
    body = flood_rendered.split("使用记忆的规矩")[0]
    check("正文被预算按住", len(body) < PROMPT_BUDGET + 400,
          f"正文 {len(body)} 字，预算 {PROMPT_BUDGET}")
    check("被截断时会说明还有多少条",
          "还有" in flood_rendered and "recall_memory" in flood_rendered,
          flood_rendered[-260:])
    check("渲染是确定的（同样输入同样输出）",
          format_for_prompt(flood) == flood_rendered)

    # ================================================== 七、脏数据容错
    print("\n=== 七、脏数据容错 ===")

    check("None 能解析", Memory.from_dict(None).is_empty())
    check("字符串能解析", Memory.from_dict("不是字典").is_empty())
    check("空字典能解析", Memory.from_dict({}).is_empty())
    check("列表项不是字典时跳过",
          Memory.from_dict({"facts": [None, 42, "x"]}).is_empty())

    mixed = Memory.from_dict({
        "facts": [
            {"text": "正常的一条", "category": "preference", "confidence": 2},
            {"text": "", "category": "other"},                 # 空文本要跳过
            {"text": "分类非法", "category": "不存在的分类"},
            {"text": "置信度非法", "confidence": "很高"},
            {"text": "置信度越界", "confidence": 99},
            "这不是字典",
        ],
        "aliases": [{"spoken": "只有一边"}],                     # 缺一半要跳过
        "tasks": [{"text": "状态非法", "status": "???"}],
        "updated": "不是数字",
    })
    check("坏条目被跳过，好条目留下", len(mixed.facts) == 4,
          f"实际 {len(mixed.facts)}: {[f.text for f in mixed.facts]}")
    check("非法分类归到 other",
          next(f for f in mixed.facts if f.text == "分类非法").category == "other")
    check("非法置信度回到默认",
          next(f for f in mixed.facts if f.text == "置信度非法").confidence == 2)
    check("越界置信度被夹住",
          next(f for f in mixed.facts if f.text == "置信度越界").confidence == 3)
    check("缺一边的叫法被丢掉", len(mixed.aliases) == 0)
    check("非法任务状态回到 open", mixed.tasks[0].status == "open")
    check("坏掉的时间戳不炸", mixed.updated == 0.0, str(mixed.updated))

    # ================================================== 八、真的跨会话
    print("\n=== 八、跨会话：写盘、重开、还在 ===")

    data_path = SCRATCH / "pet_data.json"
    backup_path = SCRATCH / "pet_data.backup.json"

    store = Store(data_path, backup_path)
    store.load()
    book = MemoryBook(store)
    book.add_fact("他上班用公司电脑，家里用另一台", "environment", 3)
    book.add_alias("那台机器", "远程桌面")
    book.note_task("给客户做报价单", "open", "先确认税率")

    check("确实写进 state 了", bool(store.memory.get("facts")),
          str(store.memory)[:80])

    # 模拟重启：全新的 Store 对象读同一个文件
    reopened = Store(data_path, backup_path)
    reopened.load()
    reloaded = Memory.from_dict(reopened.memory)
    check("重开后事实还在", any("公司电脑" in f.text for f in reloaded.facts),
          str([f.text for f in reloaded.facts]))
    check("重开后叫法还在", reloaded.resolve_alias("那台机器") == "远程桌面",
          reloaded.resolve_alias("那台机器"))
    check("重开后任务进度还在",
          any("报价单" in t.text and t.next_step for t in reloaded.tasks),
          str([(t.text, t.next_step) for t in reloaded.tasks]))
    check("置信度也保住了",
          next(f for f in reloaded.facts if "公司电脑" in f.text).confidence == 3)

    # BOM：这条踩过坑，必须专门验一次
    with open(data_path, "w", encoding="utf-8-sig") as handle:
        json.dump(reopened.state, handle, ensure_ascii=False, indent=2)
    bom_store = Store(data_path, backup_path)
    bom_store.load()
    bom_memory = Memory.from_dict(bom_store.memory)
    check("带 BOM 的数据文件也能读出来",
          any("公司电脑" in f.text for f in bom_memory.facts),
          f"load_error={bom_store.load_error!r}")
    check("读 BOM 文件不会被误判成损坏", not bom_store.load_error,
          str(bom_store.load_error))

    # 老版本数据（没有 memory 键）也要能平滑升级
    legacy_path = SCRATCH / "legacy.json"
    with open(legacy_path, "w", encoding="utf-8") as handle:
        json.dump({"schema": 2, "tasks": [], "settings": {"pet_style": "mochi"}},
                  handle, ensure_ascii=False)
    legacy = Store(legacy_path, SCRATCH / "legacy.backup.json")
    legacy.load()
    check("老数据没有 memory 键时自动补上", isinstance(legacy.state.get("memory"), dict),
          str(type(legacy.state.get("memory"))))
    check("老数据升级后记忆是空的", Memory.from_dict(legacy.memory).is_empty())
    check("老数据里原有的设置没丢",
          legacy.settings.get("pet_style") == "mochi",
          str(legacy.settings.get("pet_style")))

    # ================================================== 九、清空
    print("\n=== 九、清空 ===")

    counts = book.clear()
    check("清空返回删掉的条数",
          counts["facts"] >= 1 and counts["tasks"] >= 1, str(counts))
    check("清空后记忆是空的", MemoryBook(store).load().is_empty())
    after_clear = Store(data_path, backup_path)
    after_clear.load()
    check("清空也落盘了", Memory.from_dict(after_clear.memory).is_empty())

    # ================================================== 十、工具接线
    print("\n=== 十、工具与控制器接线 ===")

    from pawpet.ai.tools import TOOL_INDEX, openai_tools
    for name in ("remember", "forget_memory", "recall_memory", "note_task_state"):
        check(f"工具 {name} 已注册", name in TOOL_INDEX)
    names = {t["function"]["name"] for t in openai_tools()}
    check("四个工具都暴露给模型了",
          {"remember", "forget_memory", "recall_memory",
           "note_task_state"} <= names,
          str(sorted(names)))

    check("默认开启记忆", store.settings.get("ai_memory_enabled") is True,
          str(store.settings.get("ai_memory_enabled")))

    from pawpet.ai.controller import AiController

    controller = AiController(store)
    try:
        check("控制器读得到记忆条数", controller.memoryCount == 0,
              str(controller.memoryCount))
        check("控制器给出的摘要是人话",
              "记忆" in controller.memorySummary, controller.memorySummary[:60])

        controller._push("user", "记住我用 WPS")
        MemoryBook(store).add_fact("他一直用 WPS", "workflow", 3)
        controller.refreshMemory()
        check("加了记忆后条数会变", controller.memoryCount == 1,
              str(controller.memoryCount))

        history = controller._build_history()
        check("历史里的系统提示词带上了记忆",
              "WPS" in history[0]["content"], history[0]["content"][-200:])

        controller.memoryEnabled = False
        check("关掉记忆后不再注入",
              "WPS" not in controller._build_history()[0]["content"],
              controller._build_history()[0]["content"][-160:])
        check("关掉记忆不会删数据", controller.memoryCount == 1,
              str(controller.memoryCount))

        controller.memoryEnabled = True
        controller.clearMemory()
        check("clearMemory 真的清空了", controller.memoryCount == 0,
              str(controller.memoryCount))
    finally:
        controller.shutdown()

    # ================================================== 收尾
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
