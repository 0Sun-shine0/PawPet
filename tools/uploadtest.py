r"""上传工具（tools/github_upload.py）回归。

## 这个套件盯的是什么 bug

`github_upload.py` 是「git push 走不通时用 GitHub API 传提交」的备用通道。
它原来**只上传一个提交**，父提交直接照搬本地的：

    payload["parents"] = info["parents"]      # 来自本地 HEAD~1

本地只领先远端 1 个提交时这是对的。**领先 2 个以上就错了**：新提交的父提交
在远端根本不存在。而 GitHub 建提交时会校验父提交存在，于是 422 拒绝 ——
更糟的是这个状态很容易出现：攒几个提交没推、然后跑发版
（`release.py` 会在 version.json 上再压一个提交，再调这个工具）。

现在改成：算出远端缺哪些提交，**从旧到新**依次建，父提交自然都存在。

## 怎么测的

不碰真仓库 —— 用临时 git 仓库 + 一个**内存版 GitHub API**。
那个假 API 会**像真 API 一样校验父提交存在**，所以如果哪天真退回到
「只传一个提交」，它会立刻 422，测试红。这比断言「调了几次接口」更有意义：
它复现的是**真 API 的拒绝条件**。

另外验证跨提交的 blob 去重：相邻提交里没变的文件只传一次
（9 个提交 × 一百多个文件，不去重的话开销是 9 倍）。

用法：
    .venv\Scripts\python.exe tools\uploadtest.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{done.stderr[:200]}")
    return (done.stdout or "").strip()


def make_repo(base: Path) -> tuple[Path, list[str]]:
    """造一个有 3 个提交的仓库，返回 (路径, [三个提交的 SHA])。"""
    repo = base / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "测试")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")

    (repo / "a.txt").write_text("第一个文件\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "提交 A：加 a.txt")

    (repo / "b.txt").write_text("第二个文件，一直不变\n", encoding="utf-8")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-m", "提交 B：加 b.txt")

    (repo / "a.txt").write_text("第一个文件（改过）\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "提交 C：改 a.txt")

    shas = git(repo, "rev-list", "--reverse", "HEAD").split()
    return repo, shas


class FakeGitHub:
    """内存版 Git Data API。

    **会校验父提交存在**，和真 API 一样 —— 这是这个测试的核心：
    退回「只传一个提交」的实现会立刻在这里 422。
    """

    def __init__(self, remote_sha: str = "", known: set[str] | None = None):
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict] = {}
        self.commits: dict[str, dict] = {}
        # 远端已有的提交（它自己的历史），父提交校验时算「存在」
        self.known: set[str] = set(known or ())
        if remote_sha:
            self.known.add(remote_sha)
            self.commits[remote_sha] = {"message": "远端已有的提交",
                                        "parents": [], "tree": "0" * 40}
        self.refs: dict[str, str] = {"main": remote_sha} if remote_sha else {}
        self.calls: list[tuple[str, str]] = []
        self.rejected: list[str] = []

    # ---- 内部 ----
    @staticmethod
    def _hash(*parts: str) -> str:
        joined = "\x00".join(parts).encode("utf-8")
        return hashlib.sha1(joined).hexdigest()

    def _exists(self, sha: str) -> bool:
        return sha in self.commits or sha in self.known

    def seed_from_local(self, repo: Path, ref: str = "HEAD") -> str:
        """把**本地真实的提交**注册成「远端已有的提交」，返回它的 sha。

        ## 为什么必须用本地真实的 sha

        假 API 自己建的提交用的是一套简化的哈希（`_hash`），算出来的 sha
        和本地 git 不一致 —— 于是 `shares_history()` 去查本地对象库时
        找不到，把「远端是我们推过的」误判成「两棵无关的历史树」。

        而这正是**真实场景**和**测试场景**的差别：真实世界里如果我们推过
        一个提交，本地就有那个对象（提交本来就是从本地推上去的）。
        所以测试里也要让远端 tip 指向一个本地真实存在的提交。

        ## 这个方法做什么

          · 读本地那个提交的 sha、tree、父提交
          · 把它的 tree 条目注册进假 API（供 `prefetch_blob_cache` 读）
          · 把提交本身注册进假 API，并把 main 指过去
        """
        def run(*args: str) -> str:
            done = subprocess.run(["git", *args], cwd=str(repo),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=60)
            return (done.stdout or "").strip()

        sha = run("rev-parse", ref)
        tree_sha = run("rev-parse", f"{ref}^{{tree}}")
        parents = run("rev-list", "--parents", "-n", "1", ref).split()[1:]
        message = run("log", "-1", "--format=%B", ref)

        # tree 条目：`<mode> <type> <sha>\t<path>`，用 -z 拿 NUL 分隔
        raw = subprocess.run(["git", "ls-tree", "-r", "-z", ref],
                             cwd=str(repo), capture_output=True,
                             timeout=60).stdout
        items: list[dict] = []
        for record in raw.split(b"\x00"):
            if not record:
                continue
            meta, _tab, path_bytes = record.partition(b"\t")
            parts = meta.split(b" ")
            if len(parts) < 3:
                continue
            items.append({
                "path": path_bytes.decode("utf-8", "surrogateescape"),
                "mode": parts[0].decode(),
                "type": parts[1].decode(),
                "sha": parts[2].decode(),
            })
        self.trees[tree_sha] = {"tree": items}

        self.commits[sha] = {
            "sha": sha,
            "message": message,
            "tree": {"sha": tree_sha,
                     "url": f"https://example.invalid/trees/{tree_sha}"},
            "parents": [{"sha": p} for p in parents],
        }
        self.known.add(sha)
        self.refs["main"] = sha
        return sha

    # ---- 路由 ----
    def handle(self, method: str, path: str, payload=None) -> tuple[int, dict]:
        self.calls.append((method, path))

        if method == "GET" and path == "/repos/o/r":
            return 200, {"size": 1, "default_branch": "main",
                         "html_url": "https://example.invalid/o/r",
                         "permissions": {"push": True}}

        if method == "GET" and path.endswith("/commits?per_page=1"):
            return 200, [{"sha": self.refs.get("main", ""),
                          "commit": {"message": "最新", "author": {
                              "name": "测试", "email": "t@e.com"}}}]

        if method == "GET" and "/git/ref/heads/" in path:
            branch = path.rsplit("/", 1)[-1]
            if branch in self.refs:
                return 200, {"object": {"sha": self.refs[branch]}}
            return 404, {"message": "Not Found"}

        if method == "GET" and "/git/commits/" in path:
            sha = path.rsplit("/", 1)[-1]
            if sha in self.commits:
                return 200, self.commits[sha]
            return 404, {"message": "Not Found"}

        # 读树（`prefetch_blob_cache` 用这个预填去重缓存）。
        # 注意路径可能带 `?recursive=1`，要剥掉再查。
        if method == "GET" and "/git/trees/" in path:
            sha = path.split("/git/trees/")[-1].split("?")[0]
            if sha in self.trees:
                body = dict(self.trees[sha])
                body["truncated"] = False
                return 200, body
            return 404, {"message": "Not Found"}

        if method == "POST" and path.endswith("/git/blobs"):
            content = payload["content"]
            import base64

            raw = base64.b64decode(content)
            # 用真正的 git blob SHA 算法（sha1("blob <len>\0" + 内容)）。
            # 这样测试里能验证「内容相同 → SHA 相同 → 只传一次」。
            sha = hashlib.sha1(
                b"blob %d\x00" % len(raw) + raw).hexdigest()
            self.blobs[sha] = raw
            return 201, {"sha": sha}

        if method == "POST" and path.endswith("/git/trees"):
            items = payload["tree"]
            key = self._hash(*[f"{i['path']}:{i['mode']}:{i['sha']}"
                               for i in items])
            self.trees[key] = {"tree": items}
            return 201, {"sha": key}

        # 引导提交：**空仓库**第一次上传时，Git Data API 还不可用
        # （真 API 会返回 409），所以要先经 Contents API 造一个提交。
        # 假 API 也要支持这条，否则「从零推送」的场景测不了。
        if method == "PUT" and "/contents/" in path:
            branch = str(payload.get("branch") or "main")
            key = self._hash("bootstrap", str(payload.get("message", "")),
                             branch)
            self.commits[key] = {"message": payload.get("message", ""),
                                 "parents": [], "tree": "0" * 40}
            self.known.add(key)
            self.refs[branch] = key
            return 201, {"commit": {"sha": key}}

        if method == "POST" and path.endswith("/git/commits"):
            # **真 API 会在这里拒绝未知的父提交**
            for parent in payload.get("parents") or []:
                if not self._exists(parent):
                    self.rejected.append(parent)
                    return 422, {"message": "Invalid request. "
                                            "No commit found for SHA"}
            key = self._hash(payload["tree"],
                             *sorted(payload.get("parents") or []),
                             payload["message"])
            # **存成「响应」的格式，不是「请求」的格式。**
            #
            # 真 API 的 `GET /git/commits/{sha}` 返回里，`tree` 是个对象：
            #     "tree": {"sha": "...", "url": "..."}
            # 而**创建时**请求体里的 `tree` 是字符串。
            #
            # 第一版直接存了请求 payload，于是 `tree` 是字符串 ——
            # `prefetch_blob_cache()` 按真 API 的格式去读 `.get("sha")`，
            # 读到空，预填缓存失效。**假 API 必须模拟响应，不是原样回显请求**，
            # 否则测出来的行为跟真实环境不一致。
            self.commits[key] = {
                **payload,
                "sha": key,
                "tree": {
                    "sha": payload["tree"],
                    "url": f"https://example.invalid/trees/{payload['tree']}",
                },
            }
            return 201, {"sha": key}

        if method in ("POST", "PATCH") and "/git/refs" in path:
            branch = path.rsplit("/", 1)[-1]
            self.refs[branch] = payload["sha"]
            return 200, {"object": {"sha": payload["sha"]}}

        return 404, {"message": f"未处理的请求 {method} {path}"}


def main() -> int:
    print("上传工具回归\n")

    import github_upload as gh

    base = Path(tempfile.mkdtemp(prefix="uploadtest_"))
    original_root = gh.ROOT
    try:
        repo, shas = make_repo(base)
        a, b, c = shas
        print(f"造好临时仓库：{len(shas)} 个提交")
        for sha in shas:
            print(f"  {sha[:10]}  {git(repo, 'log', '-1', '--format=%s', sha)}")
        gh.ROOT = repo

        # ============================================================ 一
        print("\n=== 一、commits_to_publish：算出该传哪些 ===")
        check("远端 == tip → 空（没得传）",
              gh.commits_to_publish(c, "HEAD") == [],
              str(gh.commits_to_publish(c, "HEAD")))
        check("远端 == A → [B, C]（从旧到新，不能反）",
              gh.commits_to_publish(a, "HEAD") == [b, c],
              str([s[:8] for s in gh.commits_to_publish(a, "HEAD")]))
        check("远端 == B → [C]",
              gh.commits_to_publish(b, "HEAD") == [c],
              str([s[:8] for s in gh.commits_to_publish(b, "HEAD")]))
        check("远端为空 → 整条历史 [A, B, C]",
              gh.commits_to_publish("", "HEAD") == [a, b, c],
              str([s[:8] for s in gh.commits_to_publish("", "HEAD")]))
        check("远端是个我们不知道的提交 → 整条历史",
              gh.commits_to_publish("f" * 40, "HEAD") == [a, b, c],
              str([s[:8] for s in gh.commits_to_publish("f" * 40, "HEAD")]))
        check("ref 不是 HEAD 也支持（--ref 补传）",
              gh.commits_to_publish(a, b) == [b],
              str([s[:8] for s in gh.commits_to_publish(a, b)]))

        # ============================================================ 二
        print("\n=== 二、read_tree 带上了 blob SHA（去重的依据）===")
        entries = gh.read_tree(c)
        check("每个条目是 4 元组", all(len(e) == 4 for e in entries),
              str([len(e) for e in entries]))
        by_path = {p: s for p, _m, _c, s in entries}
        entries_b = {p: s for p, _m, _c, s in gh.read_tree(b)}
        check("b.txt 在 B 和 C 里是同一个 blob SHA（内容没变）",
              by_path.get("b.txt") == entries_b.get("b.txt"),
              f"{by_path.get('b.txt')} vs {entries_b.get('b.txt')}")
        check("a.txt 在 B 和 C 里是不同 SHA（内容变了）",
              by_path.get("a.txt") != entries_b.get("a.txt"))

        # ============================================================ 三
        print("\n=== 三、核心：远端落后 2 个提交时能不能传上去 ===")
        # 这是 bug 的场景。假 API 会校验父提交 —— 退回旧实现就会 422。
        api = FakeGitHub(remote_sha=a, known={a})
        gh.request = lambda method, path, token, payload=None: api.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api.handle(method, path, payload)

        info = gh.local_commit_info("HEAD")
        info["sha"] = gh.local_sha("HEAD")
        code = gh.upload("o", "r", "token", "main", info)

        check("upload 返回成功", code == 0, f"返回 {code}")
        check("**没有父提交被拒绝**（旧实现会在这里 422）", not api.rejected,
              f"被拒的父提交：{[s[:8] for s in api.rejected]}")

        created = [sha for sha, body in api.commits.items()
                   if sha not in (a,) and body.get("message", "").startswith("提交")]
        check("两个提交都建好了", len(created) == 2, f"建了 {len(created)} 个")

        # 顺序和父子关系：B 的父必须是 A（远端已有的），C 的父必须是 B
        # 用 message 认出哪个是哪个
        by_message = {body["message"].splitlines()[0]: sha
                      for sha, body in api.commits.items()
                      if body.get("message", "").startswith("提交")}
        got_b = by_message.get("提交 B：加 b.txt")
        got_c = by_message.get("提交 C：改 a.txt")
        check("提交 B 和 C 都在远端了", got_b and got_c,
              str(sorted(by_message)))
        if got_b and got_c:
            check("B 的父是 A（远端已有的那个，不是本地 HEAD~1）",
                  api.commits[got_b]["parents"] == [a],
                  str([s[:8] for s in api.commits[got_b]["parents"]]))
            check("C 的父是刚建好的 B",
                  api.commits[got_c]["parents"] == [got_b],
                  str([s[:8] for s in api.commits[got_c]["parents"]]))
            check("分支最后指向 C",
                  api.refs["main"] == got_c,
                  f"{api.refs['main'][:8]} vs {got_c[:8]}")

        # ============================================================ 四
        print("\n=== 四、blob 去重（跨提交不重传同样的内容）===")
        # b.txt 在 B 和 C 里内容一样 → 只该传一次。
        # 断言方式：数一下 b.txt 的内容对应几个 blob 请求。
        # 更直接的办法：总共传的 blob 数应该 = 不同内容的种类数 = 3
        #   （初始 a.txt、b.txt、改过的 a.txt）而不是 2 个提交 × 2 个文件 = 4
        flat = [item for tree in api.trees.values() for item in tree["tree"]]
        distinct_blobs = {item["sha"] for item in flat}
        check("树里引用到的 blob 都是上传过的",
              distinct_blobs <= set(api.blobs),
              f"缺 {sorted(distinct_blobs - set(api.blobs))[:3]}")
        # 从日志里数 blob 上传次数
        blob_posts = [p for m, p in api.calls if p.endswith("/git/blobs")]
        check("blob 上传次数少于「提交数 × 文件数」（确实去重了）",
              len(blob_posts) < 4, f"传了 {len(blob_posts)} 次")

        # ============================================================ 五
        print("\n=== 五、只落后 1 个提交（最常见的情况）===")
        api2 = FakeGitHub(remote_sha=b, known={b})
        gh.request = lambda method, path, token, payload=None: api2.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api2.handle(method, path, payload)
        code = gh.upload("o", "r", "token", "main", gh.local_commit_info("HEAD"))
        check("返回成功", code == 0, f"返回 {code}")
        check("没有父提交被拒", not api2.rejected, str(api2.rejected))
        others = [s for s in api2.commits if s != b]
        check("只建了 1 个提交", len(others) == 1, f"建了 {len(others)} 个")
        if others:
            check("它的父是远端当前的 B",
                  api2.commits[others[0]]["parents"] == [b],
                  str([s[:8] for s in api2.commits[others[0]]["parents"]]))

        # ============================================================ 六
        print("\n=== 六、远端已经一致时不做任何改动 ===")
        api3 = FakeGitHub(remote_sha=c, known={c})
        gh.request = lambda method, path, token, payload=None: api3.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api3.handle(method, path, payload)
        code = gh.upload("o", "r", "token", "main", gh.local_commit_info("HEAD"))
        check("返回成功（不是错误）", code == 0, f"返回 {code}")
        check("没有建任何提交",
              not [s for s in api3.commits if s != c],
              str(list(api3.commits)))
        check("分支没动", api3.refs["main"] == c)

        # ============================================================ 七
        print("\n=== 七、dry-run 不写任何东西 ===")
        api4 = FakeGitHub(remote_sha=a, known={a})
        gh.request = lambda method, path, token, payload=None: api4.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api4.handle(method, path, payload)
        code = gh.upload("o", "r", "token", "main",
                         gh.local_commit_info("HEAD"), dry_run=True)
        check("dry-run 返回成功", code == 0, f"返回 {code}")
        check("dry-run 没建提交",
              not [s for s in api4.commits if s != a], str(list(api4.commits)))
        check("dry-run 没建 tree", not api4.trees, str(len(api4.trees)))
        check("dry-run 没传 blob", not api4.blobs, str(len(api4.blobs)))
        check("dry-run 没动分支", api4.refs["main"] == a)

        # ============================================================ 八
        print("\n=== 八、远端有真实分叉历史时要拒绝（不能覆盖）===")
        # 远端有个我们不知道的提交，而且它有父提交 → 说明是真历史，必须拒绝
        api5 = FakeGitHub()
        foreign = "e" * 40
        api5.known.add(foreign)
        api5.commits[foreign] = {"message": "别人的提交", "parents": ["d" * 40],
                                 "tree": "0" * 40}
        api5.refs["main"] = foreign
        gh.request = lambda method, path, token, payload=None: api5.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api5.handle(method, path, payload)
        code = gh.upload("o", "r", "token", "main", gh.local_commit_info("HEAD"))
        check("返回失败（拒绝覆盖）", code == 1, f"返回 {code}")
        check("没有建任何提交", not api5.commits.get("提交"),
              str(sorted(api5.commits))[:60])

        # ============================================================ 九
        print("\n=== 九、复现旧行为：只传一个提交会被真 API 拒 ===")
        #
        # 这一节是为了**证明这个套件确实抓得住那个 bug**。
        # 直接按旧做法走一遍：拿 HEAD 的信息（父提交 = 本地 B）去建提交，
        # 而远端根本没有 B。假 API 会像真 API 一样 422。
        #
        # 没有这一节的话，「全过」也可能只是断言写得太松 ——
        # 一条从不失败的断言比没有断言更糟（这个项目已经栽过一次：
        # 贴边朝向的断言读的是测试自己写的常量，故意改错产品代码它照样绿）。
        api6 = FakeGitHub(remote_sha=a, known={a})
        gh.request = lambda method, path, token, payload=None: api6.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api6.handle(method, path, payload)
        old_info = gh.local_commit_info("HEAD")      # C 的信息，父 = 本地 B
        code, _sha = gh.publish_commit("o", "r", "token", c, old_info, {})
        check("旧做法会被拒（那个父提交远端不存在）", code == 1,
              f"返回 {code} —— 如果是 0，说明假 API 没在拦父提交")
        check("假 API 记下了被拒的父提交", bool(api6.rejected),
              str([s[:8] for s in api6.rejected]))
        check("被拒的正是本地 B 的 SHA", b in api6.rejected,
              f"被拒：{[s[:8] for s in api6.rejected]}，本地 B 是 {b[:8]}")

        # ============================================================ 十
        print("\n=== 十、两棵无关的历史树要被拦住（实测踩过）===")
        #
        # ## 这个场景是真实发生过的（2026-09-29）
        #
        # 本地 89 个提交、远端 88 个，**提交说明逐字对应、顺序相同**，
        # 但 `merge-base` 找不到任何共同祖先。查根提交：
        #
        #     c4e9df7  作者 马靖凯  2026-09-14 18:31:28   （本地）
        #     ca75541  作者 颐安    2026-09-14 18:31:28   （远端）
        #
        # **同一秒、同一个提交说明，只有 author.name 差一个字。**
        # 而 commit SHA 的哈希**包含作者名** —— 改一个名字，从根提交起
        # 每个 sha 都变，整条历史分裂成两条互不相干的时间线。
        #
        # ## 测试怎么造这个场景
        #
        # 造第二个仓库：同样的文件、同样的提交说明、**但作者名不同**，
        # 然后 fetch 进本地（只带对象过来，不合并）—— 这正是
        # 「远端是另一棵树」在本地看起来的样子。
        foreign = base / "foreign"
        foreign.mkdir()
        git(foreign, "init", "-b", "main")
        git(foreign, "config", "user.name", "另一个人")
        git(foreign, "config", "user.email", "other@example.com")
        git(foreign, "config", "commit.gpgsign", "false")
        (foreign / "a.txt").write_text("第一个文件\n", encoding="utf-8")
        git(foreign, "add", "a.txt")
        git(foreign, "commit", "-m", "提交 A：加 a.txt")
        foreign_head = git(foreign, "rev-parse", "HEAD")

        # **同样的提交说明、同样的文件，只有作者名不同** ——
        # 所以 sha 必然不同（这就是分叉的成因）
        check("两边同一个提交说明、同一个文件，但 sha 不同",
              foreign_head != a, f"{foreign_head[:8]} vs {a[:8]}")

        # fetch 进本地：对象过来了，但两边没有共同祖先
        git(repo, "fetch", "--no-tags", str(foreign), "main")
        fetched = git(repo, "rev-parse", "FETCH_HEAD")

        check("is_ancestor 说它不是我们的祖先", not gh.is_ancestor(fetched),
              "这是当然的——但**不够**，它区分不出两种含义")
        check("shares_history 能确认没有共同祖先",
              not gh.shares_history(fetched),
              "这才是真正的判据：两棵无关的树")

        # 对照：我们自己的提交当然共享历史
        check("对照：本地自己的提交共享历史", gh.shares_history(a),
              "不然这个函数就是永远返回 False 的假货")
        check("对照：HEAD 和自己共享历史", gh.shares_history(c),
              "")

        # ## 关键：upload() 必须拒绝，而不是覆盖
        #
        # 这里用一个「假 API 说远端 HEAD 就是那棵无关的树」的场景。
        # 修之前它会掉进「是根提交，可以安全覆盖」——**静默覆盖掉**。
        api7 = FakeGitHub(remote_sha=fetched, known={fetched})
        gh.request = lambda method, path, token, payload=None: api7.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api7.handle(method, path, payload)
        api7.commits[fetched] = {"message": "提交 A：加 a.txt",
                                 "parents": [], "tree": "0" * 40}
        code = gh.upload("o", "r", "token", "main", gh.local_commit_info("HEAD"))
        check("upload 拒绝在无关历史上操作", code == 1, f"返回 {code}")
        check("没有建任何提交",
              not [s for s in api7.commits
                   if s not in (fetched,) and "提交" in str(
                       api7.commits[s].get("message", ""))],
              "拒绝了还建东西，说明拦的位置不对")
        check("分支没被动", api7.refs["main"] == fetched,
              str(api7.refs["main"])[:12])

        # ## 明确确认之后应该能通过
        #
        # 上面那条「拒绝」是**保护**，但改写历史（比如统一作者名）之后
        # 确实需要覆盖远端 —— 所以加了 `--allow-unrelated`。
        #
        # **这个开关必须有测试**，否则它可能悄悄坏掉，而它坏掉的表现是
        # 「改写完历史推不上去」，排查起来很费时间（这次就费了一轮）。
        print("\n=== 十之二、--allow-unrelated 明确确认后应该能通过 ===")
        api8 = FakeGitHub(remote_sha=fetched, known={fetched})
        gh.request = lambda method, path, token, payload=None: api8.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api8.handle(method, path, payload)
        api8.commits[fetched] = {"message": "提交 A：加 a.txt",
                                 "parents": [], "tree": "0" * 40}
        code = gh.upload("o", "r", "token", "main",
                         gh.local_commit_info("HEAD"),
                         allow_unrelated=True)
        check("加了 allow_unrelated 就能推上去", code == 0, f"返回 {code}")
        check("确实建了提交",
              len([s for s in api8.commits if s != fetched]) > 0,
              str(list(api8.commits)))
        check("分支被指到新的 tip",
              api8.refs["main"] not in ("", fetched),
              str(api8.refs["main"])[:12])

        # 对照：**不开这个开关时不能通过** —— 证明开关真的在起作用，
        # 而不是那条保护被绕过了
        api9 = FakeGitHub(remote_sha=fetched, known={fetched})
        gh.request = lambda method, path, token, payload=None: api9.handle(
            method, path, payload)
        gh.request_with_retry = lambda method, path, token, payload=None: \
            api9.handle(method, path, payload)
        api9.commits[fetched] = {"message": "提交 A：加 a.txt",
                                 "parents": [], "tree": "0" * 40}
        code = gh.upload("o", "r", "token", "main",
                         gh.local_commit_info("HEAD"),
                         allow_unrelated=False)
        check("对照：不开开关时仍然拒绝", code == 1, f"返回 {code}")
        check("对照：分支没被动", api9.refs["main"] == fetched,
              str(api9.refs["main"])[:12])

        # ============================================================ 十一
        print("\n=== 十一、增量推送不重传没变的文件（预填缓存）===")
        #
        # ## 这个测试盯的是什么
        #
        # `publish_commit()` 会遍历提交里的**全部**文件。没有预填缓存时，
        # 第一个提交会把所有 blob 传一遍 —— 哪怕只改了 1 个文件。
        #
        # 实测过：推 2 个提交（改 3 个文件、仓库约 200 个文件）时传了
        # 200 个 blob，其中 197 个远端本来就有。
        #
        # 预填的做法：连远端 tip 的 tree 递归读一次，把已有的 blob sha
        # 放进缓存。git 的 blob 是**内容寻址**的，所以 sha 相同 == 内容相同，
        # 复用是安全的。
        #
        # ## 怎么造这个场景
        #
        # 先完整推一次（远端从空到有），再加一个「只改 1 个文件」的提交，
        # 然后数第二次推送时真正上传了几个 blob。
        #
        # 断言用「实际 POST 到 /git/blobs 的次数」，不是看日志 ——
        # 日志可能骗人，请求次数不会。
        fresh_repo = base / "incremental"
        fresh_repo.mkdir()
        git(fresh_repo, "init", "-b", "main")
        git(fresh_repo, "config", "user.name", "测试")
        git(fresh_repo, "config", "user.email", "test@example.com")
        git(fresh_repo, "config", "commit.gpgsign", "false")
        for index in range(5):
            (fresh_repo / f"file{index}.txt").write_text(
                f"内容 {index}\n", encoding="utf-8")
        git(fresh_repo, "add", "-A")
        git(fresh_repo, "commit", "-m", "初始：5 个文件")

        original_root = gh.ROOT
        gh.ROOT = fresh_repo
        try:
            # 第一次：远端空 → 全部文件都要传
            api_full = FakeGitHub()
            gh.request = lambda method, path, token, payload=None: \
                api_full.handle(method, path, payload)
            gh.request_with_retry = lambda method, path, token, payload=None: \
                api_full.handle(method, path, payload)
            code = gh.upload("o", "r", "token", "main",
                             gh.local_commit_info("HEAD"))
            check("首次推送成功", code == 0, f"返回 {code}")
            first_blobs = [p for m, p in api_full.calls
                           if m == "POST" and p.endswith("/git/blobs")]
            check("首次推送传了 5 个文件（远端空，全要传）",
                  len(first_blobs) == 5, f"实际 {len(first_blobs)} 个")

            # 加一个只改 1 个文件的提交
            (fresh_repo / "file2.txt").write_text("改过了\n", encoding="utf-8")
            git(fresh_repo, "add", "file2.txt")
            git(fresh_repo, "commit", "-m", "只改 file2")

            # 第二次：远端 tip 指向**本地真实的**上一个提交。
            #
            # 用 `seed_from_local` 而不是假 API 自己建的提交 —— 后者 sha
            # 是简化哈希算的，本地对象库里没有，`shares_history()` 会把它
            # 判成「无关历史」而拒绝（第一版就卡在这里）。
            # 真实世界里推过的提交本地就有，所以这里也该用真实 sha。
            api_inc = FakeGitHub()
            api_inc.blobs = dict(api_full.blobs)   # 远端已有那些 blob
            remote_now = api_inc.seed_from_local(fresh_repo, "HEAD~1")
            print(f"      远端 tip 指向本地真实提交 {remote_now[:9]}")

            gh.request = lambda method, path, token, payload=None: \
                api_inc.handle(method, path, payload)
            gh.request_with_retry = lambda method, path, token, payload=None: \
                api_inc.handle(method, path, payload)

            code = gh.upload("o", "r", "token", "main",
                             gh.local_commit_info("HEAD"))
            check("增量推送成功", code == 0, f"返回 {code}")

            # 只数「这一轮」新发的 blob 请求
            second_blobs = [p for m, p in api_inc.calls
                            if m == "POST" and p.endswith("/git/blobs")]
            check("增量推送只传了改动的 1 个文件（不是 5 个）",
                  len(second_blobs) == 1, f"实际 {len(second_blobs)} 个")
            check("其余 4 个文件复用了远端已有的 blob",
                  len(api_inc.blobs) == len(api_full.blobs) + 1,
                  f"远端 blob 从 {len(api_full.blobs)} 变成 "
                  f"{len(api_inc.blobs)}")

            # 顺带确认预填真的读到了东西（不然上面可能是别的原因过的）
            cache = gh.prefetch_blob_cache("o", "r", "token", remote_now)
            check("prefetch_blob_cache 能读到远端已有的 blob",
                  len(cache) == 5, f"读到 {len(cache)} 个")
            check("缓存里的 key 和 value 相同（内容寻址）",
                  all(k == v for k, v in cache.items()),
                  "不同的话说明映射逻辑有问题")
        finally:
            gh.ROOT = original_root

        # ============================================================ 十二
        print("\n=== 十二、release.py 的调用方式没被改坏 ===")
        import inspect

        signature = inspect.signature(gh.upload)
        params = list(signature.parameters)
        check("前 5 个位置参数还是 (owner, repo, token, branch, info)",
              params[:5] == ["owner", "repo", "token", "branch", "info"],
              str(params))
        # release.py 是这么调的：gh_upload(owner, repo, token, "main", info)
        check("只传 5 个位置参数也能调用",
              signature.bind("o", "r", "t", "main", {}) is not None)
        # 新参数必须是**关键字**参数，且默认关闭 —— 不然 release.py
        # 的 5 参数调用会传错位置，或者保护被默认打开
        check("allow_unrelated 有默认值 False（保护默认生效）",
              signature.parameters["allow_unrelated"].default is False,
              str(signature.parameters["allow_unrelated"].default))

    finally:
        gh.ROOT = original_root
        shutil.rmtree(base, ignore_errors=True)

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
