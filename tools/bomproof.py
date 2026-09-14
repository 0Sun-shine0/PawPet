"""验证 BOM 容错修复确实有效：对比修复前后的行为。

不修改源码，直接用 json.load 复现旧行为，证明这个 bug 是真的。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "bomproof"


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    print("BOM 容错对照验证\n")

    data = SCRATCH / "pet_data.json"
    backup = SCRATCH / "pet_data.backup.json"

    payload = {"schema": 2, "tasks": [
        {"id": "new", "text": "用户最近加的待办", "done": False, "created": 1, "priority": 0}
    ]}
    data.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    backup.write_text(json.dumps({"schema": 2, "tasks": [
        {"id": "old", "text": "备份里的旧待办", "done": False, "created": 1, "priority": 0}
    ]}, ensure_ascii=False), encoding="utf-8")

    print(f"主文件带 BOM：{data.read_bytes()[:3].hex()}")
    print(f"主文件内容：{payload['tasks'][0]['text']}")
    print(f"备份内容：  备份里的旧待办\n")

    # ---- 旧行为：encoding="utf-8"
    print('=== 修复前的写法 encoding="utf-8" ===')
    try:
        with open(data, "r", encoding="utf-8") as handle:
            json.load(handle)
        print("  能读 —— 说明这个 bug 不存在")
    except json.JSONDecodeError as exc:
        print(f"  [!!] JSONDecodeError: {exc}")
        print("       => 主文件会被误判成损坏，程序静默回退到备份，")
        print("          用户最新加的那条待办就消失了。")

    # ---- 新行为：encoding="utf-8-sig"
    print('\n=== 修复后的写法 encoding="utf-8-sig" ===')
    with open(data, "r", encoding="utf-8-sig") as handle:
        loaded = json.load(handle)
    print(f"  读到：{loaded['tasks'][0]['text']}")

    # ---- 走真正的 Store
    print("\n=== 走 Store.load() 的实际结果 ===")
    from pawpet.store import Store

    store = Store(data, backup)
    store.load()
    texts = [t.get("text") for t in store.tasks]
    print(f"  读到 {len(texts)} 条：{texts}")
    print(f"  migrated_from = {store.migrated_from!r}")

    if store.migrated_from == "backup":
        print("\n  [!!] 仍然回退到了备份 —— 修复没生效")
        return 1
    print("\n  [ok] 读的是主文件，没有误回退")

    # ---- 无 BOM 的文件行为不变
    print("\n=== 回归：不带 BOM 的文件行为不变 ===")
    plain = SCRATCH / "plain.json"
    plain.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    store2 = Store(plain, SCRATCH / "nope.json")
    store2.load()
    print(f"  读到 {len(store2.tasks)} 条：{[t.get('text') for t in store2.tasks]}")
    if len(store2.tasks) != 1:
        print("  [!!] 不带 BOM 的文件反而读不出来")
        return 1
    print("  [ok] 行为一致")

    shutil.rmtree(SCRATCH, ignore_errors=True)
    print("\n结论：BOM 容错修复有效，且不影响正常文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
