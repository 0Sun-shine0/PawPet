"""平台服务：提示音、通知、单实例管道。

提示音用标准库合成 wav 并缓存到 .cache/sounds，避免引入任何音频依赖；
通知走系统托盘气泡，同时也会让宠物弹一句「对话气泡」（托盘气泡在
专注助手被系统静音时不可靠，气泡是保底且更可爱的做法）。
"""

from __future__ import annotations

import array
import ctypes
import math
import os
import sys
import threading
import wave
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .config import PIPE_NAME, SOUND_DIR

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SAMPLE_RATE = 44100


# --------------------------------------------------------------------------
# 提示音
# --------------------------------------------------------------------------
def _tone(freq: float, seconds: float, volume: float = 0.35) -> array.array:
    frames = int(SAMPLE_RATE * seconds)
    data = array.array("h", bytes(frames * 2))
    attack = max(1, int(SAMPLE_RATE * 0.012))
    release = max(1, int(SAMPLE_RATE * 0.35))
    for i in range(frames):
        # 简单包络，避免爆音
        if i < attack:
            env = i / attack
        elif i > frames - release:
            env = max(0.0, (frames - i) / release)
        else:
            env = 1.0
        value = math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
        # 加一点二次谐波，听起来更像风铃而不是蜂鸣器
        value += 0.25 * math.sin(4 * math.pi * freq * i / SAMPLE_RATE)
        data[i] = int(max(-1.0, min(1.0, value / 1.25)) * env * volume * 32767)
    return data


def _sequence(notes: list[tuple[float, float]], gap: float = 0.02) -> array.array:
    out = array.array("h")
    for freq, seconds in notes:
        out.extend(_tone(freq, seconds))
        out.extend(array.array("h", bytes(int(SAMPLE_RATE * gap) * 2)))
    return out


CHIMES = {
    # 名字 -> 音序
    "focus_done": [(660.0, 0.16), (880.0, 0.16), (1174.7, 0.34)],
    "break_done": [(880.0, 0.14), (659.3, 0.14), (523.3, 0.30)],
    "sit": [(587.3, 0.18), (784.0, 0.30)],
    "reminder": [(784.0, 0.12), (1046.5, 0.12), (784.0, 0.22)],
    "task_done": [(1046.5, 0.10), (1318.5, 0.18)],
}


def _write_wav(path: Path, samples: array.array) -> bool:
    try:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(samples.tobytes())
        return True
    except OSError:
        return False


def ensure_sounds() -> dict[str, Path]:
    """首次运行时合成提示音，之后直接复用缓存。"""
    paths: dict[str, Path] = {}
    try:
        SOUND_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return paths
    for name, notes in CHIMES.items():
        path = SOUND_DIR / f"{name}.wav"
        if not path.exists() or path.stat().st_size < 1024:
            if not _write_wav(path, _sequence(notes)):
                continue
        paths[name] = path
    return paths


class SoundPlayer:
    """异步播放提示音；任何失败都静默忽略（声音不是关键路径）。"""

    def __init__(self) -> None:
        self.enabled = True
        self._files = ensure_sounds()
        self._backend = None
        self._lock = threading.Lock()
        # winsound 是 Windows 自带模块，正常一定能导入；
        # 真导不进来就退回 play() 里的 Qt 方案，不影响主流程。
        try:
            import winsound

            self._backend = winsound
        except ImportError:
            self._backend = None

    @property
    def available(self) -> bool:
        return bool(self._files) and self._backend is not None

    def play(self, name: str = "reminder") -> None:
        if not self.enabled:
            return
        path = self._files.get(name)
        if path is None:
            return
        if self._backend is not None:
            threading.Thread(target=self._play_blocking, args=(path,), daemon=True).start()
            return
        # 用不了 winsound 时退回 Qt 的 QSoundEffect（装了就用，没装就静默忽略）
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect  # type: ignore

            if not hasattr(self, "_qt_effect"):
                effect = QSoundEffect()
                effect.setVolume(0.5)
                self._qt_effect = effect
            self._qt_effect.setSource(QUrl.fromLocalFile(str(path)))
            self._qt_effect.play()
        except Exception:  # noqa: BLE001
            pass

    def _play_blocking(self, path: Path) -> None:
        with self._lock:
            try:
                self._backend.PlaySound(str(path), self._backend.SND_FILENAME | self._backend.SND_NODEFAULT)
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# 通知（信号由主窗口接住 → 托盘气泡 + 宠物对话气泡）
# --------------------------------------------------------------------------
class Notifier(QObject):
    posted = Signal(str, str, str)   # (kind, title, body)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def post(self, kind: str, title: str, body: str = "") -> None:
        self.posted.emit(kind, title, body)


# --------------------------------------------------------------------------
# 单实例管道服务端
# --------------------------------------------------------------------------
PIPE_ACCESS_INBOUND = 0x00000001
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3


class SingleInstanceServer:
    """监听命名管道；第二个实例连上来时触发回调（让已有窗口显示到前台）。"""

    def __init__(self, on_message, name: str = PIPE_NAME) -> None:
        self._on_message = on_message
        self._name = f"\\\\.\\pipe\\{name}"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._serve, name="pawpet-pipe", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        ]
        kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        while not self._stop.is_set():
            handle = kernel32.CreateNamedPipeW(
                self._name, PIPE_ACCESS_INBOUND,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1, 4096, 4096, 0, None,
            )
            if not handle or handle == INVALID_HANDLE_VALUE:
                self._stop.wait(1.0)
                continue
            try:
                kernel32.ConnectNamedPipe(handle, None)
                buffer = ctypes.create_string_buffer(1024)
                read = wintypes.DWORD(0)
                if kernel32.ReadFile(handle, buffer, 1023, ctypes.byref(read), None):
                    payload = buffer.raw[: read.value].decode("utf-8", "replace").strip()
                    if payload:
                        try:
                            self._on_message(payload)
                        except Exception:  # noqa: BLE001
                            pass
            except OSError:
                pass
            finally:
                kernel32.DisconnectNamedPipe(handle)
                kernel32.CloseHandle(handle)

    def stop(self) -> None:
        self._stop.set()


def ping_existing_instance(command: str = "show", name: str = PIPE_NAME) -> bool:
    """第二个实例用：把命令发给已经在跑的实例。成功返回 True。"""
    try:
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        pipe = f"\\\\.\\pipe\\{name}"
        if not kernel32.WaitNamedPipeW(pipe, 1500):
            return False
        handle = kernel32.CreateFileW(pipe, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
        if not handle or handle == INVALID_HANDLE_VALUE:
            return False
        try:
            data = command.encode("utf-8")
            written = wintypes.DWORD(0)
            kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
        finally:
            kernel32.CloseHandle(handle)
        return True
    except OSError:
        return False


def activate_existing_window() -> bool:
    """兜底：直接把已有实例的主窗口提到前台（管道不可用时用）。"""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        handle = user32.FindWindowW(None, "小爪助手")
        if not handle:
            return False
        user32.ShowWindow(handle, 9)          # SW_RESTORE
        user32.SetForegroundWindow(handle)
        return True
    except OSError:
        return False


def app_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def pythonw_path() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def relaunch_hint() -> str:
    return os.path.join(app_dir(), "run_pawpet.pyw")
