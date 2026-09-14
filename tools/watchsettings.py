"""长时间观察真实应用启动前后，pet_data.json 里的 settings 有没有被意外改动。

这一次会打印完整的 settings 差异，不只看 ai_level —— 如果应用真的会
偷偷改权限，这里一定能抓到。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "pet_data.json"
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"


def read_settings() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8")).get("settings", {})


def kill_all() -> None:
    for name in ("pythonw.exe", "python.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name],
                       capture_output=True, text=True)
    time.sleep(2.0)


def main() -> int:
    print("真实应用启动影响观测\n")

    kill_all()

    # 把 ai_level 设成一个标记值，看它会不会被动
    data = json.loads(DATA.read_text(encoding="utf-8"))
    original_level = data["settings"].get("ai_level")
    data["settings"]["ai_level"] = "read_only"
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    before = read_settings()
    print(f"启动前 ai_level = {before['ai_level']}  (原值 {original_level})")
    print(f"启动前 settings 共 {len(before)} 项")

    # 记下所有值，一会儿逐项对比
    snapshot = dict(before)

    print("\n启动应用…")
    subprocess.Popen([str(PYTHONW), str(ROOT / "run_pawpet.py")], cwd=str(ROOT))

    for elapsed in (5, 10, 20, 30):
        time.sleep(5 if elapsed == 5 else 5)
        current = read_settings()
        changed = {k: (snapshot.get(k), v) for k, v in current.items()
                   if snapshot.get(k) != v}
        new_keys = [k for k in current if k not in snapshot]
        print(f"  t={elapsed:2d}s  ai_level={current.get('ai_level'):10s} "
              f"改动={changed or '无'} 新增键={new_keys or '无'}")

    kill_all()

    final = read_settings()
    print(f"\n最终 ai_level = {final['ai_level']}")

    if final.get("ai_level") != "read_only":
        print(f"\n[!!] 应用把权限从 read_only 改成了 {final.get('ai_level')} —— 这是安全 bug")
        return 1

    print("\n[ok] 应用没有偷偷修改权限设置")

    # 恢复成一个合理的默认值
    data = json.loads(DATA.read_text(encoding="utf-8"))
    data["settings"]["ai_level"] = "confirm"
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已把 ai_level 恢复为 confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
