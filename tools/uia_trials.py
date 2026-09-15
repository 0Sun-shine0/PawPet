r"""控制实验：ElementFromHandle 到底什么条件下能用？

前面的结论互相矛盾（有时卡死、有时返回垃圾、偶尔成功），需要一次受控的
重复试验来定性。每轮记录四件事：
  1. activate_window 有没有真的把窗口弄到前台
  2. 当前前台是不是目标窗口
  3. element_from_handle 的结果（超时 / 垃圾 / 有内容）
  4. tree() 能不能读到控件

用法：
    .venv\Scripts\python.exe tools\uia_trials.py [轮数]
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai import uia  # noqa: E402
from pawpet.ai.actions import AuditLog, DesktopActions  # noqa: E402

u32 = ctypes.windll.user32


def foreground() -> int:
    return u32.GetForegroundWindow()


def describe(element) -> str:
    if element is None:
        return "None"
    return (f"{element.type_name} {element.name[:18]!r} "
            f"rect={element.rect} enabled={element.is_enabled}")


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"ElementFromHandle 受控试验（{rounds} 轮）\n")

    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(2.5)
    window = None
    for _ in range(30):
        found = [w for w in uia.enum_windows() if w.process_id == proc.pid]
        if found:
            window = found[0]
            break
        time.sleep(0.2)
    if window is None:
        print("没找到记事本窗口")
        proc.terminate()
        return 1

    actions = DesktopActions(AuditLog())
    client = uia.get_client()
    print(f"记事本 {window.title!r} hwnd={window.handle} pid={proc.pid}\n")

    wins = {"activate": 0, "foreground": 0, "handle_ok": 0, "tree_ok": 0}
    for index in range(1, rounds + 1):
        uia.forget_hanging_windows()

        result = actions.activate_window("记事本")
        time.sleep(0.6)
        is_front = foreground() == window.handle
        wins["activate"] += 1 if result.ok else 0
        wins["foreground"] += 1 if is_front else 0

        ok_h, handle_result = uia.call_with_timeout(
            lambda: client.element_from_handle(window.handle), 4.0)
        if ok_h and handle_result is not None:
            handle_txt = describe(handle_result)
            # 0x0 的 Pane 是「读到了但没内容」的垃圾
            useful = handle_result.width > 0 and handle_result.height > 0
            if useful:
                wins["handle_ok"] += 1
        elif not ok_h:
            handle_txt = "超时/出错：" + str(handle_result)[:34]
        else:
            handle_txt = "None"

        ok_t, tree_result = uia.call_with_timeout(
            lambda: client.tree(window), 8.0)
        if ok_t:
            elements, _note = tree_result
            wins["tree_ok"] += 1 if elements else 0
            tree_txt = f"{len(elements)} 个控件"
        else:
            tree_txt = "超时"

        print(f"第 {index} 轮：前台={is_front}  activate.ok={result.ok}")
        print(f"        element_from_handle -> {handle_txt}")
        print(f"        tree                -> {tree_txt}")
        print()

    print(f"汇总：activate 报成功 {wins['activate']}/{rounds}，"
          f"真的到前台 {wins['foreground']}/{rounds}，")
    print(f"      element_from_handle 有用 {wins['handle_ok']}/{rounds}，"
          f"tree 读到控件 {wins['tree_ok']}/{rounds}")

    proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
