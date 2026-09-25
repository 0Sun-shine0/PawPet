r"""专注与待办番茄的端到端回归。

覆盖用户真正会走的链路：专注页选择待办、自然完成后累计番茄、休息
不累计、待办被删后不崩溃，以及程序重启恢复时 pending 只消费一次。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PAWPET_HOME"] = str(ROOT / ".cache" / "focustasktest")
os.environ["PAWPET_INSTANCE_SUFFIX"] = "focustasktest"

SCRATCH = ROOT / ".cache" / "focustasktest"
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


def main() -> int:
    print("小爪专注关联待办回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QApplication.instance() or QApplication(sys.argv[:1])
    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    store.settings["auto_start_next"] = False
    store.settings["notify_enabled"] = False
    store.settings["sound_enabled"] = False
    backend = Backend(store)
    notifications: list[tuple[str, str, str]] = []
    backend.bubbleRequested.connect(
        lambda kind, title, body: notifications.append((kind, title, body))
    )

    try:
        print("=== 一、专注页待办选项 ===")
        backend.tasks.add("写项目报告", 1)
        task = store.tasks[-1]
        task_id = task["id"]
        options = backend.focusTaskOptions
        option_ids = [item.get("taskId") for item in options]
        check("始终提供不关联选项", options and options[0]["taskId"] == "")
        check("未完成待办出现在选项里", task_id in option_ids, str(options))

        backend.tasks.add("已完成任务", 0)
        done_id = store.tasks[-1]["id"]
        backend.tasks.toggle(done_id)
        check("已完成待办不会出现在专注选项里",
              done_id not in [item.get("taskId") for item in backend.focusTaskOptions])

        print("\n=== 二、完成专注自动记账 ===")
        backend.focus.taskId = task_id
        store.save()
        disk_store = Store(store.path, store.backup_path)
        disk_store.load()
        check("关联任务 ID 会持久化", disk_store.focus.get("task_id") == task_id)

        backend.focus._settle("focus", 25)
        check("完成专注后番茄数加一", task.get("pomodoros") == 1,
              f"实际 {task.get('pomodoros')}")
        check("完成后 pending 番茄已消费",
              store.focus.get("pending_task_pomodoro", "") == "")
        check("完成通知包含关联任务",
              any("写项目报告" in body and "+1 个番茄" in body
                  for _, _, body in notifications),
              repr(notifications[-1] if notifications else None))

        backend.focus._settle("short_break", 5)
        check("休息结束不会增加番茄", task.get("pomodoros") == 1,
              f"实际 {task.get('pomodoros')}")
        backend._consume_pending_task_pomodoro()
        check("重复消费不会重复记账", task.get("pomodoros") == 1,
              f"实际 {task.get('pomodoros')}")

        print("\n=== 三、待办删除与离线恢复 ===")
        backend.tasks.add("完成前被删除", 0)
        deleted_id = store.tasks[-1]["id"]
        backend.focus.taskId = deleted_id
        backend.tasks.remove(deleted_id)
        backend.focus._settle("focus", 25)
        check("关联待办删除后完成专注不崩溃",
              store.focus.get("pending_task_pomodoro", "") == "")

        offline_store = Store(
            SCRATCH / "offline.json", SCRATCH / "offline.backup.json"
        )
        offline_store.load()
        offline_store.settings["auto_start_next"] = False
        offline_store.settings["notify_enabled"] = False
        offline_store.settings["sound_enabled"] = False
        offline_store.tasks.append({
            "id": "offline-task",
            "text": "离线恢复任务",
            "done": False,
            "created": time.time(),
            "done_at": None,
            "priority": 0,
            "due": None,
            "pomodoros": 0,
        })
        offline_store.focus.update({
            "mode": "focus",
            "running": True,
            "end_epoch": time.time() - 5,
            "remaining": 0,
            "total_minutes": 25,
            "task_id": "offline-task",
            "pending_task_pomodoro": "",
        })
        offline_store.save()
        recovered = Backend(offline_store)
        recovered_task = offline_store.tasks[0]
        check("重启恢复的已完成专注会记番茄",
              recovered_task.get("pomodoros") == 1,
              f"实际 {recovered_task.get('pomodoros')}")
        check("重启恢复 pending 已清空",
              offline_store.focus.get("pending_task_pomodoro", "") == "")
        recovered._consume_pending_task_pomodoro()
        check("重启后再次消费不会重复记账",
              recovered_task.get("pomodoros") == 1,
              f"实际 {recovered_task.get('pomodoros')}")
        recovered.shutdown()
    finally:
        backend.shutdown()
        app.quit()

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
