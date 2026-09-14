"""列出某个进程当前打开的所有窗口标题。

窗口程序出错时会弹 MessageBox，进程活着但什么都不干 —— 用这个把
「卡在弹窗上」和「真的在正常运行」区分开。

用法：
    .venv\\Scripts\\python.exe tools\\procwindows.py PawPet.exe
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32


def windows_of_process(pid: int) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 2)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        visible = bool(user32.IsWindowVisible(hwnd))
        found.append((hwnd, buffer.value, f"{cls.value}{'' if visible else ' (隐藏)'}"))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "PawPet.exe"
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 14

    print(f"启动 {target}，等 {wait} 秒后列出它的窗口\n")
    process = subprocess.Popen([target], cwd=str(__import__("pathlib").Path(target).parent))
    time.sleep(wait)

    alive = process.poll() is None
    print(f"进程存活：{alive}" + ("" if alive else f"（退出码 {process.poll()}）"))

    if alive:
        items = windows_of_process(process.pid)
        print(f"窗口数量：{len(items)}\n")
        for hwnd, title, cls in items:
            marker = ""
            # 弹窗类窗口的特征
            if "#32770" in cls:
                marker = "   <<< 这是个对话框（很可能在等用户点确定）"
            print(f"  hwnd={hwnd}  标题={title!r}  类={cls}{marker}")
        if not items:
            print("  （没有任何窗口 —— 进程可能在后台初始化，或者卡在非窗口的地方）")

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
