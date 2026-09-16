"""验证 finishtest 真的能抓到「任务没完成就结束」这个 bug。

做法：把两处修复临时撤掉（还原成旧行为），跑 finishtest，看它是否变红。
会变红才说明这个回归测试有效。

撤掉的两处：
1. agent 里「顶到上限时把说明拼进 final_text」
2. controller 里「把最终回答 _push 进对话」

用法：
    .venv\\Scripts\\python.exe tools\\finishverify.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PATCH = r'''
import sys
sys.path.insert(0, r"{root}")

# --- 撤掉修复 1：顶到上限时不再产出收尾文字 ---
from pawpet.ai import agent as agent_mod

orig_system = agent_mod.SYSTEM_PROMPT

# 让 AgentRunner.run 在正常返回前把收尾文字清掉，模拟旧行为
_orig_run = agent_mod.AgentRunner.run

def old_run(self, user_text):
    text = _orig_run(self, user_text)
    # 旧行为：只有 on_status，final_text 里没有那段说明
    if "本轮上限" in text:
        head = text.split("⚠️")[0].strip()
        return head
    return text

agent_mod.AgentRunner.run = old_run

# --- 撤掉修复 2：最终回答不再 _push 进对话 ---
from pawpet.ai.controller import AiController

_orig_finished = AiController._apply_finished

def old_finished(self, text, error, turn=0):
    # 复刻旧实现：只发提示，不 push
    if not self._is_current(turn):
        return
    self._running = False
    self._status = "空闲"
    self.runningChanged.emit()
    self.statusChanged.emit()
    if error:
        self._push("error", error)
        self.toastRequested.emit("AI 出错", error[:120])
    elif text:
        self.toastRequested.emit("任务结束", text[:80])

AiController._apply_finished = old_finished

import runpy
sys.argv = ["finishtest.py"]
try:
    runpy.run_path(r"{root}\tools\finishtest.py", run_name="__main__")
except SystemExit as exc:
    print("FINISHTEST_EXIT=", exc.code)
'''


def main() -> int:
    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    print("检查 finishtest 是否真的能抓到旧 bug\n")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PAWPET_HOME"] = str(ROOT / ".cache" / "finishverify")
    env["PAWPET_INSTANCE_SUFFIX"] = "finishverify"

    result = subprocess.run([exe, "-c", PATCH.format(root=str(ROOT))],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env, cwd=str(ROOT),
                            timeout=300)

    out = result.stdout or ""
    failed = [line for line in out.splitlines() if "[XX]" in line]
    summary = [line for line in out.splitlines()
               if "通过" in line and "失败" in line]

    print("--- 撤掉修复后的结果 ---")
    for line in failed[:12]:
        print("   ", line.strip())
    if summary:
        print("   ", summary[-1].strip())
    if not failed and not summary:
        tail = out.strip().splitlines()[-8:]
        for line in tail:
            print("   ", line)

    print()
    if failed:
        print(f"[ok] 撤掉修复后测试变红（{len(failed)} 项失败）")
        print("     => finishtest 确实能抓到这个 bug")
        return 0
    print("[XX] 撤掉修复后测试**依然全过** —— 这个回归测试没测到点子上")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
