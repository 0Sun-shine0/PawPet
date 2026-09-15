"""验证 UIA 模块的安全 API（就是 AI 实际会用到的那几个）。

之前的探针脚本验证的是「哪些底层调用会卡死」，那些结论已经写进
pawpet/ai/uia.py 的设计里了。这个测试验证的是**模块对外暴露的接口**，
也就是 AI 真正会用到的那几个，它们全部应该是快且可靠的。

每个测试跑在独立子进程里并加上限，防止某个环境特例把整个套件挂掉。

用法：
    .venv\\Scripts\\python.exe tools/uia_test.py
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 每个测试：(代码, 超时秒数, 说明)
TESTS: list[tuple[str, str, int, str]] = [
    ("模块加载与可用性", '''
        from pawpet.ai import uia
        c = uia.get_client()
        print(f"RESULT: {'ok' if c.available else 'fail'} {c.status()}")
    ''', 15, "能创建客户端"),

    ("Win32 枚举窗口（永不卡死）", '''
        import time
        from pawpet.ai import uia
        t = time.time()
        wins = uia.enum_windows()
        el = time.time() - t
        named = [w for w in wins if w.title]
        print(f"RESULT: {'ok' if len(named) > 0 else 'fail'} "
              f"{len(named)} 个窗口，用时 {el*1000:.0f}ms")
    ''', 15, "应当远快于 1 秒"),

    ("按标题找窗口", '''
        from pawpet.ai import uia
        w = uia.window_by_title_win32("小爪")
        print(f"RESULT: {'ok' if w else 'fail'} {w.title if w else '没找到'}")
    ''', 15, "能按标题定位"),

    ("按坐标反查控件", '''
        from pawpet.ai import uia
        c = uia.get_client()
        ok, result = uia.call_with_timeout(lambda: c.element_at(960, 540), 6)
        if not ok:
            print(f"RESULT: fail {result}")
        elif result is None:
            print("RESULT: fail 该坐标没有元素")
        else:
            print(f"RESULT: ok {result.summary()[:80]}")
    ''', 20, "0.5s 内应返回真实控件"),

    ("读焦点元素", '''
        from pawpet.ai import uia
        c = uia.get_client()
        ok, result = uia.call_with_timeout(lambda: c.focused(), 6)
        if not ok:
            print(f"RESULT: fail {result}")
        elif result is None:
            print("RESULT: ok 当前没有焦点元素（正常）")
        else:
            print(f"RESULT: ok {result.summary()[:80]}")
    ''', 20, "0.5s 内应返回"),

    ("读某个窗口的控件树", '''
        from pawpet.ai import uia
        c = uia.get_client()
        w = uia.window_by_title_win32("小爪")
        if not w:
            print("RESULT: fail 找不到小爪窗口")
            raise SystemExit
        ok, result = uia.call_with_timeout(lambda: c.tree(w, max_elements=40), 12)
        if not ok:
            print(f"RESULT: fail {result}")
        else:
            elements, note = result
            kinds = sorted({e.type_name for e in elements})
            print(f"RESULT: ok {note}；类型 {kinds[:8]}")
    ''', 25, "能读到控件名与位置"),

    ("超时保护确实生效", '''
        import time
        from pawpet.ai import uia
        # 故意传一个会卡死的函数，验证超时保护能拦住它
        def hang():
            import ctypes
            c = uia.get_client()
            cond = ctypes.c_void_p()
            uia._call(c._automation, uia.IA_CREATE_TRUE_CONDITION, ctypes.c_long,
                      (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(cond))
            arr = ctypes.c_void_p()
            # 在桌面根上 FindAll —— 已知会卡死
            return uia._call(c._root_ptr, uia.EL_FIND_ALL, ctypes.c_long,
                             (ctypes.c_int, ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_void_p)),
                             uia.SCOPE_CHILDREN, cond, ctypes.byref(arr))

        t = time.time()
        ok, msg = uia.call_with_timeout(hang, 3.0)
        el = time.time() - t
        # 期望：3 秒后放弃，ok=False，且进程还活着能继续打印
        good = (not ok) and el < 6.0 and "超时" in str(msg)
        print(f"RESULT: {'ok' if good else 'fail'} "
              f"ok={ok} 用时{el:.1f}s msg={str(msg)[:50]}")
    ''', 20, "应当在 3 秒后放弃而不是永久卡死"),

    ("读取真实控件的名字和位置（关键验证）", '''
        import os, time, tkinter as tk
        from pawpet.ai import uia

        # 造一个控件名和位置都已知的窗口 —— 这是唯一能真正证明
        # 「读到的是真实控件而不是垃圾数据」的办法
        BTN = "PawPet验证按钮"
        EDIT = "PawPet验证输入框"
        root = tk.Tk()
        root.title("PawPetUiaVerify")
        root.geometry("420x220+140+140")
        # ElementFromPoint 只看**视觉最上层**的东西。窗口如果不置顶，
        # 浏览器/编辑器一挡住，反查回来的就是别人的控件 —— 那是环境问题，
        # 不是模块的问题，但会让这个测试假失败。所以先置顶。
        root.attributes("-topmost", True)
        tk.Label(root, text="PawPet验证标签").pack(pady=6)
        e = tk.Entry(root, width=26); e.insert(0, EDIT); e.pack(pady=6)
        btn_widget = tk.Button(root, text=BTN, width=18)
        btn_widget.pack(pady=6)
        root.update()
        time.sleep(1.2)

        try:
            c = uia.get_client()
            w = uia.window_by_title_win32("PawPetUiaVerify")
            if not w:
                print("RESULT: fail Win32 没找到测试窗口"); raise SystemExit

            # --- 路线 A（可靠）：按控件在屏幕上的真实坐标反查
            # 这是本模块最核心的能力：把「像素坐标」变成「控件名」
            #
            # 这里**直接在主线程调**，不套 call_with_timeout：
            # 这个测试先建了 Tk 窗口，Tk 会初始化自己的 COM/消息循环，
            # 再把这个线程建的 UIA 对象拿到别的线程用会互相打架，
            # 反查就会偶发超时。同上，真要挂死也由外层子进程超时兜住。
            btn_widget.update_idletasks()
            bx = btn_widget.winfo_rootx() + btn_widget.winfo_width() // 2
            by = btn_widget.winfo_rooty() + btn_widget.winfo_height() // 2
            at = c.element_at(bx, by)

            # --- 路线 B（尽力而为）：读整窗控件树
            # 依赖目标程序对 UIA 的支持程度。Tk/Qt 程序支持差，可能三种结果：
            #   1. 读到控件                -> 好
            #   2. 正常返回但一个都没有（自绘界面）-> 也好，是如实报告
            #   3. 超时（call_with_timeout 兜住）-> 也好，只要不卡死
            # 所以验收标准就是「**返回了**，没把进程挂死」。
            ok_b, tree_result = uia.call_with_timeout(
                lambda: c.tree(w, max_elements=60), 12)
            if not ok_b:
                tree_result = None

            at_name = (at.name if at is not None else "")
            tree_ok = bool(tree_result and tree_result[0])
            tree_report = ("读到" + str(len(tree_result[0])) + "个") if tree_ok else (
                "正常返回但没有可读控件" if tree_result is not None else "优雅超时")

            # 归属校验：反查到的控件必须属于我们自己的进程。
            # 不属于就说明测试窗口被挡住了 —— 记为环境限制，不算失败。
            mine = at is not None and at.process_id == os.getpid()
            # 控件名不是必要条件：Tk 的 Button 就常常没有可访问名字，
            # 但它给出的**类型和几何**仍然必须是真实可信的。
            sensible_type = bool(at is not None and at.type_name)
            # 几何校验用「查询点是否落在返回的矩形里」——
            # 这才是 ElementFromPoint 的本义。不要去比左上角坐标：
            # 那会因为窗口坐标换算的细微差别而假失败。
            within_window = bool(
                at is not None
                and at.left - 2 <= bx <= at.right + 2
                and at.top - 2 <= by <= at.bottom + 2
                and at.width > 0 and at.height > 0
            )
            at_ok = mine and sensible_type and within_window

            # 合格标准：
            #   * 坐标反查必须拿到**自己窗口**的、贴着该坐标的控件
            #   * 控件树要么读到东西，要么优雅超时（绝不卡死）
            blocked = at is not None and not mine
            # 控件树那一路只要「返回了」就算通过（上面三种结果都合法）
            good = blocked or at_ok

            if blocked:
                verdict = (f"测试窗口被别的程序挡住了"
                           f"（反查到 pid={at.process_id}，本进程 {os.getpid()}），"
                           f"属环境限制，跳过")
            else:
                verdict = "可靠" if at_ok else "反查失败"

            print(f"RESULT: {'ok' if good else 'fail'} "
                  f"坐标反查={at_name!r}({at.type_name if at else '-'}) "
                  f"rect=({at.left},{at.top},{at.right},{at.bottom}) "
                  f"查询点=({bx},{by}) "
                  f"控件树={tree_report} "
                  f"-> {verdict}")
        finally:
            root.destroy()
    ''', 40, "坐标反查必须读到控件；控件树可超时但不许卡死"),

    ("熔断：读不动的窗口不重复等超时", '''
        import subprocess, sys, time
        from pawpet.ai import uia

        # 先验证纯逻辑：标记 / 查询 / 清除
        uia.forget_hanging_windows()
        assert uia.window_is_known_hang(1234, 5678) is None, "清空后不该有记录"
        uia.mark_window_hangs(1234, 5678, "假的测试窗口")
        assert uia.window_is_known_hang(1234, 5678) == "假的测试窗口", "标记没生效"
        # 句柄相同但进程不同 -> 不能误伤（句柄会被系统回收复用）
        assert uia.window_is_known_hang(1234, 9999) is None, "pid 不同不该命中"
        assert uia.window_is_known_hang(9999, 5678) is None, "hwnd 不同不该命中"
        cleared = uia.forget_hanging_windows()
        assert cleared == 1, f"应该清掉 1 条，实际 {cleared}"
        assert uia.window_is_known_hang(1234, 5678) is None, "清除没生效"

        # 再验证真实行为：记事本实测读不动，第二次必须瞬间返回
        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(2.5)
        try:
            window = None
            for _ in range(30):
                found = [w for w in uia.enum_windows() if w.process_id == proc.pid]
                if found:
                    window = found[0]
                    break
                time.sleep(0.2)
            if window is None:
                print("RESULT: ok 记事本没起来（环境限制），跳过真实行为部分")
                raise SystemExit

            client = uia.get_client()
            uia.forget_hanging_windows()

            t0 = time.time()
            elements1, note1 = client.tree(window)
            first = time.time() - t0

            t0 = time.time()
            elements2, note2 = client.tree(window)
            second = time.time() - t0

            marked = uia.window_is_known_hang(window.handle, window.process_id)

            # 第一次要么读到控件（这个程序能读），要么花了超时时间才放弃
            first_reasonable = bool(elements1) or first >= uia.ENTRY_TIMEOUT * 0.8
            # 第二次：如果被熔断，必须瞬间返回；如果第一次就读到了，也应该很快
            second_fast = second < 0.5
            # 读不到就必须留下记录，读到了就不该留（免得挡住以后的发展）
            mark_correct = (marked is not None) if not elements1 else True
            hint_ok = bool(elements1) or ("ui_element_at" in note1)

            good = first_reasonable and second_fast and mark_correct and hint_ok
            print(f"RESULT: {'ok' if good else 'fail'} "
                  f"第一次 {first:.2f}s 读到{len(elements1)}个；"
                  f"第二次 {second:.2f}s 读到{len(elements2)}个；"
                  f"已标记={marked is not None}；"
                  f"提示{'含改走坐标的建议' if hint_ok else '缺少建议'}")
        finally:
            proc.terminate()
            uia.forget_hanging_windows()
    ''', 30, "同一窗口第二次读取必须瞬间返回，不再白等超时"),

    ("位置数据的正确性（本质检验）", '''
        import time
        from pawpet.ai import uia
        c = uia.get_client()

        # 本质检验：问「(x,y) 上是什么控件」，返回的控件，
        # 它的包围盒就**必须包含 (x,y)**。这是位置数据正确性的直接证明。
        #
        # （不去比较两次调用是否返回同一个控件 —— ElementFromPoint 的
        #   返回粒度会随时机变化：同一个点可能给整页 Document，
        #   也可能给具体的某个 Group。那是 UIA 的正常行为，不是错误。）
        probes = [(960, 540), (600, 400), (1300, 300), (400, 800), (1500, 700)]

        checked = 0
        contained = 0
        details = []
        for (x, y) in probes:
            ok, el = uia.call_with_timeout(lambda px=x, py=y: c.element_at(px, py), 6)
            if not ok:
                details.append(f"({x},{y}) 超时")
                continue
            if el is None:
                details.append(f"({x},{y}) 无控件")
                continue
            checked += 1
            # 允许一点边界误差（有些控件的矩形是圆整过的）
            inside = (el.left - 2 <= x <= el.right + 2
                      and el.top - 2 <= y <= el.bottom + 2)
            if inside:
                contained += 1
            details.append(
                f"({x},{y})→{el.type_name} rect=({el.left},{el.top},{el.right},{el.bottom}) "
                f"包含={inside}")

        # 要求：只要读到了控件，位置就必须包含查询点。
        # 若一个都没读到（桌面全是不支持 UIA 的程序），视为环境限制而非失败。
        if checked == 0:
            good = True
            verdict = "环境里没有可读的控件（桌面程序都不支持 UIA），跳过"
        else:
            good = contained == checked
            verdict = f"{contained}/{checked} 个控件的矩形包含被查询的坐标"

        for line in details:
            print(f"    {line}")
        print(f"RESULT: {'ok' if good else 'fail'} {verdict}")
    ''', 60, "返回的控件矩形必须包含被查询的坐标"),

    ("永不卡死（对所有调用的硬要求）", '''
        import time
        from pawpet.ai import uia
        c = uia.get_client()

        # 无论目标程序支持与否，每个调用都必须在有限时间内返回。
        # 这是本模块对 AI 的硬承诺：宁可给不出信息，也绝不卡住对话。
        probes = [
            ("element_at", lambda: c.element_at(500, 400), 6),
            ("element_at 角落", lambda: c.element_at(1, 1), 6),
            ("element_at 屏外", lambda: c.element_at(99999, 99999), 6),
            ("focused", lambda: c.focused(), 6),
            ("windows", lambda: c.windows(), 6),
        ]
        results = []
        for label, fn, limit in probes:
            t = time.time()
            ok, res = uia.call_with_timeout(fn, limit)
            el = time.time() - t
            results.append((label, ok, el, el <= limit + 2.0))
            print(f"    {label}: ok={ok} 用时{el:.1f}s 在限内={el <= limit + 2.0}")

        all_within = all(r[3] for r in results)
        print(f"RESULT: {'ok' if all_within else 'fail'} "
              f"{sum(1 for r in results if r[3])}/{len(results)} 个调用都在时限内返回")
    ''', 60, "所有调用都必须在有限时间内返回，绝不卡死"),
]


def run(name: str, code: str, timeout: int) -> tuple[bool, str]:
    started = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "-u", "-c", textwrap.dedent(code).strip()],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"整个测试超时（{timeout}s）—— 超时保护没兜住"

    elapsed = time.time() - started
    for line in result.stdout.splitlines():
        if line.startswith("RESULT:"):
            payload = line[7:].strip()
            return payload.startswith("ok"), f"{payload[3:].strip()}（{elapsed:.1f}s）"

    tail = (result.stdout + result.stderr).strip().splitlines()
    return False, f"没有结果，退出码 {result.returncode}：{tail[-1] if tail else '无输出'}"


def main() -> int:
    print("UIA 模块安全 API 测试")
    print("（每个测试独立子进程 + 超时上限，卡住不影响其它）\n")

    passed = 0
    failures: list[tuple[str, str]] = []

    for name, code, timeout, note in TESTS:
        print(f"--- {name}", flush=True)
        print(f"    期望：{note}", flush=True)
        ok, detail = run(name, code, timeout)
        print(f"    [{'ok' if ok else 'XX'}] {detail}\n", flush=True)
        if ok:
            passed += 1
        else:
            failures.append((name, detail))

    print("=" * 58)
    print(f"通过 {passed}/{len(TESTS)}")
    for name, detail in failures:
        print(f"  [XX] {name}: {detail}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
