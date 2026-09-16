"""验证 turntest 真的能抓到旧 bug（把修复临时撤掉，看测试会不会红）。

做法：临时给 _apply_learned / _apply_finished 打上「不做轮次检查」的补丁，
跑 turntest，看它是否失败。会失败才说明这个回归测试有效。

用法：
    .venv\\Scripts\\python.exe tools\\turnverify.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 让 turntest 以为自己是「旧代码」：给 __init__ 之后打补丁，
# 把 _is_current 永远返回 True（等同修复前没有轮次检查）
PATCH = r'''
import sys
sys.path.insert(0, r"{root}")
from pawpet.ai.controller import AiController

# 模拟修复前：没有轮次检查
AiController._is_current = lambda self, turn: True

import runpy
sys.argv = ["turntest.py"]
try:
    runpy.run_path(r"{root}\tools\turntest.py", run_name="__main__")
except SystemExit as exc:
    print("TURNTEST_EXIT=", exc.code)
'''


def main() -> int:
    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    print("检查 turntest 是否真的能抓到旧 bug\n")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PAWPET_HOME"] = str(ROOT / ".cache" / "turnverify")
    env["PAWPET_INSTANCE_SUFFIX"] = "turnverify"

    code = PATCH.format(root=str(ROOT))
    result = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env,
                            cwd=str(ROOT), timeout=300)

    out = result.stdout or ""
    failed_lines = [line for line in out.splitlines() if "[XX]" in line]
    summary = [line for line in out.splitlines()
               if "通过" in line and "失败" in line]

    print("--- 关闭轮次检查后的结果 ---")
    for line in failed_lines[:12]:
        print("   ", line.strip())
    if summary:
        print("   ", summary[-1].strip())
    if not failed_lines and not summary:
        tail = out.strip().splitlines()[-6:]
        for line in tail:
            print("   ", line)

    caught = bool(failed_lines)
    print()
    if caught:
        print(f"[ok] 关掉轮次检查后测试变红（{len(failed_lines)} 项失败）")
        print("     => turntest 确实能抓到这个 bug，不是白测")
        return 0
    print("[XX] 关掉轮次检查后测试**依然全过** —— 说明这个回归测试没测到点子上")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
