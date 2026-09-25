r"""数据仓库并发保存回归。

主线程、AI 后台线程和退出收尾都有机会同时触发 Store.save()。这个测试
专门验证保存不会因为共用一个 .tmp 而互相覆盖或返回失败。

用法：
    .venv\Scripts\python.exe tools\storeconcurrencytest.py
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
from pathlib import Path

from console import configure_utf8

configure_utf8()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "storeconcurrencytest"
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
    print("小爪数据并发保存回归\n")
    if not SCRATCH.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"拒绝清理工作区外目录：{SCRATCH}")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from pawpet.store import Store

    data = SCRATCH / "pet_data.json"
    backup = SCRATCH / "pet_data.backup.json"
    store = Store(data, backup)
    store.load()

    errors: list[str] = []
    errors_lock = threading.Lock()

    def worker(worker_id: int) -> None:
        for round_number in range(15):
            with store._lock:
                store.state["counters"]["completed_total"] = (
                    worker_id * 1000 + round_number
                )
            try:
                if not store.save():
                    with errors_lock:
                        errors.append(f"worker={worker_id} round={round_number}")
            except Exception as exc:  # noqa: BLE001 - 回归要记录所有竞态异常
                with errors_lock:
                    errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(index,))
               for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check("并发保存全部成功", not errors, "; ".join(errors[:4]))

    payload = None
    try:
        payload = json.loads(data.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        check("并发保存后的主文件仍是合法 JSON", False, str(exc))
    else:
        check("并发保存后的主文件仍是合法 JSON",
              isinstance(payload, dict) and payload.get("schema") == 2)

    check("没有残留临时文件", not data.with_suffix(data.suffix + ".tmp").exists())
    try:
        backup_ok = isinstance(json.loads(backup.read_text(encoding="utf-8")), dict)
    except (OSError, json.JSONDecodeError):
        backup_ok = False
    check("备份文件可解析", backup_ok)

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print("  - " + item)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
