r"""自定义工具（二次开发）的回归测试。

用户的诉求是「通过对话让小爪给自己造工具」。这条链路上最容易出事的
不是「造不出来」，是**造出来的东西越权**：用户只想要「按固定格式整理
工单」，结果拿到一个能删文件的工具。

所以这个脚本的重点是**越权能不能被挡住**，而不是功能好不好用。

用法：
    .venv\\Scripts\\python.exe tools\\exttest.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "exttest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "exttest"

PASSED = 0
FAILED: list[str] = []
_keep_alive: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


# ==========================================================================
def test_validation() -> None:
    print("\n=== 一、定义校验 ===")
    from pawpet.ai.extensions import LEVEL_CODE, LEVEL_NOTE, LEVEL_RECIPE, Extension

    good = Extension(
        name="tidy_ticket", title="整理工单",
        description="用户贴工单内容时用",
        level=LEVEL_RECIPE,
        steps=[{"op": "list_dir", "path": "{folder}"},
               {"op": "pick_lines", "keyword": "报错"}],
        parameters={"type": "object", "properties": {"folder": {"type": "string"}},
                    "required": ["folder"]},
    )
    check("合法定义没有意见", good.problems() == [], str(good.problems()))

    # 名字
    for bad_name in ("BadName", "1abc", "ab", "有中文", "has space", ""):
        ext = Extension(name=bad_name, title="t", description="d",
                        level=LEVEL_NOTE, prompt="p")
        check(f"名字 {bad_name!r} 被拒",
              any("名字" in p for p in ext.problems()), str(ext.problems()))
    check("合法的名字通过",
          Extension(name="a_b1", title="t", description="d",
                    level=LEVEL_NOTE, prompt="p").problems() == [])

    # 必填的人话字段
    ext = Extension(name="okname", title="", description="")
    issues = ext.problems()
    check("缺中文名被拒", any("中文名" in p for p in issues), str(issues))
    check("缺说明被拒", any("说明" in p for p in issues), str(issues))
    check("说明的提示说了「用户说什么的时候用」",
          any("用户说什么" in p for p in issues), str(issues))

    # 各档的必填
    ext = Extension(name="okname", title="t", description="d", level=LEVEL_NOTE)
    check("note 档缺 prompt 被拒",
          any("prompt" in p for p in ext.problems()), str(ext.problems()))
    ext = Extension(name="okname", title="t", description="d", level=LEVEL_RECIPE)
    check("recipe 档缺 steps 被拒",
          any("steps" in p for p in ext.problems()), str(ext.problems()))
    ext = Extension(name="okname", title="t", description="d", level=LEVEL_CODE)
    check("code 档缺 code 被拒",
          any("code" in p for p in ext.problems()), str(ext.problems()))
    ext = Extension(name="okname", title="t", description="d", level="magic")
    check("未知级别被拒",
          any("不认识" in p for p in ext.problems()), str(ext.problems()))

    # 参数 schema
    ext = Extension(name="okname", title="t", description="d", level=LEVEL_NOTE,
                    prompt="p",
                    parameters={"type": "object", "properties": {"a": {}},
                                "required": ["b"]})
    check("required 里有没声明的字段 → 被拒",
          any("required" in p for p in ext.problems()), str(ext.problems()))


def test_privilege_escalation() -> None:
    print("\n=== 二、越权拦截（这是最关键的一组）===")
    from pawpet.ai.extensions import (
        LEVEL_CODE,
        LEVEL_RECIPE,
        READ_OPS,
        Extension,
        run_recipe,
    )

    # recipe 档不允许写操作
    dangerous = [
        "write_file", "delete_file", "move", "copy", "run_command",
        "click", "type_text", "open_app", "press_keys", "screenshot",
    ]
    for op in dangerous:
        ext = Extension(name="evil", title="坏东西", description="d",
                        level=LEVEL_RECIPE, steps=[{"op": op, "path": "x"}])
        issues = ext.problems()
        check(f"recipe 档拒绝 {op}",
              any(op in p and "不允许" in p or op in p and "不在允许清单" in p
                  for p in issues),
              str(issues))

    check("允许清单里确实没有写操作",
          not any(op in READ_OPS for op in dangerous), str(list(READ_OPS)))

    # 即使绕过 install 校验，执行时也要拦
    evil = Extension(name="evil", title="坏东西", description="d",
                     level=LEVEL_RECIPE,
                     steps=[{"op": "write_file", "path": "x", "content": "y"}])
    ok, text = run_recipe(evil, {})
    check("执行时再拦一次（手改配置绕过校验的兜底）",
          not ok and "不允许" in text, text)

    # code 档的默认开关。
    #
    # **用户明确要求把默认改成「开」**（他要用 code 档做自己的工具，
    # 每次都要先去设置里打开太麻烦）。所以这条断言改成记录这个决定，
    # 而不是假设它必须是关的 —— 免得以后有人看到「默认开着」以为是 bug
    # 又给改回去。
    #
    # 真正要守住的是**别的**东西（见 test_critical_risk），那些没变：
    #   * code 档执行必须走确认 —— 而且是**任何权限档位都不能免**的
    #     CRITICAL，不是「完全自动下自动放行」的 CONFIRM；
    #   * 越权仍然硬拦。
    from pawpet.ai.extensions import MAX_LEVEL_DEFAULT

    check("code 档默认是开的（用户明确要求的决定，别再改回去）",
          MAX_LEVEL_DEFAULT == LEVEL_CODE, MAX_LEVEL_DEFAULT)

    ext = Extension(name="coder", title="代码工具", description="d",
                    level=LEVEL_CODE, code="print(1)")
    check("默认档位下 code 定义能过校验", ext.problems() == [],
          str(ext.problems()))

    # 把上限卡回 recipe 时，code 档仍然要能被拒 —— 说明这套闸还在，
    # 只是默认值放开了
    issues = ext.problems("recipe")
    check("把上限调回 recipe 时 code 档仍被拒（闸还在）",
          any("自定义代码" in p and "关着" in p for p in issues), str(issues))
    check("明确打开后 code 档可以过",
          Extension(name="coder", title="t", description="d",
                    level=LEVEL_CODE, code="print(1)").problems("code") == [])


def test_critical_risk() -> None:
    print("\n=== 二之二、CRITICAL：『完全自动』也免不了的那一档 ===")
    from pawpet.ai.actions import (
        LEVEL_AUTO,
        LEVEL_CONFIRM,
        LEVEL_FULL,
        LEVEL_ORDER,
        LEVEL_READ_ONLY,
        AuditLog,
        DesktopActions,
        Risk,
    )
    from pawpet.ai.tools import TOOL_INDEX

    labels = {
        LEVEL_READ_ONLY: "只读",
        LEVEL_CONFIRM: "逐步确认",
        LEVEL_AUTO: "自动执行",
        LEVEL_FULL: "完全自动",
    }

    def probe(level: str) -> DesktopActions:
        item = DesktopActions(AuditLog())
        item.level = level
        return item

    # ---- 1. 风险 × 档位全矩阵。
    #
    # 这一组**把原有语义钉死**，免得修 P0 的时候顺手把别的档位改松了。
    # 注意 CONFIRM 在 auto / full 下**确实不问** —— 这不是 bug，
    # 是这两档存在的意义（日常动作别烦我）。真正有问题的是「跑一段
    # 新造出来的代码」也被算进了 CONFIRM。
    matrix = [
        (LEVEL_READ_ONLY, Risk.READ, False),
        (LEVEL_CONFIRM, Risk.READ, False),
        (LEVEL_AUTO, Risk.READ, False),
        (LEVEL_FULL, Risk.READ, False),
        (LEVEL_READ_ONLY, Risk.CONFIRM, True),
        (LEVEL_CONFIRM, Risk.CONFIRM, True),
        (LEVEL_AUTO, Risk.CONFIRM, False),
        (LEVEL_FULL, Risk.CONFIRM, False),
        (LEVEL_READ_ONLY, Risk.DANGER, True),
        (LEVEL_CONFIRM, Risk.DANGER, True),
        (LEVEL_AUTO, Risk.DANGER, True),
        (LEVEL_FULL, Risk.DANGER, False),
    ]
    for level, risk, want in matrix:
        got = probe(level).needs_approval(risk)
        check(f"{labels[level]} × {risk} → {'要问' if want else '不问'}",
              got is want, f"实际 {got}")

    # ---- 2. critical 在任何档位下都要问。
    #
    # 这是 P0 修复的核心。原来那条链是：
    #   造草稿（READ，不问）→ 安装（CONFIRM，full 下不问）
    #   → 调用时合成 CONFIRM（full 下不问）→ 子进程跑任意 Python。
    # 全程一张卡片都没有，而 install_extension 的说明里明明写着
    # 「会弹一张卡片让用户确认」。承诺和实现对不上，就是缺陷。
    for level in LEVEL_ORDER:
        got = probe(level).needs_approval(Risk.CRITICAL)
        check(f"{labels[level]} 下 critical 仍然要问", got is True, f"实际 {got}")

    check("LEVEL_ORDER 是从紧到松的（导入备份比松紧靠它）",
          LEVEL_ORDER == (LEVEL_READ_ONLY, LEVEL_CONFIRM, LEVEL_AUTO, LEVEL_FULL),
          str(LEVEL_ORDER))
    check("critical 不在 LEVEL_ORDER 里（它说的是风险，不是权限档位）",
          Risk.CRITICAL not in LEVEL_ORDER, str(LEVEL_ORDER))
    check("critical 是个独立的值，没和 confirm 撞上",
          Risk.CRITICAL != Risk.CONFIRM, Risk.CRITICAL)

    # ---- 3. 只读模式下 critical 直接拦下（连问都不问）
    for risk in (Risk.CONFIRM, Risk.DANGER, Risk.CRITICAL):
        check(f"只读模式下 {risk} 被直接拦下",
              probe(LEVEL_READ_ONLY).blocked(risk) is True, risk)
    check("只读模式下读操作不拦",
          probe(LEVEL_READ_ONLY).blocked(Risk.READ) is False)

    # ---- 4. 装工具这一档必须挂 CRITICAL。
    #
    # 原来它是 CONFIRM，而 CONFIRM 在 auto / full 下不问 —— 于是
    # 「安装」这一步在用户以为「完全自动只是少点几下」的时候静默通过了。
    check("install_extension 是 CRITICAL（不是 CONFIRM）",
          TOOL_INDEX["install_extension"].risk == Risk.CRITICAL,
          str(TOOL_INDEX["install_extension"].risk))

    # 卸掉用户攒下来的工具仍然是 CONFIRM（不进 critical）——
    # 别因为修 P0 把普通的确认动作也升级成「每次单独弹卡」，
    # 那会把用户烦到直接开 full。
    check("remove_extension 保持 CONFIRM（没被顺手升级）",
          TOOL_INDEX["remove_extension"].risk == Risk.CONFIRM,
          str(TOOL_INDEX["remove_extension"].risk))

    # ---- 5. 装/卸/恢复之外的只读扩展工具不受影响
    for name in ("list_extensions", "propose_extension"):
        check(f"{name} 仍然是 READ（不打扰用户）",
              TOOL_INDEX[name].risk == Risk.READ,
              str(TOOL_INDEX[name].risk))


def test_step_params() -> None:
    print("\n=== 三之二、步骤参数的别名与校验 ===")
    from pawpet.ai.extensions import LEVEL_RECIPE, Extension, run_recipe

    work = SCRATCH / "params"
    work.mkdir(parents=True, exist_ok=True)
    src = work / "many.txt"
    src.write_text("\n".join(f"行{i}" for i in range(1, 201)), encoding="utf-8")

    def run_one(step: dict) -> tuple[bool, str]:
        probe = Extension(
            name="probe", title="探针", description="d", level=LEVEL_RECIPE,
            steps=[{"op": "read_file", "path": "{path}"}, step],
        )
        return run_recipe(probe, {"path": str(src)})

    # **参数名要认全。** 用户的 17 个自定义工具里有两个写了
    # max_chars / max_lines，而实现原来只读 limit —— 参数被静默忽略，
    # 永远按默认的 2000 跑。步骤「成功」了但效果不是他要的，且没有提示。
    print("  truncate 的三种写法：")
    cases = [
        ({"op": "truncate", "max_lines": 10}, 10, None),
        ({"op": "truncate", "max_chars": 30}, None, 30),
        ({"op": "truncate", "limit": 5}, None, 5),
    ]
    for step, want_lines, want_chars in cases:
        ok, text = run_one(step)
        label = [k for k in step if k != "op"][0]
        if want_lines is not None:
            check(f"truncate 认 {label}（按行截）",
                  ok and len(text.splitlines()) == want_lines,
                  f"{len(text.splitlines())} 行")
        else:
            check(f"truncate 认 {label}（按字符截）",
                  ok and len(text) == want_chars, f"{len(text)} 字符")

    # count 认 unit 和 mode 两个名字
    for key in ("unit", "mode"):
        ok, text = run_one({"op": "count", key: "lines"})
        check(f"count 认 {key}", ok and text.strip() == "200", text[:20])

    # whole_line：返回整行而不是匹配片段
    log = work / "err.log"
    log.write_text("普通行\nERROR 磁盘满了\n普通行\nERROR 超时\n",
                   encoding="utf-8")

    def run_re(step: dict) -> str:
        probe = Extension(
            name="re", title="正则", description="d", level=LEVEL_RECIPE,
            steps=[{"op": "read_file", "path": "{path}"}, step],
        )
        ok, text = run_recipe(probe, {"path": str(log)})
        return text if ok else f"（失败：{text}）"

    plain = run_re({"op": "regex_find", "pattern": "ERROR"})
    check("不加 whole_line 时返回匹配片段（原行为不变）",
          plain.strip() == "ERROR\nERROR", repr(plain))

    whole = run_re({"op": "regex_find", "pattern": "ERROR", "whole_line": True})
    check("whole_line 返回整行",
          "磁盘满了" in whole and "超时" in whole, repr(whole))
    check("whole_line 不带出无关行", "普通行" not in whole, repr(whole))

    # 写错参数名要**在校验阶段就被抓住**，不能静默忽略
    bad = Extension(
        name="bad_param", title="t", description="d", level=LEVEL_RECIPE,
        steps=[{"op": "truncate", "max_lenth": 50}],
    )
    issues = bad.problems()
    check("写错参数名会被校验抓出来",
          any("不认识的参数" in p for p in issues), str(issues))
    check("报错里列出了这一步认哪些参数",
          any("max_chars" in p for p in issues), str(issues))

    good = Extension(
        name="ok_param", title="t", description="d", level=LEVEL_RECIPE,
        steps=[{"op": "truncate", "max_lines": 5}],
    )
    check("对的参数名能过校验", good.problems() == [], str(good.problems()))


def test_remove_restore() -> None:
    print("\n=== 三之四、卸载要确认，而且要能捞回来 ===")
    from pawpet.ai.actions import AuditLog, DesktopActions, Risk
    from pawpet.ai.extensions import (
        LEVEL_RECIPE,
        Extension,
        load_all,
        load_trash,
        save_all,
    )
    from pawpet.ai.tools import TOOL_INDEX, ToolContext

    # **风险级别**：删掉的是用户手工攒下来的东西，必须确认。
    #
    # 原来 remove_extension 是 Risk.READ —— 不确认、不提示、没有后悔药。
    # 这台机器上有 17 个用户自己打磨的工具，模型听错一句话就能永久毁掉
    # 一个。目标里的「审批」就落在这里。
    check("remove_extension 需要确认（不是 READ）",
          TOOL_INDEX["remove_extension"].risk is Risk.CONFIRM,
          str(TOOL_INDEX["remove_extension"].risk))
    check("restore_extension 需要确认",
          TOOL_INDEX["restore_extension"].risk is Risk.CONFIRM,
          str(TOOL_INDEX["restore_extension"].risk))

    work = SCRATCH / "trash"
    work.mkdir(parents=True, exist_ok=True)
    book = work / "extensions.json"

    keep = Extension(name="keep_me", title="留着", description="d",
                     level=LEVEL_RECIPE,
                     steps=[{"op": "truncate", "max_lines": 5}])
    victim = Extension(name="victim", title="要删的", description="d",
                       level=LEVEL_RECIPE, run_count=7,
                       steps=[{"op": "truncate", "max_lines": 9}])
    save_all(book, [keep, victim])

    # _ext_book() 是从 store.path 推路径的，给一个最小的替身
    class _Store:
        path = work / "pet_data.json"

    actions = DesktopActions(AuditLog())
    actions.level = "confirm"
    context = ToolContext.__new__(ToolContext)
    context.actions = actions
    context.store = _Store()

    ok, text, _ = context._do_remove_extension({"name": "victim"})
    check("能卸掉", ok and "victim" not in [e.name for e in load_all(book)],
          text[:70])
    check("留着的不受影响", [e.name for e in load_all(book)] == ["keep_me"],
          str([e.name for e in load_all(book)]))

    # **回滚**：删掉的定义要进回收站
    trashed = load_trash(book)
    check("卸掉的定义进了回收站", len(trashed) == 1, str(len(trashed)))
    check("回收站里是那一个", bool(trashed) and trashed[0].get("name") == "victim",
          str(trashed[:1])[:80])
    check("回收站记了删除时间",
          bool(trashed and trashed[0].get("removedAt")), "没时间戳")
    check("卸掉时说了还能加回来", "加回来" in text, text[:80])

    # 捞回来
    ok2, text2, _ = context._do_restore_extension({"name": "victim"})
    names = sorted(e.name for e in load_all(book))
    check("能从回收站捞回来", ok2 and names == ["keep_me", "victim"],
          f"{text2[:60]} {names}")
    check("捞回来后回收站空了", load_trash(book) == [],
          str(load_trash(book))[:60])

    restored = next(e for e in load_all(book) if e.name == "victim")
    check("捞回来的定义内容完整（用过几次也还在）",
          restored.run_count == 7 and restored.title == "要删的",
          f"run_count={restored.run_count} title={restored.title!r}")

    # 边界
    ok3, text3, _ = context._do_restore_extension({"name": "victim"})
    check("已经在列表里时不给重复恢复", not ok3 and "已经在列表里" in text3,
          text3[:60])
    ok4, text4, _ = context._do_restore_extension({"name": "从没存在过"})
    check("回收站里没有时给可读提示", not ok4 and "回收站里没有" in text4,
          text4[:60])
    ok5, text5, _ = context._do_remove_extension({"name": "从没存在过"})
    check("删不存在的给可读提示（并列出有哪些）",
          not ok5 and "没有叫" in text5 and "keep_me" in text5, text5[:70])
    ok6, text6, _ = context._do_remove_extension({"name": ""})
    check("不给名字时提示要名字", not ok6 and "哪一个" in text6, text6[:60])

    # 同名反复装卸不该把回收站刷满
    for _ in range(3):
        context._do_remove_extension({"name": "victim"})
        context._do_restore_extension({"name": "victim"})
    context._do_remove_extension({"name": "victim"})
    check("同名反复装卸只留一条（不刷满回收站）",
          len(load_trash(book)) == 1, str(len(load_trash(book))))


def test_user_extensions() -> None:
    print("\n=== 三之三、体检真实装好的自定义工具 ===")
    from pawpet.ai.extensions import LEVEL_LABELS, load_all

    path = ROOT / "extensions.json"
    if not path.exists():
        print("  （这台机器上没有 extensions.json，跳过）")
        return

    items = load_all(path)
    check("扩展文件读得出来", bool(items), f"{len(items)} 个")
    print(f"  装了 {len(items)} 个：")
    broken = []
    for ext in items:
        issues = ext.problems()
        label = LEVEL_LABELS.get(ext.level, ext.level)
        if issues:
            broken.append(ext.name)
            print(f"    [XX] {ext.name:32s} {label}")
            for issue in issues:
                print(f"         {issue[:100]}")
        else:
            used = f" 用过 {ext.run_count} 次" if ext.run_count else ""
            print(f"    [ok] {ext.name:32s} {label}{used}")
    check("已装的工具全部通过校验", not broken, str(broken))


def test_recipe_execution() -> None:
    print("\n=== 三、固定流程真的能跑 ===")
    from pawpet.ai.extensions import LEVEL_RECIPE, Extension, run_recipe

    work = SCRATCH / "work"
    work.mkdir(parents=True, exist_ok=True)
    sample = work / "ticket.txt"
    sample.write_text(textwrap.dedent("""\
        工单标题：附件无法上传
        用户描述：上传附件时一直转圈，最后提示失败
        排查过程：检查了网络，正常
        报错信息：SFTP connection refused
        期望：能正常上传附件
        """), encoding="utf-8")

    # 读文件 + 挑行
    ext = Extension(name="pick_err", title="挑出报错行", description="d",
                    level=LEVEL_RECIPE,
                    steps=[{"op": "read_file", "path": "{path}"},
                           {"op": "pick_lines", "keyword": "报错"}])
    ok, text = run_recipe(ext, {"path": str(sample)})
    check("读文件 + 挑行能跑", ok and "SFTP" in text, text)
    check("没挑到不该出现的行", "网络" not in text, text)

    # 列目录
    ext = Extension(name="ls", title="列目录", description="d",
                    level=LEVEL_RECIPE, steps=[{"op": "list_dir", "path": "{path}"}])
    ok, text = run_recipe(ext, {"path": str(work)})
    check("列目录能跑", ok and "ticket.txt" in text, text)

    # 正则提取
    ext = Extension(name="re", title="提取", description="d", level=LEVEL_RECIPE,
                    steps=[{"op": "read_file", "path": "{path}"},
                           {"op": "regex_find", "pattern": "^报错信息：(.+)$"}])
    ok, text = run_recipe(ext, {"path": str(sample)})
    check("正则提取能跑", ok and "SFTP connection refused" in text, text)

    # 模板生成回复
    ext = Extension(
        name="reply", title="生成回复", description="d", level=LEVEL_RECIPE,
        steps=[{"op": "read_file", "path": "{path}"},
               {"op": "pick_lines", "keyword": "报错"},
               {"op": "template",
                "text": "您好，问题定位在 {text}。我们会尽快处理。"}])
    ok, text = run_recipe(ext, {"path": str(sample)})
    check("模板替换能跑", ok and "您好" in text and "SFTP" in text, text)

    # count / truncate
    ext = Extension(name="cnt", title="数行", description="d", level=LEVEL_RECIPE,
                    steps=[{"op": "read_file", "path": "{path}"},
                           {"op": "count", "unit": "lines"}])
    ok, text = run_recipe(ext, {"path": str(sample)})
    check("count 能跑", ok and text.strip().isdigit(), text)

    # 安全：路径被 files 层的安全策略管着
    ext = Extension(name="peek", title="偷看", description="d", level=LEVEL_RECIPE,
                    steps=[{"op": "read_file", "path": "{path}"}])
    ok, text = run_recipe(ext, {"path": str(Path.home() / ".ssh" / "id_rsa")})
    check("读敏感目录被安全策略拦下", not ok, text)

    # 读不存在的文件要给可读错误
    ok, text = run_recipe(ext, {"path": str(work / "没有这个.txt")})
    check("读不存在的文件给可读错误", not ok and "读不了" in text, text)

    # 空产出要报错而不是返回空
    ext = Extension(name="empty", title="空", description="d", level=LEVEL_RECIPE,
                    steps=[{"op": "pick_lines", "keyword": "不存在的关键词"}])
    ok, text = run_recipe(ext, {})
    check("空产出时报错而不是假装成功",
          not ok and "没有产出" in text, text)

    # 坏正则不能把整个流程搞崩
    ext = Extension(name="badre", title="坏正则", description="d", level=LEVEL_RECIPE,
                    steps=[{"op": "read_file", "path": "{path}"},
                           {"op": "regex_find", "pattern": "([未闭合"}])
    ok, text = run_recipe(ext, {"path": str(sample)})
    check("坏正则给可读错误", not ok and "出错" in text, text)


def test_registry_and_tools() -> None:
    print("\n=== 四、注册表与工具导出 ===")
    from pawpet.ai.extensions import (
        LEVEL_RECIPE,
        Extension,
        as_openai_tools,
        find_by_tool_name,
        load_all,
        save_all,
    )

    path = SCRATCH / "extensions.json"
    items = [
        Extension(name="tidy", title="整理", description="d1", level=LEVEL_RECIPE,
                  steps=[{"op": "list_dir", "path": "{p}"}], approved=True),
        Extension(name="draft", title="草稿", description="d2", level=LEVEL_RECIPE,
                  steps=[{"op": "list_dir", "path": "{p}"}], approved=False),
    ]
    ok, _ = save_all(path, items)
    check("能保存", ok)
    back = load_all(path)
    check("能读回", len(back) == 2, str(len(back)))
    check("字段没丢", back[0].title == "整理" and back[0].steps, str(back[0].as_dict())[:80])

    exported = as_openai_tools(back)
    names = [item["function"]["name"] for item in exported]
    check("只导出 approved 的", names == ["ext_tidy"], str(names))
    check("名字带 ext_ 前缀", names[0].startswith("ext_"), names[0])

    check("能按工具名找回定义",
          find_by_tool_name(back, "ext_tidy") is not None)
    check("没有的返回 None", find_by_tool_name(back, "ext_nope") is None)
    check("非 ext_ 前缀返回 None", find_by_tool_name(back, "tidy") is None)

    # 坏文件不能崩
    path.write_text("{ 不是 json", encoding="utf-8")
    check("文件坏了返回空", load_all(path) == [])
    path.write_text('{"extensions": "不是数组"}', encoding="utf-8")
    check("结构不对也返回空", load_all(path) == [])


def test_end_to_end() -> None:
    print("\n=== 五、端到端：造 → 确认 → 装上 → 能调 → 能卸 ===")
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.actions import AuditLog, DesktopActions
    from pawpet.ai.tools import TOOL_INDEX, ToolContext, openai_tools
    from pawpet.ai.vision import ScreenCapture
    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QApplication.instance() or QApplication(sys.argv[:1])
    scratch = SCRATCH / "e2e"
    scratch.mkdir(parents=True, exist_ok=True)
    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    backend = Backend(store)

    actions = DesktopActions(AuditLog())
    actions.level = "confirm"
    context = ToolContext(ScreenCapture(), actions, store, backend)

    for name in ("propose_extension", "install_extension",
                 "list_extensions", "remove_extension"):
        check(f"工具 {name} 注册了", name in TOOL_INDEX)

    # --- 1. 造草稿
    ok, text, _ = context.execute("propose_extension", {
        "name": "tidy_ticket",
        "title": "整理工单",
        "description": "用户贴工单内容、需要提取报错和生成回复时用",
        "level": "recipe",
        "steps": [{"op": "read_file", "path": "{path}"},
                  {"op": "pick_lines", "keyword": "报错"}],
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string",
                                               "description": "工单文件路径"}}},
    })
    check("草稿能生成", ok, text[:120])
    check("草稿里带了「还没有生效」的提醒",
          "还没有生效" in text, text[:120])
    check("草稿给了人话说明", "整理工单" in text and "只读" in text, text[:200])

    # 从返回里抠出 draft
    marker = "draft = "
    draft = json.loads(text[text.index(marker) + len(marker):].strip())
    check("draft 是合法 JSON 且名字对",
          draft.get("name") == "tidy_ticket", str(draft.get("name")))

    # 还没装，不该出现在工具清单里
    check("草稿阶段不出现在给模型的清单里",
          not any(t["function"]["name"] == "ext_tidy_ticket"
                  for t in openai_tools(context.extension_tools())))

    # --- 2. 装上
    ok, text, _ = context.execute("install_extension", {"draft": draft})
    check("能装上", ok, text[:120])
    check("装好之后提示了数量", "1 个自定义工具" in text, text[:160])

    exported = openai_tools(context.extension_tools())
    check("装好后出现在给模型的清单里",
          any(t["function"]["name"] == "ext_tidy_ticket" for t in exported),
          str([t["function"]["name"] for t in exported])[-3:])

    # --- 3. 调用它
    work = SCRATCH / "e2e_work"
    work.mkdir(parents=True, exist_ok=True)
    ticket = work / "t.txt"
    ticket.write_text("工单：附件传不上去\n报错：SFTP 满了\n建议：扩容",
                      encoding="utf-8")

    ok, text, _ = context.execute("ext_tidy_ticket", {"path": str(ticket)})
    check("装好的工具能调", ok and "SFTP" in text, text[:120])
    check("只挑出了报错那一行", "扩容" not in text, text[:120])

    # --- 4. 越权定义装不上
    ok, text, _ = context.execute("install_extension", {"draft": {
        "name": "evil_tool", "title": "坏工具", "description": "d",
        "level": "recipe",
        "steps": [{"op": "write_file", "path": "x", "content": "y"}],
    }})
    check("越权的定义装不上",
          not ok and "允许清单" in text, text[:160])
    check("拒绝时说清了该走哪一档", "自定义代码" in text, text[:200])
    # 确认它真的没被写进注册表
    from pawpet.ai.extensions import load_all as _load

    names = [e.name for e in _load(scratch / "extensions.json")]
    check("被拒的定义没有落盘", "evil_tool" not in names, str(names))

    # --- 5. 列出来
    ok, text, _ = context.execute("list_extensions", {})
    check("列表里有它", ok and "tidy_ticket" in text, text[:160])

    # --- 6. 卸掉
    ok, text, _ = context.execute("remove_extension", {"name": "tidy_ticket"})
    check("能卸掉", ok and "已卸掉" in text, text[:120])
    ok, text, _ = context.execute("ext_tidy_ticket", {"path": str(ticket)})
    check("卸掉之后调不动了", not ok and "没有名为" in text, text[:120])
    ok, text, _ = context.execute("remove_extension", {"name": "不存在的"})
    check("卸不存在的给可读错误", not ok and "没有叫" in text, text[:120])

    # --- 7. 没确认过的定义不该能执行
    from pawpet.ai.extensions import Extension, load_all, save_all

    path = scratch / "extensions.json"
    items = load_all(path)
    items.append(Extension(name="sneaky", title="偷偷装", description="d",
                           level="recipe",
                           steps=[{"op": "list_dir", "path": "{p}"}],
                           approved=False))
    save_all(path, items)
    ok, text, _ = context.execute("ext_sneaky", {"p": str(work)})
    check("手改配置塞进来的未确认定义不执行",
          not ok and "确认" in text, text[:140])

    backend.shutdown()


def test_note_level() -> None:
    print("\n=== 六、固定说法那一档 ===")
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.actions import AuditLog, DesktopActions
    from pawpet.ai.tools import ToolContext
    from pawpet.ai.vision import ScreenCapture
    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QApplication.instance() or QApplication(sys.argv[:1])
    scratch = SCRATCH / "note"
    scratch.mkdir(parents=True, exist_ok=True)
    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    backend = Backend(store)
    actions = DesktopActions(AuditLog())
    actions.level = "confirm"
    context = ToolContext(ScreenCapture(), actions, store, backend)

    ok, text, _ = context.execute("propose_extension", {
        "name": "four_part_reply",
        "title": "四段式回复",
        "description": "用户要起草工单回复时用",
        "level": "note",
        "prompt": "按四段写：①结论先行 ②工作量 ③风险 ④建议。用户的问题：{question}",
        "parameters": {"type": "object",
                       "properties": {"question": {"type": "string"}}},
    })
    check("note 档草稿能生成", ok, text[:100])
    marker = "draft = "
    draft = json.loads(text[text.index(marker) + len(marker):].strip())

    context.execute("install_extension", {"draft": draft})
    ok, text, _ = context.execute("ext_four_part_reply",
                                  {"question": "能不能支持达梦数据库"})
    check("note 档能调", ok, text[:100])
    check("参数被填进了指令", "能不能支持达梦数据库" in text, text[:160])
    check("返回的是「用户的固定要求」而不是普通文本",
          "自定义要求" in text and "照它办" in text, text[:160])

    backend.shutdown()


def main() -> int:
    print("小爪自定义工具（二次开发）回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    test_validation()
    test_privilege_escalation()
    test_critical_risk()
    test_step_params()
    test_remove_restore()
    test_user_extensions()
    test_recipe_execution()
    test_registry_and_tools()
    test_end_to_end()
    test_note_level()

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
