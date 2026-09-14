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
    subprocess.run(["taskkill", "/F", "/IM", "PawPet.exe"],
                   capture_output=True, text=True)
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

    # 用隔离的数据目录，绝不碰真实数据
    sandbox = ROOT / ".cache" / "packtest-home"
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PAWPET_HOME"] = str(sandbox)
    env["PAWPET_DEBUG"] = "1"
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

    check("进程还活着（没有闪退）", alive,
          f"退出码 {process.poll()}" if not alive else f"存活 {elapsed:.0f}s")

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
