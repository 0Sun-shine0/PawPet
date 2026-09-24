"""一键打包：生成 exe，再（可选）编译成安装包。

用法：
    .venv\\Scripts\\python.exe tools\\build.py              # 只打 exe
    .venv\\Scripts\\python.exe tools\\build.py --installer  # 再打安装包
    .venv\\Scripts\\python.exe tools\\build.py --zip        # 再打绿色版 zip

产物：
    dist\\PawPet\\                       解压即用的绿色版目录
    dist\\小爪助手-<版本>-安装包.exe      安装程序（需要 Inno Setup）
    dist\\小爪助手-<版本>-绿色版.zip      绿色版压缩包

打包前会做的事：
  * 生成 build\\pawpet.ico
  * 检查 QML 文件齐全
  * 跑一遍 selftest，逻辑不通就不打包
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from console import configure_utf8
from buildmeta import write_manifest

configure_utf8()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pawpet.version import APP_VERSION as VERSION

VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe"
PYINSTALLER = VENV / "Scripts" / "pyinstaller.exe"
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
BUILD_INFO = BUILD_DIR / "build_info.json"
SPEC = BUILD_DIR / "pawpet.spec"
SETUP_SPEC = BUILD_DIR / "setup.spec"


def log(message: str) -> None:
    print(message, flush=True)


def step(title: str) -> None:
    log("")
    log(f"=== {title} ===")


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def sync_version_file() -> str:
    """把仓库里 version.json 的版本号对齐到代码里的 VERSION。

    **为什么要在打包时自动做。** 发版时最典型的失误是改了
    `pawpet/config.py` 的 APP_VERSION 却忘了改 `version.json` ——
    后果是新版本发出去了、老用户的「检查更新」却永远不提示。
    而且它**不报错**：客户端拿到版本号、比一下、发现不新、静默返回。
    这种静默失效最难发现，索性让机器来保证。

    只改 version 字段，url 和 note 原样保留 —— note 是发布说明，
    是给人写的，不该被脚本抹掉。
    """
    path = ROOT / "version.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        log(f"  [!!] version.json 读不出来，按空的重建")
        data = {}
    if not isinstance(data, dict):
        data = {}

    previous = str(data.get("version") or "")
    if previous == VERSION:
        return ""

    data["version"] = VERSION
    # 缺失时补上默认值，不动已经写好的
    data.setdefault("url", "https://github.com/0Sun-shine0/PawPet/releases/latest")
    data.setdefault("note", "")

    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return f"  [XX] version.json 写不进去（{exc}）—— 检查更新会失效，记得手动改"

    if previous:
        return f"  [ok] version.json：{previous} → {VERSION}（记得连同它一起提交）"
    return f"  [ok] version.json：新建，版本 {VERSION}"


def write_build_info() -> str:
    """写入随包清单，防止同版本旧包被误当成最新构建。"""
    try:
        manifest = write_manifest(BUILD_INFO, ROOT, VERSION)
    except OSError as exc:
        return f"  [XX] 构建清单写不进去（{exc}）"
    return (f"  [ok] 构建清单：{manifest['build_id']} · "
            f"源码 {manifest['source_fingerprint']} · "
            f"{manifest['source_file_count']} 个文件")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> int:
    """跑一个子进程并把输出直接透传出来。"""
    log(f"  $ {' '.join(str(c) for c in cmd)}")
    started = time.time()
    try:
        completed = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd or ROOT),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log(f"  !! 超时（>{timeout}s）")
        return 1
    elapsed = time.time() - started
    log(f"  -> 退出码 {completed.returncode}，用时 {elapsed:.1f}s")
    return completed.returncode


def check_prerequisites() -> list[str]:
    problems: list[str] = []

    if not PYTHON.exists():
        problems.append(f"找不到虚拟环境解释器：{PYTHON}")
    if not PYINSTALLER.exists():
        problems.append(
            "没装 PyInstaller。执行：\n"
            "      .venv\\Scripts\\python.exe -m pip install pyinstaller"
        )
    if not SPEC.exists():
        problems.append(f"找不到打包配置：{SPEC}")
    if not SETUP_SPEC.exists():
        problems.append(f"找不到安装程序配置：{SETUP_SPEC}")
    if not (BUILD_DIR / "setup_ui.py").exists():
        problems.append("找不到安装界面代码：build/setup_ui.py")

    qml_dir = ROOT / "pawpet" / "qml" / "PawPet"
    if not qml_dir.exists():
        problems.append(f"找不到 QML 目录：{qml_dir}")
    else:
        qml_count = len(list(qml_dir.rglob("*.qml")))
        if qml_count < 15:
            problems.append(f"QML 文件数量异常偏少：{qml_count} 个")

    return problems


def clean_stale_dist() -> list[str]:
    """删掉 dist 里**过期的**安装包/压缩包。

    为什么需要：命名规则改过几次，结果 dist 里同时躺着
    「小爪助手-安装程序.exe」和旧的「小爪助手-v2.1.0-安装程序.exe」——
    大小一样、时间不同，用户很可能把旧的那个发出去。

    只删 dist 根目录下、名字以「小爪助手-」开头、但不是本次要产出的那几个。
    """
    keep = {
        f"小爪助手-{VERSION}-绿色版.zip",
        "小爪助手-安装程序.exe",
    }
    removed: list[str] = []
    if not DIST_DIR.exists():
        return removed
    for item in DIST_DIR.iterdir():
        if not item.is_file():
            continue
        if not item.name.startswith("小爪助手-"):
            continue
        if item.name in keep:
            continue
        try:
            item.unlink()
            removed.append(item.name)
        except OSError:
            pass
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="打包小爪助手")
    parser.add_argument("--installer", action="store_true",
                        help="额外生成安装程序（推荐，用于分发给别人）")
    parser.add_argument("--zip", action="store_true", help="额外生成绿色版 zip")
    parser.add_argument("--skip-tests", action="store_true", help="跳过打包前的自测")
    parser.add_argument("--keep-build", action="store_true", help="保留临时目录")
    args = parser.parse_args()

    log("小爪助手 打包")
    log(f"项目目录：{ROOT}")
    log(f"版本    ：{VERSION}")

    stale = clean_stale_dist()
    if stale:
        step("0. 清理过期产物")
        for name in stale:
            log(f"  已删除旧产物：{name}")

    step("0.5 同步版本号文件")
    log(sync_version_file() or "  [ok] version.json 已是最新，无需改动")

    step("1. 环境检查")
    problems = check_prerequisites()
    if problems:
        for item in problems:
            log(f"  [XX] {item}")
        return 1
    log("  [ok] 环境齐备")

    if not args.skip_tests:
        step("2. 打包前自测")
        # 跑**全套**而不是只跑 selftest。
        #
        # 只跑 selftest 的话，AI 模块（记忆/自动学习/失败自纠/批量确认/
        # 文件安全策略）出问题不会被发现 —— 而那些恰恰是最容易悄悄坏掉、
        # 打出发给别人又不会立刻暴露的部分。
        #
        # **清单和「按需拉应用」都从 regress.py 拿，不在这里再写一份。**
        #
        # 以前这里有一份 24 个的硬编码清单，而实际回归用的是 33 个 ——
        # 差的 9 个正好是 QML/界面那批。也就是说打包前自测**发现不了
        # QML 语法错误**（实测踩过：一个 rgba 写法炸掉 Pet.qml，
        # 而那 9 个里有 4 个会红）。清单只留一份，就不会再漂。
        #
        # 同理，`uia_test` 那几条要按标题找「小爪」窗口，应用没开着就报
        # 「找不到小爪窗口」—— 打包前通常正把应用关掉，于是必然红。
        # 实测踩到：整套自测跑到 uia_test 中止打包，而同一份代码在
        # regress.py 里是绿的（那边会自己拉应用）。
        # AppInstance 直接复用，别再写第二份。
        try:
            sys.path.insert(0, str(ROOT / "tools"))
            from regress import NEEDS_APP, SUITES, AppInstance  # noqa: PLC0415

            suites = [(label, script) for _group, label, script in SUITES]
        except Exception as exc:  # noqa: BLE001
            log(f"  [XX] 读不到套件清单（tools/regress.py）：{exc}")
            return 1

        failures: list[str] = []
        app_instance = AppInstance()
        try:
            for label, script in suites:
                path = ROOT / "tools" / script
                if not path.exists():
                    failures.append(f"{label}：找不到 {script}")
                    continue
                # 需要窗口的套件先确保应用在跑
                if script in NEEDS_APP:
                    note = app_instance.ensure()
                    log(f"       （{label} 需要小爪在运行：{note}）")
                code = run([PYTHON, path], timeout=600)
                if code != 0:
                    failures.append(f"{label}（{script}）")
        finally:
            app_instance.stop()
        if failures:
            log("")
            log("  [XX] 以下自测没通过，中止打包：")
            for item in failures:
                log(f"       · {item}")
            log("       （--skip-tests 可以跳过，但别拿没测过的包去分发）")
            return 1
        log(f"  [ok] {len(suites)} 套自测全部通过")
    else:
        log("\n=== 2. 打包前自测（已跳过）===")

    step("3. 生成图标")
    code = run([PYTHON, ROOT / "tools" / "make_icon.py"])
    if code != 0:
        log("  !! 图标生成失败，会用默认图标继续")

    step("4. PyInstaller 打包")
    # 清掉上一次的产物，避免旧文件混进来
    for stale in (DIST_DIR / "PawPet", BUILD_DIR / "PawPet"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    build_info_status = write_build_info()
    log(build_info_status)
    if build_info_status.startswith("  [XX]"):
        return 1

    cmd = [PYINSTALLER, "--noconfirm", "--clean", "--distpath", DIST_DIR,
           "--workpath", BUILD_DIR / "work", SPEC]
    code = run(cmd, cwd=ROOT)
    if code != 0:
        log("  [XX] PyInstaller 失败")
        return 1

    app_dir = DIST_DIR / "PawPet"
    exe = app_dir / "PawPet.exe"
    if not exe.exists():
        log(f"  [XX] 没有生成 {exe}")
        return 1

    size = dir_size(app_dir)
    log(f"  [ok] 产物：{app_dir}")
    log(f"       体积：{human(size)}")
    log(f"       文件：{len(list(app_dir.rglob('*')))} 个")

    # 打包后必须真的跑一下 —— 缺 QML 插件、缺 DLL 这类问题
    # 在打包阶段是看不出来的，只有启动才知道
    step("5. 冒烟测试打出来的 exe")
    code = run([PYTHON, ROOT / "tools" / "packtest.py", exe], timeout=180)
    if code != 0:
        log("  [XX] 打出来的 exe 跑不起来，先别发布")
        return 1

    if not args.keep_build:
        shutil.rmtree(BUILD_DIR / "work", ignore_errors=True)

    # ------------------------------------------------------------ 绿色版
    if args.zip:
        step("6. 打包绿色版 zip")
        zip_path = DIST_DIR / f"小爪助手-{VERSION}-绿色版.zip"
        if zip_path.exists():
            zip_path.unlink()
        count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for item in sorted(app_dir.rglob("*")):
                if not item.is_file():
                    continue
                if any(part in ("__pycache__", ".cache") for part in item.parts):
                    continue
                bundle.write(item, Path("小爪助手") / item.relative_to(app_dir))
                count += 1
            # 附上说明，别人解压就知道怎么用
            for extra, name in ((ROOT / "build" / "使用说明.md", "使用说明.md"),):
                if extra.exists():
                    bundle.write(extra, f"小爪助手/{name}")
                    count += 1
        log(f"  [ok] 绿色版：{zip_path}")
        log(f"       {count} 个文件，{human(zip_path.stat().st_size)}")

    # ------------------------------------------------------------ 安装包
    if args.installer:
        step("7. 生成安装程序")
        code = run([PYTHON, ROOT / "tools" / "make_payload.py"], timeout=900)
        if code != 0:
            log("  [XX] 压缩程序本体失败")
            return 1

        code = run([
            PYINSTALLER, "--noconfirm", "--clean",
            "--distpath", DIST_DIR,
            "--workpath", BUILD_DIR / "setupwork",
            BUILD_DIR / "setup.spec",
        ], timeout=900)
        if code != 0:
            log("  [XX] 安装程序打包失败")
            return 1

        setup = DIST_DIR / "小爪助手-安装程序.exe"
        if setup.exists():
            log(f"  [ok] 安装程序：{setup}")
            log(f"       体积：{human(setup.stat().st_size)}")

    # ------------------------------------------------------------ 验收
    if args.installer:
        step("8. 安装流程端到端测试")
        code = run([PYTHON, ROOT / "tools" / "setuptest.py"], timeout=900)
        if code != 0:
            log("  [XX] 安装流程有问题，先别分发")
            return 1

    if not args.keep_build:
        shutil.rmtree(BUILD_DIR / "work", ignore_errors=True)
        shutil.rmtree(BUILD_DIR / "setupwork", ignore_errors=True)

    # ------------------------------------------------------------ 汇总
    step("完成")
    log(f"  exe 目录   : {app_dir}")

    setup = DIST_DIR / "小爪助手-安装程序.exe"
    if setup.exists():
        log(f"  安装包     : {setup}")
        log(f"               {human(setup.stat().st_size)}")
        log("")
        log("  把这个文件发给别人，双击即可安装。")
        log("  它会装到 用户目录\\Programs\\PawPet，数据放在 %APPDATA%\\PawPet。")
        log("  安装向导里可以选目录、桌面快捷方式、开机自启。")
        log("  卸载走 设置→应用，或开始菜单里的卸载项，不会删用户数据。")
    elif not args.installer:
        log("")
        log("  要生成安装包，加 --installer：")
        log("      .venv\\Scripts\\python.exe tools\\build.py --installer")

    zip_path = DIST_DIR / f"小爪助手-{VERSION}-绿色版.zip"
    if zip_path.exists():
        log(f"  绿色版     : {zip_path}")
        log(f"               {human(zip_path.stat().st_size)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
