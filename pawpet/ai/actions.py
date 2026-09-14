"""桌面动作层。

三件事必须做对：

1. **分级安全**。只读动作直接执行；会改变屏幕状态的动作要用户点头；
   影响系统的动作默认关闭。每一级都可配置，但默认值偏保守。

2. **中文输入**。pyautogui.write() 只能打 ASCII，中文会静默丢掉。
   所以非 ASCII 文本走「剪贴板 + Ctrl+V」，并且用完恢复原剪贴板内容。

3. **随时能停**。除了界面上的停止按钮，还打开 pyautogui 的 FAILSAFE：
   把鼠标猛甩到屏幕左上角就能强制中断，这是给用户的物理急停。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

try:
    import pyautogui

    pyautogui.FAILSAFE = True      # 鼠标甩到左上角即中断
    pyautogui.PAUSE = 0.05         # 每个动作之间留一点时间，别把系统打爆
    PYAUTOGUI_AVAILABLE = True
except Exception:  # noqa: BLE001 - 无显示器环境会抛异常
    pyautogui = None
    PYAUTOGUI_AVAILABLE = False


class Risk:
    READ = "read"        # 只观察，不改变任何东西
    CONFIRM = "confirm"  # 会动你的屏幕/键鼠，需要确认
    DANGER = "danger"    # 会影响系统或文件，默认禁用


# 自动放行等级：越高越宽松
LEVEL_READ_ONLY = "read_only"   # 仅只读
LEVEL_CONFIRM = "confirm"       # 只读自动 + 其它需确认（默认）
LEVEL_AUTO = "auto"             # 只读和键鼠自动 + 危险需确认
LEVEL_FULL = "full"             # 全部自动（危险，需要用户明确选）

LEVEL_LABELS = {
    LEVEL_READ_ONLY: "只读",
    LEVEL_CONFIRM: "逐步确认",
    LEVEL_AUTO: "自动执行",
    LEVEL_FULL: "完全自动",
}


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)

    def as_text(self) -> str:
        return self.message or ("完成" if self.ok else "失败")


class AuditLog:
    """动作审计：内存里保留最近若干条，可选同时落盘。"""

    def __init__(self, limit: int = 400) -> None:
        self._items: list[dict] = []
        self._lock = threading.Lock()
        self._limit = limit
        self._path: str | None = None

    def set_file(self, path: str | None) -> None:
        self._path = path

    def add(self, action: str, detail: str, risk: str, approved: str) -> dict:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "stamp": time.time(),
            "action": action,
            "detail": detail,
            "risk": risk,
            "approved": approved,   # auto / user / denied
        }
        with self._lock:
            self._items.append(entry)
            del self._items[:-self._limit]
        if self._path:
            try:
                with open(self._path, "a", encoding="utf-8") as handle:
                    handle.write(
                        f"{datetime.now().isoformat(timespec='seconds')}\t"
                        f"{risk}\t{approved}\t{action}\t{detail}\n"
                    )
            except OSError:
                pass
        return entry

    def recent(self, count: int = 60) -> list[dict]:
        with self._lock:
            return list(self._items[-count:])

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class DesktopActions:
    """受控的键鼠与窗口操作。"""

    def __init__(self, audit: AuditLog | None = None) -> None:
        self.audit = audit or AuditLog()
        self.level = LEVEL_CONFIRM
        self._stop = threading.Event()

    # ------------------------------------------------------------- 运行时状态
    @property
    def available(self) -> bool:
        return PYAUTOGUI_AVAILABLE

    def status(self) -> str:
        if not PYAUTOGUI_AVAILABLE:
            return "桌面控制不可用：缺少 pyautogui"
        return f"桌面控制就绪（急停：鼠标甩到左上角）"

    def request_stop(self) -> None:
        self._stop.set()

    def clear_stop(self) -> None:
        self._stop.clear()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # --------------------------------------------------------------- 安全策略
    def needs_approval(self, risk: str) -> bool:
        if risk == Risk.READ:
            return False
        if risk == Risk.CONFIRM:
            return self.level in (LEVEL_READ_ONLY, LEVEL_CONFIRM)
        # DANGER
        if self.level == LEVEL_FULL:
            return False
        if self.level == LEVEL_READ_ONLY:
            return True     # 只读模式下干脆不放行
        return True

    def blocked(self, risk: str) -> bool:
        """只读模式下，任何会改变状态的动作都不执行。"""
        return self.level == LEVEL_READ_ONLY and risk != Risk.READ

    # ------------------------------------------------------------------ 查询
    def screen_size(self) -> tuple[int, int]:
        if not PYAUTOGUI_AVAILABLE:
            return (0, 0)
        size = pyautogui.size()
        return (int(size.width), int(size.height))

    def mouse_position(self) -> tuple[int, int]:
        if not PYAUTOGUI_AVAILABLE:
            return (0, 0)
        point = pyautogui.position()
        return (int(point.x), int(point.y))

    def active_window(self) -> str:
        import ctypes

        try:
            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            return buffer.value or "(无标题)"
        except Exception:  # noqa: BLE001
            return "(未知)"

    def list_windows(self, limit: int = 40) -> list[str]:
        import ctypes
        from ctypes import wintypes

        titles: list[str] = []
        try:
            user32 = ctypes.windll.user32
            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def callback(handle, _param):
                if user32.IsWindowVisible(handle):
                    length = user32.GetWindowTextLengthW(handle)
                    if length > 0:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(handle, buffer, length + 1)
                        title = buffer.value.strip()
                        if title and title not in titles:
                            titles.append(title)
                return True

            user32.EnumWindows(enum_proc(callback), 0)
        except Exception:  # noqa: BLE001
            pass
        return titles[:limit]

    def activate_window(self, title_part: str) -> ActionResult:
        import ctypes

        title_part = (title_part or "").strip()
        if not title_part:
            return ActionResult(False, "没有给出窗口标题")
        try:
            user32 = ctypes.windll.user32
            found = []

            def callback(handle, _param):
                if user32.IsWindowVisible(handle):
                    length = user32.GetWindowTextLengthW(handle)
                    if length > 0:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(handle, buffer, length + 1)
                        if title_part.lower() in buffer.value.lower():
                            found.append(handle)
                return True

            from ctypes import wintypes

            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(enum_proc(callback), 0)
            if not found:
                return ActionResult(False, f"没找到标题包含「{title_part}」的窗口")

            handle = found[0]
            if user32.IsIconic(handle):
                user32.ShowWindow(handle, 9)    # SW_RESTORE
            user32.SetForegroundWindow(handle)
            time.sleep(0.25)
            return ActionResult(True, f"已切到窗口：{self.active_window()}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"切换窗口失败：{exc}")

    # -------------------------------------------------------------- 只读动作
    def clipboard_text(self) -> str:
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                text = root.clipboard_get()
            finally:
                root.destroy()
            return text or ""
        except Exception:  # noqa: BLE001
            try:
                import pyperclip

                return pyperclip.paste() or ""
            except Exception:  # noqa: BLE001
                return ""

    def set_clipboard(self, text: str) -> bool:
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                root.clipboard_clear()
                root.clipboard_append(text or "")
                root.update()
            finally:
                root.destroy()
            return True
        except Exception:  # noqa: BLE001
            try:
                import pyperclip

                pyperclip.copy(text or "")
                return True
            except Exception:  # noqa: BLE001
                return False

    # ------------------------------------------------------------ 键鼠动作
    def move(self, x: int, y: int, duration: float = 0.2) -> ActionResult:
        try:
            pyautogui.moveTo(int(x), int(y), duration=max(0.0, min(1.5, duration)))
            return ActionResult(True, f"鼠标移到 ({x}, {y})")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"移动鼠标失败：{exc}")

    def click(self, x: int | None = None, y: int | None = None,
              button: str = "left", clicks: int = 1) -> ActionResult:
        try:
            if x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.15)
                time.sleep(0.05)
            button = button if button in ("left", "right", "middle") else "left"
            pyautogui.click(button=button, clicks=max(1, min(3, int(clicks))), interval=0.08)
            where = f"({x}, {y})" if x is not None else "当前位置"
            name = {"left": "左键", "right": "右键", "middle": "中键"}[button]
            times = "双击" if clicks == 2 else ("单击" if clicks == 1 else "三击")
            return ActionResult(True, f"{where} {name}{times}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"点击失败：{exc}")

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> ActionResult:
        try:
            pyautogui.moveTo(int(x1), int(y1), duration=0.15)
            pyautogui.mouseDown()
            time.sleep(0.08)
            pyautogui.moveTo(int(x2), int(y2), duration=max(0.1, min(2.0, duration)))
            time.sleep(0.08)
            pyautogui.mouseUp()
            return ActionResult(True, f"从 ({x1}, {y1}) 拖到 ({x2}, {y2})")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"拖拽失败：{exc}")

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> ActionResult:
        try:
            if x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.15)
            amount = max(-20, min(20, int(amount)))
            pyautogui.scroll(amount * 120)
            return ActionResult(True, f"滚动 {amount} 格")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"滚动失败：{exc}")

    def type_text(self, text: str) -> ActionResult:
        """输入文本。非 ASCII 走剪贴板粘贴，否则中文会丢。"""
        text = "" if text is None else str(text)
        if not text:
            return ActionResult(False, "没有要输入的文本")
        try:
            if text.isascii():
                pyautogui.write(text, interval=0.012)
                return ActionResult(True, f"已输入 {len(text)} 个字符")

            # 中文等非 ASCII：借剪贴板
            previous = self.clipboard_text()
            if not self.set_clipboard(text):
                return ActionResult(False, "无法写入剪贴板，中文输入失败")
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.25)
            if previous:
                # 尽量把用户原来的剪贴板还回去，别把人家复制的东西弄丢
                self.set_clipboard(previous)
            return ActionResult(True, f"已粘贴 {len(text)} 个字符（走剪贴板输入）")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"输入失败：{exc}")

    KEY_ALIASES = {
        "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
        "win": "win", "super": "win", "cmd": "win", "meta": "win",
        "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc",
        "tab": "tab", "space": "space", "backspace": "backspace",
        "delete": "delete", "del": "delete", "home": "home", "end": "end",
        "pageup": "pageup", "pagedown": "pagedown",
        "up": "up", "down": "down", "left": "left", "right": "right",
        "capslock": "capslock", "insert": "insert", "printscreen": "printscreen",
    }

    def press_keys(self, keys: list[str]) -> ActionResult:
        if not keys:
            return ActionResult(False, "没有给出按键")
        normalised: list[str] = []
        for key in keys:
            token = str(key).strip().lower()
            if not token:
                continue
            normalised.append(self.KEY_ALIASES.get(token, token))
        if not normalised:
            return ActionResult(False, "按键解析失败")
        try:
            if len(normalised) == 1:
                pyautogui.press(normalised[0])
                return ActionResult(True, f"按下 {normalised[0]}")
            pyautogui.hotkey(*normalised)
            return ActionResult(True, f"按下组合键 {'+'.join(normalised)}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"按键失败：{exc}")

    # ------------------------------------------------------------ 系统动作
    def open_app(self, command: str) -> ActionResult:
        """启动一个程序或打开一个 URL。"""
        command = (command or "").strip()
        if not command:
            return ActionResult(False, "没有给出要打开的目标")
        lowered = command.lower()
        blocked = ("format", "diskpart", "shutdown", "reg delete", "rm -rf",
                   "del /f", "rd /s", "cipher /w")
        if any(bad in lowered for bad in blocked):
            return ActionResult(False, "这个命令在禁用列表里，拒绝执行")
        try:
            if command.startswith(("http://", "https://")):
                os.startfile(command)  # noqa: S606 - 用户明确要求打开 URL
            else:
                subprocess.Popen(command, shell=True, close_fds=True)
            time.sleep(0.6)
            return ActionResult(True, f"已启动：{command}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"启动失败：{exc}")

    def run_command(self, command: str, timeout: int = 20) -> ActionResult:
        """执行一条命令并取回输出。默认关闭，属于高危动作。"""
        command = (command or "").strip()
        if not command:
            return ActionResult(False, "没有给出命令")
        lowered = command.lower()
        blocked = ("format ", "diskpart", "shutdown", "rm -rf /", "del /f /s /q c:",
                   "rd /s /q c:", "cipher /w", "vssadmin delete")
        if any(bad in lowered for bad in blocked):
            return ActionResult(False, "这个命令在禁用列表里，拒绝执行")
        try:
            completed = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=max(1, min(120, int(timeout))),
                encoding="utf-8", errors="replace",
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output = output.strip()
            if len(output) > 4000:
                output = output[:4000] + "\n…（输出被截断）"
            return ActionResult(
                completed.returncode == 0,
                output or f"命令结束，退出码 {completed.returncode}",
                {"returncode": completed.returncode},
            )
        except subprocess.TimeoutExpired:
            return ActionResult(False, f"命令超时（>{timeout} 秒）已终止")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"命令执行失败：{exc}")

    def wait(self, seconds: float) -> ActionResult:
        seconds = max(0.0, min(10.0, float(seconds)))
        # 分片等待，这样停止请求能及时生效
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop.is_set():
                return ActionResult(False, "已被用户中断")
            time.sleep(min(0.1, max(0.0, deadline - time.time())))
        return ActionResult(True, f"等待了 {seconds:.1f} 秒")
