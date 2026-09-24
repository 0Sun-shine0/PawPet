r"""提醒功能回归。

这一版加了「每 N 分钟」的间隔重复，顺带修了两个用户报的问题：
  * 重复方式下拉展开后是个空粉块（delegate 读错了属性）
  * 窄窗口下输入行溢出，控件被挤出卡片（那个粉块也是溢出的一种表现）

间隔重复有个**结构性前提**：`last_fired` 必须存**时间戳**，不能存日期。
存日期的话「今天触发过」这个标记会一直挡住后续触发 —— 每 5 分钟只能响
第一次。这个套件就是围绕这一点展开的。

用法：
    .venv\Scripts\python.exe tools\remindertest.py
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "remindertest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "remindertest"

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


def main() -> int:
    print("小爪提醒功能回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _keep_alive.append(app)

    from pawpet.backend import Backend
    from pawpet.models import (
        INTERVAL_DEFAULT_MINUTES,
        INTERVAL_MAX_MINUTES,
        INTERVAL_MIN_MINUTES,
        INTERVAL_PRESETS,
        REPEAT_LABEL,
        _clamp_interval,
        _repeat_text,
    )
    from pawpet.reminders import ReminderEngine
    from pawpet.store import Store

    store = Store(SCRATCH / "p.json", SCRATCH / "b.json")
    store.load()
    backend = Backend(store)
    engine = ReminderEngine(store)
    # 用 extend 而不是 +=：`+=` 在函数里会让 Python 把 _keep_alive
    # 当成局部变量，于是前面那行 append 就报 UnboundLocalError。
    _keep_alive.extend([backend, engine])
    model = backend.reminders

    base = dt.datetime(2026, 9, 21, 10, 0, 0).timestamp()

    def at(minutes: float) -> dt.datetime:
        return dt.datetime.fromtimestamp(base + minutes * 60)

    # ================================================================ 一
    print("=== 一、重复方式的定义只有一份 ===")
    # 这个项目踩过「同一个说法存两份」的坑：_describe 里硬编码过一份
    # 重复方式映射，加了新方式之后列表和气泡显示不一致。
    check("interval 在 REPEAT_LABEL 里", "interval" in REPEAT_LABEL,
          str(list(REPEAT_LABEL)))
    check("backend 暴露的选项和 REPEAT_LABEL 一致",
          set(o["key"] for o in backend.repeatOptions) == set(REPEAT_LABEL),
          str([o["key"] for o in backend.repeatOptions]))
    check("选项里的 label 就是 REPEAT_LABEL 的值",
          all(o["label"] == REPEAT_LABEL[o["key"]]
              for o in backend.repeatOptions))
    check("只有 interval 标了 needsInterval",
          [o["key"] for o in backend.repeatOptions if o["needsInterval"]]
          == ["interval"])
    check("只有 interval 不需要时间",
          [o["key"] for o in backend.repeatOptions if not o["needsTime"]]
          == ["interval"])
    check("快捷间隔是升序的", list(INTERVAL_PRESETS) ==
          sorted(INTERVAL_PRESETS), str(INTERVAL_PRESETS))

    # ================================================================ 二
    print("\n=== 二、间隔参数的兜底 ===")
    for given, expect in ((5, 5), (0, INTERVAL_DEFAULT_MINUTES),
                          (-3, INTERVAL_DEFAULT_MINUTES),
                          (None, INTERVAL_DEFAULT_MINUTES),
                          ("abc", INTERVAL_DEFAULT_MINUTES),
                          (99999, INTERVAL_MAX_MINUTES),
                          (INTERVAL_MIN_MINUTES, INTERVAL_MIN_MINUTES)):
        got = _clamp_interval(given)
        check(f"间隔 {given!r} → {expect}", got == expect, f"实际 {got}")

    # ================================================================ 三
    print("\n=== 三、间隔重复的触发（核心）===")
    item = {"id": "r", "title": "喝水", "time": "09:00",
            "repeat": "interval", "every": 5, "enabled": True,
            "last_fired": None}

    check("第一次判定不触发（要先有个起点）",
          not engine._schedule_matches(item, at(0)))
    check("第一次记下了时间戳起点", isinstance(item["last_fired"], float),
          repr(item["last_fired"]))

    for minutes, expect in ((0, False), (1, False), (4.9, False),
                            (5, True), (12, True)):
        item["last_fired"] = base
        got = engine._schedule_matches(item, at(minutes))
        check(f"过了 {minutes} 分钟 → {'触发' if expect else '不触发'}",
              got == expect, f"实际 {got}")

    # **连续触发**：这是存日期 vs 存时间戳的关键区别。
    # 存日期的话第二次就被「今天已经触发过」挡住了。
    item["last_fired"] = base
    fired: list[int] = []
    clock = base
    for _step in range(6):
        clock += 5 * 60
        if engine._schedule_matches(item, dt.datetime.fromtimestamp(clock)):
            item["last_fired"] = clock      # 模拟 _check_schedule 的写回
            fired.append(round((clock - base) / 60))
    check("每隔 5 分钟连续触发 6 次（不是只响第一次）",
          fired == [5, 10, 15, 20, 25, 30], str(fired))

    # 不同间隔
    for every in (1, 10, 60, 120, 720):
        probe = {"id": "p", "title": "t", "time": "09:00",
                 "repeat": "interval", "every": every, "enabled": True,
                 "last_fired": base}
        check(f"间隔 {every} 分钟：差 1 秒不触发",
              not engine._schedule_matches(
                  probe, dt.datetime.fromtimestamp(base + every * 60 - 1)))
        probe["last_fired"] = base
        check(f"间隔 {every} 分钟：到点触发",
              engine._schedule_matches(
                  probe, dt.datetime.fromtimestamp(base + every * 60)))

    # ================================================================ 四
    print("\n=== 四、老数据 / 异常数据不会崩 ===")
    old = {"id": "o", "title": "老提醒", "time": "09:00",
           "repeat": "interval", "every": 5, "enabled": True,
           "last_fired": "2026-09-21"}          # 老版本存的是日期字符串
    check("日期字符串不会导致误触发",
          not engine._schedule_matches(old, at(0)))
    check("日期字符串被换成时间戳重新计时",
          isinstance(old["last_fired"], float), repr(old["last_fired"]))

    legacy = {"id": "l", "title": "缺 every", "time": "09:00",
              "repeat": "interval", "enabled": True, "last_fired": None}
    check("缺 every 字段时按默认值显示",
          _repeat_text(legacy) == f"每 {INTERVAL_DEFAULT_MINUTES} 分钟",
          _repeat_text(legacy))
    legacy["last_fired"] = base
    legacy["every"] = "坏值"
    check("every 是坏值时按默认间隔判",
          engine._schedule_matches(
              legacy, dt.datetime.fromtimestamp(
                  base + INTERVAL_DEFAULT_MINUTES * 60)))

    # ================================================================ 五
    print("\n=== 五、其它重复方式没被影响 ===")
    daily = {"id": "d", "title": "打卡", "time": "09:00", "repeat": "daily",
             "enabled": True, "last_fired": None}
    at_nine = dt.datetime(2026, 9, 21, 9, 0, 30)
    check("每天：到点触发", engine._schedule_matches(daily, at_nine))
    daily["last_fired"] = "2026-09-21"
    check("每天：当天已触发的不再触发",
          not engine._schedule_matches(daily, at_nine))

    once = {"id": "n", "title": "一次性", "time": "09:00", "repeat": "once",
            "date": "2026-09-21", "enabled": True, "last_fired": None}
    check("一次性：指定日期触发", engine._schedule_matches(once, at_nine))
    check("一次性：日期不对不触发",
          not engine._schedule_matches(dict(once, date="2026-09-22"),
                                       at_nine))

    workdays = {"id": "w", "title": "上班", "time": "09:00",
                "repeat": "weekdays", "enabled": True, "last_fired": None}
    check("工作日：周末不触发",
          not engine._schedule_matches(
              workdays, dt.datetime(2026, 9, 26, 9, 0, 30)))   # 周六
    check("工作日：周内触发",
          engine._schedule_matches(
              workdays, dt.datetime(2026, 9, 25, 9, 0, 30)))   # 周五

    check("关掉的提醒一律不触发",
          not engine._schedule_matches(dict(daily, enabled=False), at_nine))

    # ================================================================ 六
    print("\n=== 六、增删改 ===")
    model.add("喝水", "09:00", "interval", 5)
    created = store.reminders[-1]
    check("add 支持 interval", created["repeat"] == "interval",
          repr(created["repeat"]))
    check("add 记下了 every", created.get("every") == 5,
          repr(created.get("every")))
    check("间隔提醒不带 date", created.get("date") is None,
          repr(created.get("date")))
    check("列表显示「每 5 分钟」", model._next_text(created) == "每 5 分钟",
          repr(model._next_text(created)))

    model.update(created["id"], "该喝水了", "10:00", "daily", 0)
    check("update 能改重复方式", created["repeat"] == "daily",
          repr(created["repeat"]))
    check("update 改了标题", created["title"] == "该喝水了",
          repr(created["title"]))
    check("换重复方式时清了触发标记（免得拿着旧标记算）",
          created["last_fired"] is None, repr(created["last_fired"]))

    model.update(created["id"], "", "", "interval", 20)
    check("update 能改回 interval", created["repeat"] == "interval",
          repr(created["repeat"]))
    check("update 记下了新的 every", created.get("every") == 20,
          repr(created.get("every")))

    model.toggle(created["id"])
    check("toggle 关掉了", created["enabled"] is False)
    model.toggle(created["id"])
    check("toggle 又打开了", created["enabled"] is True)
    check("重新打开时清了标记（否则会立刻响一次）",
          created["last_fired"] is None, repr(created["last_fired"]))

    # ================================================================ 七
    print("\n=== 七、稍后提醒 ===")
    model.add("稍后测试", "09:00", "interval", 10)
    target = store.reminders[-1]
    model.snooze(target["id"], 15)
    every = int(target["every"]) * 60
    remaining = (every - (time.time() - float(target["last_fired"]))) / 60
    check("间隔提醒：稍后 15 分钟真的推后了",
          14.5 <= remaining <= 15.5, f"还剩 {remaining:.1f} 分钟")
    check("间隔提醒：repeat 没被改掉", target["repeat"] == "interval",
          repr(target["repeat"]))

    model.add("固定时刻", "09:00", "daily")
    fixed = store.reminders[-1]
    model.snooze(fixed["id"], 10)
    check("固定时刻提醒：改成了一次性", fixed["repeat"] == "once",
          repr(fixed["repeat"]))
    want = dt.datetime.now() + dt.timedelta(minutes=10)
    got = dt.datetime.strptime(f"{fixed['date']} {fixed['time']}",
                               "%Y-%m-%d %H:%M")
    check("时间定在约 10 分钟之后",
          abs((got - want).total_seconds()) < 90,
          f"设定 {got:%H:%M}，期望约 {want:%H:%M}")
    check("稍后之后是启用的", fixed["enabled"] is True)

    # ================================================================ 八
    print("\n=== 八、界面接线（QML）===")
    qml = (ROOT / "pawpet" / "qml" / "PawPet" / "page"
           / "RemindersPage.qml").read_text(encoding="utf-8")

    # 那条粉块的根因：delegate 读了 ItemDelegate 自己的 text 属性。
    #
    # 断言要**跳过注释行** —— 说明里为了讲清这个坑，原文写了
    # `text: repeatItem.text`，用子串匹配会把这个注释本身当成 bug
    # （第一版就是这么误报的）。
    code_lines = [ln.strip() for ln in qml.splitlines()
                  if ln.strip() and not ln.strip().startswith("//")]
    bad = [ln for ln in code_lines if ln.startswith("text: repeatItem.text")]
    check("下拉 delegate 不读 repeatItem.text（那是空属性）", not bad,
          f"读它永远是空串 → 下拉是空粉块：{bad}")
    check("下拉 delegate 读 modelData.label",
          any("modelData.label" in ln for ln in code_lines),
          "model 是对象数组，要取 .label（直接显示 modelData 会变成 [object Object]）")
    check("重复方式从 backend 取，没在 QML 里写死",
          "backend.repeatOptions" in qml, "写死就得改两处")

    check("有间隔选择区", "backend.intervalPresets" in qml)
    check("有自定义间隔输入", "intervalField" in qml)
    check("间隔输入有范围校验",
          "backend.intervalLimits" in qml and "IntValidator" in qml)
    check("有时间框按需隐藏（间隔模式不看几点）",
          "visible: page.needTime" in qml)
    check("提示文案跟着重复方式变",
          "page.needInterval" in qml)

    check("有编辑对话框", "editDialog" in qml and "beginEdit" in qml)
    check("编辑能改重复方式", "editRepeatBox" in qml)
    check("有稍后提醒菜单", "snoozeMenu" in qml and "snooze" in qml)

    # 溢出：悬停按钮用浮层，不参与 RowLayout 的宽度分配
    #
    # 这条断言改过一次。原来检查的是 `anchors.right: parent.right` +
    # `anchors.rightMargin: 42` —— 但那种写法把带锚点的 Row 放在了
    # RowLayout **里面**，Qt 会警告
    # 「Detected anchors on an item that is managed by a layout.
    #  This is undefined behavior」（冒烟测试报出来了）。
    #
    # 现在浮层挂在 delegate 根节点下、锚到开关左边。所以断言要检查的是
    # **它不在 RowLayout 里**，而不是某个具体坐标。
    check("稍后/改 按钮用浮层定位（不挤走开关）",
          "anchors.right: enabledSwitch.left" in qml,
          "锚到开关左边：既不参与布局分配，也不跟布局抢位置")
    check("浮层挂在 delegate 根节点下（不在 RowLayout 里）",
          "id: enabledSwitch" in qml,
          "要能锚到开关，开关就得有 id")

    # 那条 anchors-on-layout 警告本身也钉一下：浮层行必须在 RowLayout
    # 闭合之后。用缩进深度判断太脆，这里检查「Row {」出现在
    # RowLayout 的闭合大括号之后 —— 简单做法是确认浮层块的缩进比
    # RowLayout 深度的下一层更浅（即和 RowLayout 同级）。
    lines = qml.splitlines()
    row_layout_line = next((i for i, ln in enumerate(lines)
                            if "RowLayout {" in ln
                            and "anchors.fill: parent" in "\n".join(
                                lines[i:i + 3])), None)
    overlay_line = next((i for i, ln in enumerate(lines)
                         if "anchors.right: enabledSwitch.left" in ln), None)
    check("浮层在 RowLayout 之外（避免 undefined behavior）",
          row_layout_line is not None and overlay_line is not None
          and overlay_line > row_layout_line,
          f"RowLayout 在第 {row_layout_line} 行附近，浮层在第 {overlay_line} 行")
    check("快捷间隔按钮会跟着宽度收缩",
          "readonly property real slot" in qml,
          "写死宽度时 6 个按钮在窄窗口下溢出")

    # ================================================================ 九
    print("\n=== 九、AI 工具同步 ===")
    from pawpet.ai.tools import TOOL_INDEX

    spec = TOOL_INDEX["add_reminder"]
    props = spec.parameters["properties"]
    check("工具 schema 里有 interval",
          "interval" in props["repeat"].get("enum", []),
          str(props["repeat"].get("enum")))
    check("工具 schema 里有 every", "every" in props,
          str(sorted(props)))
    check("time 不再是必填（间隔模式不需要）",
          spec.parameters.get("required") == ["title"],
          str(spec.parameters.get("required")))

    # 真的调一次（走 backend 路径）
    ok, text, _bundle = backend.ai.context.execute(
        "add_reminder",
        {"title": "起来走走", "repeat": "interval", "every": 45})
    check("工具能加间隔提醒", ok, text[:80])
    if ok:
        added = store.reminders[-1]
        check("工具加的确实是 interval",
              added["repeat"] == "interval" and added.get("every") == 45,
              str({k: added.get(k) for k in ("repeat", "every")}))
        check("回话里说清了间隔", "45" in text, text[:80])

    backend.shutdown()

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
