r"""损坏数据容错回归。

用户可能会用记事本手改数据文件，也可能在升级、断电或第三方同步时留下
类型不对的字段。单个字段异常不应该让整个工作台无法启动。

用法：
    .venv\Scripts\python.exe tools\corruptdatatest.py
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
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
os.environ["PAWPET_INSTANCE_SUFFIX"] = "corruptdatatest"

from console import configure_utf8

configure_utf8()

SCRATCH = ROOT / ".cache" / "corruptdatatest"
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


def _run_case(name: str, payload: dict, checks) -> None:
    from pawpet.store import Store

    folder = SCRATCH / name
    folder.mkdir(parents=True, exist_ok=True)
    data = folder / "pet.json"
    backup = folder / "pet.backup.json"
    data.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    store = Store(data, backup)
    backend = None
    try:
        store.load()
        checks(store)
        from pawpet.backend import Backend

        backend = Backend(store)
        check(f"{name}：Backend 可以启动", True)
        check(f"{name}：修复提示可读",
              not store.repaired or "自动修复" in backend.migrationNote,
              repr(backend.migrationNote))
    except Exception as exc:  # noqa: BLE001
        check(f"{name}：损坏数据不阻塞启动", False,
              f"{type(exc).__name__}: {exc}")
    finally:
        if backend is not None:
            backend.shutdown()


def main() -> int:
    print("小爪损坏数据容错回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    now = time.time()
    broken = {
        "schema": "oops",
        "settings": {
            "focus_minutes": "oops",
            "pet_opacity": "oops",
            "ai_timeout": "oops",
        },
        "tasks": [{
            "id": "task-1",
            "text": "保留这条待办",
            "done": "false",
            "created": "oops",
            "done_at": "oops",
            "priority": "oops",
            "pomodoros": "oops",
            "due": "not-a-date",
        }],
        "reminders": [{
            "id": "reminder-1",
            "title": "间隔提醒",
            "time": "not-a-clock",
            "repeat": "interval",
            "every": "oops",
            "enabled": "true",
            "last_fired": "oops",
            "created": "oops",
        }],
        "notes": [{
            "id": "note-1",
            "title": "一条便签",
            "text": "内容还在",
            "created": now,
            "updated": "oops",
        }],
        "sessions": [{
            "id": "session-1",
            "start": "oops",
            "end": "oops",
            "minutes": "oops",
            "label": "专注",
            "offline": "false",
        }],
        "stats": {"2026-09-24": {
            "focus_minutes": "oops",
            "focus_rounds": "oops",
            "tasks_done": "oops",
        }},
        "focus": {
            "mode": "unknown",
            "running": "oops",
            "end_epoch": "oops",
            "remaining": "oops",
            "round": "oops",
            "total_minutes": "oops",
            "task_id": 123,
            "pending_task_pomodoro": [],
        },
        "counters": {
            "focus_rounds": "oops",
            "completed_total": "oops",
            "sit_since_epoch": "oops",
        },
    }

    def check_broken(store) -> None:
        check("字段损坏：标记为已修复", store.repaired)
        check("字段损坏：schema 回到当前版本", store.state["schema"] == 2)
        check("字段损坏：专注分钟安全", store.settings["focus_minutes"] == 25)
        check("字段损坏：AI 超时安全", store.settings["ai_timeout"] == 90)
        check("字段损坏：任务优先级安全", store.tasks[0]["priority"] == 0)
        check("字段损坏：番茄数安全", store.tasks[0]["pomodoros"] == 0)
        check("字段损坏：任务日期安全", store.tasks[0]["due"] is None)
        check("字段损坏：提醒时间安全", store.reminders[0]["time"] == "09:00")
        check("字段损坏：间隔分钟安全", store.reminders[0]["every"] == 30)
        check("字段损坏：便签时间戳安全",
              isinstance(store.notes[0]["updated"], float))
        check("字段损坏：专注记录安全",
              store.state["sessions"][0]["start"] == 0.0 and
              store.state["sessions"][0]["minutes"] == 0)
        check("字段损坏：统计数字安全",
              store.stats["2026-09-24"]["focus_minutes"] == 0)
        check("字段损坏：计时器不会误结算",
              store.focus["running"] is False and store.focus["end_epoch"] == 0.0)

    _run_case("mixed", broken, check_broken)

    def check_wrong_containers(store) -> None:
        check("容器损坏：列表回退为空", not store.tasks and not store.reminders)
        check("容器损坏：便签保留可用种子", len(store.notes) == 0)
        check("容器损坏：统计回退为空", store.stats == {})
        check("容器损坏：settings 回退默认",
              store.settings["focus_minutes"] == 25)

    _run_case("containers", {
        "schema": 2,
        "settings": [],
        "tasks": "oops",
        "reminders": {},
        "notes": [None, 3],
        "sessions": [None],
        "stats": "oops",
        "focus": [],
        "counters": [],
    }, check_wrong_containers)

    def check_valid(store) -> None:
        check("正常数据：不误报修复", not store.repaired)
        check("正常数据：任务保持原值", store.tasks[0]["priority"] == 2)
        check("正常数据：间隔保持原值", store.reminders[0]["every"] == 5)

    _run_case("valid", {
        "schema": 2,
        "tasks": [{
            "id": "t1", "text": "正常任务", "done": False,
            "created": now, "done_at": None, "priority": 2,
            "due": None, "pomodoros": 1,
        }],
        "reminders": [{
            "id": "r1", "title": "正常提醒", "time": "09:30",
            "date": None, "repeat": "interval", "enabled": True,
            "last_fired": None, "created": now, "every": 5,
        }],
        "notes": [{
            "id": "n1", "title": "正常便签", "text": "文本",
            "created": now, "updated": now,
        }],
    }, check_valid)

    from pawpet.store import Store

    recovery = SCRATCH / "recovery"
    recovery.mkdir(parents=True, exist_ok=True)
    main_file = recovery / "pet.json"
    backup_file = recovery / "pet.backup.json"
    backup_payload = {
        "schema": 2,
        "tasks": [{
            "id": "backup-task", "text": "备份里的任务", "done": False,
            "created": now, "priority": 0,
        }],
    }
    main_file.write_text("{ broken", encoding="utf-8")
    backup_file.write_text(json.dumps(backup_payload, ensure_ascii=False),
                           encoding="utf-8")
    recovered = Store(main_file, backup_file)
    recovered.load()
    recovered.save()
    main_after = json.loads(main_file.read_text(encoding="utf-8"))
    backup_after = json.loads(backup_file.read_text(encoding="utf-8"))
    check("恢复后保存：主文件被修复", main_after["tasks"][0]["text"] == "备份里的任务")
    check("恢复后保存：不覆盖好备份",
          backup_after["tasks"][0]["text"] == "备份里的任务")
    recovered.tasks.append({
        "id": "new-task", "text": "第二次保存", "done": False,
        "created": now, "priority": 0,
    })
    recovered.save()
    second_backup = json.loads(backup_file.read_text(encoding="utf-8"))
    check("恢复后保存：第二次保存仍可生成备份",
          len(second_backup["tasks"]) == 1 and
          second_backup["tasks"][0]["text"] == "备份里的任务")

    no_backup = SCRATCH / "no-backup"
    no_backup.mkdir(parents=True, exist_ok=True)
    no_backup_main = no_backup / "pet_data.json"
    no_backup_file = no_backup / "pet_data.backup.json"
    corrupt_text = "{ 这份原文不能被覆盖"
    no_backup_main.write_text(corrupt_text, encoding="utf-8")
    no_backup_store = Store(no_backup_main, no_backup_file)
    no_backup_store.load()
    no_backup_backend = None
    try:
        from pawpet.backend import Backend

        no_backup_backend = Backend(no_backup_store)
        preserved_path = no_backup_store.corrupt_path
        check("无备份损坏：不会阻塞启动", no_backup_store.state["schema"] == 2)
        check("无备份损坏：原文已保留",
              preserved_path is not None and preserved_path.exists())
        if preserved_path is not None and preserved_path.exists():
            check("无备份损坏：保留内容完整",
                  preserved_path.read_text(encoding="utf-8") == corrupt_text)
        check("无备份损坏：提示创建安全空数据",
              "安全空数据" in no_backup_backend.migrationNote,
              repr(no_backup_backend.migrationNote))
        no_backup_store.save()
        check("无备份损坏：保存后原文副本仍在",
              preserved_path is not None and preserved_path.exists())
        if preserved_path is not None and preserved_path.exists():
            check("无备份损坏：保存不覆盖原文副本",
                  preserved_path.read_text(encoding="utf-8") == corrupt_text)
    finally:
        if no_backup_backend is not None:
            no_backup_backend.shutdown()

    app.quit()
    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
