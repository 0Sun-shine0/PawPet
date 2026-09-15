r"""自开程序实测：UIA 能读到多少控件（不依赖用户当时开着什么）。

自己启动记事本 / 计算器 / 画图，读它们的控件树，然后关掉。
这样结论是确定的，不受「窗口是不是最小化了」影响。

用法：
    .venv\Scripts\python.exe tools\uia_coverage.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai import uia  # noqa: E402


def read_tree(window, timeout: float = 8.0):
    """读一个窗口的控件树。返回 (状态, 控件列表或说明)。

    状态只有三种：ok / empty / error。**不要把出错说成超时** ——
    那会把「写得不对」误判成「这个程序不支持」，掩盖真问题。
    """
    t0 = time.time()
    ok, result = uia.call_with_timeout(
        lambda: uia.get_client().tree(window, max_elements=80), timeout)
    elapsed = time.time() - t0

    if not ok:
        # ok=False 有两种：真的超时（线程还活着），或者调用抛异常
        if elapsed >= timeout - 0.05:
            return "timeout", f"读取超时（{elapsed:.1f}s），该程序不响应辅助功能请求"
        return "error", str(result)

    elements, note = result
    if not elements:
        return "empty", note
    return "ok", (elements, note, elapsed)


def report(label: str, status: str, payload) -> None:
    if status == "ok":
        elements, note, elapsed = payload
        clickable = [e for e in elements if 10000 in e.patterns]
        print(f"  {label:10s} [ok] {len(elements)} 个控件，{len(clickable)} 个可点击"
              f"   （{elapsed:.2f}s）")
        print(f"       {note}")
        for e in elements[:6]:
            print(f"       · {e.summary()[:76]}")
    elif status == "empty":
        print(f"  {label:10s} [--] 窗口在，但读不到控件：{payload}")
    elif status == "timeout":
        print(f"  {label:10s} [!!] {payload}")
    else:
        print(f"  {label:10s} [XX] 调用出错（这是 bug，不是不支持）：{payload}")


def selfopen() -> list[tuple[str, str]]:
    """自己开程序、自己关。返回测试结果列表。"""
    print("=== 自己启动程序来测（最可靠的验证）===\n")

    targets = [
        (["notepad.exe"], "记事本"),
        (["calc.exe"], "计算器"),
        (["mspaint.exe"], "画图"),
    ]

    results: list[tuple[str, str]] = []
    before = {w.handle for w in uia.enum_windows()}
    started: list[subprocess.Popen] = []

    for argv, label in targets:
        if shutil.which(argv[0]) is None:
            print(f"  {label:10s} 这个系统上没有 {argv[0]}")
            continue
        try:
            proc = subprocess.Popen(argv)
            started.append(proc)
        except OSError as exc:
            print(f"  {label:10s} 启动失败：{exc}")
            continue

        # 等新窗口出现（最多 10 秒）
        window = None
        for _ in range(50):
            time.sleep(0.2)
            fresh = [w for w in uia.enum_windows() if w.handle not in before]
            if fresh:
                window = fresh[0]
                break
        if window is None:
            print(f"  {label:10s} 启动了但没等到窗口")
            continue

        before.add(window.handle)
        status, payload = read_tree(window)
        report(label, status, payload)
        results.append((label, status))
        print()

    for proc in started:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    return results


def already_open() -> None:
    print("=== 当前已经开着的程序 ===")
    for keyword, label in [
        ("小爪助手", "小爪(Qt)"),
        ("Chrome", "Chrome"),
        ("Edge", "Edge"),
        ("Visual Studio Code", "VS Code"),
        ("命令提示符", "cmd"),
    ]:
        window = uia.window_by_title_win32(keyword)
        if window is None:
            print(f"  {label:10s} 没找到窗口（没开或已最小化）")
            continue
        status, payload = read_tree(window)
        report(label, status, payload)


def main() -> int:
    print("UIA 真实程序覆盖实测\n")
    print(f"模块状态：{uia.status()}\n")

    results = selfopen()
    already_open()

    print("\n=== 永不卡死复检 ===")
    t0 = time.time()
    windows = uia.enum_windows()
    print(f"  Win32 枚举 {len(windows)} 个窗口，{(time.time()-t0)*1000:.0f}ms")

    t0 = time.time()
    ok, element = uia.call_with_timeout(
        lambda: uia.get_client().element_at(960, 540), 6.0, default=None)
    print(f"  坐标反查 {'成功' if ok else '失败'}，{time.time()-t0:.2f}s"
          + (f" -> {element.summary()[:60]}" if element else ""))

    errors = [label for label, status in results if status == "error"]
    print()
    if errors:
        print(f"[XX] {len(errors)} 个程序读取出错（真 bug）：{errors}")
        return 1
    print("没有出现「调用出错」——读不到的都被正确归类为超时或自绘界面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
