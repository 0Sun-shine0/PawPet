"""确认 venv 里运行时 sys.executable 指向哪，以及自启命令是否正确。

这一项很关键：venv 的 pythonw.exe 是个「重定向器」，它会再拉起真正的
解释器。如果 sys.executable 报的是基础 Python，那么「重启」和「开机自启」
就会用到一个没装 PySide6 的解释器，程序再也起不来。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    print(f"sys.executable      = {sys.executable}")
    print(f"sys._base_executable= {getattr(sys, '_base_executable', '(无)')}")
    print(f"sys.prefix          = {sys.prefix}")
    print(f"sys.base_prefix     = {sys.base_prefix}")
    in_venv = sys.prefix != sys.base_prefix
    print(f"在虚拟环境里        = {in_venv}")

    from pawpet import win32

    command = win32._autostart_command()
    print(f"\n自启命令行          = {command}")

    exe = Path(sys.executable)
    has_venv = ".venv" in str(exe)
    print(f"sys.executable 在 .venv 里 = {has_venv}")

    pythonw = exe.with_name("pythonw.exe")
    print(f"同目录 pythonw.exe 存在    = {pythonw.exists()}")

    # 关键验证：用这条命令解释器能不能 import PySide6
    import subprocess

    probe = subprocess.run(
        [sys.executable, "-c", "import PySide6, sys; print('PySide6 OK', sys.executable)"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    print(f"\n用 sys.executable 导入 PySide6：rc={probe.returncode}")
    print(f"  stdout: {probe.stdout.strip()}")
    if probe.stderr.strip():
        print(f"  stderr: {probe.stderr.strip()[:200]}")

    ok = has_venv and pythonw.exists() and probe.returncode == 0
    print(f"\n结论：{'正确，重启和自启都会用到带 PySide6 的 .venv 解释器' if ok else '有问题，需要修正'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
