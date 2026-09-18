"""路径、常量与 .env 加载。

三种运行形态，路径规则不同：

1. **开发模式**（从源码跑）：数据放在项目根目录旁边，保持便携。
2. **打包后的绿色版**（exe 旁边有 pet_data.json 或者可写）：
   数据放在 exe 旁边，仍然便携，可以塞进 U 盘带走。
3. **打包后安装到 Program Files**：那里是只读的，数据必须放到
   `%APPDATA%\\PawPet`，否则一保存就报权限错误。

`.env`（存 API Key）同理：安装版写到 %APPDATA%，绿色版写到 exe 旁边。

判断顺序很关键：先看有没有被 PyInstaller 打包（sys.frozen），再看
程序目录能不能写。这样同一个 exe 既能当安装版也能当绿色版。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "小爪助手"
APP_ID = "PawPet"
# **版本号唯一来源。** 别在别处再写一份（`pawpet/__init__.py` 里那个已删，
# `tools/releasetest.py` 有断言禁止它回来）。`tools/build.py` 会把
# `version.json` 同步成这个值，`tools/release.py` 拿它建 tag。
APP_VERSION = "2.2.0"

# 单实例：命名互斥体 + 本地命名管道（第二个实例通过管道让第一个实例显示面板）
MUTEX_NAME = "Local\\PawPet.SingleInstance.v2"
PIPE_NAME = "PawPet.SingleInstance.v2"

PACKAGE_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    """是不是被 PyInstaller 之类的工具打包过。"""
    return bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    """打包后 QML 等只读资源所在的位置（PyInstaller 解包到 _MEIPASS）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return PACKAGE_DIR


def _exe_dir() -> Path:
    """exe（或源码入口）所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return PACKAGE_DIR.parent


def _dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pawpet-write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _roaming_dir() -> Path:
    """安装版的数据目录：%APPDATA%\\PawPet。"""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_ID


def _resolve_data_dir() -> tuple[Path, Path, bool]:
    """返回 (可写数据目录, 只读资源目录, 是否便携模式)。"""
    resources = _bundle_dir()

    # 显式指定数据目录（自动化测试、多份配置并存时用）
    override = os.environ.get("PAWPET_HOME", "").strip()
    if override:
        target = Path(override).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target, resources, True
        except OSError:
            pass    # 指定的目录建不出来就退回默认逻辑

    if not is_frozen():
        # 开发模式：就在项目目录旁边
        root = PACKAGE_DIR.parent
        return root, resources, True

    exe_dir = _exe_dir()

    # 绿色版：exe 旁边已经有一份数据，或者那里可写
    if (exe_dir / "pet_data.json").exists() or _dir_is_writable(exe_dir):
        return exe_dir, resources, True

    # 安装版：Program Files 只读，改用 %APPDATA%
    return _roaming_dir(), resources, False


ROOT, RESOURCE_DIR, PORTABLE = _resolve_data_dir()

DATA_FILE = ROOT / "pet_data.json"
BACKUP_FILE = ROOT / "pet_data.backup.json"
ENV_FILE = ROOT / ".env"
LOG_FILE = ROOT / "pawpet.log"
CACHE_DIR = ROOT / ".cache"
SOUND_DIR = CACHE_DIR / "sounds"
PREVIEW_DIR = CACHE_DIR / "preview"
AI_DIR = CACHE_DIR / "ai"
AUDIT_FILE = CACHE_DIR / "ai-actions.log"
LIVE_PREVIEW = AI_DIR / "screen.png"

# 单实例互斥体的名字后缀。
#
# 默认是 "v2"，也就是「全机器只能开一个小爪」。但自动化测试需要
# 在**用户已经开着**小爪的情况下另起一个实例（否则新实例会静默退出、
# 测试看起来像「启动失败」）。设置这个环境变量就能用独立命名空间，
# 两边互不干扰。顺手也让「同时开两份配置」成为可能。
INSTANCE_SUFFIX = os.environ.get("PAWPET_INSTANCE_SUFFIX", "").strip() or "v2"
INSTANCE_NAME = f"Local\\PawPet.SingleInstance.{INSTANCE_SUFFIX}"

# QML 在打包后位于解包目录里，不能再用 PACKAGE_DIR 推
QML_DIR = RESOURCE_DIR / "pawpet" / "qml"
if not QML_DIR.exists():          # 开发模式下走这里
    QML_DIR = PACKAGE_DIR / "qml"

# 用户自己的界面配色。
#
# 放数据目录而不是资源目录：打包后资源是只读的（解包到 _MEIPASS、
# 退出就删），只有数据目录能持久写。用户（或模型）通过对话改的主题
# 存在这里，升级不会覆盖掉。
THEME_FILE = ROOT / "theme.json"

# 随包发布的 MCP server（目前是 pawkit）。
#
# 打包后在只读资源目录（_MEIPASS/mcp_servers）；开发模式在项目根目录下。
# stdio MCP 需要一个能被 spawn 的子进程，而**打包后没有 python.exe** ——
# 所以「怎么启动它」由 pawpet/ai/mcp.py 的 resolve_command() 统一处理：
# 一律改成用当前解释器（或 PawPet.exe 自己）跑 `--mcp-server <名字>`。
MCP_DIR = RESOURCE_DIR / "mcp_servers"
if not MCP_DIR.exists():          # 开发模式下走这里
    MCP_DIR = PACKAGE_DIR.parent / "mcp_servers"

# MCP 的服务器清单。
#
# 注意它在**数据目录**（ROOT）而不是资源目录：用户要能自己加 server，
# 而资源目录打包后是只读的。首次启动时会从随包的模板里播种一份
# （见 pawpet/ai/mcp.py 的 ensure_config）。
MCP_CONFIG = ROOT / "mcp_servers.json"

DEBUG = bool(os.environ.get("PAWPET_DEBUG"))


def stdio_available() -> bool:
    """打包成窗口程序后 stdout/stderr 是 None，print 会直接崩。

    所有 print 都要先过这个判断（或者用 safe_print）。
    """
    return sys.stdout is not None and sys.stderr is not None


def safe_print(*args, **kwargs) -> None:
    if not stdio_available():
        return
    try:
        print(*args, **kwargs)
    except (OSError, ValueError):
        pass


def load_env(path: Path = ENV_FILE) -> None:
    """把 .env 读进环境变量；已经存在的真实环境变量优先，不会被覆盖。"""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lstrip("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_dirs() -> None:
    for directory in (CACHE_DIR, SOUND_DIR, PREVIEW_DIR, AI_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def save_env_value(key: str, value: str, path: Path = ENV_FILE) -> bool:
    """把某个键写进 .env（没有就追加，有就替换），同时更新当前进程的环境变量。

    API Key 只写进 .env，不写进 pet_data.json —— 后者是会被随手备份、
    甚至可能被分享出去的文件。
    """
    key = (key or "").strip()
    if not key:
        return False

    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []

    replaced = False
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            existing = stripped.partition("=")[0].strip().lstrip("export ").strip()
            if existing == key:
                output.append(f"{key}={value}")
                replaced = True
                continue
        output.append(line)

    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(f"{key}={value}")

    try:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    except OSError:
        return False

    if value:
        os.environ[key] = value
    else:
        os.environ.pop(key, None)
    return True


def read_env_value(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()
