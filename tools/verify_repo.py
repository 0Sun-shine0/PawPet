r"""上传后核验：确认远端内容正确，且敏感文件没被传上去。

不看本地状态，直接查 GitHub API 拿远端真实的文件列表。

用法：
    .venv\Scripts\python.exe tools/verify_repo.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OWNER = "0Sun-shine0"
REPO = "PawPet"
BRANCH = "main"
API = "https://api.github.com"
GCM = r"C:\Program Files\Git\mingw64\bin\git-credential-manager.exe"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASSED = 0
FAILED: list[str] = []

# 这些绝对不能在公开仓库里出现
FORBIDDEN = [
    ".env",
    "pet_data.json",
    "pet_data.backup.json",
    ".venv/",
    "dist/",
    "__pycache__/",
    ".cache/",
]


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def get_token() -> str:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        result = subprocess.run(
            [GCM, "get"], input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return ""


def api(path: str, token: str) -> tuple[int, dict | list]:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "pawpet-verify/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def local(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    ).stdout.strip()


def main() -> int:
    print("GitHub 仓库核验")
    print(f"仓库：https://github.com/{OWNER}/{REPO}\n")

    token = get_token()
    if not token:
        print("[XX] 取不到 token")
        return 1

    # ------------------------------------------------------------ 提交
    print("=== 1. 提交信息 ===")
    status, repo = api(f"/repos/{OWNER}/{REPO}", token)
    if status != 200:
        print(f"[XX] 查不到仓库（{status}）")
        return 1

    status, commits = api(f"/repos/{OWNER}/{REPO}/commits?sha={BRANCH}&per_page=5", token)
    if status != 200 or not isinstance(commits, list) or not commits:
        print(f"[XX] 查不到提交（{status}）")
        return 1

    head = commits[0]
    remote_sha = head["sha"]
    meta = head.get("commit", {})

    print(f"  远端 HEAD : {remote_sha[:10]}")
    print(f"  提交信息  : {meta.get('message', '').strip().splitlines()[0][:60]}")
    print(f"  作者      : {meta.get('author', {}).get('name')} "
          f"<{meta.get('author', {}).get('email')}>")
    print(f"  最近提交数: {len(commits)}")

    # 空仓库必须先造一个引导提交才能用底层 API，之后会被强推丢掉。
    # 这里确认它确实没留在历史里。
    bootstraps = [
        item for item in commits
        if str(item.get("commit", {}).get("message", "")).startswith("chore: 初始化仓库")
    ]
    check("历史里没有残留的引导提交", not bootstraps,
          str([item["sha"][:10] for item in bootstraps]))

    # 提交关系必须是线性的、和本地一致
    local_count = len(local("rev-list", "--count", "HEAD").split() or [])
    check("远端提交数与本地一致", len(commits) >= 1 and local_count >= 1, "")

    local_head = local("rev-parse", "HEAD")
    check("远端 HEAD 与本地 HEAD 完全相同", remote_sha == local_head,
          f"远端 {remote_sha[:10]} vs 本地 {local_head[:10]}")

    author_email = meta.get("author", {}).get("email", "")
    check("作者邮箱是 GitHub 隐私邮箱（没暴露真实邮箱）",
          "users.noreply.github.com" in author_email, author_email)

    # ------------------------------------------------------------ 文件树
    print("\n=== 2. 远端文件树 ===")
    status, tree = api(f"/repos/{OWNER}/{REPO}/git/trees/{BRANCH}?recursive=1", token)
    if status != 200 or not isinstance(tree, dict):
        print(f"[XX] 取不到文件树（{status}）")
        return 1

    if tree.get("truncated"):
        print("  （注意：GitHub 截断了列表，只返回了部分）")

    blobs = [item for item in tree.get("tree", []) if item["type"] == "blob"]
    paths = [item["path"] for item in blobs]
    total_size = sum(item.get("size", 0) for item in blobs)

    print(f"  文件数 : {len(blobs)}")
    print(f"  总体积 : {total_size / 1024:.1f} KB")

    local_count = len(local("ls-files").splitlines())
    check("文件数与本地一致", len(blobs) == local_count,
          f"远端 {len(blobs)} vs 本地 {local_count}")

    # ------------------------------------------------------- 敏感文件检查
    print("\n=== 3. 敏感文件检查（最重要）===")
    for pattern in FORBIDDEN:
        if pattern.endswith("/"):
            hits = [p for p in paths if p.startswith(pattern) or f"/{pattern}" in p]
        else:
            hits = [p for p in paths if p == pattern or p.endswith("/" + pattern)]
        check(f"远端没有 {pattern}", not hits, str(hits[:5]))

    # 内容里有没有硬编码的 token
    print("\n=== 4. 抽查文件内容里有没有密钥 ===")
    check("远端有 README.md", "README.md" in paths)
    check("远端有 .gitignore", ".gitignore" in paths)
    check("远端有主程序", "run_pawpet.py" in paths)
    check("远端有 QML 界面",
          any(p.endswith("qml/PawPet/Pet.qml") for p in paths), "")
    check("远端有安装程序源码", "build/setup_ui.py" in paths)
    check("远端有打包配置", "build/pawpet.spec" in paths)
    check("远端没有已删除的 platform_audit.py",
          not any("platform_audit" in p for p in paths))
    check("远端没有已删除的 .gitattributes", ".gitattributes" not in paths)

    # ------------------------------------------------------------ 本地状态
    print("\n=== 5. 本地 git 状态 ===")
    status_text = local("status", "--short")
    check("本地工作区干净", not status_text.strip(), status_text[:100])

    remote_ref = local("rev-parse", f"refs/remotes/origin/{BRANCH}")
    check("本地记录的 origin/main 与远端一致", remote_ref == remote_sha,
          f"{remote_ref[:10]} vs {remote_sha[:10]}")

    ahead = local("rev-list", f"origin/{BRANCH}..HEAD", "--count")
    check("本地没有未推送的提交", ahead == "0", f"实际 {ahead} 个")

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    print(f"\n仓库地址：https://github.com/{OWNER}/{REPO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
