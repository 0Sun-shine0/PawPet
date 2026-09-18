"""检查更新。

**为什么只做「检查 + 提示 + 打开下载页」，不做自动下载替换。**

静默替换自己的 exe 在 Windows 上要处理一串麻烦事：正在运行的文件不能
被覆盖（得先改名重启）、下载中断要能续、包要校验完整性、还要处理
杀软拦截。收益只是省用户点两下。

而真问题是「老用户永远停在装机那个版本，修复推不到他手上」。先解决
这个 —— 一个提示 + 一个按钮就够了。等确实需要了再做静默替换。

三条约束：

1. **绝不阻塞启动。** 走后台线程，主线程照常出宠物。
2. **失败必须静默。** 用户可能在公司内网、可能没网、GitHub 可能不通 ——
   这些都不是他的错，不该在界面上报错。检查失败就当没有新版。
3. **请求要能关掉。** 桌面宠物用户对"程序偷偷联网"是敏感的。设置里
   明确给开关，并且写清楚只发了什么。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# 版本信息的来源，按顺序试。
#
# 第一个是仓库里的一个静态文件 —— 比 Releases API 轻得多（几百字节），
# 而且不要求维护者每次都建 Release。
# 第二个是 GitHub 的 Releases API，作为兜底：raw.githubusercontent.com
# 在国内经常连不上，而 api.github.com 相对稳（tools/github_upload.py
# 的注释里也是这么说的，它正是因为这个才走 API 上传）。
VERSION_SOURCES = (
    "https://raw.githubusercontent.com/0Sun-shine0/PawPet/main/version.json",
    "https://api.github.com/repos/0Sun-shine0/PawPet/releases/latest",
)

DOWNLOAD_PAGE = "https://github.com/0Sun-shine0/PawPet/releases/latest"

# 超时要短。这是个锦上添花的功能，卡住启动或后台线程久了不值得。
TIMEOUT = 6

# 两次检查之间的最小间隔（秒）。默认一天一次 ——
# 频率再高对用户没意义，只是徒增请求。
CHECK_INTERVAL = 24 * 60 * 60

# 伪装成常见浏览器没意义（我们就是程序在请求），但给一个明确的 UA
# 是基本礼貌，也方便对方在日志里认出流量来自哪个版本。
USER_AGENT = "PawPet-UpdateChecker"


def parse_version(text: str) -> tuple[int, ...]:
    """把 "v2.10.0" / "2.1" / "2.1.0-beta.1" 解析成可比较的元组。

    只取数字部分，非数字后缀（-beta / -rc）直接丢掉。

    为什么不用字符串比较：`"2.10.0" < "2.9.0"` 在字符串比较下是 True，
    而那是个要命的误判 —— 用户永远看不到 2.10 这个版本。
    """
    if not text:
        return ()
    # 去掉前缀 v/V，抓出开头的数字串
    match = re.match(r"^[vV]?(\d+(?:\.\d+)*)", str(text).strip())
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return ()


def is_newer(candidate: str, current: str) -> bool:
    """candidate 是不是比 current 新。

    位数不齐时按 0 补齐再比：`2.2` 和 `2.2.0` 视为相同（不是更新）。
    """
    left = parse_version(candidate)
    right = parse_version(current)
    if not left or not right:
        return False

    length = max(len(left), len(right))
    left = left + (0,) * (length - len(left))
    right = right + (0,) * (length - len(right))
    return left > right


def _fetch(url: str) -> dict | None:
    """取一个 JSON。任何失败都返回 None —— 调用方只关心「拿到没有」。"""
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(64 * 1024)      # 上限 64KB，防止被灌爆
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _interpret(data: dict, source: str) -> dict | None:
    """把两种来源的返回统一成 {version, url, note}。"""
    if "tag_name" in data:      # GitHub Releases API 的形状
        version = str(data.get("tag_name") or "")
        url = str(data.get("html_url") or DOWNLOAD_PAGE)
        note = str(data.get("name") or "")
    else:                        # 我们自己的 version.json
        version = str(data.get("version") or "")
        url = str(data.get("url") or DOWNLOAD_PAGE)
        note = str(data.get("note") or "")

    if not parse_version(version):
        return None
    return {"version": version, "url": url, "note": note, "source": source}


def check(current_version: str) -> dict:
    """查有没有新版。

    返回 `{"ok": bool, "found": dict | None}`：

    * `ok=False` —— 网络不通 / 对方挂了 / 返回的内容看不懂。**这不等于
      "没有新版"**，要分开：设置页手动点检查时，告诉用户「没查到」比
      骗他说「已是最新」诚实。
    * `ok=True, found=None` —— 确实是最新版。
    * `ok=True, found={...}` —— 有新版。

    这个函数会阻塞（最坏情况两个源各超时 6 秒），**必须在后台线程里调**。
    """
    for source in VERSION_SOURCES:
        data = _fetch(source)
        if not data:
            continue
        found = _interpret(data, source)
        if found is None:
            # 连上了、也是 JSON，但里面没有可用的版本号 —— 当作源有问题，
            # 换下一个试。
            continue
        if is_newer(found["version"], current_version):
            return {"ok": True, "found": found}
        # 拿到版本号了而且不比当前新 —— 这已经是有效答案，不用再试下一个源。
        # 否则「确实没有新版」这个最常见的情况会白跑一次网络请求。
        return {"ok": True, "found": None}

    return {"ok": False, "found": None}
