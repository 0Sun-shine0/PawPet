r"""验证 element_from_handle 卡死是不是「跨线程用 COM 对象」造成的。

假设：UiaClient 在主线程创建（STA），却在工作线程里使用。
COM 的 STA 对象跨线程访问需要封送（marshalling），UIA 客户端封送时
会去问被查程序的消息循环 —— 对方不响应就永久卡住。
小爪自己（Qt 消息循环一直转）不卡，记事本/计算器/画图/Edge 卡，
正好符合这个解释。

三条对照实验：
  A. 主线程建 → 主线程用      （同线程）
  B. 主线程建 → 工作线程用    （正式代码的走法，预期卡死）
  C. 工作线程建 → 同一线程用  （同线程，但在非主线程）

用法：
    .venv\Scripts\python.exe tools\uia_apartment.py
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai import uia  # noqa: E402

TIMEOUT = 8.0


def probe(client: "uia.UiaClient", handle: int, label: str) -> str:
    """在**当前线程**直接调用（不加线程包装），由外层控制超时。"""
    element = client.element_from_handle(handle)
    if element is None:
        return "返回 None"
    return f"name={element.name!r} type={element.type_name}"


def run_case(name: str, make_client_here: bool, call_here: bool) -> None:
    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(2.5)
    window = uia.window_by_title_win32("记事本")
    if window is None:
        print(f"  {name}: 没找到记事本窗口")
        proc.terminate()
        return

    box: dict = {}

    def work():
        # 按需要在这条线程里创建客户端
        client = uia.get_client()
        if make_client_here:
            client.close()
            fresh = uia.UiaClient()
            fresh._init()
            box["client"] = fresh
        else:
            box["client"] = client

    if make_client_here:
        # C：整条链路都在工作线程里
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(10)

    if call_here and not make_client_here:
        # A：主线程建 + 主线程用
        client = uia.get_client()
        runner = lambda: probe(client, window.handle, name)  # noqa: E731
    elif make_client_here:
        client = box.get("client")
        if client is None:
            print(f"  {name}: 客户端创建失败")
            proc.terminate()
            return
        # 仍然在同一条工作线程里调
        def caller():
            try:
                box["result"] = probe(client, window.handle, name)
            except Exception as exc:  # noqa: BLE001
                box["result"] = f"{type(exc).__name__}: {exc}"
        thread = threading.Thread(target=caller, daemon=True)
        thread.start()
        thread.join(TIMEOUT)
        if thread.is_alive():
            print(f"  {name}: [卡死] >{TIMEOUT:.0f}s（同线程创建也一样卡）")
        else:
            print(f"  {name}: {box.get('result')}")
        proc.terminate()
        return
    else:
        # B：主线程建 + 工作线程用
        client = uia.get_client()
        runner = lambda: probe(client, window.handle, name)  # noqa: E731

    thread = threading.Thread(target=lambda: box.update(result=runner()), daemon=True)
    thread.start()
    thread.join(TIMEOUT)
    if thread.is_alive():
        print(f"  {name}: [卡死] >{TIMEOUT:.0f}s —— 该调用永久不返回")
    else:
        print(f"  {name}: {box.get('result')}")
    proc.terminate()


def main() -> int:
    # 每个实验单独子进程，避免互相污染
    if "--case" in sys.argv:
        case = sys.argv[sys.argv.index("--case") + 1]
        if case == "A":
            run_case("A 主线程建+主线程用", False, True)
        elif case == "B":
            run_case("B 主线程建+工作线程用", False, False)
        elif case == "C":
            run_case("C 工作线程建+同线程用", True, False)
        return 0

    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    print("element_from_handle 卡死 vs COM 线程模型\n")
    for case, desc in [("A", "主线程建 + 主线程用"),
                       ("B", "主线程建 + 工作线程用（正式代码走法）"),
                       ("C", "工作线程建 + 同一线程用")]:
        print(f"--- 实验 {case}：{desc}")
        try:
            proc = subprocess.run([exe, __file__, "--case", case],
                                  cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
            for line in (proc.stdout or "").strip().splitlines():
                print("   ", line)
            if proc.returncode != 0 and (proc.stderr or "").strip():
                print("    stderr:", proc.stderr.strip().splitlines()[-1])
        except subprocess.TimeoutExpired:
            print("    整个子进程 60 秒没退出")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
