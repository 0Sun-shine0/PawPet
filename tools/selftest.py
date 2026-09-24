"""端到端自测：用临时数据文件把核心逻辑跑一遍，验证行为和边界。

用法：
    .venv\\Scripts\\python.exe tools\\selftest.py

不碰你的真实 pet_data.json —— 所有测试都在临时目录里做。
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
os.environ["QT_QPA_PLATFORM"] = "offscreen"   # 只需要 QObject，不需要真的画

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_store(tmp: Path):
    from pawpet.store import Store

    return Store(tmp / "pet_data.json", tmp / "pet_data.backup.json")


# --------------------------------------------------------------------------
def test_migration(tmp: Path) -> None:
    section("旧版数据迁移（v1 -> v2）")
    legacy = {
        "tasks": [
            {"text": "旧待办一", "done": False},
            {"text": "旧待办二", "done": True},
        ],
        "note": "这是旧版便笺\n第二行",
        "focus_seconds": 900,
        "timer_mode": "专注",
        "break_minutes": 60,
        "last_break_notice": 123.0,
    }
    path = tmp / "pet_data.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    store = make_store(tmp)
    store.load()

    check("识别为 v1 迁移", store.migrated_from == "v1", f"实际 {store.migrated_from}")
    check("待办条数保留", len(store.tasks) == 2, f"实际 {len(store.tasks)}")
    check("完成状态保留", store.tasks[1]["done"] is True)
    check("便笺转成 notes", len(store.notes) == 1 and "第二行" in store.notes[0]["text"])
    check("休息间隔保留", store.settings["sit_reminder_minutes"] == 60,
          f"实际 {store.settings['sit_reminder_minutes']}")
    check("剩余时间保留", store.focus["remaining"] == 900, f"实际 {store.focus['remaining']}")
    check("旧计时器以暂停状态恢复", store.focus["running"] is False)


def test_atomic_save(tmp: Path) -> None:
    section("原子保存与备份")
    sub = tmp / "atomic"
    sub.mkdir(exist_ok=True)
    store = make_store(sub)
    store.load()
    store.tasks.append({"id": "t1", "text": "写入测试", "done": False,
                        "created": time.time(), "priority": 0})
    check("首次保存成功", store.save() is True)
    check("文件存在", store.path.exists())

    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    check("内容已落盘", on_disk["tasks"][0]["text"] == "写入测试")
    check("schema 已写入", on_disk["schema"] == 2)
    check("没有残留临时文件", not (sub / "pet_data.json.tmp").exists())

    store.tasks.append({"id": "t2", "text": "第二条", "done": False,
                        "created": time.time(), "priority": 0})
    store.save()
    check("第二次保存生成备份", store.backup_path.exists())
    backup = json.loads(store.backup_path.read_text(encoding="utf-8"))
    check("备份是上一版内容", len(backup["tasks"]) == 1, f"实际 {len(backup['tasks'])}")


def test_env_atomic_save(tmp: Path) -> None:
    section(".env 原子保存")
    from pawpet.config import save_env_value

    path = tmp / "env" / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 保留这行\nTEST_KEY=旧值\n", encoding="utf-8")
    previous = os.environ.get("TEST_KEY")
    try:
        check("首次写入 .env", save_env_value("TEST_KEY", "新值", path))
        text = path.read_text(encoding="utf-8")
        check("替换已有键且保留注释", "TEST_KEY=新值" in text and "# 保留这行" in text)
        check("进程环境同步更新", os.environ.get("TEST_KEY") == "新值")
        check("没有残留临时文件", not list(path.parent.glob(".env.*.tmp")))

        check("清空键值成功", save_env_value("TEST_KEY", "", path))
        check("清空键值不会继续泄露到环境变量", "TEST_KEY" not in os.environ)
        check("清空后的键仍是合法 .env 行",
              "TEST_KEY=\n" in path.read_text(encoding="utf-8"))
    finally:
        if previous is None:
            os.environ.pop("TEST_KEY", None)
        else:
            os.environ["TEST_KEY"] = previous


def test_recovery(tmp: Path) -> None:
    section("损坏文件回退到备份")
    sub = tmp / "recover"
    sub.mkdir(exist_ok=True)
    data = sub / "pet_data.json"
    backup = sub / "pet_data.backup.json"
    data.write_text("{ 这不是合法 JSON", encoding="utf-8")
    backup.write_text(json.dumps({"schema": 2, "tasks": [
        {"id": "b1", "text": "来自备份", "done": False, "created": 1, "priority": 0}
    ]}, ensure_ascii=False), encoding="utf-8")

    store = make_store(sub)
    store.load()
    check("从备份恢复", store.migrated_from == "backup", f"实际 {store.migrated_from}")
    check("备份内容读出来了", len(store.tasks) == 1 and store.tasks[0]["text"] == "来自备份")
    check("记录了失败原因", bool(store.load_error), store.load_error)


def test_bom_tolerance(tmp: Path) -> None:
    """带 UTF-8 BOM 的文件必须能正常读。

    这是个真实踩过的坑：记事本和很多编辑器的「另存为 UTF-8」会写 BOM，
    而 json.load 遇到 BOM 会抛 JSONDecodeError。如果这里不容错，程序会把
    主文件误判成损坏、静默回退到备份，用户最近改的东西就没了。
    """
    section("UTF-8 BOM 容错（记事本另存为 UTF-8）")
    sub = tmp / "bom"
    sub.mkdir(exist_ok=True)
    data = sub / "pet_data.json"
    backup = sub / "pet_data.backup.json"

    payload = {
        "schema": 2,
        "tasks": [{"id": "t1", "text": "带 BOM 的待办", "done": False,
                   "created": 1, "priority": 0}],
        "settings": {"ai_level": "read_only", "pet_style": "shiba"},
    }
    # 手写 BOM，模拟记事本的行为
    data.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    # 备份里放一份明显不同的内容，用来判断有没有错误地回退
    backup.write_text(json.dumps({"schema": 2, "tasks": [
        {"id": "old", "text": "备份里的旧内容", "done": False, "created": 1, "priority": 0}
    ]}, ensure_ascii=False), encoding="utf-8")

    store = make_store(sub)
    store.load()

    check("没有误判成损坏", store.migrated_from != "backup",
          f"实际来源 {store.migrated_from}")
    check("读到了主文件内容", len(store.tasks) == 1 and store.tasks[0]["text"] == "带 BOM 的待办",
          str([t.get("text") for t in store.tasks]))
    check("设置也读对了", store.settings.get("api_level", store.settings.get("ai_level")) == "read_only",
          str(store.settings.get("ai_level")))
    check("形象设置读对了", store.settings.get("pet_style") == "shiba")

    # 存回去之后不应该再带 BOM（避免累积）
    store.save()
    raw = data.read_bytes()
    check("保存后不带 BOM", not raw.startswith(b"\xef\xbb\xbf"), raw[:6].hex())
    reloaded = make_store(sub)
    reloaded.load()
    check("重新读取仍然正确", len(reloaded.tasks) == 1 and reloaded.tasks[0]["text"] == "带 BOM 的待办")


def test_task_model(tmp: Path) -> None:
    section("待办模型（旧版只显示 5 条的 bug）")
    from PySide6.QtCore import QCoreApplication, Qt

    from pawpet.backend import Backend

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    sub = tmp / "tasks"
    sub.mkdir(exist_ok=True)
    store = make_store(sub)
    store.load()
    backend = Backend(store)
    model = backend.tasks

    ROLE_TEXT = Qt.UserRole + 2
    ROLE_OVERDUE = Qt.UserRole + 7
    ROLE_PRIORITY = Qt.UserRole + 4

    for i in range(12):
        model.add(f"任务 {i}", 0)
    check("12 条待办全部在模型里", model.count == 12, f"实际 {model.count}")

    first_id = model.firstPendingId()
    check("能取到第一条待办的 id", bool(first_id))
    model.toggle(first_id)
    check("勾选后待办数减少", model.pendingCount == 11, f"实际 {model.pendingCount}")
    check("勾选后完成数增加", model.doneCount == 1, f"实际 {model.doneCount}")
    check("默认视图隐藏已完成", model.count == 11, f"实际 {model.count}")

    model.showDone = True
    check("切到全部后 12 条都在", model.count == 12, f"实际 {model.count}")
    model.showDone = False

    model.remove(model.firstPendingId())
    check("删除生效", model.pendingCount == 10, f"实际 {model.pendingCount}")

    model.add("重要的事", 2)
    model.add("次要的事", 0)
    model.sortMode = "priority"
    check("按优先级排序后紧急项在最前",
          model.data(model.index(0, 0), ROLE_TEXT) == "重要的事",
          f"实际 {model.data(model.index(0, 0), ROLE_TEXT)!r}")
    check("排序后优先级字段正确",
          model.data(model.index(0, 0), ROLE_PRIORITY) == 2,
          f"实际 {model.data(model.index(0, 0), ROLE_PRIORITY)}")
    model.sortMode = "smart"

    model.add("", 0)
    check("空文本不会创建待办", model.totalCount == 13, f"实际 {model.totalCount}")

    # 到期日：昨天 -> 逾期；明天 -> 不逾期
    target = model.firstPendingId()
    model.setDue(target, "2020-01-01")
    row = next(i for i in range(model.count)
               if model.data(model.index(i, 0), Qt.UserRole + 1) == target)
    check("过去的日期标记为逾期",
          model.data(model.index(row, 0), ROLE_OVERDUE) is True,
          f"实际 {model.data(model.index(row, 0), ROLE_OVERDUE)}")

    future = (__import__("datetime").date.today()
              + __import__("datetime").timedelta(days=1)).isoformat()
    model.setDue(target, future)
    row = next(i for i in range(model.count)
               if model.data(model.index(i, 0), Qt.UserRole + 1) == target)
    check("未来的日期不算逾期",
          model.data(model.index(row, 0), ROLE_OVERDUE) is False,
          f"实际 {model.data(model.index(row, 0), ROLE_OVERDUE)}")
    model.setDue(target, "")

    model.clearDone()
    check("清除已完成", model.doneCount == 0)

    check("count 属性存在（QML 要用）", isinstance(model.count, int))
    del app


def test_focus_engine(tmp: Path) -> None:
    section("番茄钟引擎（墙钟恢复 / 自动轮转）")
    from PySide6.QtCore import QCoreApplication

    from pawpet.focus import FocusEngine

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    sub = tmp / "focus"
    sub.mkdir(exist_ok=True)
    store = make_store(sub)
    store.load()
    store.settings["focus_minutes"] = 25
    store.settings["short_break_minutes"] = 5
    store.settings["auto_start_next"] = True
    engine = FocusEngine(store)

    check("初始为暂停", engine.running is False)
    check("初始剩余 25 分钟", engine.remaining == 1500, f"实际 {engine.remaining}")

    engine.startFocus()
    check("开始后运行中", engine.running is True)
    check("运行中剩余约 25 分钟", 1495 <= engine.remaining <= 1500, f"实际 {engine.remaining}")
    check("写入了墙钟截止时间", store.focus["end_epoch"] > time.time())

    engine.toggle()
    check("暂停后停止运行", engine.running is False)
    paused = engine.remaining
    check("暂停保留剩余时间", 1490 <= paused <= 1500, f"实际 {paused}")

    # 模拟关机期间跑完
    store.focus["running"] = True
    store.focus["mode"] = "focus"
    store.focus["end_epoch"] = time.time() - 10
    engine2 = FocusEngine(store)
    check("离线跑完会结算", store.stats != {})
    check("结算后进入休息", store.focus["mode"] in ("short_break", "long_break"),
          f"实际 {store.focus['mode']}")
    check("统计记录了专注分钟", any(d.get("focus_minutes", 0) > 0 for d in store.stats.values()))

    # 阶段切换：每 4 个专注轮之后来一次长休息。
    # 一轮「专注 + 休息」是 2 次 _settle，所以第 4 次专注结束在第 7 次调用。
    store.settings["rounds_before_long_break"] = 4
    store.focus["round"] = 0
    store.focus["mode"] = "focus"
    engine3 = FocusEngine(store)
    modes = []
    for _ in range(7):
        engine3._settle(store.focus["mode"], 25)
        modes.append(store.focus["mode"])
    check("前 3 轮专注后是短休息",
          modes[0] == "short_break" and modes[2] == "short_break",
          f"实际序列 {modes}")
    check("休息后回到专注", modes[1] == "focus" and modes[3] == "focus", f"实际序列 {modes}")
    check("第 4 轮专注后是长休息", modes[6] == "long_break", f"实际序列 {modes}")
    check("轮次计数到 4", store.focus["round"] == 4, f"实际 {store.focus['round']}")

    # 第 8 轮重新开始一个新周期
    engine3._settle(store.focus["mode"], 15)   # 长休息结束 -> 专注
    check("长休息后回到专注", store.focus["mode"] == "focus", f"实际 {store.focus['mode']}")
    engine3._settle("focus", 25)
    check("新一轮回到短休息", store.focus["mode"] == "short_break", f"实际 {store.focus['mode']}")
    del app


def test_reminder_schedule(tmp: Path) -> None:
    section("定时提醒的重复规则")
    from datetime import datetime

    from pawpet.reminders import ReminderEngine
    from pawpet.store import new_id

    sub = tmp / "rem"
    sub.mkdir(exist_ok=True)
    store = make_store(sub)
    store.load()
    engine = ReminderEngine(store)

    monday = datetime(2025, 9, 15, 10, 30)      # 周一
    saturday = datetime(2025, 9, 13, 10, 30)    # 周六

    daily = {"id": new_id("r"), "title": "每天", "time": "10:30",
             "repeat": "daily", "enabled": True, "last_fired": None}
    check("daily 周一命中", engine._schedule_matches(daily, monday) is True)
    check("daily 周六也命中", engine._schedule_matches(daily, saturday) is True)
    daily["last_fired"] = "2025-09-15"
    check("daily 同一天不重复触发", engine._schedule_matches(daily, monday) is False)

    weekdays = {"id": new_id("r"), "title": "工作日", "time": "10:30",
                "repeat": "weekdays", "enabled": True, "last_fired": None}
    check("weekdays 周一命中", engine._schedule_matches(weekdays, monday) is True)
    check("weekdays 周六不命中", engine._schedule_matches(weekdays, saturday) is False)

    once = {"id": new_id("r"), "title": "仅一次", "time": "10:30", "date": "2025-09-15",
            "repeat": "once", "enabled": True, "last_fired": None}
    check("once 指定日期命中", engine._schedule_matches(once, monday) is True)
    check("once 其他日期不命中", engine._schedule_matches(once, saturday) is False)

    # 补触发窗口：过点 20 分钟就不该再响
    late = {"id": new_id("r"), "title": "迟到", "time": "10:00",
            "repeat": "daily", "enabled": True, "last_fired": None}
    check("过点 30 分钟不补触发",
          engine._schedule_matches(late, datetime(2025, 9, 15, 10, 30)) is False)
    check("过点 5 分钟内会补触发",
          engine._schedule_matches(late, datetime(2025, 9, 15, 10, 5)) is True)

    disabled = {"id": new_id("r"), "title": "关掉", "time": "10:30",
                "repeat": "daily", "enabled": False, "last_fired": None}
    check("关闭的提醒不触发", engine._schedule_matches(disabled, monday) is False)

    upcoming = engine.next_upcoming(3)
    check("能算出接下来的提醒", isinstance(upcoming, list))


def test_note_model(tmp: Path) -> None:
    section("便签模型")
    from PySide6.QtCore import QCoreApplication

    from pawpet.models import NoteModel, ReminderModel

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    sub = tmp / "notes"
    sub.mkdir(exist_ok=True)
    store = make_store(sub)
    store.load()

    notes = NoteModel(store)
    check("首次自动建一条便签", notes.count == 1, f"实际 {notes.count}")
    first = notes.currentId
    notes.update(first, "标题改过", "正文内容")
    check("更新生效", notes.currentTitle == "标题改过", f"实际 {notes.currentTitle}")
    check("正文生效", notes.currentText == "正文内容")
    check("按 id 取文本", notes.textOf(first) == "正文内容")

    notes.add("第二条", "内容二")
    check("新增后共两条", notes.count == 2, f"实际 {notes.count}")
    notes.remove(first)
    check("删除后剩一条", notes.count == 1, f"实际 {notes.count}")
    notes.remove(notes.currentId)
    check("删光后会自动补一条，不会出现空编辑器", notes.count == 1, f"实际 {notes.count}")

    reminders = ReminderModel(store)
    reminders.add("喝水", "10:30", "daily")
    check("新增提醒", reminders.count == 1 and reminders.activeCount == 1)
    rid = store.reminders[0]["id"]
    reminders.toggle(rid)
    check("关闭提醒后 activeCount 归零", reminders.activeCount == 0)
    check("count 属性存在", isinstance(reminders.count, int))
    reminders.remove(rid)
    check("删除提醒", reminders.count == 0)
    del app


def test_settings_and_host(tmp: Path) -> None:
    section("设置项与 Windows 集成")
    from pawpet import win32
    from pawpet.store import default_settings

    settings = default_settings()
    for key in ("focus_minutes", "hotkeys" if False else "hotkey_dashboard",
                "sit_reminder_minutes", "pet_scale", "autostart"):
        check(f"默认设置含 {key}", key in settings)

    check("空闲时长能取到数值", isinstance(win32.idle_seconds(), float))
    check("通知状态能取到数值", isinstance(win32.notification_state(), int))

    parsed = win32.parse_hotkey("ctrl+alt+p")
    check("热键解析 ctrl+alt+p", parsed == (win32.MOD_CONTROL | win32.MOD_ALT, 0x50), f"实际 {parsed}")
    check("热键解析大写也认", win32.parse_hotkey("Ctrl+Shift+F") is not None)
    check("非法热键返回 None", win32.parse_hotkey("ctrl+") is None)
    check("未知键返回 None", win32.parse_hotkey("ctrl+alt+f13") is None)


def test_sound(tmp: Path) -> None:
    section("提示音合成")
    from pawpet.services import CHIMES, ensure_sounds

    files = ensure_sounds()
    check("合成出了提示音文件", len(files) > 0, f"实际 {list(files)}")
    for name in CHIMES:
        path = files.get(name)
        check(f"{name}.wav 有效", path is not None and path.exists() and path.stat().st_size > 1024,
              f"size={path.stat().st_size if path and path.exists() else 'N/A'}")


# --------------------------------------------------------------------------
def main() -> int:
    print("小爪助手 自测")
    print(f"项目目录：{ROOT}")

    # 临时目录放在工作区内：这台机器的系统 Temp 目录不在沙箱可写范围内。
    scratch = ROOT / ".cache" / "selftest"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        test_migration(scratch)
        test_atomic_save(scratch)
        test_env_atomic_save(scratch)
        test_recovery(scratch)
        test_bom_tolerance(scratch)
        test_task_model(scratch)
        test_focus_engine(scratch)
        test_reminder_schedule(scratch)
        test_note_model(scratch)
        test_settings_and_host(scratch)
        test_sound(scratch)
    finally:
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
