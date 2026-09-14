"""把安装程序的界面截下来，确认能正常显示、控件没被裁掉。

安装器是 tkinter 写的，只在 Windows 上真实渲染才知道排版对不对。

用法：
    .venv\\Scripts\\python.exe tools\\setupui_shot.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
os.environ["QT_QPA_PLATFORM"] = "windows"

OUT = ROOT / ".cache" / "preview"


def main() -> int:
    import tkinter as tk

    from setup_ui import InstallerWindow

    OUT.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    InstallerWindow(root)

    # 挪到屏幕外，别闪到用户
    root.update_idletasks()
    root.geometry(f"+{-3600}+{100}")
    root.update()

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            root.update()
            time.sleep(0.02)

    pump(1.2)

    # 用 PrintWindow 抓 tkinter 窗口（它不吃 Qt 的 grabWindow）
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwnd = int(root.frame(), 16) if isinstance(root.frame(), str) else root.winfo_id()
    # tkinter 的 winfo_id 给的是子窗口，要往上找到顶层
    top = user32.GetAncestor(hwnd, 2)  # GA_ROOT
    if top:
        hwnd = top

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    print(f"窗口尺寸 {width}x{height}")

    # 用 Qt 的 QImage 来保存，省得自己写 BMP
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    gdi32.SelectObject(hdc_mem, hbitmap)

    # PW_RENDERFULLCONTENT = 0x2，能抓到带主题的控件
    user32.PrintWindow(hwnd, hdc_mem, 0x2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height      # 负数 = 自上而下
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, hbitmap, 0, height, buffer, ctypes.byref(header), 0)

    image = QImage(bytes(buffer), width, height, QImage.Format_RGB32)
    target = OUT / "40_setup_wizard.png"
    image.save(str(target))
    print(f"已保存 {target}")

    gdi32.DeleteObject(hbitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    root.destroy()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
