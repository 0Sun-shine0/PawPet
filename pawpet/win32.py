"""Windows 系统能力：单实例、全局热键、空闲检测、全屏感知、开机自启。

全部走 ctypes + 标准库，不引入任何第三方依赖。
每个函数都做了兜底：系统调用失败时返回一个中性的默认值，
不会因为某个 API 拿不到数据就把整个程序打断。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)


# --------------------------------------------------------------------------
# 单实例
# --------------------------------------------------------------------------
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """命名互斥体；第二个实例拿不到锁，于是退出并让第一个实例显示面板。"""

    def __init__(self, name: str) -> None:
        self._handle = None
        self.already_running = False
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self.already_running = True
            return
        self._handle = handle

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


# --------------------------------------------------------------------------
# 空闲检测（判断用户是不是离开了电脑）
# --------------------------------------------------------------------------
class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """返回距离最后一次键鼠输入的秒数；失败时返回 0（当作正在使用）。"""
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    try:
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = kernel32.GetTickCount64() & 0xFFFFFFFF
        return max(0.0, ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0)
    except OSError:
        return 0.0


def is_user_active(threshold_seconds: float = 60.0) -> bool:
    return idle_seconds() < threshold_seconds


# --------------------------------------------------------------------------
# 全屏 / 免打扰检测（打游戏、放 PPT、全屏看片时不要弹提醒）
# --------------------------------------------------------------------------
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7


def notification_state() -> int:
    """SHQueryUserNotificationState：返回系统当前是否适合弹通知。"""
    try:
        state = ctypes.c_int(0)
        hr = shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:
            return QUNS_ACCEPTS_NOTIFICATIONS
        return state.value
    except OSError:
        return QUNS_ACCEPTS_NOTIFICATIONS


def is_quiet_context() -> bool:
    """True 表示现在处于全屏/演示/免打扰状态，应该推迟提醒。"""
    return notification_state() in (
        QUNS_BUSY,
        QUNS_RUNNING_D3D_FULL_SCREEN,
        QUNS_PRESENTATION_MODE,
        QUNS_QUIET_TIME,
        QUNS_NOT_PRESENT,
    )


# --------------------------------------------------------------------------
# 全局热键（独立线程 + 自己的消息循环，最稳的做法）
# --------------------------------------------------------------------------
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK = {
    "P": 0x50, "T": 0x54, "F": 0x46, "N": 0x4E, "B": 0x42,
    "SPACE": 0x20, "Q": 0x51, "S": 0x53,
}

MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
             "shift": MOD_SHIFT, "win": MOD_WIN, "super": MOD_WIN}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """把 "ctrl+alt+p" 解析成 (modifiers, virtual_key)。"""
    parts = [p.strip().lower() for p in spec.replace("_", "+").split("+") if p.strip()]
    if not parts:
        return None
    key = parts[-1].upper()
    mods = 0
    for name in parts[:-1]:
        mod = MOD_NAMES.get(name)
        if mod is None:
            return None
        mods |= mod
    vk = VK.get(key)
    if vk is None:
        if len(key) == 1 and key.isalnum():
            vk = ord(key)
        else:
            return None
    return mods, vk


class HotkeyManager:
    """在后台线程注册全局热键，命中后回调（回调在线程里执行）。"""

    def __init__(self, bindings: dict[str, str], on_hotkey) -> None:
        """bindings: {动作名: "ctrl+alt+p"}"""
        self._bindings = bindings
        self._on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self.registered: list[str] = []
        self.failed: list[str] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="pawpet-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]

        id_to_action: dict[int, str] = {}
        for index, (action, spec) in enumerate(self._bindings.items(), start=1):
            parsed = parse_hotkey(spec)
            if parsed is None:
                self.failed.append(action)
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, index, mods | MOD_NOREPEAT, vk):
                id_to_action[index] = action
                self.registered.append(action)
            else:
                self.failed.append(action)
        self._ready.set()

        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                action = id_to_action.get(int(msg.wParam))
                if action:
                    try:
                        self._on_hotkey(action)
                    except Exception:  # pragma: no cover - 回调不允许拖垮热键线程
                        pass
        for hotkey_id in id_to_action:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except OSError:
                pass
        self._thread = None
        self._thread_id = None


# --------------------------------------------------------------------------
# 开机自启（HKCU\...\Run）
# --------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_command() -> str:
    """生成开机自启命令行。

    打包后就是 exe 自己；源码运行时优先用 venv 里的 pythonw.exe（无窗口），
    这样不会在开机时弹出一个黑框。
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else exe
    root = Path(__file__).resolve().parent.parent
    return f'"{launcher}" "{root / "run_pawpet.py"}"'


def autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID_KEY)
        return True
    except OSError:
        return False


APP_ID_KEY = "PawPet"


def set_autostart(enabled: bool) -> bool:
    """写入/删除注册表自启项，返回操作后的真实状态。"""
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, APP_ID_KEY, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_ID_KEY)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
    return autostart_enabled()


# --------------------------------------------------------------------------
# 让单实例的第二个进程把命令转给第一个进程用的辅助函数
# --------------------------------------------------------------------------
def set_process_dpi_awareness() -> None:
    """Qt6 默认已按显示器 DPI 缩放，这里只做兜底，避免在某些老驱动上糊掉。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except OSError:
            pass


def focus_console_if_attached() -> None:
    """调试模式下把控制台窗口拉到前台，方便看日志。"""
    if not os.environ.get("PAWPET_DEBUG"):
        return
    try:
        handle = kernel32.GetConsoleWindow()
        if handle:
            user32.ShowWindow(handle, 5)
            user32.SetForegroundWindow(handle)
    except OSError:
        pass
