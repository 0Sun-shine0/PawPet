"""在独立子进程里跑单个 UIA 测试，卡住就杀 —— 避免一个测试拖垮全部。

背景：`FindAll` 在某些情况下会永久卡住（等一个无响应的 UIA provider）。
如果所有测试都在同一个进程里跑，第一个卡住的就会把整个测试套件挂掉，
后面什么信息都拿不到。

所以每个测试跑在独立子进程里，主控用 timeout 控制，超时就杀掉并继续。

用法：
    .venv\\Scripts\\python.exe tools/uia_isolate.py            # 跑全部
    .venv\\Scripts\\python.exe tools/uia_isolate.py findall_window   # 跑单个
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 每个测试是独立的 Python 代码片段，在子进程里执行。
# 统一约定：成功 print("RESULT: ok ...")，失败 print("RESULT: fail ...")
TESTS: dict[str, tuple[str, int]] = {
    # 名字: (代码, 超时秒数)

    "findall_desktop": ('''
        import ctypes
        from pawpet.ai import uia
        c = uia.UiaClient()
        cond = ctypes.c_void_p()
        uia._call(c._automation, uia.IA_CREATE_TRUE_CONDITION, ctypes.c_long,
                  (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(cond))
        arr = ctypes.c_void_p()
        hr = uia._call(c._root_ptr, uia.EL_FIND_ALL, ctypes.c_long,
                       (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                       uia.SCOPE_CHILDREN, cond, ctypes.byref(arr))
        print(f"RESULT: {'ok' if hr == 0 and arr.value else 'fail'} hr={hr} arr={arr.value}")
    ''', 12),

    "findall_window": ('''
        import ctypes
        from pawpet.ai import uia
        c = uia.UiaClient()
        # 找一个真实窗口：用 FindFirst 从桌面拿一个
        cond = ctypes.c_void_p()
        uia._call(c._automation, uia.IA_CREATE_TRUE_CONDITION, ctypes.c_long,
                  (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(cond))
        win = ctypes.c_void_p()
        uia._call(c._root_ptr, uia.EL_FIND_FIRST, ctypes.c_long,
                  (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                  uia.SCOPE_CHILDREN, cond, ctypes.byref(win))
        if not win.value:
            print("RESULT: fail 没找到窗口"); raise SystemExit
        name = ctypes.c_void_p()
        uia._call(win.value, uia.EL_CURRENT_NAME, ctypes.c_long,
                  (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(name))
        title = uia._bstr_to_str(name.value)
        # 对这个具体窗口做 FindAll
        arr = ctypes.c_void_p()
        hr = uia._call(win.value, uia.EL_FIND_ALL, ctypes.c_long,
                       (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                       uia.SCOPE_DESCENDANTS, cond, ctypes.byref(arr))
        n = 0
        if hr == 0 and arr.value:
            ln = ctypes.c_int(0)
            uia._call(arr.value, uia.ARR_LENGTH, ctypes.c_long,
                      (ctypes.POINTER(ctypes.c_int),), ctypes.byref(ln))
            n = ln.value
        print(f"RESULT: {'ok' if hr == 0 and arr.value else 'fail'} "
              f"窗口={title!r} 子元素={n} hr={hr}")
    ''', 15),

    "findall_condition": ('''
        import ctypes
        from pawpet.ai import uia
        c = uia.UiaClient()
        # 不用 TrueCondition，改成「只找 Window 类型」的属性条件，
        # 看是不是 TrueCondition 导致的
        pid = ctypes.c_int(0)
        cond = ctypes.c_void_p()
        hr_cond = uia._call(c._automation, uia.IA_CREATE_PROPERTY_CONDITION, ctypes.c_long,
                            (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                            uia.PROP_CONTROL_TYPE,
                            ctypes.c_void_p(50032), ctypes.byref(cond))
        arr = ctypes.c_void_p()
        hr = uia._call(c._root_ptr, uia.EL_FIND_ALL, ctypes.c_long,
                       (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                       uia.SCOPE_CHILDREN, cond, ctypes.byref(arr))
        print(f"RESULT: {'ok' if hr == 0 and arr.value else 'fail'} "
              f"cond_hr={hr_cond} hr={hr} arr={arr.value}")
    ''', 12),

    "enumwindows_uia": ('''
        import ctypes
        from ctypes import wintypes
        from pawpet.ai import uia
        # 方案：用 Win32 EnumWindows 拿句柄，再用 ElementFromHandle 转成 UIA 元素
        # 完全绕开桌面根上的 FindAll
        c = uia.UiaClient()
        user32 = ctypes.windll.user32
        handles = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _p):
            if user32.IsWindowVisible(hwnd):
                handles.append(hwnd)
            return True
        user32.EnumWindows(enum_proc(cb), 0)
        named = 0
        for h in handles[:20]:
            el = c.element_from_handle(h)
            if el and el.name:
                named += 1
        print(f"RESULT: ok 可见窗口={len(handles)} 前20个里有名字的={named}")
    ''', 20),

    "element_from_point": ('''
        import ctypes
        from pawpet.ai import uia
        c = uia.UiaClient()
        el = c.element_at(960, 540)
        print(f"RESULT: {'ok' if el else 'fail'} "
              f"{el.describe()[:70] if el else '没取到'}")
    ''', 12),

    "focused_element": ('''
        from pawpet.ai import uia
        c = uia.UiaClient()
        el = c.focused()
        print(f"RESULT: {'ok' if el else 'fail'} "
              f"{el.describe()[:70] if el else '没取到（可能没焦点）'}")
    ''', 12),
}


def run_test(name: str, code: str, timeout: int) -> tuple[bool, str]:
    script = textwrap.dedent(code).strip()
    started = __import__("time").time()
    try:
        result = subprocess.run(
            [sys.executable, "-u", "-c", script],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        elapsed = __import__("time").time() - started
        for line in result.stdout.splitlines():
            if line.startswith("RESULT:"):
                return line[7:].strip().startswith("ok"), f"{line[7:].strip()}（{elapsed:.1f}s）"
        detail = (result.stdout + result.stderr).strip().splitlines()
        return False, f"没有结果输出，退出码 {result.returncode}：{detail[-1] if detail else ''}"
    except subprocess.TimeoutExpired:
        return False, f"卡死，超过 {timeout}s 被强杀"


def main() -> int:
    print("UIA 操作隔离测试（每个测试独立子进程，卡住不影响其他）\n")

    only = sys.argv[1] if len(sys.argv) > 1 else None
    names = [only] if only and only in TESTS else list(TESTS)

    results: list[tuple[str, bool, str]] = []
    for name in names:
        code, timeout = TESTS[name]
        print(f"--- {name} （上限 {timeout}s）", flush=True)
        ok, detail = run_test(name, code, timeout)
        results.append((name, ok, detail))
        print(f"    [{'ok' if ok else 'XX'}] {detail}\n", flush=True)

    print("=" * 58)
    passed = sum(1 for _n, ok, _d in results if ok)
    print(f"通过 {passed}/{len(results)}")
    for name, ok, detail in results:
        if not ok:
            print(f"  [XX] {name}: {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
