r"""用「不卡死」的方式定位 element_from_handle 到底该用哪个槽位。

背景：正式的 element_from_handle（槽位 IA_ELEMENT_FROM_HANDLE）在记事本、
计算器、画图、Edge 上会**永久卡死**，但在小爪自己身上正常。
这可能是
  (a) 这些程序真的不响应从 HWND 进入的请求，或者
  (b) 我们读错了 vtable 槽位，调到了别的函数上。

区分方法：每个槽位都试一遍，看哪个能返回「记事本」这个窗口名字。
正确的槽位必然返回窗口元素（名字 = 窗口标题）。
每个尝试都在独立线程 + 硬超时里跑，卡住就放弃，绝不影响下一个。

用法：
    .venv\Scripts\python.exe tools\uia_vtable_scan.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai import uia  # noqa: E402

# 按 MSDN 上 IUIAutomation 的声明顺序，ElementFromHandle 附近应该是这几个
CANDIDATES = [14, 15, 16, 17, 18, 19, 20, 21, 22, 23]


def scan_one(index: int, handle: int, label: str) -> None:
    """在独立线程里用第 index 个槽位当 ElementFromHandle 调一次。"""
    c = uia.get_client()
    box: dict = {}

    def runner():
        try:
            ptr = ctypes.c_void_p()
            hr = uia._call(c._automation, index, ctypes.c_long,
                           (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                           ctypes.c_void_p(handle), ctypes.byref(ptr))
            if hr != 0 or not ptr.value:
                box["result"] = f"HRESULT=0x{hr & 0xFFFFFFFF:08X} ptr={ptr.value}"
                return
            info = c._read_element(ptr.value, with_patterns=True)
            uia._release(ptr.value)
            box["result"] = f"name={info.name!r} type={info.type_name} rect={info.rect}"
        except Exception as exc:  # noqa: BLE001
            box["result"] = f"{type(exc).__name__}: {exc}"

    t0 = time.time()
    import threading
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(6.0)

    if thread.is_alive():
        print(f"  槽位 {index:3d}: [卡死] >6s —— 这个槽位会永久挂起")
        return
    elapsed = time.time() - t0
    print(f"  槽位 {index:3d}: {box.get('result', '?')}   （{elapsed:.2f}s）")


def main() -> int:
    print("扫描 IUIAutomation 的 vtable 槽位，找 ElementFromHandle\n")

    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    if "--child" in sys.argv:
        index = int(sys.argv[sys.argv.index("--child") + 1])
        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(2.5)
        window = uia.window_by_title_win32("记事本")
        print(f"记事本 hwnd={window.handle}")
        if window:
            scan_one(index, window.handle, "记事本")
        proc.terminate()
        return 0

    print("先确认当前正式常量：IA_ELEMENT_FROM_HANDLE =",
          getattr(uia, "IA_ELEMENT_FROM_HANDLE", "?"))
    print("先用独立子进程跑一次正式调用（确认卡死可复现）：\n")

    code = (
        "import subprocess, sys, time\n"
        "sys.path.insert(0, r'%s')\n"
        "from pawpet.ai import uia\n"
        "p = subprocess.Popen(['notepad.exe']); time.sleep(2.5)\n"
        "w = uia.window_by_title_win32('记事本')\n"
        "print('hwnd=', w.handle)\n"
        "c = uia.get_client()\n"
        "ok, res = uia.call_with_timeout(lambda: c.element_from_handle(w.handle), 8.0)\n"
        "print('ok=', ok, 'res=', str(res)[:70])\n"
        "p.terminate()\n" % str(ROOT)
    )
    try:
        proc = subprocess.run([exe, "-c", code], cwd=str(ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=45)
        for line in (proc.stdout or "").strip().splitlines():
            print("   ", line)
    except subprocess.TimeoutExpired:
        print("    子进程 45 秒没退出")

    print("\n请手动运行下面这条来逐个扫槽位（每个槽位一个子进程）：")
    for index in CANDIDATES:
        print(f"    {exe} tools\\uia_vtable_scan.py --child {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
