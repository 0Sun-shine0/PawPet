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


def local_commit_info() -> tuple[str, str]:
    """取本地 HEAD 的提交信息，用作上传时的默认提交信息。"""
    message = subprocess.run(
        ["git", "log", "-1", "--format=%B"], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    ).stdout.strip()

    author = subprocess.run(
        ["git", "log", "-1", "--format=%an%n%ae"], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    ).stdout.strip().splitlines()

    name = author[0] if author else "PawPet"
    email = author[1] if len(author) > 1 else "noreply@example.com"
    return message or "Update", f"{name} <{email}>"


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
           message: str, author: str, dry_run: bool = False) -> int:
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

    # ---------------------------------------------------------- 2. 读本地内容
    print(f"\n=== 2. 读取本地提交 ===")
    entries = read_tree("HEAD")
    total_bytes = sum(len(content) for _p, _m, content in entries)
    print(f"  {len(entries)} 个文件，合计 {human(total_bytes)}")

    if dry_run:
        print("\n=== 计划上传的文件 ===")
        for path, mode, content in sorted(entries):
            print(f"  {human(len(content)):>9}  {path}")
        print(f"\n[dry-run] 共 {len(entries)} 个文件，未实际上传")
        return 0

    name, email = author.split(" <")
    email = email.rstrip(">")

    # ---------------------------------------------------------- 3. 上传 blobs
    print(f"\n=== 3. 上传文件（blob）===")
    tree_items: list[dict] = []
    started = time.time()
    failed: list[tuple[str, str]] = []

    for index, (path, mode, content) in enumerate(entries, 1):
        payload = {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        }
        try:
            status, body = request("POST", f"/repos/{owner}/{repo}/git/blobs",
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

    # ---------------------------------------------------------- 4. 建 tree
    print(f"\n=== 4. 创建 tree ===")
    status, body = request("POST", f"/repos/{owner}/{repo}/git/trees", token,
                           {"tree": tree_items})
    if status not in (200, 201):
        print(f"  [XX] 失败（{status}）：{str(body)[:300]}")
        return 1
    tree_sha = body["sha"]
    print(f"  [ok] tree {tree_sha[:10]}")

    # ------------------------------------------------- 5. 是否已有分支（增量）
    parents: list[str] = []
    status, body = request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
    if status == 200 and isinstance(body, dict):
        parents = [body["object"]["sha"]]
        print(f"\n  已有分支 {branch}，将以 {parents[0][:10]} 为父提交（增量更新）")
    else:
        print(f"\n  分支 {branch} 还不存在，将创建首个提交")

    # ---------------------------------------------------------- 6. 建 commit
    print(f"\n=== 5. 创建 commit ===")
    payload = {
        "message": message,
        "tree": tree_sha,
        "author": {"name": name, "email": email},
        "committer": {"name": name, "email": email},
    }
    if parents:
        payload["parents"] = parents

    status, body = request("POST", f"/repos/{owner}/{repo}/git/commits", token, payload)
    if status not in (200, 201):
        print(f"  [XX] 失败（{status}）：{str(body)[:300]}")
        return 1
    commit_sha = body["sha"]
    print(f"  [ok] commit {commit_sha[:10]}")
    print(f"       作者 {name} <{email}>")

    # ---------------------------------------------------------- 7. 更新 ref
    print(f"\n=== 6. 更新分支 {branch} ===")
    if parents:
        status, body = request("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
                               token, {"sha": commit_sha, "force": False})
        action = "更新"
    else:
        status, body = request("POST", f"/repos/{owner}/{repo}/git/refs", token,
                               {"ref": f"refs/heads/{branch}", "sha": commit_sha})
        action = "创建"

    if status not in (200, 201):
        print(f"  [XX] 失败（{status}）：{str(body)[:300]}")
        return 1
    print(f"  [ok] 已{action} refs/heads/{branch}")

    # ---------------------------------------------------------- 8. 核对
    print(f"\n=== 7. 核对结果 ===")
    status, body = request("GET", f"/repos/{owner}/{repo}", token)
    if status == 200:
        print(f"  仓库大小 : {body.get('size')} KB")
        print(f"  默认分支 : {body.get('default_branch')}")
        print(f"  仓库地址 : {body.get('html_url')}")

    status, body = request("GET", f"/repos/{owner}/{repo}/commits?per_page=1", token)
    if status == 200 and isinstance(body, list) and body:
        head = body[0]
        commit = head.get("commit", {})
        print(f"  最新提交 : {head.get('sha', '')[:10]}  "
              f"{commit.get('message', '').splitlines()[0][:50]}")
        print(f"  提交者   : {commit.get('author', {}).get('name')} "
              f"<{commit.get('author', {}).get('email')}>")

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

    message, author = local_commit_info()
    if args.message:
        message = args.message
    print(f"提交信息：{message.splitlines()[0]}")
    print(f"作者：{author}")

    try:
        return upload(args.owner, args.repo, token, args.branch,
                      message, author, dry_run=args.dry_run)
    except GitHubError as exc:
        print(f"\n[XX] 网络错误：{exc}")
        print("     api.github.com 也连不上的话，只能换网络或开代理了。")
        return 1
    except KeyboardInterrupt:
        print("\n已取消")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
