"""Windows UI Automation（UIA）的零依赖封装。

**为什么需要它**

在此之前，AI 操作屏幕的方式是：截图 → 让模型看图猜坐标 → 移动鼠标点击 →
再截图确认。这条链条里每一步都可能出错，而且慢（一张截图就是几百 KB 和
几千 token）。

UIA 是 Windows 提供的辅助功能接口：它能**直接读到控件**——按钮叫什么名字、
在哪个位置、能不能点、当前是什么值。于是「点击保存按钮」可以变成
「找到名为『保存』的按钮，调用它的 Invoke」。

**收益**

* 准确：不再根据像素猜坐标，控件移动/缩放都不影响
* 快：读一次控件树是毫秒级，不产生 token
* 强：`ValuePattern.SetValue` 能直接设置输入框内容，绕过输入法问题
* 稳：窗口被挡住也能操作（不依赖可见性）

**为什么手写 ctypes 而不用 comtypes**

项目一直保持核心零依赖。comtypes 虽然能自动生成 COM 包装，但它在运行时
生成 Python 模块，PyInstaller 冻结时容易出问题，而且要新增一个依赖。
这里只用 UIA 的很小一个子集，直接用 ctypes 调 vtable 更可控。

vtable 就是 COM 接口的方法表，本质是一个函数指针数组。索引 0/1/2 固定是
IUnknown 的 QueryInterface/AddRef/Release，之后才是接口自己的方法。

**UIA 会挂 —— 这是本模块最重要的设计约束**

实测（tools/uia_isolate.py 可复现）：

    操作                              结果
    element_at(坐标)                  0.4s   OK
    focused()                         0.5s   OK
    FindFirst（在桌面上找窗口）         0.003s OK
    FindAll（在**某个具体窗口**上）     0.5s   OK
    FindAll（在**桌面根**上）           永久卡死
    ElementFromHandle（遍历多个窗口）   卡死
    TreeWalker.GetFirstChildElement    返回垃圾 HRESULT

根因是 UIA 会同步等待每个 provider 响应：只要桌面上有一个无响应的程序，
在根节点上做批量枚举就会永远等下去。

所以本模块的设计原则是：

  1. **枚举用 Win32，细节用 UIA**。EnumWindows + GetWindowText 是纯 Win32，
     永不挂；UIA 只用来读某个具体窗口内部的控件。
  2. **每个 UIA 调用都有超时保护**（见 call_with_timeout）。超时就放弃那次
     查询并返回空结果 —— 绝不让 AI 卡死。
  3. **只在需要时读**，不做全桌面扫描。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass, field
from functools import lru_cache

# ==========================================================================
#  COM 基础设施
# ==========================================================================

IS_WINDOWS = sys.platform == "win32"

COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106：线程已用另一种模型初始化过

S_OK = 0

if IS_WINDOWS:
    _ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    _oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
else:  # pragma: no cover - 这个模块本来就只有 Windows 用
    _ole32 = _oleaut32 = _user32 = None


class UiaError(Exception):
    """UIA 调用失败。"""


class UiaTimeout(UiaError):
    """UIA 调用超时 —— 多半是碰到了无响应的程序。"""


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, text: str) -> "GUID":
        import uuid

        value = uuid.UUID(text)
        return cls(
            value.time_low, value.time_mid, value.time_hi_version,
            (ctypes.c_ubyte * 8)(*value.bytes[8:]),
        )


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


CLSID_CUIAUTOMATION = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
IID_IUIAUTOMATION = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"


# --------------------------------------------------------------------------
#  vtable 调用
# --------------------------------------------------------------------------
@lru_cache(maxsize=256)
def _prototype(restype, argtypes: tuple):
    """按签名缓存函数原型。

    缓存键里**不含槽位索引**：vtable 里的函数指针本身不带类型信息，
    类型是调用方按签名声明的。同一个签名可以复用到不同槽位，
    所以按签名缓存就够了。
    """
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)


def _method_at(ptr: int, index: int) -> int:
    """取接口指针第 index 个槽位的函数地址。"""
    vtable = ctypes.cast(
        ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    if not vtable:
        raise UiaError("接口指针无效")
    address = vtable[index]
    if not address:
        raise UiaError(f"vtable 槽位 {index} 为空")
    return address


def _call(ptr: int, index: int, restype, argtypes: tuple, *args):
    """调用接口的第 index 个方法。"""
    address = _method_at(ptr, index)
    prototype = _prototype(restype, tuple(argtypes))
    return prototype(address)(ptr, *args)


def _release(ptr: int | None) -> None:
    """Release 一个 COM 对象（槽位 2）。"""
    if not ptr:
        return
    try:
        _call(ptr, 2, ctypes.c_ulong, ())
    except Exception:  # noqa: BLE001 - 释放失败不该影响主流程
        pass


def _bstr_to_str(ptr: int | None) -> str:
    """读 BSTR 并释放它。

    BSTR 是 COM 的字符串类型，调用方负责用 SysFreeString 释放，
    不释放就会泄漏。

    注意：UIA 有时返回 null 表示「没有这个属性」，那是正常的，
    不是错误（比如一个没有名字的面板）。
    """
    if not ptr:
        return ""
    try:
        text = ctypes.wstring_at(ptr)
    finally:
        try:
            _oleaut32.SysFreeString(ctypes.c_void_p(ptr))
        except Exception:  # noqa: BLE001
            pass
    return text or ""


def _check(hr: int, what: str) -> None:
    if hr != S_OK:
        raise UiaError(f"{what} 失败（HRESULT=0x{hr & 0xFFFFFFFF:08X}）")


# ==========================================================================
#  超时保护
# ==========================================================================
def call_with_timeout(function, timeout: float = 4.0, default=None):
    """在子线程里执行一个 UIA 调用，超时就放弃。

    **这是本模块最关键的一层。** UIA 会同步等待 provider 响应，桌面上只要
    有一个卡住的程序，某些调用就会永远不返回。而超时的那个线程无法被
    强行终止（COM 调用不在 Python 的字节码层面），所以只能放弃它、让它
    成为 daemon 线程自生自灭。

    代价是极端情况下会累积几个卡住的线程，但换来的是 **AI 永远不会因为
    一个坏窗口而彻底卡死**。这笔交易很划算。

    返回 (成功与否, 结果或错误说明)。
    """
    box: dict = {}

    def runner():
        try:
            box["value"] = function()
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=runner, daemon=True,
                              name="pawpet-uia-call")
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        return False, f"UI Automation 查询超时（>{timeout:.0f}s），已放弃（可能有无响应的程序）"
    if "error" in box:
        return False, box["error"]
    return True, box.get("value")


# ==========================================================================
#  熔断：记住哪些窗口读不动，别每次都白等一遍超时
# ==========================================================================
# 实测：ElementFromHandle 在记事本、计算器、画图、Edge 上会**永久**不返回
# （tools/uia_coverage.py 可复现，8 秒超时都拉不回来）。而这个调用恰好是
# tree() / ui_controls / 按名字点击的唯一入口。
#
# 一次超时至少 12 秒。如果模型对同一个窗口试三次，用户就要白等 36 秒，
# 而且结果注定是失败。所以这里把「已经确认读不动」的窗口记下来，
# 下次直接拒绝，瞬间返回一条能让模型改走坐标点击的说明。
#
# 注意用 (pid, hwnd) 一起做键：句柄会被系统回收复用，只认句柄可能误伤
# 一个新开的、其实读得动的窗口。窗口关掉后惰性清理即可，不用定时器。
_HANG_LOCK = threading.Lock()
_HANGING_WINDOWS: dict[tuple[int, int], str] = {}
_HANG_LIMIT = 200

# 从 HWND 拿窗口元素这一步的超时。它要么几十毫秒返回，要么永久不返回，
# 所以不需要给太长 —— 早一点放弃，模型就能早一点改走坐标。
ENTRY_TIMEOUT = 4.0


def mark_window_hangs(handle: int, process_id: int, title: str = "") -> None:
    """记下这个窗口读不动，下次别再试。"""
    if not handle:
        return
    with _HANG_LOCK:
        if len(_HANGING_WINDOWS) >= _HANG_LIMIT:
            _HANGING_WINDOWS.clear()
        _HANGING_WINDOWS[(int(process_id), int(handle))] = title or ""


def window_is_known_hang(handle: int, process_id: int) -> str:
    """这个窗口是不是已知读不动？是就返回它的标题（空字符串也行），否返回 None。"""
    if not handle:
        return None
    with _HANG_LOCK:
        key = (int(process_id), int(handle))
        if key not in _HANGING_WINDOWS:
            return None
        return _HANGING_WINDOWS[key]


def forget_hanging_windows() -> int:
    """把这些记录清掉（用户重启了程序之后想再试一次时用）。返回清掉的条数。"""
    with _HANG_LOCK:
        count = len(_HANGING_WINDOWS)
        _HANGING_WINDOWS.clear()
    return count


def known_hanging_windows() -> list[tuple[int, int, str]]:
    """当前被标记的窗口，(pid, hwnd, 标题) 列表。"""
    with _HANG_LOCK:
        return [(pid, handle, title) for (pid, handle), title in _HANGING_WINDOWS.items()]


HANG_HINT = (
    "这个窗口的辅助功能接口没有响应，读不到它的控件清单。\n"
    "**不要反复重试**，直接改用坐标：\n"
    "  · ui_element_at(x, y) 确认某个位置上是什么控件\n"
    "  · ui_click 传 x/y 坐标点击，或者先截图目测位置\n"
    "（记事本、计算器、画图、Edge 这类程序实测就是这个情况）"
)


# ==========================================================================
#  UIA 常量
# ==========================================================================

SCOPE_NONE = 0x0
SCOPE_ELEMENT = 0x1
SCOPE_CHILDREN = 0x2
SCOPE_DESCENDANTS = 0x4
SCOPE_PARENT = 0x8
SCOPE_SUBTREE = SCOPE_ELEMENT | SCOPE_CHILDREN | SCOPE_DESCENDANTS

CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar",
    50038: "Separator", 50039: "SemanticZoom", 50040: "AppBar",
}

# 这些类型承载信息，值得报给模型。
# 其余（Pane / Group / Custom / Image 之类）大多是布局容器，
# 全报过去会把上下文塞满，而且对定位没有帮助。
INTERESTING_TYPES = {
    "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem",
    "Menu", "MenuItem", "RadioButton", "Slider", "Spinner", "Tab",
    "TabItem", "Text", "ToolBar", "TreeItem", "SplitButton", "DataItem",
    "Document", "Table", "HeaderItem", "StatusBar", "TitleBar",
    "ScrollBar", "ProgressBar", "Separator",
}

PATTERN_INVOKE = 10000
PATTERN_VALUE = 10002
PATTERN_RANGE_VALUE = 10003
PATTERN_SCROLL = 10004
PATTERN_EXPAND_COLLAPSE = 10005
PATTERN_SELECTION_ITEM = 10010
PATTERN_TEXT = 10014
PATTERN_TOGGLE = 10015
PATTERN_SCROLL_ITEM = 10017
PATTERN_LEGACY = 10018

PATTERN_NAMES = {
    PATTERN_INVOKE: "点击",
    PATTERN_VALUE: "设值",
    PATTERN_RANGE_VALUE: "调值",
    PATTERN_SCROLL: "滚动",
    PATTERN_EXPAND_COLLAPSE: "展开",
    PATTERN_SELECTION_ITEM: "选中",
    PATTERN_TEXT: "读文本",
    PATTERN_TOGGLE: "切换",
    PATTERN_SCROLL_ITEM: "滚入视野",
    PATTERN_LEGACY: "传统访问",
}

PROP_NAME = 30005
PROP_AUTOMATION_ID = 30011
PROP_CLASS_NAME = 30012
PROP_CONTROL_TYPE = 30003
PROP_IS_ENABLED = 30010
PROP_IS_OFFSCREEN = 30022
PROP_NATIVE_WINDOW_HANDLE = 30020
PROP_PROCESS_ID = 30002
PROP_BOUNDING_RECT = 30001

# IUIAutomation 的 vtable 槽位
IA_GET_ROOT = 5
IA_ELEMENT_FROM_HANDLE = 6
IA_ELEMENT_FROM_POINT = 7
IA_GET_FOCUSED = 8
IA_CREATE_TRUE_CONDITION = 21
IA_CREATE_PROPERTY_CONDITION = 23

# IUIAutomationElement 的 vtable 槽位
EL_SET_FOCUS = 3
EL_FIND_FIRST = 5
EL_FIND_ALL = 6
EL_GET_CURRENT_PATTERN = 16
EL_CURRENT_PROCESS_ID = 20
EL_CURRENT_CONTROL_TYPE = 21
EL_CURRENT_NAME = 23
EL_CURRENT_IS_ENABLED = 28
EL_CURRENT_AUTOMATION_ID = 29
EL_CURRENT_CLASS_NAME = 30
EL_CURRENT_NATIVE_HANDLE = 36
EL_CURRENT_IS_OFFSCREEN = 38
EL_CURRENT_BOUNDS = 43

# IUIAutomationElementArray
ARR_LENGTH = 3
ARR_GET_ELEMENT = 4


# ==========================================================================
#  Win32 窗口枚举（不用 UIA，所以永不卡死）
# ==========================================================================
@dataclass
class WindowInfo:
    """一个顶层窗口的基本信息。纯 Win32 读出来的，不碰 UIA。"""

    handle: int
    title: str
    process_id: int = 0
    visible: bool = True

    def as_dict(self) -> dict:
        return {"title": self.title, "handle": self.handle}


def enum_windows(with_title_only: bool = True) -> list[WindowInfo]:
    """用 Win32 枚举顶层窗口。

    刻意不用 UIA 做这件事 —— 实测 EnumWindows 是纯 Win32 调用，
    微秒级返回且永不卡死；而 UIA 在桌面上批量枚举会挂。

    默认只返回**可见且有标题**的窗口，这正是用户心里「打开的窗口」的含义。
    """
    if not IS_WINDOWS:
        return []

    results: list[WindowInfo] = []
    seen: set[str] = set()

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _param):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True

            length = _user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                # 没有标题的窗口大多是内部辅助窗口，用户看不见也不关心
                return True

            buffer = ctypes.create_unicode_buffer(length + 2)
            _user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = (buffer.value or "").strip()
            if not title:
                return True

            # 同一个标题只报一次（有些程序会开多个同标题窗口）
            if title in seen:
                return True
            seen.add(title)

            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            results.append(WindowInfo(
                handle=int(hwnd), title=title,
                process_id=int(pid.value), visible=True,
            ))
        except Exception:  # noqa: BLE001 - 单个窗口出错不影响其它
            pass
        return True

    try:
        _user32.EnumWindows(enum_proc(callback), 0)
    except Exception:  # noqa: BLE001
        pass

    return results


def window_by_title_win32(keyword: str) -> WindowInfo | None:
    """按标题片段找窗口（Win32，不碰 UIA）。"""
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return None
    for item in enum_windows():
        if keyword in item.title.lower():
            return item
    return None


def handle_alive(handle: int) -> bool:
    return bool(IS_WINDOWS and handle and _user32.IsWindow(handle))


# ==========================================================================
#  UIA 元素
# ==========================================================================
_thread_state = threading.local()


def _ensure_com() -> bool:
    """在当前线程初始化 COM。返回 True 表示需要在本线程结束时反初始化。

    同一个线程重复调用是安全的。如果线程已经用别的模型初始化过
    （RPC_E_CHANGED_MODE），那就沿用已有的，不算失败。
    """
    if not IS_WINDOWS:
        return False
    hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    if hr in (S_OK, 1):            # S_OK 或 S_FALSE（已初始化）
        return True
    # RPC_E_CHANGED_MODE：线程已是 STA（Qt 主线程就是），继续用即可
    return False


@dataclass
class ElementInfo:
    """一个控件的快照。

    刻意做成纯数据：UIA 的元素对象是跨进程引用，持有太久会失效，
    而且在别的线程用不安全。读成快照后就只是普通 Python 对象。
    """

    name: str = ""
    control_type: int = 0
    type_name: str = ""
    automation_id: str = ""
    class_name: str = ""
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0
    is_enabled: bool = True
    is_offscreen: bool = False
    native_handle: int = 0
    process_id: int = 0
    depth: int = 0
    patterns: tuple = ()
    # 内部用：拿它来做 invoke / set_value
    _handle: int = field(default=0, repr=False)

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)

    @property
    def label(self) -> str:
        """给人/模型看的一行描述。"""
        text = self.name or self.automation_id or self.class_name or "(无名称)"
        return f"[{self.type_name}] {text}"

    def describe(self) -> str:
        """详细一点的一行，用于日志和排查。"""
        bits = [f"[{self.type_name}]", repr(self.name) if self.name else "(无名)"]
        if self.automation_id:
            bits.append(f"id={self.automation_id}")
        if self.class_name:
            bits.append(f"class={self.class_name}")
        bits.append(f"@({self.left},{self.top},{self.width}x{self.height})")
        if not self.is_enabled:
            bits.append("已禁用")
        if self.is_offscreen:
            bits.append("不可见")
        if self.patterns:
            names = [PATTERN_NAMES.get(p, str(p)) for p in self.patterns]
            bits.append("可用:" + "/".join(names))
        return " ".join(bits)

    def summary(self) -> str:
        """给模型看的一行（简洁版）。"""
        text = self.name or self.automation_id or "(无名)"
        extra = []
        if not self.is_enabled:
            extra.append("禁用")
        if self.patterns:
            names = [PATTERN_NAMES.get(p, "") for p in self.patterns]
            actions = "/".join(n for n in names if n)
            if actions:
                extra.append(actions)
        tail = f"  [{', '.join(extra)}]" if extra else ""
        cx, cy = self.center
        return (f"{self.type_name} {text!r} 中心({cx},{cy}) "
                f"尺寸{self.width}x{self.height}{tail}")

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type_name,
            "id": self.automation_id,
            "class": self.class_name,
            "rect": self.rect,
            "center": self.center,
            "enabled": self.is_enabled,
            "offscreen": self.is_offscreen,
            "patterns": [PATTERN_NAMES.get(p, str(p)) for p in self.patterns],
        }


class UiaClient:
    """UI Automation 的封装。

    每个线程一个实例（COM 要求），用 thread-local 缓存。
    所有对外方法返回的都是 ElementInfo 快照，不是活的 COM 引用。
    """

    def __init__(self) -> None:
        self._available = False
        self._error = ""
        self._root_ptr = 0
        self._automation = 0
        self._needs_uninit = False
        if not IS_WINDOWS:
            self._error = "UI Automation 只在 Windows 上可用"
            return
        try:
            self._init()
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ 生命周期
    def _init(self) -> None:
        self._needs_uninit = _ensure_com()

        clsid = GUID.from_string(CLSID_CUIAUTOMATION)
        iid = GUID.from_string(IID_IUIAUTOMATION)
        ptr = ctypes.c_void_p()

        _ole32.CoCreateInstance.restype = ctypes.c_long
        _ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        hr = _ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)
        )
        _check(hr, "创建 IUIAutomation")
        self._automation = ptr.value or 0

        # 这个 root 引用只用来做 FindFirst（实测 0.003s）。
        # 绝不在它上面做 FindAll —— 那会永久卡死。
        root = ctypes.c_void_p()
        hr = _call(self._automation, IA_GET_ROOT, ctypes.c_long,
                   (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(root))
        _check(hr, "读取根元素")
        self._root_ptr = root.value or 0

    def close(self) -> None:
        """释放 COM 引用。线程结束前调用。"""
        _release(self._root_ptr)
        _release(self._automation)
        self._root_ptr = 0
        self._automation = 0
        if self._needs_uninit:
            try:
                _ole32.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
            self._needs_uninit = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error(self) -> str:
        return self._error

    def status(self) -> str:
        if self._available:
            return "UI Automation 已就绪（可直接读控件名和位置）"
        return f"UI Automation 不可用：{self._error}"

    # ---------------------------------------------------------- 元素读取
    def _read_element(self, ptr: int, depth: int = 0,
                      with_patterns: bool = False) -> ElementInfo:
        """把一个活的元素指针读成快照。"""
        info = ElementInfo(_handle=ptr, depth=depth)

        buf = ctypes.c_void_p()
        if _call(ptr, EL_CURRENT_NAME, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(buf)) == S_OK:
            info.name = _bstr_to_str(buf.value)

        type_value = ctypes.c_int(0)
        if _call(ptr, EL_CURRENT_CONTROL_TYPE, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_int),), ctypes.byref(type_value)) == S_OK:
            info.control_type = type_value.value
            info.type_name = CONTROL_TYPES.get(type_value.value,
                                               f"Unknown({type_value.value})")

        buf2 = ctypes.c_void_p()
        if _call(ptr, EL_CURRENT_AUTOMATION_ID, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(buf2)) == S_OK:
            info.automation_id = _bstr_to_str(buf2.value)

        buf3 = ctypes.c_void_p()
        if _call(ptr, EL_CURRENT_CLASS_NAME, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(buf3)) == S_OK:
            info.class_name = _bstr_to_str(buf3.value)

        rect = RECT()
        if _call(ptr, EL_CURRENT_BOUNDS, ctypes.c_long,
                 (ctypes.POINTER(RECT),), ctypes.byref(rect)) == S_OK:
            info.left, info.top = rect.left, rect.top
            info.right, info.bottom = rect.right, rect.bottom

        flag = ctypes.c_int(0)
        if _call(ptr, EL_CURRENT_IS_ENABLED, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_int),), ctypes.byref(flag)) == S_OK:
            info.is_enabled = bool(flag.value)

        off = ctypes.c_int(0)
        if _call(ptr, EL_CURRENT_IS_OFFSCREEN, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_int),), ctypes.byref(off)) == S_OK:
            info.is_offscreen = bool(off.value)

        pid = ctypes.c_int(0)
        if _call(ptr, EL_CURRENT_PROCESS_ID, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_int),), ctypes.byref(pid)) == S_OK:
            info.process_id = pid.value

        handle = ctypes.c_void_p()
        if _call(ptr, EL_CURRENT_NATIVE_HANDLE, ctypes.c_long,
                 (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(handle)) == S_OK:
            info.native_handle = handle.value or 0

        if with_patterns:
            info.patterns = self._patterns_of(ptr)

        return info

    def _patterns_of(self, ptr: int) -> tuple:
        """探测元素支持哪些模式。

        GetCurrentPattern 对不支持的模式会返回失败，这是正常情况
        （不是错误），所以逐个试。
        """
        found = []
        for pattern_id in (
            PATTERN_INVOKE, PATTERN_VALUE, PATTERN_TOGGLE,
            PATTERN_SELECTION_ITEM, PATTERN_EXPAND_COLLAPSE,
            PATTERN_SCROLL, PATTERN_SCROLL_ITEM, PATTERN_RANGE_VALUE,
        ):
            pattern = ctypes.c_void_p()
            try:
                hr = _call(ptr, EL_GET_CURRENT_PATTERN, ctypes.c_long,
                           (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                           pattern_id, ctypes.byref(pattern))
            except Exception:  # noqa: BLE001
                continue
            if hr == S_OK and pattern.value:
                found.append(pattern_id)
                _release(pattern.value)
        return tuple(found)

    def _true_condition(self) -> int:
        """创建一个「匹配一切」的条件对象，返回指针。

        注意返回的是 int（指针值），不是 ctypes 对象 —— 调用方别再 .value。
        """
        condition = ctypes.c_void_p()
        hr = _call(self._automation, IA_CREATE_TRUE_CONDITION, ctypes.c_long,
                   (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(condition))
        _check(hr, "创建条件")
        return condition.value or 0

    def _find_descendants(self, parent_ptr: int, limit: int = 400) -> list[int]:
        """对**具体元素**取后代元素。

        实测：在具体窗口上 FindAll 是 0.5s；在桌面根上会永久卡死。
        所以调用方必须保证传进来的是具体窗口/控件的指针。
        """
        condition_ptr = self._true_condition()
        array = ctypes.c_void_p()
        try:
            hr = _call(parent_ptr, EL_FIND_ALL, ctypes.c_long,
                       (ctypes.c_int, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_void_p)),
                       SCOPE_DESCENDANTS, ctypes.c_void_p(condition_ptr),
                       ctypes.byref(array))
            if hr != S_OK or not array.value:
                return []

            length = ctypes.c_int(0)
            _call(array.value, ARR_LENGTH, ctypes.c_long,
                  (ctypes.POINTER(ctypes.c_int),), ctypes.byref(length))

            result = []
            for index in range(min(length.value, limit)):
                item = ctypes.c_void_p()
                hr = _call(array.value, ARR_GET_ELEMENT, ctypes.c_long,
                           (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                           index, ctypes.byref(item))
                if hr == S_OK and item.value:
                    result.append(item.value)
            return result
        finally:
            _release(array.value)
            _release(condition_ptr)

    # ------------------------------------------------------------ 对外接口
    def windows(self) -> list[WindowInfo]:
        """枚举顶层窗口。

        **用 Win32 而不是 UIA** —— 实测 UIA 在桌面上批量枚举会永久卡死
        （见模块开头的说明），而 EnumWindows 微秒级返回。
        """
        return enum_windows()

    def find_window(self, keyword: str) -> WindowInfo | None:
        """按标题片段找窗口（Win32 路线）。"""
        return window_by_title_win32(keyword)

    def focused(self) -> ElementInfo | None:
        """当前有键盘焦点的元素。实测 0.5s，可靠。"""
        ptr = ctypes.c_void_p()
        hr = _call(self._automation, IA_GET_FOCUSED, ctypes.c_long,
                   (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(ptr))
        if hr != S_OK or not ptr.value:
            return None
        return self._read_element(ptr.value, with_patterns=True)

    def element_at(self, x: int, y: int) -> ElementInfo | None:
        """某个屏幕坐标下的元素。实测 0.4s，可靠。"""
        point = wintypes.POINT(int(x), int(y))
        ptr = ctypes.c_void_p()
        hr = _call(self._automation, IA_ELEMENT_FROM_POINT, ctypes.c_long,
                   (wintypes.POINT, ctypes.POINTER(ctypes.c_void_p)),
                   point, ctypes.byref(ptr))
        if hr != S_OK or not ptr.value:
            return None
        return self._read_element(ptr.value, with_patterns=True)

    def element_from_handle(self, handle: int) -> ElementInfo | None:
        """从窗口句柄取元素。

        注意：实测对**某些**窗口会卡死（尤其是有无响应子进程的）。
        调用方必须通过 call_with_timeout 使用它。
        """
        ptr = ctypes.c_void_p()
        hr = _call(self._automation, IA_ELEMENT_FROM_HANDLE, ctypes.c_long,
                   (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                   ctypes.c_void_p(handle), ctypes.byref(ptr))
        if hr != S_OK or not ptr.value:
            return None
        return self._read_element(ptr.value, with_patterns=True)

    def window_element(self, keyword: str) -> ElementInfo | None:
        """按标题拿窗口的 UIA 元素（Win32 找句柄 + UIA 包装）。"""
        window = window_by_title_win32(keyword)
        if window is None:
            return None
        return self.element_from_handle(window.handle)

    def tree(self, window: WindowInfo | ElementInfo,
             max_elements: int = 80, interesting_only: bool = True,
             include_offscreen: bool = False) -> tuple[list[ElementInfo], str]:
        """读一个窗口内部的控件。

        **只在具体窗口上调用**（实测 0.5s）。返回 (控件列表, 说明)。

        max_elements 是必要的 —— 浏览器一个页面能有几千个节点，
        不设限会卡死并且撑爆模型的上下文。

        已知读不动的窗口会**立刻**返回，不再白等一次超时（见 mark_window_hangs）。
        """
        if isinstance(window, ElementInfo):
            root_ptr = window._handle
            title = window.name
        else:
            # 熔断：这个窗口之前已经确认读不动了，别再花 12 秒试一次
            known = window_is_known_hang(window.handle, window.process_id)
            if known is not None:
                return [], HANG_HINT

            # element_from_handle 是已知会永久挂起的那个调用，必须带超时。
            # 这一层以前只在调用方（tools.py）加，模块自己调的时候就裸奔了，
            # 所以放到这里来，让 tree() 不管被谁调用都安全。
            ok, element = call_with_timeout(
                lambda: self.element_from_handle(window.handle), ENTRY_TIMEOUT)
            if not ok:
                mark_window_hangs(window.handle, window.process_id, window.title)
                return [], HANG_HINT
            if element is None:
                mark_window_hangs(window.handle, window.process_id, window.title)
                return [], f"读不到窗口「{window.title}」的控件（可能是权限或无响应）"
            root_ptr = element._handle
            title = window.title

        if not root_ptr:
            return [], "窗口元素无效"

        pointers = self._find_descendants(root_ptr, limit=max_elements * 4)
        if not pointers:
            return [], f"窗口「{title}」里没有读到控件"

        collected: list[ElementInfo] = []
        for ptr in pointers:
            if len(collected) >= max_elements:
                break
            try:
                info = self._read_element(ptr, depth=1)
            except Exception:  # noqa: BLE001
                continue

            if not include_offscreen and info.is_offscreen:
                continue
            if interesting_only:
                if info.type_name not in INTERESTING_TYPES:
                    continue
                if not (info.name or info.automation_id):
                    continue
            collected.append(info)

        note = (f"窗口「{title}」读到 {len(collected)} 个可用控件"
                f"（共扫描 {len(pointers)} 个节点）")
        return collected, note

    # ------------------------------------------------------------ 模式操作
    def _pattern(self, element: ElementInfo, pattern_id: int) -> int:
        """取元素的某个模式接口指针（调用方负责释放）。"""
        if not element._handle:
            raise UiaError("这个元素已经失效，请重新读取控件")
        ptr = ctypes.c_void_p()
        hr = _call(element._handle, EL_GET_CURRENT_PATTERN, ctypes.c_long,
                   (ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                   pattern_id, ctypes.byref(ptr))
        if hr != S_OK or not ptr.value:
            raise UiaError(
                f"「{element.name or element.type_name}」不支持这个操作"
                f"（缺少 {PATTERN_NAMES.get(pattern_id, pattern_id)} 模式）"
            )
        return ptr.value

    def invoke(self, element: ElementInfo) -> str:
        """直接触发元素（按钮点击、菜单项选择等），不移动鼠标。"""
        pattern = self._pattern(element, PATTERN_INVOKE)
        try:
            _check(_call(pattern, 3, ctypes.c_long, ()), "Invoke")
            return f"已触发「{element.name or element.type_name}」"
        finally:
            _release(pattern)

    def set_value(self, element: ElementInfo, text: str) -> str:
        """直接设置输入框的内容。

        比逐字符输入可靠得多：不受输入法、焦点、键盘布局影响，
        也不会因为窗口失焦而丢字。
        """
        pattern = self._pattern(element, PATTERN_VALUE)
        try:
            _oleaut32.SysAllocString.restype = ctypes.c_void_p
            _oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
            buf = _oleaut32.SysAllocString(str(text))
            try:
                _check(_call(pattern, 3, ctypes.c_long, (ctypes.c_void_p,),
                             ctypes.c_void_p(buf)), "SetValue")
            finally:
                if buf:
                    _oleaut32.SysFreeString(ctypes.c_void_p(buf))
            return f"已设置「{element.name or element.type_name}」的内容"
        finally:
            _release(pattern)

    def read_value(self, element: ElementInfo) -> str:
        """读输入框当前的内容。"""
        pattern = self._pattern(element, PATTERN_VALUE)
        try:
            buf = ctypes.c_void_p()
            hr = _call(pattern, 4, ctypes.c_long,
                       (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(buf))
            if hr != S_OK:
                return ""
            return _bstr_to_str(buf.value)
        finally:
            _release(pattern)

    def toggle(self, element: ElementInfo) -> str:
        pattern = self._pattern(element, PATTERN_TOGGLE)
        try:
            _check(_call(pattern, 3, ctypes.c_long, ()), "Toggle")
            return f"已切换「{element.name or element.type_name}」"
        finally:
            _release(pattern)

    def select(self, element: ElementInfo) -> str:
        pattern = self._pattern(element, PATTERN_SELECTION_ITEM)
        try:
            _check(_call(pattern, 3, ctypes.c_long, ()), "Select")
            return f"已选中「{element.name or element.type_name}」"
        finally:
            _release(pattern)

    def expand(self, element: ElementInfo) -> str:
        pattern = self._pattern(element, PATTERN_EXPAND_COLLAPSE)
        try:
            _check(_call(pattern, 3, ctypes.c_long, ()), "Expand")
            return f"已展开「{element.name or element.type_name}」"
        finally:
            _release(pattern)

    def collapse(self, element: ElementInfo) -> str:
        pattern = self._pattern(element, PATTERN_EXPAND_COLLAPSE)
        try:
            _check(_call(pattern, 4, ctypes.c_long, ()), "Collapse")
            return f"已折叠「{element.name or element.type_name}」"
        finally:
            _release(pattern)

    def focus(self, element: ElementInfo) -> str:
        if not element._handle:
            raise UiaError("这个元素已经失效")
        _check(_call(element._handle, EL_SET_FOCUS, ctypes.c_long, ()), "SetFocus")
        return f"已聚焦「{element.name or element.type_name}」"

    # ---------------------------------------------------------------- 查找
    def find_in(self, elements: list[ElementInfo], name: str = "",
                control_type: str = "", automation_id: str = "",
                exact: bool = False) -> ElementInfo | None:
        """在一批已读到的元素里查找。

        刻意做成「在已有列表里找」而不是自己去遍历整个桌面 ——
        调用方先用 tree() 读某个窗口，再在这里找，范围可控。
        """
        def matches(info: ElementInfo) -> bool:
            if automation_id and info.automation_id != automation_id:
                return False
            if control_type and info.type_name.lower() != control_type.lower():
                return False
            if name:
                target = info.name
                if exact:
                    if target != name:
                        return False
                elif name.lower() not in target.lower():
                    return False
            return bool(automation_id or control_type or name)

        for info in elements:
            if matches(info):
                return info
        return None


# ==========================================================================
#  线程级入口
# ==========================================================================
def get_client() -> UiaClient:
    """取当前线程的 UIA 客户端（没有就建一个）。"""
    client = getattr(_thread_state, "client", None)
    if client is None:
        client = UiaClient()
        _thread_state.client = client
    return client


def close_client() -> None:
    """释放当前线程的 UIA 客户端。线程结束前应该调一次。"""
    client = getattr(_thread_state, "client", None)
    if client is not None:
        client.close()
        _thread_state.client = None


def available() -> bool:
    try:
        return get_client().available
    except Exception:  # noqa: BLE001
        return False


def status() -> str:
    try:
        return get_client().status()
    except Exception as exc:  # noqa: BLE001
        return f"UI Automation 不可用：{exc}"
