"""验证打包出来的 exe 真的能跑。

这一步不能省。打包后最常见的问题（缺 QML 插件、缺 DLL、路径写死）
在打包阶段完全看不出来，只有启动才知道。

做法：
  1. 用一个临时的、隔离的数据目录（设置 PAWPET_HOME），绝不碰真实数据。
  2. 启动 exe，等它把窗口建起来。
  3. 检查：
     - 进程活着（没闪退）
     - 数据文件被创建了（说明存储层通了）
     - QML 加载成功（窗口真的存在 —— 靠进程有没有立刻退出 + 数据文件判断）
  4. 关掉它，检查退出码。

用法：
    .venv\\Scripts\\python.exe tools\\packtest.py dist\\PawPet\\PawPet.exe
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def kill_leftovers() -> None:
    # **必须显式给 encoding。**
    #
    # 不给的话 Python 按系统区域设置解码（中文 Windows 上是 GBK），而
    # taskkill 的输出跟着控制台代码页走 —— 控制台是 UTF-8 时就解不开，
    # 解码线程抛 UnicodeDecodeError。进程照样跑完，但**脚本退出码变成 1**，
    # 构建脚本就会把一次成功的打包报成失败。实测踩过。
    # 输出本来也不看，errors="replace" 保证不会再崩。
    subprocess.run(["taskkill", "/F", "/IM", "PawPet.exe"],
                   capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
    time.sleep(1.0)


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "PawPet" / "PawPet.exe"
    if not exe.is_absolute():
        exe = (ROOT / exe).resolve()

    print("打包产物冒烟测试")
    print(f"目标：{exe}")

    check("exe 存在", exe.exists())
    if not exe.exists():
        return 1

    size = exe.stat().st_size
    check("exe 体积合理（>1MB）", size > 1024 * 1024, f"实际 {size / 1e6:.1f} MB")

    app_dir = exe.parent
    check("有 Qt 运行库", (app_dir / "_internal").exists() or (app_dir / "PySide6").exists(),
          str([p.name for p in app_dir.iterdir()][:8]))

    qml_files = list(app_dir.rglob("Main.qml"))
    check("QML 资源已打进包里", len(qml_files) > 0,
          f"找到 {len(qml_files)} 个 Main.qml")

    # ---------------------------------------------------- 内置 MCP server
    #
    # **这一节的存在本身就是教训。** 随包发的 pawkit（8 个工具）是 .py
    # 脚本、不在 import 图里，PyInstaller 的静态分析看不到它 ——
    # 曾经有很长一段时间它压根没被打进包里，于是装出来的小爪显示
    # 「没有内置的 server：pawkit」，MCP 那套东西一次都没生效过。
    #
    # 模块自检也抓不到：`--selfcheck` 只 import python 模块，
    # 而 pawkit 是被**当脚本 spawn** 的。
    print("\n内置 MCP server…")
    mcp_dir = None
    for candidate in app_dir.rglob("pawkit.py"):
        mcp_dir = candidate.parent
        break
    check("pawkit.py 打进包了", mcp_dir is not None,
          "没打进去的话「外部工具」那一栏会显示找不到 server")
    check("没把 __pycache__ 塞给用户",
          not any(mcp_dir.rglob("*.pyc")) if mcp_dir else False,
          "陈旧字节码不该随包发（实测混进过一个 3.14 的 .pyc）")

    # 真的当 MCP server 跑一次：喂一条 initialize，看有没有合法响应。
    #
    # 打包后是 GUI 子系统程序（console=False），PyInstaller 会把
    # sys.stdout/stdin 设成 None —— 而 MCP 走的就是 stdio。没有
    # run_pawpet.py 里的 _ensure_stdio() 兜底，这里会连上但一个工具都没有。
    if exe.exists():
        request = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "packtest", "version": "1"}},
        })
        try:
            served = subprocess.run(
                [str(exe), "--mcp-server", "pawkit"],
                input=request, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=120, cwd=str(app_dir))
            handshake = served.stdout or ""
            check("exe 能当 MCP server 用（窗口程序没有 stdout，靠兜底补上）",
                  '"protocolVersion"' in handshake, handshake[:120])
            check("握手报告了工具能力",
                  '"tools"' in handshake and '"pawkit"' in handshake,
                  handshake[:120])
        except subprocess.TimeoutExpired:
            check("exe 能当 MCP server 用", False, "握手超时（可能卡在读 stdin）")

    # ---------------------------------------------------------- 模块自检
    #
    # **这一步不能省。** PyInstaller 靠静态分析找模块，而项目里有一批
    # 「函数内部的 import」（controller 的 `from .providers import ...`、
    # backend 的 `from . import theme`）。这类导入没被收进包里的话，
    # 打包阶段完全看不出来，要等用户点到那个功能才炸。
    #
    # `PawPet.exe --selfcheck` 会把关键模块真的 import 一遍并逐个打印。
    print("\n模块自检（--selfcheck）…")
    probe_env = dict(os.environ)
    # 让 exe 按 UTF-8 输出，免得中文标签被按系统编码写出来、这边按 UTF-8 读成乱码。
    # （断言本身用的是 ASCII 标记，这里只是为了让日志好看。）
    probe_env["PYTHONIOENCODING"] = "utf-8"
    try:
        probe = subprocess.run(
            [str(exe), "--selfcheck"],
            cwd=str(app_dir), env=probe_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        probe_out = (probe.stdout or "") + (probe.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        probe = None
        probe_out = f"跑不起来：{exc}"

    if probe_out.strip():
        print("--- 自检输出 ---")
        print(probe_out.strip()[:3000])
        print("--- 输出结束 ---\n")

    check("自检进程正常退出", probe is not None and probe.returncode == 0,
          f"退出码 {getattr(probe, 'returncode', '?')}")
    # 认 ASCII 标记而不是中文：见 run_pawpet.selfcheck 里的说明
    check("关键模块全部就位（没有 [XX]）", "[XX]" not in probe_out,
          "有模块没被打进包里 —— 见上面的 [XX] 行")
    check("自检明确报告通过", "SELFCHECK OK" in probe_out,
          "没看到 SELFCHECK OK 标记")
    check("没有失败标记", "SELFCHECK FAILED" not in probe_out)

    # 用隔离的数据目录，绝不碰真实数据
    sandbox = ROOT / ".cache" / "packtest-home"
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PAWPET_HOME"] = str(sandbox)
    env["PAWPET_DEBUG"] = "1"
    # 用独立的单实例命名空间。
    #
    # 不然会跟**用户正在跑的那份小爪**撞上：新实例检测到已有实例就
    # 静默退出（退出码 0、什么也不打印），测试看起来像「exe 启动失败」。
    # 我就在这上面绕过一次弯路 —— 打出来的包其实是好的。
    env["PAWPET_INSTANCE_SUFFIX"] = "packtest"
    env.pop("OPENAI_API_KEY", None)

    kill_leftovers()

    print("\n启动 exe…")
    started = time.time()
    process = subprocess.Popen(
        [str(exe)],
        cwd=str(app_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # 给它足够时间完成启动（首次启动要解包、初始化 Qt）
    time.sleep(12)
    elapsed = time.time() - started
    alive = process.poll() is None
    exit_code = process.poll()

    check("进程还活着（没有闪退）", alive,
          f"退出码 {exit_code}" if not alive else f"存活 {elapsed:.0f}s")

    # 快速退出且退出码 0、又没写数据 —— 典型是撞上了「已有实例」。
    # 这种情况要**说清楚**，不要让它看起来像 exe 坏了。
    if not alive and exit_code == 0:
        hint = ("启动后立刻以 0 退出，通常是单实例互斥体被占："
                "可能还有一个没关干净的小爪在跑。"
                "本测试已用 PAWPET_INSTANCE_SUFFIX=packtest 隔离，"
                "若仍如此，检查是否有 taskkill 没清掉的 PawPet.exe。")
        print(f"  [提示] {hint}")

    # 数据文件出现在沙箱目录里 => 配置层、存储层、路径解析都通了
    data_candidates = list(sandbox.rglob("pet_data.json"))
    check("数据文件落在了隔离目录里", len(data_candidates) > 0,
          f"沙箱内容：{[p.name for p in sandbox.rglob('*')][:10]}")

    # 抓一段输出，看有没有 QML 报错
    if alive:
        process.terminate()
        try:
            out, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            out, _ = process.communicate()
    else:
        out, _ = process.communicate()

    output = out or ""
    if output.strip():
        print("\n--- 程序输出 ---")
        print(output.strip()[:2000])
        print("--- 输出结束 ---\n")

    lowered = output.lower()
    check("没有 QML 加载失败", "qml 加载失败" not in lowered and "failed to load component" not in lowered)
    check("没有缺模块错误", "modulenotfounderror" not in lowered and "no module named" not in lowered)
    check("没有缺 DLL 错误", "dll load failed" not in lowered)

    kill_leftovers()
    shutil.rmtree(sandbox, ignore_errors=True)

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
