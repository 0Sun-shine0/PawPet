r"""待办批量编辑：验证一次落盘、按 ID 选择和整批撤销。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "taskbulktest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "taskbulktest"

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


def task(task_id: str, text: str, done: bool = False) -> dict:
    return {
        "id": task_id,
        "text": text,
        "done": done,
        "created": time.time(),
        "done_at": time.time() if done else None,
        "priority": 0,
        "due": None,
        "pomodoros": 0,
    }


def main() -> int:
    print("待办批量编辑回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from pawpet.models import TaskModel
    from pawpet.store import Store, today_key

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    store.tasks[:] = [
        task("a", "批量一"),
        task("b", "批量二"),
        task("c", "已经完成", done=True),
        task("d", "批量三"),
    ]
    store.stats[today_key()] = {"tasks_done": 4}
    model = TaskModel(store)

    save_calls = 0
    original_save = store.save

    def counted_save() -> bool:
        nonlocal save_calls
        save_calls += 1
        return original_save()

    store.save = counted_save

    print("=== 一、批量完成 ===")
    model.completeMany(["a", "b", "c", "a", "不存在"])
    check("批量完成只保存一次", save_calls == 1, str(save_calls))
    check("选中的未完成任务全部完成",
          all(next(item for item in store.tasks if item["id"] == key)["done"]
              for key in ("a", "b")))
    check("已经完成的任务不重复计数",
          store.stats[today_key()]["tasks_done"] == 6,
          str(store.stats[today_key()]))
    check("完成后模型只重建一次可见结果", model.count == 1, str(model.count))

    print("\n=== 二、按 ID 选中后批量删除 ===")
    save_calls = 0
    model.removeMany(["a", "d", "a", "不存在"])
    check("批量删除只保存一次", save_calls == 1, str(save_calls))
    check("批量删除移除目标 ID",
          [item["id"] for item in store.tasks] == ["b", "c"],
          str([item["id"] for item in store.tasks]))
    check("撤销状态记录整批数量", model.lastRemovedCount == 2,
          str(model.lastRemovedCount))
    check("撤销提示显示数量", model.lastRemovedText == "2 条待办",
          model.lastRemovedText)

    save_calls = 0
    model.undoRemove()
    check("整批撤销只保存一次", save_calls == 1, str(save_calls))
    check("整批撤销恢复全部任务",
          [item["id"] for item in store.tasks] == ["a", "b", "c", "d"],
          str([item["id"] for item in store.tasks]))
    check("整批撤销保留原始相对顺序",
          [item["text"] for item in store.tasks] == ["批量一", "批量二",
                                                       "已经完成", "批量三"])
    check("撤销后入口关闭", not model.canUndo)

    print("\n=== 三、筛选结果和持久化 ===")
    model.showDone = True
    model.searchText = "批量"
    check("visibleIds 来自真实筛选结果",
          set(model.visibleIds) == {"a", "b", "d"}
          and len(model.visibleIds) == 3, str(model.visibleIds))
    payload = json.loads((SCRATCH / "pet_data.json").read_text(encoding="utf-8"))
    check("批量操作后的文件可重新读取",
          [item["id"] for item in payload["tasks"]] == ["a", "b", "c", "d"])

    qml_text = (ROOT / "pawpet" / "qml" / "PawPet" / "page" / "TasksPage.qml").read_text(
        encoding="utf-8")
    check("页面有选择模式", "selectionMode" in qml_text)
    check("页面按 visibleIds 全选当前结果", "visibleIds" in qml_text)
    check("页面提供批量完成", "completeMany" in qml_text)
    check("页面删除前打开确认框", "deleteSelectedDialog.open()" in qml_text)
    check("页面提供 Esc 退出", 'sequence: "Escape"' in qml_text)
    check("页面提供 Ctrl+A 全选", 'sequence: "Ctrl+A"' in qml_text)

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
