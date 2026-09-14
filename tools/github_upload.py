r"""不依赖 git push，直接用 GitHub REST API 把本地提交上传上去。

**为什么需要这个：**

在很多网络环境下（尤其是国内），`github.com:443` 会被干扰，表现为
TCP 有时能连上但 TLS 握手超时，于是 `git push` 直接失败：

    fatal: unable to access 'https://github.com/...':
    Failed to connect to github.com:443 after 21059 ms

但 `api.github.com` 通常是通的。这个脚本就走 API 上传，
绕开被干扰的那个域名。

**它做了什么：**

    1. 从 git 对象库读出当前提交的完整内容（不是工作区，保证和提交一致）
    2. 逐个上传为 blob
    3. 用这些 blob 组装一棵 tree
    4. 创建 commit
    5. 创建或更新 refs/heads/<分支>

**凭据：** 从 git 的凭据管理器读，和 git push 用的是同一份。
不需要把 token 写进任何文件。

用法：
    .venv\\Scripts\\python.exe tools\\github_upload.py
    .venv\\Scripts\\python.exe tools\\github_upload.py --owner X --repo Y
    .venv\\Scripts\\python.exe tools\\github_upload.py --dry-run     # 只看计划
    .venv\\Scripts\\python.exe tools\\github_upload.py --message "..."  # 指定提交信息
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"

# Git Credential Manager 的常见位置
GCM_CANDIDATES = [
    Path(r"C:\Program Files\Git\mingw64\bin\git-credential-manager.exe"),
    Path(r"C:\Program Files (x86)\Git\mingw64\bin\git-credential-manager.exe"),
    Path("/usr/local/bin/git-credential-manager"),
    Path("/usr/bin/git-credential-manager"),
]


# ==========================================================================
#  凭据
# ==========================================================================
def find_helper() -> Path | None:
    for candidate in GCM_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def get_credential(host: str = "github.com") -> tuple[str, str]:
    """从凭据管理器取 (用户名, token)。取不到就返回空串。"""
    helper = find_helper()
    if helper is None:
        return "", ""

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"     # 绝不弹窗
    env["GCM_INTERACTIVE"] = "never"

    try:
        result = subprocess.run(
            [str(helper), "get"],
            input=f"protocol=https\nhost={host}\n\n",
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""

    username = password = ""
    for line in result.stdout.splitlines():
        if line.startswith("username="):
            username = line.split("=", 1)[1]
        elif line.startswith("password="):
            password = line.split("=", 1)[1]
    return username, password


# ==========================================================================
#  HTTP
# ==========================================================================
class GitHubError(Exception):
    pass


def request(method: str, path: str, token: str,
            payload: dict | None = None, timeout: int = 120) -> tuple[int, dict]:
    url = path if path.startswith("http") else f"{API}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pawpet-uploader/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body) if body else {}
            except json.JSONDecodeError:
                return response.status, {"raw": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}
    except Exception as exc:  # noqa: BLE001
        raise GitHubError(f"{type(exc).__name__}: {exc}") from exc


def request_with_retry(method: str, path: str, token: str,
                       payload: dict | None = None,
                       attempts: int = 4, timeout: int = 120) -> tuple[int, dict]:
    """带重试的请求。

    这台机器到 api.github.com 的连接会偶发地被重置
    （RemoteDisconnected / Connection reset），单次失败不代表真的失败。
    5xx 和 429 也值得重试。
    """
    last: tuple[int, dict] = (0, {"error": "未发起请求"})
    for attempt in range(1, attempts + 1):
        try:
            status, body = request(method, path, token, payload, timeout)
            if status in (200, 201):
                return status, body
            if status >= 500 or status == 429:
                last = (status, body)
                if attempt < attempts:
                    wait = 1.5 * attempt
                    print(f"      （第 {attempt} 次拿到 {status}，{wait:.1f}s 后重试）")
                    time.sleep(wait)
                    continue
            return status, body
        except GitHubError as exc:
            last = (0, {"error": str(exc)})
            if attempt < attempts:
                wait = 1.5 * attempt
                print(f"      （第 {attempt} 次连接失败，{wait:.1f}s 后重试）")
                time.sleep(wait)
    return last


# ==========================================================================
#  读取本地提交
# ==========================================================================
def read_tree(ref: str = "HEAD") -> list[tuple[str, str, bytes]]:
    """读出提交里的所有文件。

    返回 [(path, mode, content_bytes)]。
    **从 git 对象库读**而不是读工作区 —— 这样保证上传的内容和提交完全一致
    （换行符已经按 .gitattributes 规范化过）。

    用 -z 拿到 NUL 分隔的原始输出，避免中文文件名被转义。
    """
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", ref],
        cwd=str(ROOT), capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise GitHubError(f"git ls-tree 失败：{result.stderr.decode('utf-8', 'replace')}")

    entries: list[tuple[str, str, bytes]] = []
    for record in result.stdout.split(b"\x00"):
        if not record:
            continue
        # 格式: "<mode> <type> <sha>\t<path>"
        try:
            meta, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        parts = meta.split(b" ")
        if len(parts) < 3:
            continue
        mode = parts[0].decode()
        obj_type = parts[1].decode()
        sha = parts[2].decode()
        if obj_type != "blob":
            continue

        path = path_bytes.decode("utf-8", "surrogateescape")

        # 用 cat-file 取规范化后的内容
        blob = subprocess.run(
            ["git", "cat-file", "blob", sha],
            cwd=str(ROOT), capture_output=True, timeout=60,
        )
        if blob.returncode != 0:
            raise GitHubError(f"读取 blob 失败：{path}")
        entries.append((path, mode, blob.stdout))

    return entries


def local_commit_info() -> dict:
    """读出本地 HEAD 的完整元信息，用于在远端复现出**完全相同**的提交。

    commit SHA 就是 tree + parents + author + committer + message 的哈希，
    所以任何一处的字节差异都会让 SHA 不同 —— 包括**提交信息末尾的换行**。

    这里踩过一个坑：用 `git log --format=%B` 拿到消息后 strip() 了一下，
    尾部换行没了，结果远端算出的 SHA 和本地不一致，以后 push 会冲突。
    正确做法是直接读原始提交对象，把空行之后的字节原样取出来。
    """
    def git_bytes(*args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, timeout=30,
        ).stdout

    # 先拿头部字段（%B 只取消息，但我们要的是精确字节，所以后面单独读原始对象）
    fields = subprocess.run(
        ["git", "log", "-1", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%T"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    ).stdout.split("\x00")
    if len(fields) < 7:
        raise GitHubError("读不出本地提交信息")

    # 原始 commit 对象：头部、空行、然后是消息（含尾部换行）
    raw = git_bytes("cat-file", "commit", "HEAD")
    _header, separator, message_bytes = raw.partition(b"\n\n")
    if not separator:
        raise GitHubError("提交对象格式异常")
    message = message_bytes.decode("utf-8")

    return {
        "message": message,
        # %aI / %cI 是严格 ISO 8601（带时区偏移），正好是 GitHub API 要的格式
        "author": {"name": fields[0], "email": fields[1], "date": fields[2].strip()},
        "committer": {"name": fields[3], "email": fields[4], "date": fields[5].strip()},
        "tree": fields[6].strip(),
    }


def verify_payload(info: dict) -> tuple[bool, str]:
    """本地自检：用即将发给 API 的参数复现一次提交，看 SHA 是否等于 HEAD。

    相当于在本地先把服务端要做的哈希算一遍。能对上，就说明上传后
    远端提交的 SHA 会和本地完全一致，本地和远程天然同步。
    """
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": info["author"]["name"],
        "GIT_AUTHOR_EMAIL": info["author"]["email"],
        "GIT_AUTHOR_DATE": info["author"]["date"],
        "GIT_COMMITTER_NAME": info["committer"]["name"],
        "GIT_COMMITTER_EMAIL": info["committer"]["email"],
        "GIT_COMMITTER_DATE": info["committer"]["date"],
    })
    try:
        result = subprocess.run(
            ["git", "commit-tree", info["tree"]],
            input=info["message"].encode("utf-8"),
            cwd=str(ROOT), capture_output=True, env=env, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"自检执行失败：{exc}"

    if result.returncode != 0:
        return False, result.stderr.decode("utf-8", "replace")[:200]

    predicted = result.stdout.decode("utf-8", "replace").strip()
    actual = local_sha()
    if predicted == actual:
        return True, predicted
    return False, f"本地复现得到 {predicted[:10]}，但 HEAD 是 {actual[:10]}"


def local_sha(ref: str = "HEAD") -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref], cwd=str(ROOT),
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def local_parents(ref: str = "HEAD") -> list[str]:
    """取本地提交的父提交列表（根提交返回空）。"""
    result = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", ref], cwd=str(ROOT),
        capture_output=True, text=True, timeout=30,
    )
    parts = result.stdout.strip().split()
    return parts[1:] if len(parts) > 1 else []


def is_ancestor(sha: str) -> bool:
    """sha 是不是本地 HEAD 的祖先。

    本地没有这个对象时返回 False（比如远端那份提交不是我们推的）。
    """
    if not sha:
        return False
    exists = subprocess.run(
        ["git", "cat-file", "-e", sha], cwd=str(ROOT),
        capture_output=True, timeout=30,
    )
    if exists.returncode != 0:
        return False
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=str(ROOT),
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


# ==========================================================================
#  上传
# ==========================================================================
def human(size: float) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def upload(owner: str, repo: str, token: str, branch: str,
           info: dict, dry_run: bool = False) -> int:
    # ---------------------------------------------------------- 1. 确认仓库
    print(f"\n=== 1. 检查仓库 {owner}/{repo} ===")
    status, body = request("GET", f"/repos/{owner}/{repo}", token)
    if status == 404:
        print(f"  [XX] 仓库不存在或无权访问")
        return 1
    if status != 200:
        print(f"  [XX] 查询失败（{status}）：{str(body)[:200]}")
        return 1

    perms = body.get("permissions") or {}
    print(f"  可见性 : {'私有' if body.get('private') else '公开'}")
    print(f"  权限   : push={perms.get('push')}")
    if not perms.get("push"):
        print("  [XX] 没有推送权限")
        return 1

    # -------------------------------------------- 1.5 空仓库要先造引导提交
    #
    # 这是 GitHub 的一个坑：Git Data API（blobs / trees / commits）
    # 在**完全空的仓库**上会返回 409「Git Repository is empty」。
    # 必须先有一个提交，这些接口才可用。
    #
    # 办法是用 Contents API 先写一个 README，造出首个提交；
    # 等我们的正式提交建好之后，再把分支强制指过去，
    # 让历史里只剩一个干净的提交（引导提交会被丢弃）。
    print(f"\n=== 2. 检查分支 {branch} ===")
    branch_existed = False    # 远端已有提交
    bootstrapped = False      # 远端已有引导提交（这次或上次建的），不能再建一次
    existing_sha = ""
    remote_parents: list[str] = []
    status, ref_body = request_with_retry(
        "GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)

    if status == 200 and isinstance(ref_body, dict):
        existing_sha = ref_body["object"]["sha"]
        # 判断这个提交是不是上一次中断时留下的引导提交。
        # 必须识别出来，否则重跑时会把它当成父提交，
        # 导致新提交的 SHA 和本地对不上。
        is_bootstrap = False
        st, meta = request_with_retry(
            "GET", f"/repos/{owner}/{repo}/git/commits/{existing_sha}", token)
        if st == 200 and isinstance(meta, dict):
            no_parents = not (meta.get("parents") or [])
            is_bootstrap = no_parents and str(meta.get("message", "")).startswith("chore: 初始化仓库")

        if is_bootstrap:
            print(f"  分支上只有一个引导提交（上次中断留下的），将替换成正式提交")
            bootstrapped = True
        else:
            branch_existed = True
            remote_parents = meta.get("parents") or []
            print(f"  分支已存在，当前指向 {existing_sha[:10]}")
            if remote_parents:
                print(f"       它有 {len(remote_parents)} 个父提交（远端已有真实历史）")
            else:
                print(f"       它是个根提交（很可能是上一次上传尝试留下的）")
    else:
        print(f"  分支不存在 —— 仓库是空的，需要先造一个引导提交")

    # branch_existed=True  -> 远端已有正式提交，走增量更新
    # bootstrapped=True    -> 远端已有引导提交（不管是这次还是上次建的），
    #                         不能再创建一次（README 已存在，会 422）
    if not branch_existed and not bootstrapped:
        print(f"\n=== 2.5 创建引导提交 ===")
        status, boot_body = request_with_retry(
            "PUT", f"/repos/{owner}/{repo}/contents/README.md", token,
            {
                "message": "chore: 初始化仓库",
                "content": base64.b64encode(
                    "# PawPet\n\n内容正在上传…\n".encode("utf-8")
                ).decode("ascii"),
                "branch": branch,
            },
        )
        if status not in (200, 201):
            detail = boot_body.get("message", str(boot_body)[:200]) if isinstance(boot_body, dict) else str(boot_body)[:200]
            print(f"  [XX] 引导提交失败（{status}）：{detail}")
            return 1
        bootstrapped = True
        print("  [ok] 引导提交已创建，底层 API 现在可用了")

    # ---------------------------------------------------------- 3. 读本地内容
    print(f"\n=== 3. 读取本地提交 ===")
    entries = read_tree("HEAD")
    total_bytes = sum(len(content) for _p, _m, content in entries)
    print(f"  {len(entries)} 个文件，合计 {human(total_bytes)}")

    if dry_run:
        print("\n=== 计划上传的文件 ===")
        for path, mode, content in sorted(entries):
            print(f"  {human(len(content)):>9}  {path}")
        print(f"\n[dry-run] 共 {len(entries)} 个文件，未实际上传")
        return 0

    # ---------------------------------------------------------- 4. 上传 blobs
    print(f"\n=== 4. 上传文件（blob）===")
    tree_items: list[dict] = []
    started = time.time()
    failed: list[tuple[str, str]] = []

    for index, (path, mode, content) in enumerate(entries, 1):
        payload = {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }
        try:
            status, body = request_with_retry("POST", f"/repos/{owner}/{repo}/git/blobs",
                                   token, payload)
        except GitHubError as exc:
            failed.append((path, str(exc)))
            print(f"  [XX] {path}: {exc}")
            continue

        if status not in (200, 201):
            detail = body.get("message", str(body)[:120]) if isinstance(body, dict) else str(body)[:120]
            failed.append((path, detail))
            print(f"  [XX] {path}: {detail}")
            continue

        tree_items.append({
            "path": path,
            "mode": mode,
            "type": "blob",
            "sha": body["sha"],
        })

        if index % 10 == 0 or index == len(entries):
            elapsed = time.time() - started
            rate = index / elapsed if elapsed else 0
            print(f"  {index:>3}/{len(entries)}  "
                  f"({rate:.1f} 个/秒, 已用 {elapsed:.0f}s)")

    if failed:
        print(f"\n  [XX] {len(failed)} 个文件上传失败，中止")
        for path, detail in failed[:10]:
            print(f"       {path}: {detail}")
        return 1

    print(f"  [ok] {len(tree_items)} 个 blob 全部上传完成")

    # 本地 tree 与远端 tree 必须一致，否则说明有文件在上传过程中被改动过
    local_tree = info["tree"]
    print(f"       本地 tree {local_tree[:10]}")

    # ---------------------------------------------------------- 4. 建 tree
    print(f"\n=== 4. 创建 tree ===")
    status, tree_body = request_with_retry("POST", f"/repos/{owner}/{repo}/git/trees", token,
                                {"tree": tree_items})
    if status not in (200, 201):
        print(f"  [XX] 失败（{status}）：{str(tree_body)[:300]}")
        return 1
    tree_sha = tree_body["sha"]
    print(f"  [ok] tree {tree_sha[:10]}")

    if tree_sha == local_tree:
        print("       [ok] tree 与本地完全一致（说明内容没有任何偏差）")
    else:
        print(f"       [!!] tree 与本地不一致（本地 {local_tree[:10]}）—— 内容可能有差异")

    # ------------------------------------------------ 5. 组装父提交
    # 父提交**直接照搬本地的**。SHA 是父提交的哈希的一部分，只要父子关系
    # 和本地一致、tree/作者/时间/消息也一致，服务端算出的 SHA 就必然相同。
    # 本地是根提交 → 远端也必须是根提交。
    parents = local_parents()
    if parents:
        print(f"\n  本地提交的父：{[p[:10] for p in parents]}")
    else:
        print(f"\n  本地是根提交，远端也建成根提交（SHA 才能对上）")

    # 判断改 ref 要不要 force。
    #
    # 正确的问题是：**远端当前的头是不是我们历史的一部分**？
    #   - 是   → 正常快进（fast-forward），不用 force
    #   - 不是 → 要把分支挪到一条不同的线上，需要 force
    #
    # 之前这里写错了：用的是「远端头有没有父提交」来判断，
    # 但远端头恰好就是我们的父提交时（正常增量上传），它本身是根提交，
    # 于是会误判成「需要覆盖」。所以改成查祖先关系。
    need_force = False
    if branch_existed:
        if existing_sha == local_sha() or is_ancestor(existing_sha):
            print("  远端是我们历史的一部分 → 正常快进更新")
        elif not remote_parents:
            need_force = True
            print(f"  远端当前 {existing_sha[:10]} 不在我们历史里，")
            print("  但它是根提交（上次尝试的产物），可以安全覆盖")
        else:
            print(f"  [XX] 远端已有 {len(remote_parents)} 个父提交的真实历史，")
            print("       用 API 覆盖会丢东西，已中止。请改用 git push。")
            return 1
    elif bootstrapped:
        # 远端只有引导提交，它不是我们祖先，必须覆盖
        need_force = True
        print("  远端只有引导提交，需要覆盖")

    # ---------------------------------------------------------- 6. 建 commit
    print(f"\n=== 5. 创建 commit ===")
    print(f"  作者   : {info['author']['name']} <{info['author']['email']}>")
    print(f"  作者时间: {info['author']['date']}")
    print(f"  提交时间: {info['committer']['date']}")

    payload = {
        "message": info["message"],
        "tree": tree_sha,
        # 姓名/邮箱/时间全部显式指定，让服务端算出和本地相同的 SHA
        "author": info["author"],
        "committer": info["committer"],
    }
    if parents:
        payload["parents"] = parents

    status, commit_body = request_with_retry("POST", f"/repos/{owner}/{repo}/git/commits", token, payload)
    if status not in (200, 201):
        print(f"  [XX] 失败（{status}）：{str(commit_body)[:300]}")
        return 1
    commit_sha = commit_body["sha"]
    print(f"  [ok] commit {commit_sha[:10]}")

    local = local_sha()
    if commit_sha == local:
        print("       [ok] 与本地 HEAD 的 SHA 完全相同 —— 本地和远程天然同步")
        sha_matches = True
    else:
        print(f"       [!!] 与本地 HEAD 不同（本地 {local[:10]}）")
        print("            内容一致但 SHA 不同，以后 push 需要先 fetch 再 reset")
        sha_matches = False

    # ---------------------------------------------------------- 7. 更新 ref
    print(f"\n=== 6. 更新分支 {branch} ===")
    if not branch_existed and not bootstrapped:
        # 分支还不存在，直接创建
        status, ref_result = request_with_retry("POST", f"/repos/{owner}/{repo}/git/refs", token,
                                     {"ref": f"refs/heads/{branch}", "sha": commit_sha})
        action = "创建"
    else:
        # 分支已存在（正式提交、引导提交、或上次尝试的产物），一律用 PATCH。
        # need_force 在需要覆盖非后代提交时才为 True。
        status, ref_result = request_with_retry("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
                                     token, {"sha": commit_sha, "force": need_force})
        action = "覆盖为正式提交" if need_force else "更新"

    if status not in (200, 201):
        print(f"  [XX] 失败（{status}）：{str(ref_result)[:300]}")
        return 1
    print(f"  [ok] 已{action}")

    # ------------------------------------------- 8. 让本地的远程跟踪分支跟上
    # 这样 git status / git log 就能看到正确的远程状态，
    # 以后网络恢复时直接 git push 即可（会显示 already up to date）
    if sha_matches:
        subprocess.run(
            ["git", "update-ref", f"refs/remotes/origin/{branch}", commit_sha],
            cwd=str(ROOT), capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "config", f"branch.{branch}.remote", "origin"],
            cwd=str(ROOT), capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"],
            cwd=str(ROOT), capture_output=True, timeout=30,
        )
        print(f"  [ok] 本地 origin/{branch} 已指向同一个提交")

    # ---------------------------------------------------------- 9. 核对
    print(f"\n=== 7. 核对结果 ===")
    status, repo_body = request("GET", f"/repos/{owner}/{repo}", token)
    if status == 200:
        print(f"  仓库大小 : {repo_body.get('size')} KB")
        print(f"  默认分支 : {repo_body.get('default_branch')}")
        print(f"  仓库地址 : {repo_body.get('html_url')}")

    status, commits = request("GET", f"/repos/{owner}/{repo}/commits?per_page=1", token)
    if status == 200 and isinstance(commits, list) and commits:
        head = commits[0]
        commit_meta = head.get("commit", {})
        print(f"  最新提交 : {head.get('sha', '')[:10]}  "
              f"{commit_meta.get('message', '').splitlines()[0][:50]}")
        print(f"  提交者   : {commit_meta.get('author', {}).get('name')} "
              f"<{commit_meta.get('author', {}).get('email')}>")

    elapsed = time.time() - started
    print(f"\n全部完成，用时 {elapsed:.0f} 秒")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 GitHub API 上传提交")
    parser.add_argument("--owner", default="0Sun-shine0", help="仓库所有者")
    parser.add_argument("--repo", default="PawPet", help="仓库名")
    parser.add_argument("--branch", default="main", help="目标分支")
    parser.add_argument("--message", default="", help="提交信息（默认取本地 HEAD 的）")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不上传")
    args = parser.parse_args()

    print("GitHub API 上传工具")
    print(f"目标：{args.owner}/{args.repo}  分支 {args.branch}")

    # ------------------------------------------------------------ 凭据
    username, token = get_credential()
    if not token:
        print("\n[XX] 取不到 GitHub 凭据。")
        print("     请先用 git push 过一次（让凭据管理器记住），")
        print("     或者设置 GITHUB_TOKEN 环境变量。")
        env_token = os.environ.get("GITHUB_TOKEN", "").strip()
        if env_token:
            token = env_token
            print("     （改用环境变量 GITHUB_TOKEN）")
        else:
            return 1
    else:
        print(f"凭据：{username}，token 长度 {len(token)}")

    # ------------------------------------------------------------ 校验
    status, body = request("GET", "/user", token)
    if status != 200:
        print(f"\n[XX] token 无效（{status}）：{str(body)[:200]}")
        return 1
    print(f"身份：{body.get('login')}（{body.get('name')}）")

    info = local_commit_info()
    if args.message:
        info["message"] = args.message
    print(f"提交信息：{info['message'].strip().splitlines()[0]}")
    print(f"作者：{info['author']['name']} <{info['author']['email']}>")

    # 本地自检：确认这套参数能复现出和 HEAD 相同的 SHA
    ok, detail = verify_payload(info)
    if ok:
        print(f"自检：参数能精确复现本地提交 {detail[:10]} —— 远端 SHA 会与本地一致")
    else:
        print(f"自检：[!!] {detail}")
        print("      上传仍然可以成功，但远端 SHA 会和本地不同，")
        print("      以后 push 前需要先 git fetch && git reset --hard origin/main")

    try:
        return upload(args.owner, args.repo, token, args.branch,
                      info, dry_run=args.dry_run)
    except GitHubError as exc:
        print(f"\n[XX] 网络错误：{exc}")
        print("     api.github.com 也连不上的话，只能换网络或开代理了。")
        return 1
    except KeyboardInterrupt:
        print("\n已取消")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
