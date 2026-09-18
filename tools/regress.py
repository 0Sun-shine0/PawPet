r"""全量回归：一个入口跑完所有套件，输出压到最短。

为什么单独做这个
----------------
以前每次回归都是临时拼一段 PowerShell 循环，有两个毛病：

1. **套件清单会漂。** `build.py` 里有一份 24 个的清单，我手写循环时用
   的是另一份 33 个的 —— 差的 9 个正好是 QML/界面那批。也就是说打包前
   自测**根本不会发现 QML 语法错误**（实测踩过：一个 rgba 写法炸掉
   Pet.qml，而那 9 个里有 4 个会红）。现在清单只有这一份，
   `build.py` 从这儿导入。

2. **输出太长。** 每个套件都会打几十行 [ok]，全量跑一遍几千行，
   人和模型都读不完。这里默认每个套件只打一行；失败才展开它自己的
   [XX] 行。完整日志仍然写文件，需要时再看。

用法
----
    .venv\Scripts\python.exe tools\regress.py              # 全部
    .venv\Scripts\python.exe tools\regress.py --list       # 看清单
    .venv\Scripts\python.exe tools\regress.py mood exttest # 按名字筛选
    .venv\Scripts\python.exe tools\regress.py --quiet      # 只打结论
    .venv\Scripts\python.exe tools\regress.py --core       # 只跑核心档

退出码：0 全通过，1 有失败。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)
TOOLS = ROOT / "tools"
LOG_DIR = ROOT / ".cache" / "regress"

# ==========================================================================
#  套件清单 —— **唯一来源**。build.py 从这里导入。
# ==========================================================================
#
# 分档说明：
#   core  逻辑与安全，坏了一定是 bug，必须在打包前跑
#   ui    QML/界面，语法或布局坏了用户直接看不到东西
#   ai    AI 能力（记忆/知识库/工具/扩展）
#   pet   宠物动画与形象
#   reg   曾经真出过的 bug，钉住别再回来
#
# 加新套件的原则：**「坏了用户会看到」的就加进来。**
# 全跑一遍几分钟，比发一个坏包出去便宜得多。
SUITES: list[tuple[str, str, str]] = [
    # ---- 逻辑与安全
    ("core", "核心逻辑", "selftest.py"),
    ("core", "文件读写安全", "filetest.py"),
    ("core", "工具消息历史", "historytest.py"),
    ("core", "对话持久化", "convtest.py"),
    ("core", "Markdown 渲染", "mdtest.py"),
    ("core", "字体与字形", "glyphcheck.py"),
    # ---- 界面（打包前自测原来漏了这一档）
    ("ui", "QML 加载", "qmlcheck.py"),
    ("ui", "界面自检", "uicheck.py"),
    ("ui", "界面缩放", "uiscaletest.py"),
    ("ui", "布局不变量", "layoutcheck.py"),
    ("ui", "两栏布局", "layouttest.py"),
    ("ui", "气泡渲染", "bubshot.py"),
    ("ui", "弹窗与指令栏", "popupcheck.py"),
    ("ui", "启动冒烟", "smoketest.py"),
    ("ui", "界面配色定制", "themetest.py"),
    ("ui", "模型配置向导", "setuptest2.py"),
    ("ui", "安装向导界面", "setupui_test.py"),
    ("ui", "设置页截图归档", "screenshothist.py"),
    # ---- AI 能力
    ("ai", "AI 模块", "aitest.py"),
    ("ai", "Agent 端到端", "agenttest.py"),
    ("ai", "提问交互", "asktest.py"),
    ("ai", "跨会话记忆", "memtest.py"),
    ("ai", "自动记忆", "autolearntest.py"),
    ("ai", "知识库", "kbtest.py"),
    ("ai", "失败自纠", "advisortest.py"),
    ("ai", "批量与合并确认", "batchtest.py"),
    ("ai", "自定义工具", "exttest.py"),
    ("ai", "MCP 接线", "mcptest.py"),
    ("ai", "MCP 可达性", "mcpwiringtest.py"),
    ("ai", "可用性功能", "featuretest.py"),
    ("ai", "UI Automation", "uia_test.py"),
    # ---- 宠物
    ("pet", "宠物情绪动画", "moodtest.py"),
    # ---- 曾经真出过的 bug
    ("reg", "轮次串台", "turntest.py"),
    ("reg", "任务收尾", "finishtest.py"),
    ("reg", "步数上限", "steptest.py"),
    ("reg", "用户反馈三问题", "fixtest.py"),
]

# 每档的中文名，给输出用
GROUP_LABEL = {
    "core": "逻辑与安全",
    "ui": "界面",
    "ai": "AI 能力",
    "pet": "宠物",
    "reg": "回归钉",
}
GROUP_ORDER = ["core", "ui", "ai", "pet", "reg"]

# 单个套件最多等多久。有窗口/子进程的套件慢一些，给足。
TIMEOUT = 900


# ==========================================================================
#  运行
# ==========================================================================
def run_suite(script: str, log_path: Path) -> tuple[int, float, str]:
    """跑一个套件。返回 (退出码, 用时秒, 输出全文)。"""
    path = TOOLS / script
    if not path.exists():
        return 127, 0.0, f"找不到 {path}"

    started = time.time()
    try:
        result = subprocess.run(
            [str(PYTHON), str(path)],
            cwd=str(ROOT), capture_output=True, text=True,
            # 显式 encoding：不给的话按系统区域设置解码，
            # 中文输出会变成乱码（这个坑踩过好几次）
            encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
        code = result.returncode
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        code = 124
        output = f"超时（超过 {TIMEOUT} 秒）"
    except OSError as exc:
        code = 126
        output = f"启动失败：{exc}"

    elapsed = time.time() - started
    try:
        log_path.write_text(output, encoding="utf-8")
    except OSError:
        pass
    return code, elapsed, output


def summarize(output: str, code: int, limit: int = 4) -> list[str]:
    """从输出里抠出「为什么失败」——只取 [XX] 行和最后一行。"""
    lines = [ln.rstrip() for ln in output.splitlines()]
    bad = [ln.strip() for ln in lines if "[XX]" in ln]
    if not bad:
        # 不是用 check() 报错的（比如异常退出），退而取最后几行非空输出
        bad = [ln.strip() for ln in lines if ln.strip()][-3:]
    return bad[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="小爪全量回归",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*",
                        help="只跑名字里含这些词的套件（可多个）")
    parser.add_argument("--list", action="store_true", help="列出套件就退出")
    parser.add_argument("--quiet", action="store_true",
                        help="只打最后一行结论（给脚本/模型用）")
    parser.add_argument("--core", action="store_true", help="只跑核心档")
    parser.add_argument("--group", action="append", default=[],
                        help=f"只跑某档：{', '.join(GROUP_ORDER)}")
    parser.add_argument("--stop-on-fail", action="store_true",
                        help="第一个失败就停（改代码时快速反馈）")
    args = parser.parse_args()

    if args.list:
        for group in GROUP_ORDER:
            items = [s for s in SUITES if s[0] == group]
            print(f"[{group}] {GROUP_LABEL[group]}（{len(items)}）")
            for _g, label, script in items:
                print(f"    {script:22s} {label}")
        print(f"\n共 {len(SUITES)} 个套件")
        return 0

    selected = list(SUITES)
    if args.core:
        selected = [s for s in selected if s[0] == "core"]
    if args.group:
        wanted = set(args.group)
        selected = [s for s in selected if s[0] in wanted]
    if args.names:
        keys = [k.lower() for k in args.names]
        selected = [s for s in selected
                    if any(k in s[1].lower() or k in s[2].lower() for k in keys)]
    if not selected:
        print("没有匹配的套件。用 --list 看清单。")
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not args.quiet:
        print(f"小爪全量回归 · {len(selected)} 个套件")
        print("─" * 62)

    failures: list[tuple[str, str, list[str]]] = []
    passed = 0
    started = time.time()

    for group, label, script in selected:
        code, elapsed, output = run_suite(script, LOG_DIR / f"{script}.log")
        ok = code == 0
        if ok:
            passed += 1
        else:
            failures.append((script, label, summarize(output, code)))
        if not args.quiet:
            mark = "ok " if ok else "XX "
            # 名字对齐，一屏能扫完
            print(f"  {mark} {label:14s} {elapsed:6.1f}s  {script}")
        if not ok and args.stop_on_fail:
            break

    total_time = time.time() - started
    minutes = int(total_time // 60)

    if args.quiet:
        if failures:
            print(f"FAIL {passed}/{len(selected)} in {minutes}m{total_time % 60:.0f}s")
            for script, label, why in failures:
                print(f"  - {label}（{script}）")
                for line in why[:3]:
                    print(f"      {line[:100]}")
            print(f"  日志：{LOG_DIR}")
            return 1
        print(f"PASS {passed}/{len(selected)} in {minutes}m{total_time % 60:.0f}s")
        return 0

    print("─" * 62)
    if not failures:
        print(f"全部通过 · {passed}/{len(selected)} · 用时 {minutes}分{total_time % 60:.0f}秒")
        return 0

    print(f"失败 {len(failures)} 个（通过 {passed}/{len(selected)}）"
          f" · 用时 {minutes}分{total_time % 60:.0f}秒")
    for script, label, why in failures:
        print(f"\n  [XX] {label}（{script}）")
        for line in why:
            print(f"        {line[:110]}")
    print(f"\n  完整日志：{LOG_DIR}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
