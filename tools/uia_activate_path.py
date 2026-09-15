r"""端到端验证：能不能靠「先激活窗口」让卡死的程序变得可读？

已知：ElementFromHandle 在记事本上永久卡死，而 ElementFromPoint + 子树遍历
在同一个程序上 0.25-0.4 秒就能拿到几十个带名字的控件。
唯一的障碍是 ElementFromPoint 只认**视觉最上层**的东西。

那么：先用 ui_activate_window 把目标窗口提到前台，再走 ElementFromPoint，
能不能真正读到记事本的控件？

用法：
    .venv\Scripts\python.exe tools\uia_activate_path.py
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


def foreground_handle() -> int:
    return u32.GetForegroundWindow()


def pid_of(handle: int) -> int:
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(pid))
    return pid.value


def window_rect(handle: int):
    rect = wintypes.RECT()
    u32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def collect_from_points(client, left, top, right, bottom, target_pid, limit=60):
    width, height = right - left, bottom - top
    samples = [
        (left + width // 2, top + height // 2),
        (left + width // 2, top + height // 8),
        (left + width // 8, top + height // 2),
        (left + width // 2, top + height - height // 8),
        (left + width - width // 8, top + height // 2),
    ]
    kept, dropped = {}, 0
    for x, y in samples:
        hit = client.element_at(x, y)          # 已知这条是快的
        if hit is None or hit.process_id != target_pid:
            if hit is not None:
                dropped += 1
            continue
        pointers = client._find_descendants(hit._handle, limit=limit * 3)
        for ptr in pointers:
            if len(kept) >= limit:
                break
            try:
                info = client._read_element(ptr, depth=1)
            except Exception:  # noqa: BLE001
                continue
            if info.process_id != target_pid or info.is_offscreen:
                continue
            if info.type_name not in uia.INTERESTING_TYPES:
                continue
            if not (info.name or info.automation_id):
                continue
            kept[f"{info.rect}-{info.type_name}"] = info
    return list(kept.values()), dropped


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

    fg = foreground_handle()
    left, top, right, bottom = window_rect(window.handle)
    client = uia.get_client()

    print(f"记事本 {window.title!r} hwnd={window.handle} pid={proc.pid}")
    print(f"初始前台 pid={pid_of(fg)}（记事本 {proc.pid}）")
    print(f"记事本矩形 ({left},{top})-({right},{bottom})\n")

    print("=== A. 未激活时用采样点读 ===")
    hit = client.element_at((left + right) // 2, (top + bottom) // 2)
    print(f"   中心命中 pid={hit.process_id if hit else None}"
          f"{'（是记事本）' if hit and hit.process_id == proc.pid else '（不是记事本 -> 被挡住）'}")

    print("\n=== B. 调真实的 ui_activate_window ===")
    actions = DesktopActions(AuditLog())
    result = actions.activate_window("记事本")
    time.sleep(0.8)
    fg2 = foreground_handle()
    now_fg = fg2 == window.handle
    print(f"   activate_window 返回：ok={result.ok} {result.message}")
    print(f"   现在前台 hwnd={fg2} pid={pid_of(fg2)}")
    print(f"   记事本是否成为前台：{now_fg}")

    print("\n=== C. 激活后用采样点读控件 ===")
    elements, dropped = collect_from_points(client, left, top, right, bottom, proc.pid)
    named = [e for e in elements if e.name]
    print(f"   读到 {len(elements)} 个控件（{len(named)} 个有名字），"
          f"挡掉 {dropped} 个不属于本进程的采样点")
    for e in named[:8]:
        print(f"      · {e.summary()[:72]}")

    print("\n=== D. 对照：这个窗口走 ElementFromHandle ===")
    ok, res = uia.call_with_timeout(lambda: client.element_from_handle(window.handle), 6.0)
    print(f"   {'返回成功' if ok else '仍然失败：' + str(res)[:60]}")

    proc.terminate()
    return 0


def main() -> int:
    if "--child" in sys.argv:
        return child()
    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    print("激活 -> 采样点读取 端到端验证\n")
    try:
        proc = subprocess.run([exe, "-u", __file__, "--child"], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        for line in (proc.stdout or "").strip().splitlines():
            print(line)
        if proc.returncode != 0 and (proc.stderr or "").strip():
            print("stderr:", proc.stderr.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        print("子进程 120 秒没退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
