"""下载 PySide6 运行所需的 wheel。

本机 pip 下载大 wheel 时会在写文件阶段抛 PermissionError（小包正常），
所以绕开 pip 的下载器：直接按 URL 拉取，再让 pip 安装本地文件。
带断点续传、多镜像回退和读超时，避免卡死。
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(r"D:\pet\.cache\dl")
OUT.mkdir(parents=True, exist_ok=True)

# 要下载的包。PySide6 那两个是运行时依赖，PyInstaller 是打包工具，
# altgraph/pefile/pywin32-ctypes 是 PyInstaller 的依赖。
PACKAGES = [
    "shiboken6",
    "PySide6-Essentials",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "pefile",
    "pywin32-ctypes",
    "packaging",
]
TAG = "win_amd64"
API = "https://pypi.org/pypi/{name}/json"
MIRROR_HOSTS = [
    ("https://files.pythonhosted.org", "官方源"),
    ("https://pypi.tuna.tsinghua.edu.cn", "清华镜像"),
    ("https://mirrors.aliyun.com/pypi", "阿里云镜像"),
]
CHUNK = 512 * 1024
READ_TIMEOUT = 45


def log(message: str) -> None:
    print(message, flush=True)


def fetch_json(name: str) -> dict:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(API.format(name=name), headers={"User-Agent": "pawpet-setup/1.0"})
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_win_wheel(meta: dict) -> dict:
    """挑一个适用于当前解释器的 wheel。

    优先选纯 Python 的 (py3-none-any)，其次选 Windows 平台的，
    都没有就退回任意一个 —— 有些包只发源码包。
    """
    version = meta["info"]["version"]
    urls = [u for u in meta["urls"] if u["packagetype"] == "bdist_wheel"]
    if not urls:
        urls = [u for u in meta["urls"] if u["packagetype"] == "sdist"]
    if not urls:
        raise RuntimeError(f"{meta['info']['name']} 没有可下载的文件")

    def score(item: dict) -> tuple:
        name = item["filename"]
        return (
            0 if "py3-none-any" in name else 3,      # 纯 Python 最优先
            1 if TAG in name else 2,                 # 其次是 win_amd64
            0 if version in name else 1,
        )

    urls.sort(key=score)
    return urls[0]


def source_urls(official: str) -> list[tuple[str, str]]:
    """把官方 URL 映射到各个镜像的等价 URL。

    官方 URL 形如 https://files.pythonhosted.org/packages/xx/yy/....whl
    镜像把同一份文件挂在同样的 /packages/... 路径下，所以直接换主机名即可。
    """
    marker = "files.pythonhosted.org"
    path = official.split(marker, 1)[-1] if marker in official else official
    if not path.startswith("/"):
        path = "/" + path
    return [(official, "官方源")] + [(f"{host}{path}", label) for host, label in MIRROR_HOSTS[1:]]


def download_one(url: str, label: str, dest: Path, expected: int) -> bool:
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have >= expected and expected > 0:
        os.replace(part, dest)
        return True

    ctx = ssl.create_default_context()
    headers = {"User-Agent": "pawpet-setup/1.0"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=READ_TIMEOUT, context=ctx) as resp:
            if have and resp.status != 206:
                have = 0  # 服务器不支持断点续传，从头来
            mode = "ab" if have else "wb"
            total = have + int(resp.headers.get("Content-Length") or 0)
            last_report = 0
            with open(part, mode) as handle:
                while True:
                    try:
                        chunk = resp.read(CHUNK)
                    except (socket.timeout, TimeoutError, OSError):
                        log(f"    读取超时，已下载 {have / 1e6:.1f} MB，稍后重试")
                        return False
                    if not chunk:
                        break
                    handle.write(chunk)
                    have += len(chunk)
                    if have - last_report > 8 * 1024 * 1024:
                        last_report = have
                        log(f"    {label}: {have / 1e6:.1f} / {expected / 1e6:.1f} MB")
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # 已经下完了
            os.replace(part, dest)
            return True
        log(f"    {label} HTTP {exc.code}")
        return False
    except Exception as exc:  # noqa: BLE001
        log(f"    {label} 失败：{exc}")
        return False

    if expected and part.stat().st_size < expected:
        log(f"    大小不符（{part.stat().st_size} < {expected}），保留断点")
        return False
    os.replace(part, dest)
    log(f"    完成 {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return True


def main() -> int:
    # 已经装好的包不用再下（PySide6 有 77MB，重复下载很浪费时间）
    import importlib.metadata as md

    installed: set[str] = set()
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").lower().replace("-", "_")
        if name:
            installed.add(name)

    wheels: list[str] = []
    for name in PACKAGES:
        key = name.lower().replace("-", "_")
        if key in installed:
            version = md.version(name) if key in installed else "?"
            log(f"[{name}] 已安装（{version}），跳过")
            continue

        log(f"[{name}] 查询版本…")
        info = pick_win_wheel(fetch_json(name))
        dest = OUT / info["filename"]
        size = int(info["size"])
        if dest.exists() and dest.stat().st_size == size:
            log(f"  已存在：{dest.name}")
            wheels.append(str(dest))
            continue
        log(f"  {info['filename']}  {size / 1e6:.1f} MB")
        ok = False
        for attempt in range(3):
            for url, label in source_urls(info["url"]):
                log(f"  第 {attempt + 1} 轮 / {label}")
                if download_one(url, label, dest, size):
                    ok = True
                    break
            if ok:
                break
        if not ok:
            log(f"  !! {name} 下载失败")
            return 1
        wheels.append(str(dest))

    log("")
    if wheels:
        log("WHEELS=" + ";".join(wheels))
    else:
        log("所有依赖都已安装，无需下载")
    return 0


if __name__ == "__main__":
    sys.exit(main())
