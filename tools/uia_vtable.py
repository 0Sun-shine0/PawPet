"""反查 IUIAutomationElement 的 vtable，确定各方法的真实槽位。

之前把 FindAll 猜成槽位 6，结果调用卡死（多半是打到了一个签名不同的
方法上，参数不匹配导致死等）。

既然没有 comtypes 帮忙生成接口定义，就用**特征指纹**来定位：
给槽位传一组「明显不对」的参数，看它返回的 HRESULT。
E_INVALIDARG / E_POINTER 说明签名接近；卡死或崩溃说明不是它。

更可靠的办法：从 IUIAutomationElement 继承链推算。
IUnknown 占 0/1/2，之后按 uiautomationclient.h 的声明顺序排。

这里把两侧都做一遍：
  A. 按 SDK 顺序打印出预期布局，人工核对
  B. 实测几个「安全」的槽位（读属性的那些），验证索引正确
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 按 Windows SDK uiautomationclient.h 里 IUIAutomationElement 的声明顺序。
# 顺序 = vtable 槽位（从 3 开始，前 3 个是 IUnknown）。
# 标注 ★ 的是我们实际要用的。
EXPECTED_ELEMENT = [
    (3, "SetFocus", "★ 聚焦"),
    (4, "GetRuntimeId", ""),
    (5, "FindFirst", "★ 找第一个"),
    (6, "FindAll", "★ 找全部"),
    (7, "FindFirstBuildCache", ""),
    (8, "FindAllBuildCache", ""),
    (9, "BuildUpdatedCache", ""),
    (10, "GetCurrentPropertyValue", "★ 读属性（通用）"),
    (11, "GetCurrentPropertyValueEx", ""),
    (12, "GetCachedPropertyValue", ""),
    (13, "GetCachedPropertyValueEx", ""),
    (14, "GetCurrentPatternAs", ""),
    (15, "GetCachedPatternAs", ""),
    (16, "GetCurrentPattern", "★ 取模式"),
    (17, "GetCachedPattern", ""),
    (18, "get_CachedParent", ""),
    (19, "get_CachedChildren", ""),
    (20, "get_CurrentProcessId", ""),
    (21, "get_CurrentControlType", ""),
    (22, "get_CurrentLocalizedControlType", ""),
    (23, "get_CurrentName", ""),
    (24, "get_CurrentAcceleratorKey", ""),
    (25, "get_CurrentAccessKey", ""),
    (26, "get_CurrentHasKeyboardFocus", ""),
    (27, "get_CurrentIsKeyboardFocusable", ""),
    (28, "get_CurrentIsEnabled", ""),
    (29, "get_CurrentAutomationId", ""),
    (30, "get_CurrentClassName", ""),
    (31, "get_CurrentHelpText", ""),
    (32, "get_CurrentCulture", ""),
    (33, "get_CurrentIsControlElement", ""),
    (34, "get_CurrentIsContentElement", ""),
    (35, "get_CurrentIsPassword", ""),
    (36, "get_CurrentNativeWindowHandle", ""),
    (37, "get_CurrentItemType", ""),
    (38, "get_CurrentIsOffscreen", ""),
    (39, "get_CurrentOrientation", ""),
    (40, "get_CurrentFrameworkId", ""),
    (41, "get_CurrentIsRequiredForForm", ""),
    (42, "get_CurrentItemStatus", ""),
    (43, "get_CurrentBoundingRectangle", "★ 位置"),
    (44, "get_CurrentLabeledBy", ""),
    (45, "get_CurrentAriaRole", ""),
    (46, "get_CurrentAriaProperties", ""),
    (47, "get_CurrentIsDataValidForForm", ""),
    (48, "get_CurrentControllerFor", ""),
    (49, "get_CurrentDescribedBy", ""),
    (50, "get_CurrentFlowsTo", ""),
    (51, "get_CurrentProviderDescription", ""),
]

# IUIAutomationElementArray
EXPECTED_ARRAY = [
    (3, "get_Length", "★ 数组长度"),
    (4, "GetElement", "★ 取第 n 个"),
]


def main() -> int:
    print("IUIAutomationElement vtable 布局核对\n")

    print("=== 按 Windows SDK 声明顺序推算的槽位 ===")
    for slot, name, note in EXPECTED_ELEMENT:
        print(f"  {slot:3d}  {name:34s} {note}")

    print("\n=== IUIAutomationElementArray ===")
    for slot, name, note in EXPECTED_ARRAY:
        print(f"  {slot:3d}  {name:34s} {note}")

    # ------------------------------------------------------------ 实测
    print("\n=== 实测：用属性访问器验证槽位 ===")
    print("（get_* 属性访问器是签名最简单的，用它们对账最安全）")

    from pawpet.ai import uia

    client = uia.UiaClient()
    if not client.available:
        print(f"客户端不可用：{client.error}")
        return 1

    root = client._root_ptr
    print(f"根元素指针 {root:#x}\n")

    # 逐个测属性槽位。这些方法都是 (this, out*) 的形式，签名统一，
    # 不会因为参数不匹配而卡死。
    tests = [
        (uia.EL_CURRENT_NAME, "CurrentName", "BSTR", "壳窗口"),
        (uia.EL_CURRENT_CONTROL_TYPE, "CurrentControlType", "int", "Window(50032)"),
        (uia.EL_CURRENT_PROCESS_ID, "CurrentProcessId", "int", "正整数"),
        (uia.EL_CURRENT_CLASS_NAME, "CurrentClassName", "BSTR", "非空"),
        (uia.EL_CURRENT_AUTOMATION_ID, "CurrentAutomationId", "BSTR", "(可为空)"),
        (uia.EL_CURRENT_IS_ENABLED, "CurrentIsEnabled", "int", "1"),
        (uia.EL_CURRENT_IS_OFFSCREEN, "CurrentIsOffscreen", "int", "0"),
        (uia.EL_CURRENT_NATIVE_HANDLE, "CurrentNativeWindowHandle", "ptr", "非 0"),
        (uia.EL_CURRENT_BOUNDS, "CurrentBoundingRectangle", "RECT", "合理矩形"),
    ]

    results: dict[str, object] = {}
    for slot, name, kind, expect in tests:
        try:
            if kind == "BSTR":
                box = ctypes.c_void_p()
                hr = uia._call(root, slot, ctypes.c_long,
                               (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(box))
                value = uia._bstr_to_str(box.value)
            elif kind == "int":
                box = ctypes.c_int(0)
                hr = uia._call(root, slot, ctypes.c_long,
                               (ctypes.POINTER(ctypes.c_int),), ctypes.byref(box))
                value = box.value
            elif kind == "ptr":
                box = ctypes.c_void_p()
                hr = uia._call(root, slot, ctypes.c_long,
                               (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(box))
                value = box.value
            else:  # RECT
                box = uia.RECT()
                hr = uia._call(root, slot, ctypes.c_long,
                               (ctypes.POINTER(uia.RECT),), ctypes.byref(box))
                value = (box.left, box.top, box.right, box.bottom)

            ok = hr == 0
            results[name] = value
            flag = "ok" if ok else "XX"
            print(f"  [{flag}] 槽位 {slot:3d} {name:28s} = {value!r}   期望 {expect}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [XX] 槽位 {slot:3d} {name:28s} 异常 {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ 结论
    print("\n=== 判断 ===")
    name_ok = isinstance(results.get("CurrentName"), str)
    type_ok = results.get("CurrentControlType") == 50032   # UIA_WindowControlTypeId
    pid_ok = isinstance(results.get("CurrentProcessId"), int) and results["CurrentProcessId"] > 0
    handle_ok = isinstance(results.get("CurrentNativeWindowHandle"), int) and results["CurrentNativeWindowHandle"] > 0

    checks = [
        ("CurrentName 槽位 23 正确", name_ok),
        ("CurrentControlType 槽位 21 正确（根元素应为 50032 Window）", type_ok),
        ("CurrentProcessId 槽位 20 正确", pid_ok),
        ("CurrentNativeWindowHandle 槽位 36 正确", handle_ok),
    ]
    for label, ok in checks:
        print(f"  [{'ok' if ok else 'XX'}] {label}")

    print()
    if all(ok for _l, ok in checks):
        print("属性槽位全部正确 —— 问题只在 FindAll 相关的槽位上。")
    else:
        print("有属性槽位不对，需要重新核对布局。")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
