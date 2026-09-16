r"""探测 GitHub 连通性，并找出可用的连接方式。

需要判断三件事：
  1. github.com 能不能直连（不走代理）
  2. 死代理 127.0.0.1:7890 之外，有没有活着的代理
  3. HTTPS 到 github.com 的 git 端点能不能通

用法：
    .venv\Scripts\python.exe tools/netcheck.py
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

TARGETS = [
    ("https://github.com", "GitHub 主页"),
    ("https://api.github.com", "GitHub API"),
    ("https://codeload.github.com", "GitHub 代码下载"),
    ("https://objects.githubusercontent.com", "GitHub 资源"),
]

# 常见的代理端口
PROXY_PORTS = [7890, 7897, 10809, 1080, 8080, 10808, 20171, 33210]


def probe_port(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_url(url: str, label: str, proxy: str | None = None,
              timeout: int = 20) -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ctx),
        )
    else:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),          # 显式禁掉代理
            urllib.request.HTTPSHandler(context=ctx),
        )

    started = time.time()
    try:
        request = urllib.request.Request(
            url, method="GET",
            headers={"User-Agent": "pawpet-netcheck/1.0", "Accept": "*/*"},
        )
        with opener.open(request, timeout=timeout) as response:
            response.read(256)
            elapsed = time.time() - started
            return True, f"{response.status}  {elapsed:.2f}s"
    except urllib.error.HTTPError as exc:
        # 有响应就说明连通了，403/404 都算通
        elapsed = time.time() - started
        return True, f"HTTP {exc.code}（能连上）  {elapsed:.2f}s"
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - started
        return False, f"{type(exc).__name__}: {exc}  ({elapsed:.2f}s)"


def main() -> int:
    print("网络连通性探测\n")

    # ---------------------------------------------------------- 1. 本地代理
    print("=== 本地代理端口扫描 ===")
    alive: list[str] = []
    for port in PROXY_PORTS:
        if probe_port("127.0.0.1", port):
            alive.append(f"http://127.0.0.1:{port}")
            print(f"  [活] 127.0.0.1:{port}")
    if not alive:
        print("  （所有常见代理端口都没有监听）")

    print()
    print("=== git 全局代理配置 ===")
    result = subprocess.run(["git", "config", "--global", "--get", "http.proxy"],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    configured = result.stdout.strip()
    print(f"  http.proxy = {configured or '(未设置)'}")
    if configured:
        host_port = configured.replace("http://", "").replace("https://", "")
        if ":" in host_port:
            host, _, port = host_port.partition(":")
            if probe_port(host, int(port)):
                print(f"  -> 这个代理是活的")
            else:
                print(f"  -> [!!] 这个代理没有在监听，会导致所有 git 操作失败")

    # ---------------------------------------------------------- 2. 直连
    print()
    print("=== 直连测试（不走代理）===")
    direct_ok = 0
    for url, label in TARGETS:
        ok, detail = check_url(url, label, proxy=None)
        print(f"  [{'ok' if ok else 'XX'}] {label:16s} {detail}")
        if ok:
            direct_ok += 1

    # ---------------------------------------------------------- 3. 走代理
    if alive:
        print()
        print("=== 走可用代理测试 ===")
        for proxy in alive:
            for url, label in TARGETS[:2]:
                ok, detail = check_url(url, label, proxy=proxy, timeout=25)
                print(f"  [{proxy}] [{'ok' if ok else 'XX'}] {label:16s} {detail}")

    # ---------------------------------------------------------- 结论
    print()
    print("=" * 56)
    if direct_ok == len(TARGETS):
        print("结论：GitHub 直连完全可用 —— 应该把 git 代理配置删掉")
        return 0
    if direct_ok:
        print(f"结论：直连部分可用（{direct_ok}/{len(TARGETS)}）")
        return 0
    if alive:
        print("结论：直连不通，但有活着的代理可用 —— 给 git 配那个代理")
        return 2
    print("结论：直连不通且没有可用代理 —— 需要换网络或开代理")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
