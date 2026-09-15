r"""探测 Windows UI Automation 的可用通道。

UIA 是 COM 接口，Python 里访问它有几种方式，成本和能力差别很大：

  1. comtypes —— 标准做法，纯 Python，能直接生成 COM 包装。最省事。
  2. uiautomation —— 第三方封装，依赖 comtypes。
  3. 裸 ctypes 手写 COM vtable —— 零依赖，但要手算 vtable 偏移，易错。
  4. pywin32 的 win32com —— 能跑 COM，但对 UIA 这种接口不友好。

这个脚本把每种都试一遍，报告哪条路走得通，并实测能不能读到真实的控件树。

用法：
    .venv\Scripts\python.exe tools/uia_probe.py
"""

from __future__ import annotations

import ctypes
import importlib
import sys
from ctypes import wintypes

ROOT = r"D:\pet"
sys.path.insert(0, ROOT)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def check_comtypes() -> tuple[bool, str]:
    """comtypes 是最省事的通道，先看它在不在。"""
    try:
        import comtypes  # noqa: F401
        from comtypes import client  # noqa: F401

        version = getattr(comtypes, "__version__", "?")
        return True, version
    except ImportError as exc:
        return False, str(exc)


def check_uiautomation() -> tuple[bool, str]:
    try:
        import uiautomation  # noqa: F401

        return True, getattr(uiautomation, "VERSION", "?")
    except ImportError as exc:
        return False, str(exc)


def check_uia_core() -> tuple[bool, str]:
    """UIAutomationCore.dll 存在吗，能不能 CoCreateInstance。"""
    try:
        core = ctypes.WinDLL("UIAutomationCore.dll")
    except OSError as exc:
        return False, f"加载 DLL 失败：{exc}"

    # CUIAutomation 的 CLSID / IID
    import uuid

    clsid = uuid.UUID("{ff48dba4-60ef-4201-aa87-54103eef594e}")
    iid = uuid.UUID("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def to_guid(value: uuid.UUID) -> GUID:
        return GUID(
            value.time_low, value.time_mid, value.time_hi_version,
            (ctypes.c_ubyte * 8)(*value.bytes[8:]),
        )

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, 2)  # APARTMENTTHREADED

    ptr = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(to_guid(clsid)), None, 1,
        ctypes.byref(to_guid(iid)), ctypes.byref(ptr),
    )
    if hr != 0:
        return False, f"CoCreateInstance 失败，HRESULT=0x{hr & 0xFFFFFFFF:08X}"

    # 能创建出来就说明裸 ctypes 路线可行（后面还要手写 vtable 调用）
    return True, f"UIAutomationCore 可用（接口指针 {ptr.value:#x}）"


def probe_comtypes_uia() -> tuple[bool, str, int]:
    """用 comtypes 真的读一次桌面控件树，看能拿到多少元素。"""
    try:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
    except Exception as exc:  # noqa: BLE001
        return False, f"生成 UIA 包装失败：{type(exc).__name__}: {exc}", 0

    try:
        automation = comtypes.client.CreateObject(
            UIA.CUIAutomation, interface=UIA.IUIAutomation
        )
        root = automation.GetRootElement()
        name = root.CurrentName
        rect = root.CurrentBoundingRectangle
        children = root.FindAll(
            UIA.TreeScope_Children, automation.CreateTrueCondition()
        )
        count = children.Length
        return True, f"根元素 name={name!r} rect=({rect.left},{rect.top})", count
    except Exception as exc:  # noqa: BLE001
        return False, f"读取控件树失败：{type(exc).__name__}: {exc}", 0


def probe_ctypes_uia() -> tuple[bool, str]:
    """裸 ctypes：手写 vtable 调用来拿根元素的名字。

    IUIAutomation 的 vtable 顺序（继承自 IUnknown 之后）：
        3: CompareElements
        4: CompareRuntimeIds
        5: GetRootElement
        ...
    IUIAutomationElement 的 vtable：
        3: SetFocus
        4: GetRuntimeId
        5: FindFirst
        6: FindAll
        7: FindFirstBuildCache
        8: FindAllBuildCache
        9: BuildUpdatedCache
       10: GetCurrentPropertyValue
       11: GetCurrentPropertyValueEx
       12: GetCachedPropertyValue
        ...
      以及 get_CurrentName 等属性访问器

    这里只需要证明「能调用成功」，所以只调 GetRootElement 并检查返回。
    """
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def to_guid(value: uuid.UUID) -> GUID:
        return GUID(
            value.time_low, value.time_mid, value.time_hi_version,
            (ctypes.c_ubyte * 8)(*value.bytes[8:]),
        )

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
    ]

    clsid = to_guid(uuid.UUID("{ff48dba4-60ef-4201-aa87-54103eef594e}"))
    iid = to_guid(uuid.UUID("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"))

    ptr = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)
    )
    if hr != 0:
        return False, f"创建实例失败 0x{hr & 0xFFFFFFFF:08X}"

    # vtable 是「指针数组」，第 0 项指向 QueryInterface
    vtable = ctypes.cast(
        ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    # 取第 5 个方法（索引 5）= GetRootElement
    get_root = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    )(vtable[5])

    element = ctypes.c_void_p()
    hr2 = get_root(ptr, ctypes.byref(element))
    if hr2 != 0:
        return False, f"GetRootElement 失败 0x{hr2 & 0xFFFFFFFF:08X}"

    return True, f"vtable 调用成功，根元素指针 {element.value:#x}"


def main() -> int:
    print("Windows UI Automation 通道探测")

    section("1. Python 库")
    has_comtypes, ct_detail = check_comtypes()
    print(f"  comtypes     : {'可用 ' + ct_detail if has_comtypes else '缺失（' + ct_detail + '）'}")
    has_uiauto, ua_detail = check_uiautomation()
    print(f"  uiautomation : {'可用 ' + ua_detail if has_uiauto else '缺失'}")

    section("2. UIAutomationCore.dll")
    core_ok, core_detail = check_uia_core()
    print(f"  {'[ok]' if core_ok else '[XX]'} {core_detail}")

    section("3. 裸 ctypes 调用 vtable")
    if core_ok:
        ct_ok, ct_msg = probe_ctypes_uia()
        print(f"  {'[ok]' if ct_ok else '[XX]'} {ct_msg}")
    else:
        print("  跳过（DLL 不可用）")
        ct_ok = False

    section("4. comtypes 读控件树")
    element_count = 0
    if has_comtypes:
        ok, message, element_count = probe_comtypes_uia()
        print(f"  {'[ok]' if ok else '[XX]'} {message}")
        if ok:
            print(f"       顶层子元素：{element_count} 个")
    else:
        print("  跳过（comtypes 未安装）")

    section("5. 结论")
    if has_comtypes and element_count > 0:
        print("  推荐：comtypes 路线 —— 能直接读控件树，代码最简洁")
        return 0
    if ct_ok:
        print("  可用：裸 ctypes 路线 —— 零依赖，但需要手写 vtable 调用")
        print("  建议：先装 comtypes（体积很小），装不上再走 ctypes")
        return 0
    if core_ok:
        print("  DLL 在，但都读不到控件树。可能需要在交互式桌面会话里运行。")
        return 1
    print("  UIA 不可用。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
