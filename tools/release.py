r"""把打好的包发出去，一步到位。

**为什么需要这个脚本。**

`tools/build.py` 能把包打出来，但它只打到 `dist/` 就结束了 ——
「把这个包送到用户手上」这一段原来是纯手工，而手工有三处会静默漏掉：

1. **忘了提交 `version.json`。** 它在仓库根目录，是客户端检查更新的
   **第一个来源**。不提交就等于这个源一直 404 —— 而客户端对失败是
   **静默**处理的（内网、没网、GitHub 不通，这些都是用户的正常处境，
   不该在界面上报错）。所以「更新检查从来没生效过」这件事，
   本地跑一万次测试也发现不了。
2. **忘了建 Release。** 客户端那个「去下载」按钮指向
   `releases/latest`。一个 Release 都没有的话，用户点过去是 404。
3. **忘了传安装包**，或者传上去的是 `dist/` 里上一版的旧文件。

这三件事没有任何一步会报错。所以这里把它们串成一条命令，
并且在每一步之后**回头验证一遍结果**。

**顺序是有讲究的：先建 Release、传包，最后才提交 `version.json`。**

反过来的话，从提交 version.json 到安装包传完之间有一个几分钟的窗口，
窗口里用户会收到「有新版本」的提示，点「去下载」却是 404 或者旧的包。
先备好货再挂招牌，就不会有这个窗口。

用法：
    .venv\Scripts\python.exe tools\release.py                 # 发版（会确认一次）
    .venv\Scripts\python.exe tools\release.py --dry-run       # 只看计划，什么都不改
    .venv\Scripts\python.exe tools\release.py --note "修了xx"  # 写发布说明
    .venv\Scripts\python.exe tools\release.py --yes           # 不问，直接发

前置条件：`tools/build.py --installer --zip` 已经跑过。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# 凭据和 HTTP 重试直接用上传工具那一套 —— 同一台机器到 api.github.com
# 会偶发断连，重试逻辑已经在那儿了，别抄第二份。
sys.path.insert(0, str(Path(__file__).resolve().parent))

DIST = ROOT / "dist"
VERSION_FILE = ROOT / "version.json"
INSTALLER_NAME = "小爪助手-安装程序.exe"


# ==========================================================================
#  前置检查
# ==========================================================================
def read_local_version() -> str:
    from pawpet.config import APP_VERSION
    return APP_VERSION


def read_version_file() -> dict:
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  [XX] version.json 读不出来：{exc}")
        return {}
    return data if isinstance(data, dict) else {}


def remote_name(local: Path, version: str) -> str:
    """上传到 Release 时用的文件名 —— **必须是纯 ASCII**。

    为什么不能直接用本地文件名（实测踩的坑）：

    `小爪助手-安装程序.exe` 传上去会变成 **`-.exe`** —— 中文全没了。
    用户下载到的就是一个叫 `-.exe` 的文件，完全看不出是什么。

    查证过程：URL 编码本身是**对的**（`urllib.parse.quote` 往返无误、
    发出去是纯 ASCII），问题在 GitHub 侧。做了个受控实验上传小文件：

        测试-中文名.txt      →  -.txt
        测试-手动utf8-q.txt  →  -.utf8-q.txt

    两种编码方式（`quote` 默认 和 `quote(safe="")`）都被剥光，
    所以不是我们编码错了 —— **GitHub 会把 `?name=` 里的非 ASCII
    字符直接丢掉**（是丢弃，不是乱码：`测试` 变成空，`-` 和 `.txt` 保留）。
    换编码技巧绕不过「按字符过滤」这件事。

    于是：本地文件保留中文名（用户在 dist/ 里看着顺眼），
    上传时换成 ASCII 名。顺带这也是更稳的选择 —— 中文文件名在部分
    浏览器和下载工具里会出现乱码或截断，而用户群体里什么环境都有。
    """
    suffix = local.suffix.lower()
    if suffix == ".exe":
        return f"PawPet-Setup-{version}.exe"
    if suffix == ".zip":
        return f"PawPet-Portable-{version}.zip"
    # 兜底：把非 ASCII 字符去掉，至少不会变成空名字
    stem = "".join(ch for ch in local.stem if ord(ch) < 128) or "PawPet"
    return f"{stem}{local.suffix}"


def collect_assets(version: str) -> list[tuple[Path, str, str]]:
    """要上传的文件：安装包（主）、绿色版（备）。

    返回 (本地路径, 上传用的 ASCII 名, 给人看的说明)。

    安装包排第一个 —— GitHub Releases 页面上文件的顺序就是上传顺序，
    用户第一眼要看到的是「双击就能装」的那个。
    """
    candidates = [
        (DIST / INSTALLER_NAME, "双击安装。装到用户目录，不会碰你的数据。"),
        (DIST / f"小爪助手-{version}-绿色版.zip", "不想装？解压即用。"),
    ]
    assets: list[tuple[Path, str, str]] = []
    for path, label in candidates:
        if path.exists() and path.stat().st_size > 0:
            assets.append((path, remote_name(path, version), label))
        else:
            print(f"  [!!] 找不到 {path.name}（跳过）")
    return assets


def check_local(version: str, need_upload: bool) -> tuple[bool, list[tuple[Path, str, str]]]:
    """本地检查。返回 (能不能继续, 要上传的文件)。"""
    ok = True

    data = read_version_file()
    file_version = str(data.get("version") or "")
    if not file_version:
        print("  [!!] version.json 里没有 version 字段 —— 发版时会补上")
    elif file_version != version:
        # 不阻止，只是提醒：build.py 会在打包时对齐它。
        # 如果这里对不上，通常意味着「改了版本号但没重新打包」。
        print(f"  [!!] version.json 写的是 {file_version}，代码里是 {version}")
        print("       多半是改了 APP_VERSION 但没重跑 build.py —— 重跑一次更稳")

    if not need_upload:
        return ok, []

    assets = collect_assets(version)
    if not assets:
        print("  [XX] dist 里没有可发布的产物")
        print(f"       先跑：.venv\\Scripts\\python.exe tools\\build.py --installer --zip")
        return False, []

    for path, upload_as, _label in assets:
        size = path.stat().st_size
        age_days = (time.time() - path.stat().st_mtime) / 86400
        print(f"  [ok] {path.name}  {human(size)}")
        if upload_as != path.name:
            # 让「本地叫什么 / 传上去叫什么」一眼可见 —— 用户下载到的是后者
            print(f"       上传为 {upload_as}（附件名必须 ASCII，见 remote_name）")
        if age_days > 3:
            # 只警告不阻止：产物可能是几天前打的，照样能发。
            # 但「我明明重新打包了，怎么还是旧包」这种困惑值得先提醒一句。
            print(f"       [!!] 这个文件是 {age_days:.1f} 天前的 —— 确认是最新的那一版？")
    return ok, assets


def human(size: float) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# ==========================================================================
#  远程状态
# ==========================================================================
def remote_latest_release(owner: str, repo: str, token: str) -> dict | None:
    """拿最新的 Release。没有 Release 时返回 None。

    这里**不用 `/releases/latest`** —— 对于「一个 Release 都没有」的仓库，
    它返回 404，和「仓库不存在」长得一样，不好区分。列表接口拿空数组
    是明确信号。
    """
    from github_upload import request_with_retry
    status, body = request_with_retry(
        "GET", f"/repos/{owner}/{repo}/releases?per_page=1", token)
    if status == 200 and isinstance(body, list):
        return body[0] if body else None
    print(f"  [!!] 查 Release 列表失败（{status}）：{str(body)[:150]}")
    return None


def version_gate(version: str, remote_tag: str) -> tuple[bool, str]:
    """本地版本能不能发。返回 (放行吗, 给人看的说明)。

    **这是整个发版流程里最该拦的一道。** 版本号没往上走的话，包发得再新，
    老用户的更新检查也不会提示 —— 白忙一场，而且**不报错**。
    那种「我明明发了版，怎么没人收到更新」的问题最难查。

    抽成纯函数是为了能被测试覆盖：这道门只要写错一次，代价是一整个版本。
    """
    from pawpet.update import is_newer

    if not remote_tag:
        return True, "远程还没有任何 Release —— 这会是第一个"
    if not is_newer(version, remote_tag):
        return False, (f"本地版本 {version} 不比远程的 {remote_tag} 新 —— "
                       "先把 pawpet/config.py 的 APP_VERSION 改大")
    if remote_tag.lstrip("vV") == version.lstrip("vV"):
        return False, (f"远程已经有一个 {remote_tag} 了（版本号相同，标签不同）—— "
                       "换个版本号，或者先手工删掉那个 Release")
    return True, f"{remote_tag} → {version}，是个真的新版本"


def check_remote_assets(release_body: dict,
                        assets: list[tuple[Path, str]]) -> tuple[list[str], list[str]]:
    """核对 Release 上的文件和本地是不是一致。

    返回 (问题列表, 对得上的文件名列表)。

    单位是字节、逐个文件核对。上传中断时 GitHub 也可能返回 201，
    但那一刻 asset 的 size 会对不上 —— 这是能验的。

    两个返回值都给出来，而不是让调用方从问题文案里反推名字 ——
    反推要解析自己刚拼的字符串，那种代码迟早会对不上格式。
    """
    problems: list[str] = []
    good: list[str] = []
    remote = {a.get("name"): a.get("size")
              for a in (release_body.get("assets") or [])
              if isinstance(a, dict)}
    for path, upload_as, _label in assets:
        size = path.stat().st_size
        # **按上传名查，不是按本地名。** 两者的区别就是这轮修的那个 bug：
        # 本地叫「小爪助手-安装程序.exe」，上传用的 ASCII 名是
        # 「PawPet-Setup-2.2.0.exe」。用本地名去查永远查不到，
        # 于是回验会一直报「Release 上没有 …」—— 而其实传上去了。
        if upload_as not in remote:
            problems.append(f"Release 上没有 {upload_as}")
        elif remote[upload_as] != size:
            problems.append(f"{upload_as} 大小不一致：本地 {size}，"
                            f"远程 {remote[upload_as]}")
        else:
            good.append(upload_as)
    return problems, good


def check_remote(owner: str, repo: str, token: str,
                 version: str) -> tuple[bool, dict | None]:
    """远程检查。返回 (能不能继续, 已有 Release 或 None)。"""
    latest = remote_latest_release(owner, repo, token)
    tag = str(latest.get("tag_name") or "") if latest else ""
    if latest:
        print(f"  远程最新：{tag}  发布 {str(latest.get('published_at') or '')[:10]}")

    ok, reason = version_gate(version, tag)
    if ok:
        print(f"  [ok] {reason}")
    else:
        print(f"  [XX] {reason}")
    return ok, latest


# ==========================================================================
#  发布
# ==========================================================================
def create_release(owner: str, repo: str, token: str, version: str,
                   body: str, assets: list[tuple[Path, str]]) -> int | None:
    """建 tag + Release。返回 release id。"""
    from github_upload import request_with_retry

    tag = f"v{version}"
    # target_commitish 指到 main：Release 的 tag 打在分支头上。
    # 注意如果本地有没推上去的提交，这个 tag 指向的是远程的旧提交 ——
    # 对用户没影响（他们下载的是 exe），但仓库看起来会落后。
    # 所以发版前先把代码推上去（见 README「发一个版本」）。
    status, body_json = request_with_retry(
        "POST", f"/repos/{owner}/{repo}/releases", token,
        {
            "tag_name": tag,
            "target_commitish": "main",
            "name": f"小爪助手 {version}",
            "body": body,
            "draft": False,
            "prerelease": False,
        },
    )
    if status not in (200, 201):
        detail = body_json.get("message", str(body_json)[:200]) \
            if isinstance(body_json, dict) else str(body_json)[:200]
        print(f"  [XX] 建 Release 失败（{status}）：{detail}")
        return None
    print(f"  [ok] Release {tag}（id={body_json['id']}）")
    return int(body_json["id"])


def asset_url(owner: str, repo: str, release_id: int, name: str) -> str:
    """拼上传地址。

    **主机名必须是 uploads.github.com**，不是 api.github.com —— 这是
    GitHub 的规定，用错了拿到 404，而 404 在文档里不显眼，很容易查半天。
    路径里要带 `?name=`，否则文件在页面上会显示成 "assets"。

    单独抽成函数是为了能被测试钉住 —— 这种拼错了不会本地报错的东西，
    只能靠断言守着。
    """
    return (f"https://uploads.github.com/repos/{owner}/{repo}"
            f"/releases/{release_id}/assets?name={urllib.parse.quote(name)}")


def upload_asset(owner: str, repo: str, token: str, release_id: int,
                 path: Path, upload_as: str, label: str) -> bool:
    """把一个文件传到 Release 上。

    传二进制不能走 `github_upload.request()` —— 那个函数会把 payload
    做 json.dumps，二进制内容过不了。所以这里手工发。

    `upload_as` 是附件在 Release 上的名字，**必须 ASCII**
    （见 remote_name 的说明）。
    """
    from github_upload import GitHubError

    size = path.stat().st_size
    url = asset_url(owner, repo, release_id, upload_as)
    request = urllib.request.Request(url, method="POST", data=path.read_bytes())
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/octet-stream")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "pawpet-releaser/1.0")

    print(f"  上传 {path.name} → {upload_as}（{human(size)}）…")
    started = time.time()
    try:
        # 上百 MB，超时要给足。实测 120MB 在家里宽带上一两分钟。
        with urllib.request.urlopen(request, timeout=1800) as response:
            result = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        print(f"  [XX] 上传失败（{exc.code}）：{detail}")
        return False
    except (urllib.error.URLError, OSError, ValueError, GitHubError) as exc:
        print(f"  [XX] 上传失败：{type(exc).__name__}: {exc}")
        return False

    elapsed = time.time() - started
    # 回头核对大小。上传中断时 GitHub 也可能返回 201，
    # 但那一刻 asset 的 size 会对不上 —— 这是能验的。
    uploaded = result.get("size")
    if uploaded is not None and int(uploaded) != size:
        print(f"  [XX] 大小对不上：本地 {size}，远程 {uploaded}")
        return False

    # **核对名字。** 这是踩过的坑：GitHub 会把非 ASCII 字符剥掉，
    # 「小爪助手-安装程序.exe」变成「-.exe」。上传返回 201、大小也对，
    # 只有名字是错的 —— 不查这一项，用户就会下载到一个叫「-.exe」的文件。
    landed = str(result.get("name") or "")
    if landed and landed != upload_as:
        print(f"  [XX] 附件名被改了：想要 {upload_as!r}，实际 {landed!r}")
        return False

    print(f"  [ok] {landed or upload_as}  ({elapsed:.0f}s)")
    return True


def publish_version_file(owner: str, repo: str, token: str, version: str,
                         note: str) -> bool:
    """最后一步：把 version.json 提交上去。

    用 Contents API 而不是 git push —— 只动这一个文件，不会把本地
    其他没提交的改动（可能是不想发的）顺手带上去。
    """
    from github_upload import request_with_retry

    data = read_version_file()
    data["version"] = version
    if note:
        data["note"] = note
    data.setdefault("url", f"https://github.com/{owner}/{repo}/releases/latest")
    data.setdefault("note", "")

    payload = {
        "message": f"chore: 更新 version.json 到 {version}",
        "content": base64.b64encode(
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ).decode("ascii"),
        "branch": "main",
    }

    # 文件已存在时必须带上它的 sha，否则 GitHub 会拒绝（409）。
    # 第一次发版时它是 404 —— 那就是纯创建，不带 sha。
    status, existing = request_with_retry(
        "GET", f"/repos/{owner}/{repo}/contents/version.json?ref=main", token)
    if status == 200 and isinstance(existing, dict) and existing.get("sha"):
        payload["sha"] = existing["sha"]
        action = "更新"
    else:
        action = "创建"

    status, body = request_with_retry(
        "PUT", f"/repos/{owner}/{repo}/contents/version.json", token, payload)
    if status not in (200, 201):
        detail = body.get("message", str(body)[:200]) \
            if isinstance(body, dict) else str(body)[:200]
        print(f"  [XX] 提交 version.json 失败（{status}）：{detail}")
        return False
    print(f"  [ok] 已在 main 上{action} version.json（{version}）")
    return True


# ==========================================================================
#  验收 —— 这一步才是这个脚本存在的意义
# ==========================================================================
def verify(owner: str, repo: str, token: str, version: str,
           assets: list[tuple[Path, str]]) -> list[str]:
    """真的去看一眼「客户端能不能拿到新版」。返回问题列表。

    前面每一步都自己报了 [ok]，但那只说明**请求发出去了**。
    这里换个身份（就是普通客户端的读法）再查一遍。
    """
    from github_upload import request_with_retry

    problems: list[str] = []

    # ---------------------------------------------------- 1. Releases API
    print("  1) Releases API（客户端的兜底来源）")
    status, body = request_with_retry(
        "GET", f"/repos/{owner}/{repo}/releases/latest", token)
    tag = str(body.get("tag_name") or "") if isinstance(body, dict) else ""
    if status != 200:
        problems.append(f"releases/latest 拿不到（{status}）—— 用户的下载按钮会 404")
    elif tag.lstrip("vV") != version.lstrip("vV"):
        problems.append(f"releases/latest 指向 {tag}，不是 v{version}")
    else:
        print(f"     [ok] releases/latest → {tag}")

    # 远程的 asset 清单里，每个本地文件都要在，大小还要对得上
    if isinstance(body, dict):
        asset_problems, good_names = check_remote_assets(body, assets)
        problems += asset_problems
        for name in good_names:
            print(f"     [ok] {name} 已在 Release 上")

    # ------------------------------------------- 2. raw 上的 version.json
    #
    # **这一条会有假报警。** raw.githubusercontent.com 前面挂着 CDN，
    # 缓存大约 5 分钟。刚提交完就读，很可能还是旧内容 ——
    # 那是缓存，不是失败。
    #
    # 所以：重试若干次；如果最后还是旧值，就**明说是缓存**而不是报错。
    # 而且真有缓存也不影响用户：客户端第二个来源（Releases API）
    # 已经是新的了，上面那条过了就说明更新提示能弹出来。
    print("  2) raw 上的 version.json（客户端的首选来源，有 CDN 缓存）")
    raw_url = (f"https://raw.githubusercontent.com/{owner}/{repo}"
               f"/main/version.json")
    fresh = False
    last_seen = ""
    for attempt in range(1, 7):
        request = urllib.request.Request(raw_url, headers={
            "User-Agent": "PawPet-UpdateChecker",
            "Accept": "application/json",
            # 显式绕开我们**本地**的缓存，但绕不开 CDN —— CDN 是照
            # 客户端原样请求的 URL 缓存的，所以这里不能加查询参数，
            # 加了就变成在验证一个客户端根本不会用的 URL。
            "Cache-Control": "no-cache",
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(64 * 1024)
            last_seen = str(json.loads(raw.decode("utf-8", "replace"))
                            .get("version") or "")
        except Exception:  # noqa: BLE001 - 读不到就重试，下面统一处理
            last_seen = ""

        if last_seen and last_seen.lstrip("vV") == version.lstrip("vV"):
            fresh = True
            print(f"     [ok] 第 {attempt} 次读到 {last_seen}")
            break
        if attempt < 6:
            print(f"     （第 {attempt} 次读到 {last_seen or '读不到'}，20s 后重试）")
            time.sleep(20)

    if not fresh:
        if last_seen:
            print(f"     [!!] 还是旧值 {last_seen} —— CDN 缓存没到期，不是失败。")
            print("          客户端有 Releases API 兜底，更新提示照样能弹。")
            print("          5 分钟后想确认的话：")
            print(f"            curl -s {raw_url}")
        else:
            # 连内容都读不到，那才是真问题（比如仓库私有 → raw 是 404，
            # 或者根本没提交上去）
            problems.append("raw 上的 version.json 读不到 —— 首选来源不可用")

    return problems


# ==========================================================================
#  主流程
# ==========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="发一个新版本（建 Release + 传包 + 同步 version.json）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default="0Sun-shine0")
    parser.add_argument("--repo", default="PawPet")
    parser.add_argument("--note", default="", help="发布说明（一句话就行）")
    parser.add_argument("--yes", action="store_true", help="确认过了，别问")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检查、只打印计划，不改任何东西")
    parser.add_argument("--skip-upload", action="store_true",
                        help="跳过建 Release 和传包（包已经手工传过了），只同步 version.json")
    args = parser.parse_args()

    version = read_local_version()
    print("小爪助手 发版")
    print(f"  目标   ：{args.owner}/{args.repo}")
    print(f"  版本   ：{version}")
    print(f"  产物   ：{DIST}")

    # ---------------------------------------------------------- 1. 本地
    print("\n=== 1. 本地检查 ===")
    ok, assets = check_local(version, need_upload=not args.skip_upload)
    if not ok:
        return 1

    # ---------------------------------------------------------- 2. 凭据
    print("\n=== 2. 身份 ===")
    from github_upload import GitHubError, get_credential, request

    username, token = get_credential()
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            print("  [XX] 取不到 GitHub 凭据")
            print("       先用 git push 过一次（让凭据管理器记住），")
            print("       或者设一个 GITHUB_TOKEN 环境变量")
            return 1
        print("  （用的是环境变量 GITHUB_TOKEN）")
    status, body = request("GET", "/user", token)
    if status != 200:
        print(f"  [XX] token 无效（{status}）：{str(body)[:150]}")
        return 1
    print(f"  [ok] 身份：{body.get('login')}")

    # ---------------------------------------------------------- 3. 远程
    print("\n=== 3. 远程状态 ===")
    ok, _latest = check_remote(args.owner, args.repo, token, version)
    if not ok:
        return 1

    if not args.skip_upload:
        # 提醒工作区状态，但不阻止 —— 发版发的是 exe，源码推不推
        # 不影响用户能不能用。只是 tag 会打在远程旧提交上，仓库看起来会落后。
        dirty = _git_dirty()
        if dirty:
            print(f"  [!!] 本地有 {dirty} 项没提交的改动")
            print("       不影响用户（他们下的是 exe），但这个 Release 的 tag 会")
            print("       指向远程的旧提交。想让仓库一致：先 git add/commit/push")

    # ---------------------------------------------------------- 4. 确认
    tag = f"v{version}"
    print("\n=== 4. 将要做什么 ===")
    if args.skip_upload:
        print(f"  · 只把 version.json 同步到 {version}")
    else:
        print(f"  · 建 tag {tag} + Release「小爪助手 {version}」")
        for path, upload_as, _label in assets:
            size = human(path.stat().st_size)
            if upload_as != path.name:
                print(f"  · 上传 {path.name}（{size}）→ 附件名 {upload_as}")
            else:
                print(f"  · 上传 {path.name}（{size}）")
        print(f"  · 最后提交 version.json → {version}")
        print("\n  （顺序是刻意的：先把包备好，再让客户端知道有新版本）")

    if args.dry_run:
        print("\n[dry-run] 什么都没改。去掉 --dry-run 才是真发。")
        return 0

    if not args.yes:
        try:
            answer = input("\n确认发版？输入 yes 继续：").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("已取消")
            return 1

    # ---------------------------------------------------------- 5. 执行
    if not args.skip_upload:
        print("\n=== 5. 建 Release ===")
        body = args.note or f"小爪助手 {version}"
        release_id = create_release(args.owner, args.repo, token, version,
                                    _release_body(version, args.note, assets), assets)
        if release_id is None:
            return 1

        print("\n=== 6. 上传产物 ===")
        failed = [upload_as for _path, upload_as, _label in assets
                  if not upload_asset(args.owner, args.repo, token,
                                      release_id, _path, upload_as, _label)]
        if failed:
            print(f"\n  [XX] 有文件没传上去：{', '.join(failed)}")
            print("       先别提交 version.json —— 否则用户会看到更新提示却下不到包。")
            print(f"       手工补传：https://github.com/{args.owner}/{args.repo}"
                  f"/releases/edit/{tag}")
            return 1

    print("\n=== 7. 同步 version.json ===")
    if not publish_version_file(args.owner, args.repo, token, version, args.note):
        print("  [!!] 这一步失败的话，客户端至少还有 Releases API 兜底，")
        print("       但首选来源会一直是旧版本号 —— 手工改一下更稳。")
        return 1

    print("\n=== 8. 回头验一遍 ===")
    problems = verify(args.owner, args.repo, token, version, assets)

    print("\n" + "=" * 56)
    if problems:
        print(f"发完了，但有 {len(problems)} 处要你自己确认：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(f"发完了：小爪助手 {version}")
    print(f"  Release 页：https://github.com/{args.owner}/{args.repo}/releases/tag/{tag}")
    print(f"  下载页    ：https://github.com/{args.owner}/{args.repo}/releases/latest")
    print("\n  老用户那边：启动后一天内会弹一次更新提示（也可以在设置里手动点检查）。")
    return 0


def _git_dirty() -> int:
    """工作区有多少项未提交改动。git 不可用时返回 0（当它干净）。"""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return 0
    return len([ln for ln in result.stdout.splitlines() if ln.strip()])


def _release_body(version: str, note: str,
                  assets: list[tuple[Path, str, str]]) -> str:
    """Release 页面上的正文。

    下载页是**唯一**一个用户会认真读的页面（他要点下载，视线必然落在这里），
    所以把「装哪个、怎么装」直接写在这儿，别指望他去翻 README。

    **文件名要用上传后的 ASCII 名**（`upload_as`），不是本地名 ——
    用户点下载拿到的是前者，写成本地中文名就对不上了。
    """
    lines = [f"### 小爪助手 {version}", ""]
    if note:
        lines += [note, ""]
    lines += ["#### 下载哪个", ""]
    for path, upload_as, label in assets:
        lines.append(f"- **{upload_as}**（{human(path.stat().st_size)}）— {label}")
    lines += [
        "",
        "#### 安装",
        "",
        "双击 `PawPet-Setup` 那个 exe。装到你的用户目录，**不会碰你的数据**；",
        "卸载走「设置 → 应用」，卸载也不会删数据。",
        "",
        "> Windows 可能会弹「已保护你的电脑」—— 那是因为这个版本还没有代码签名。",
        "> 点「更多信息」→「仍要运行」即可。",
        "",
        "#### 已经在用旧版本？",
        "",
        "不用管，小爪启动后一天内会自己提示有新版本（设置 → 更新里也能手动检查）。",
        "装上之后待办、专注、便签、提醒、AI 对话记录**都会保留**。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
