r"""按需使用 Gitee 镜像 —— 不再全局改写 github 地址。

## 背景：为什么要把那条全局规则删掉

以前的 `~/.gitconfig` 里有这么一条：

```ini
[url "https://gitee.com/mirrors/"]
    insteadOf = https://github.com/
```

这是 git 的「URL 重写」：**任何** `https://github.com/...` 都会被
静默改成 `https://gitee.com/mirrors/...`。

它有两个真实的害处，都在这个项目上发生过：

1. **你以为在推 github，实际推的是 gitee。**
   `git remote -v` 显示 github，`git push` 却连 gitee —— 因为
   `remote -v` 显示配置里的原始值，而重写发生在真正发起连接时。

2. **弹出的登录框问的是 gitee，不是 github。**
   用 `git push https://github.com/...` 想让凭据管理器记住 github 的
   token，结果它弹出 `Enter your credentials for 'https://gitee.com/'` ——
   填进去也只会存到 gitee 名下，而真正需要凭据的 github 仍然没有。

所以全局规则已删除。需要镜像时**显式用这个脚本**，不要改全局配置。

## 用法

```powershell
# 看当前状态（有没有全局规则、origin 实际连哪）
.venv\Scripts\python.exe tools\gitmirror.py status

# 用镜像跑一次 git 命令 —— **不改任何配置**，只影响这一次
.venv\Scripts\python.exe tools\gitmirror.py run clone https://github.com/0Sun-shine0/PawPet
.venv\Scripts\python.exe tools\gitmirror.py run push origin main

# 确实想全局启用时（会写进 ~/.gitconfig，影响所有仓库）
.venv\Scripts\python.exe tools\gitmirror.py on
.venv\Scripts\python.exe tools\gitmirror.py off
```

## 为什么用脚本而不是加 `-c` 参数

`git -c 'url."https://gitee.com/mirrors/".insteadOf="https://github.com/"' ...`
在 PowerShell 里的引号嵌套很容易写错（这个项目的工具链已经因为引号问题
踩过好几次）。脚本用 `subprocess` 的参数列表传，**不经过 shell**，
不依赖使用者的引号习惯。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MIRROR = "https://gitee.com/mirrors/"
UPSTREAM = "https://github.com/"
CONFIG_KEY = f"url.{MIRROR}.insteadOf"


def git(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    """跑 git，返回 (退出码, stdout, stderr)。

    参数用列表传，不拼 shell 字符串 —— 见模块 docstring 的说明。
    """
    done = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    return done.returncode, (done.stdout or "").strip(), \
        (done.stderr or "").strip()


def global_rule() -> str:
    """全局配置里这条重写规则的值（没有则空串）。"""
    code, out, _err = git("config", "--global", "--get", CONFIG_KEY)
    return out if code == 0 else ""


def cmd_status(_args: argparse.Namespace) -> int:
    print("=== 全局 URL 重写规则 ===")
    rule = global_rule()
    if rule:
        print(f"  {CONFIG_KEY}")
        print(f"  = {rule}")
        print()
        print("  **这条会静默改写所有 github 地址**：")
        print(f"    https://github.com/xxx  →  {MIRROR}xxx")
        print("  建议用 `off` 关掉，需要时用 `run`。")
    else:
        print("  （没有）—— github 地址不会被改写 ✓")

    print()
    print("=== 当前仓库的 origin ===")
    code, configured, _err = git("config", "remote.origin.url")
    if code != 0:
        print("  （这个目录没有 origin）")
        return 0
    _code, effective, _err = git("ls-remote", "--get-url", "origin")
    print(f"  配置里写的：{configured}")
    print(f"  实际会连的：{effective}")
    if configured != effective:
        print()
        print("  **两者不一致 —— 有重写规则在起作用**")
    else:
        print("  （一致 ✓）")

    print()
    print("=== 系统里其它仓库 ===")
    # **只在规则确实存在时才标「受影响」。**
    # 第一版只要 URL 里有 github.com 就标记，结果规则删掉之后
    # 还在标「会受重写规则影响」—— 那是错的，会让人以为没删干净。
    affected = bool(global_rule())
    parents = {ROOT.parent}
    for candidate in sorted(parents):
        try:
            for child in candidate.iterdir():
                if not (child.is_dir() and (child / ".git").exists()):
                    continue
                _c, url, _e = git("-C", str(child), "config",
                                  "remote.origin.url")
                mark = ""
                if affected and "github.com" in url:
                    mark = "  ← 会被重写规则改写"
                elif "github.com" in url:
                    mark = "  （走 github）"
                print(f"  {child}{mark}")
        except OSError:
            pass
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """用镜像跑一次命令，不改任何配置。"""
    if not args.git_args:
        print("用法：gitmirror.py run <git 的子命令和参数>")
        print("例如：gitmirror.py run clone https://github.com/a/b")
        return 2

    # 用 -c 传一次性的配置覆盖。参数列表传，不经过 shell。
    command = ["git", "-c", f"{CONFIG_KEY}={UPSTREAM}", *args.git_args]
    print(f"用镜像跑：git {' '.join(args.git_args)}")
    print(f"  （临时重写 {UPSTREAM} → {MIRROR}，只影响这一次）")
    print()
    try:
        done = subprocess.run(command, cwd=str(ROOT), timeout=1800)
        return done.returncode
    except KeyboardInterrupt:
        print("\n已取消")
        return 130
    except subprocess.TimeoutExpired:
        print("\n超时")
        return 1


def cmd_on(_args: argparse.Namespace) -> int:
    existing = global_rule()
    if existing:
        print(f"已经是启用状态（{CONFIG_KEY} = {existing}）")
        return 0
    code, _out, err = git("config", "--global", CONFIG_KEY, UPSTREAM)
    if code != 0:
        print(f"[XX] 设置失败：{err[:200]}")
        return 1
    print("已全局启用镜像重写。")
    print()
    print("**提醒**：这会静默改写所有 github 地址，包括其它仓库的。")
    print("          `git remote -v` 会显示 github 但实际连 gitee。")
    print("          不需要时请跑 `off`。")
    return 0


def cmd_off(_args: argparse.Namespace) -> int:
    if not global_rule():
        print("本来就是关闭的")
        return 0
    code, _out, err = git("config", "--global", "--unset", CONFIG_KEY)
    if code != 0:
        print(f"[XX] 取消失败：{err[:200]}")
        return 1
    print("已关闭全局镜像重写 —— github 地址不再被改写。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按需使用 Gitee 镜像（不改全局配置）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("## 用法", 1)[-1] if "## 用法" in __doc__ else "")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="看当前状态").set_defaults(func=cmd_status)

    run_parser = sub.add_parser(
        "run", help="用镜像跑一次 git 命令（不改配置）")
    run_parser.add_argument("git_args", nargs=argparse.REMAINDER,
                            help="git 的子命令和参数")
    run_parser.set_defaults(func=cmd_run)

    sub.add_parser("on", help="全局启用（影响所有仓库）").set_defaults(
        func=cmd_on)
    sub.add_parser("off", help="全局关闭").set_defaults(func=cmd_off)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
