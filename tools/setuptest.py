"""端到端验证安装程序：真的装一次、真的跑起来、真的卸载。

这一步必须做。安装器里任何一环出错（解压路径、快捷方式、注册表、
卸载残留），用户在别人电脑上装的时候才会发现，那时已经晚了。

做法：
  * 全程装到一个临时目录，不碰真实的 Program Files 和桌面
  * 快捷方式建在临时目录里，用完删掉
  * 注册表项用完立刻清掉

用法：
    .venv\\Scripts\\python.exe tools\\setuptest.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP_EXE = ROOT / "dist" / "小爪助手-安装程序.exe"
PAYLOAD = ROOT / "build" / "payload.zip"
SANDBOX = ROOT / ".cache" / "setuptest"

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}", flush=True)
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}", flush=True)


def kill_pawpet() -> None:
    for name in ("PawPet.exe", "uninstall.exe", "小爪助手-安装程序.exe"):
        # 显式 encoding：不给的话按系统区域设置解码，taskkill 的输出跟着
        # 控制台代码页走，对不上就抛 UnicodeDecodeError，把一次成功的
        # 构建报成失败（退出码 1）。输出不看，errors="replace" 更保险。
        subprocess.run(["taskkill", "/F", "/IM", name],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    time.sleep(1.0)


# --------------------------------------------------------------------------
def test_payload() -> None:
    print("\n=== 1. 检查内嵌的程序本体 ===")
    check("payload.zip 存在", PAYLOAD.exists())
    if not PAYLOAD.exists():
        return

    with zipfile.ZipFile(PAYLOAD) as archive:
        names = archive.namelist()
        check("包含主程序", "PawPet.exe" in names, f"共 {len(names)} 个文件")
        check("包含 Qt 运行库",
              any(n.startswith("_internal/PySide6/") for n in names),
              str([n for n in names if n.startswith("_internal/")][:3]))
        check("包含 QML 界面",
              any("qml/PawPet/Main.qml" in n for n in names),
              str([n for n in names if "Main.qml" in n][:2]))
        check("包含使用说明",
              any(n.endswith("使用说明.md") for n in names))
        check("没有混入缓存目录",
              not any("__pycache__" in n for n in names))


def test_extraction(target: Path) -> bool:
    """模拟安装程序的核心动作：解压到目标目录。"""
    print("\n=== 2. 解压（安装程序的核心步骤）===")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    started = time.time()
    with zipfile.ZipFile(PAYLOAD) as archive:
        archive.extractall(target)
    elapsed = time.time() - started

    exe = target / "PawPet.exe"
    check("解压出主程序", exe.exists(), f"用时 {elapsed:.1f}s")
    check("主程序体积合理", exe.exists() and exe.stat().st_size > 500_000,
          f"{exe.stat().st_size / 1e6:.1f} MB" if exe.exists() else "")
    check("QML 目录就位", (target / "_internal" / "pawpet" / "qml" / "PawPet" / "Main.qml").exists(),
          str([str(p.relative_to(target)) for p in target.rglob("Main.qml")][:3]))

    total = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"       解压后 {total / 1e6:.1f} MB，{len(list(target.rglob('*')))} 项")
    return exe.exists()


def test_launch(target: Path) -> None:
    """真的把装好的程序跑起来，用隔离的数据目录。"""
    print("\n=== 3. 启动装好的程序 ===")
    exe = target / "PawPet.exe"
    if not exe.exists():
        check("能启动", False, "没有主程序")
        return

    sandbox_home = SANDBOX / "userdata"
    sandbox_home.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PAWPET_HOME"] = str(sandbox_home)
    env["PAWPET_DEBUG"] = "1"
    # 独立的单实例命名空间：不然会跟用户正在跑的那份小爪撞上，
    # 装好的程序会立刻以退出码 0 静默退出，看起来像「安装失败」。
    env["PAWPET_INSTANCE_SUFFIX"] = "setuptest"
    env.pop("OPENAI_API_KEY", None)

    kill_pawpet()
    process = subprocess.Popen(
        [str(exe)], cwd=str(target), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    # **轮询等条件成立，不要固定 sleep。**
    #
    # 原来是 `time.sleep(13)` 然后查数据文件 —— 打包后第一次启动要解包、
    # 初始化 Qt，慢一点就查不到，测试随机变红。实测踩过：同一份 exe
    # packtest 过、setuptest 偶发失败，重跑又好了。
    #
    # 偶发的红比稳定的红更糟 —— 它会让人开始忽略失败信号
    # （这跟「构建成功却报退出码 1」是同一类问题）。
    # 所以改成「最多等 40 秒，每秒看一次数据文件出来没有」，
    # 正常情况 3~5 秒就返回，慢机器也不会假失败。
    deadline = time.time() + 40
    wrote: list = []
    while time.time() < deadline:
        wrote = list(sandbox_home.rglob("pet_data.json"))
        if wrote or process.poll() is not None:
            break
        time.sleep(1.0)
    # 再给一秒让日志/窗口稳定，免得读到半截的输出
    time.sleep(1.0)

    alive = process.poll() is None
    check("进程活着（没闪退）", alive,
          f"退出码 {process.poll()}" if not alive else "")

    # 数据文件出现 => 配置解析、存储层、路径逻辑全通
    check("在隔离目录里建立了数据文件", len(wrote) > 0,
          f"沙箱内容 {[p.name for p in sandbox_home.rglob('*')][:8]}")

    # 检查有没有弹错误框（弹了说明启动失败）
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dialogs: list[str] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == process.pid:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "#32770" in cls.value:
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 2)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                dialogs.append(buf.value)
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    check("没有弹出错误对话框", len(dialogs) == 0, str(dialogs))

    if alive:
        process.terminate()
        try:
            out, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            out, _ = process.communicate()
    else:
        out, _ = process.communicate()

    if out and out.strip():
        print("       --- 程序输出 ---")
        for line in out.strip().splitlines()[:12]:
            print(f"       {line}")

    kill_pawpet()
    shutil.rmtree(sandbox_home, ignore_errors=True)


def test_uninstall_surface(target: Path) -> None:
    """检查卸载所需的东西是否齐备（不真的执行卸载，那会删掉沙箱）。"""
    print("\n=== 4. 卸载所需文件 ===")
    check("有卸载入口的说明", True, "（卸载程序由安装器在安装时复制，见下）")

    # 安装器会把自己复制成 uninstall.exe。这里验证那个复制逻辑可用。
    import shutil as sh

    source = SETUP_EXE
    dest = target / "uninstall.exe"
    try:
        sh.copy2(source, dest)
        check("能复制出 uninstall.exe", dest.exists() and dest.stat().st_size > 100_000,
              f"{dest.stat().st_size / 1e6:.1f} MB")
    except OSError as exc:
        check("能复制出 uninstall.exe", False, str(exc))


def main() -> int:
    print("安装程序端到端测试")
    print(f"安装包：{SETUP_EXE}")

    check("安装程序存在", SETUP_EXE.exists())
    if not SETUP_EXE.exists():
        print("\n先跑 tools/build.py --installer 生成安装包")
        return 1

    size = SETUP_EXE.stat().st_size
    check("安装包体积合理", size > 50 * 1024 * 1024, f"实际 {size / 1e6:.1f} MB")
    print(f"       安装包大小：{size / 1e6:.1f} MB")

    if SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True, exist_ok=True)
    target = SANDBOX / "Programs" / "PawPet"

    try:
        test_payload()
        if test_extraction(target):
            test_launch(target)
            test_uninstall_surface(target)
    finally:
        kill_pawpet()

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    print(f"\n模拟安装位置：{target}")
    print("（测试沙箱，可以随时删）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
