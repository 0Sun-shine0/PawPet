r"""最终实验：ElementFromHandle 到底在什么条件下能用？

已排除：vtable 槽位读错、COM 线程模型、单纯的前台锁定。
现在用最激进的方式让记事本真正成为**前台窗口**（SetWindowPos TOPMOST +
AttachThreadInput + SetForegroundWindow + 校验），成功后再调 ElementFromHandle。

如果前台状态下能返回，那结论就是「UIA 从 HWND 进入需要目标程序在处理消息」，
方案就是先激活窗口再读；如果还是卡死，那就是记事本自身 provider 的问题。

用法：
    .venv\Scripts\python.exe tools\uia_foreground.py --hard
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai import uia  # noqa: E402

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040


def pid_of(handle: int) -> int:
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(pid))
    return pid.value


def hard_foreground(handle: int) -> bool:
    """尽最大努力把窗口弄到前台，并如实返回是否成功。"""
    hwnd = wintypes.HWND(handle)
    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, 9)                     # SW_RESTORE
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW

    # 1) 先置顶，确保不是被挡住的那个
    u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    time.sleep(0.4)

    # 2) 挂到当前前台线程上再抢前台
    foreground = u32.GetForegroundWindow()
    target_thread = u32.GetWindowThreadProcessId(hwnd, None)
    current_thread = k32.GetCurrentThreadId()
    attach_targets = {target_thread, u32.GetWindowThreadProcessId(
        wintypes.HWND(foreground), None)}
    attached = []
    for thread_id in attach_targets:
        if thread_id and thread_id != current_thread:
            if u32.AttachThreadInput(current_thread, thread_id, True):
                attached.append(thread_id)
    try:
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        u32.SetFocus(hwnd)
    finally:
        for thread_id in attached:
            u32.AttachThreadInput(current_thread, thread_id, False)
    time.sleep(0.5)

    ok = u32.GetForegroundWindow() == handle

    # 3) 取消置顶（不影响前台状态）
    u32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    time.sleep(0.2)
    return ok


def attempt(fn, timeout=8.0):
    box = {}

    def runner():
        try:
            box["v"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["e"] = f"{type(exc).__name__}: {exc}"

    t0 = time.time()
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return "hang", time.time() - t0
    if "e" in box:
        return "err", box["e"]
    return "ok", (box.get("v"), time.time() - t0)


def child() -> int:
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

    client = uia.get_client()
    fg = u32.GetForegroundWindow()
    print(f"记事本 hwnd={window.handle} pid={proc.pid}")
    print(f"初始前台 hwnd={fg} pid={pid_of(fg)}")
    print(f"记事本本来就是前台：{fg == window.handle}\n")

    print("=== A. 强行置前（TOPMOST + AttachThreadInput）===")
    ok = hard_foreground(window.handle)
    fg2 = u32.GetForegroundWindow()
    print(f"   hard_foreground -> {ok}")
    print(f"   现在前台 hwnd={fg2} pid={pid_of(fg2)}"
          f"   （记事本 {window.handle}）")
    # 用 WindowFromPoint 确认头顶上确实是记事本
    rect = wintypes.RECT()
    u32.GetWindowRect(wintypes.HWND(window.handle), ctypes.byref(rect))
    cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    top = u32.WindowFromPoint(wintypes.POINT(cx, cy))
    print(f"   WindowFromPoint({cx},{cy}) -> hwnd={top} pid={pid_of(top)}"
          f"  {'（是记事本）' if top == window.handle else '（不是记事本）'}\n")

    print("=== B. 在这个状态下调 ElementFromHandle ===")
    state, result = attempt(lambda: client.element_from_handle(window.handle), 8.0)
    if state == "ok":
        element, elapsed = result
        print(f"   [ok] {elapsed:.2f}s  "
              f"{element.summary()[:70] if element else 'None'}")
    else:
        print(f"   [{state}] {result}")

    print("\n=== C. 读整棵树 ===")
    state3, result3 = attempt(lambda: client.tree(window), 10.0)
    if state3 == "ok":
        (elements, note), elapsed = result3
        print(f"   [ok] {elapsed:.2f}s  {note}")
        for e in elements[:8]:
            print(f"      · {e.summary()[:72]}")
    else:
        print(f"   [{state3}] {result3}")

    print("\n=== D. 前置状态下用 ElementFromPoint 采样 ===")
    c = uia.get_client()
    state4, hit = attempt(lambda: c.element_at(cx, cy), 5.0)
    if state4 == "ok" and hit is not None:
        print(f"   [ok] 命中 {hit.type_name} {hit.name[:36]!r} pid={hit.process_id}")
    else:
        print(f"   [{state4}] {hit}")

    proc.terminate()
    return 0


def main() -> int:
    if "--hard" in sys.argv:
        return child()
    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    print("ElementFromHandle 可用条件 —— 最终实验\n")
    try:
        proc = subprocess.run([exe, "-u", __file__, "--hard"], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        for line in (proc.stdout or "").strip().splitlines():
            print(line)
        if proc.returncode != 0 and (proc.stderr or "").strip():
            print("stderr:", proc.stderr.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        print("子进程 180 秒没退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
