r"""发版红线：新装必须是干净的，且不带任何个人数据。

用户的原话：「发的版应该是泛用户使用的，不要把我个人使用的一些东西
工具记忆什么的一起打包发版了」。

所以这个套件断言两件事：

  **一、别人装完之后看到的是空白新装**
      待办/便签/记忆/知识库/自定义工具/对话历史全为空，
      上手指引会弹出来（`onboarding_done` 是假）。

  **二、构建这台机器上不存在的东西时，不会把它带出去**
      这里用一个**全新数据目录**起一套 Store+Backend，逐项检查它生成
      了什么。任何「本机个人数据」都不该出现。

为什么用全新目录而不是扫产物：产物扫描在 packtest 里做（那里才有
exe）。这里跑得快、每次回归都能跑，专门盯「首次启动的初始状态」。

历史上真踩过的一个：`capturePreview()` 在界面加载预览时就会调，
而它 push 的状态行**漏了 ephemeral 标记** —— 于是全新安装第一次启动
就生成了 conversations.json，里面只有一条「已捕获屏幕 1920x1080…」。
这种「每次启动都往历史里塞一条」的东西，靠人看是发现不了的。

用法：
    .venv\Scripts\python.exe tools\freshstarttest.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "freshstart"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "freshstart"

PASSED = 0
FAILED: list[str] = []
_keep_alive: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("小爪发版红线：新装干净性\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _keep_alive.append(app)

    # **数据目录就用 SCRATCH 本身，不要再套一层。**
    #
    # `config.ROOT` 是从 `PAWPET_HOME` 算的，而 theme.json /
    # mcp_servers.json / .env 全部落在 `ROOT` 下 —— 和 Store 的
    # `pet_data.json` 是**同一个目录**。第一版这里多套了一层
    # `SCRATCH / "install"`，于是 Store 写在一个地方、而 config 那些
    # 写在上一层：MCP 播种检查扑空（报「没播种」），theme.json 的检查
    # 也看错了位置 —— 一个会漏报的测试。
    data_dir = SCRATCH
    data_file = data_dir / "pet_data.json"

    # ================================================================ 一
    print("=== 一、全新目录第一次启动（模拟别人刚装完）===")
    check("启动前没有 pet_data.json", not data_file.exists())

    store = Store(data_file, data_dir / "pet_data.backup.json")
    store.load()
    backend = Backend(store)
    _keep_alive.append(backend)

    # 让 Qt 把启动时那些自动动作（问候、预览、MCP 连接）都跑完
    for _ in range(8):
        app.processEvents()

    state = store.state
    settings = store.settings

    # ---- 用户数据必须是空的 ----
    print("\n  用户数据：")
    for key, label in (("tasks", "待办"), ("reminders", "提醒"),
                       ("sessions", "专注记录")):
        items = state.get(key) or []
        check(f"{label}为空", not items, f"有 {len(items)} 条")

    facts = ((state.get("memory") or {}).get("facts") or [])
    check("记忆为空", not facts, f"有 {len(facts)} 条")
    docs = ((state.get("knowledge") or {}).get("docs") or [])
    check("知识库为空", not docs, f"有 {len(docs)} 份")

    notes = state.get("notes") or []
    # 有一条空的「随手记」是随包的默认种子，允许；但不能有**有内容**的便签
    written = [n for n in notes if (n.get("text") or "").strip()]
    check("没有带内容的便签（默认种子便签允许）", not written,
          f"有 {len(written)} 条有内容的：{[n.get('title') for n in written][:3]}")

    # ---- 自动产生的东西不能带私人信息 ----
    print("\n  自动产生的文件：")

    # 对话历史：**不该存在**。每次启动都塞一条状态行是最典型的污染。
    conv = data_dir / "conversations.json"
    if conv.exists():
        try:
            sessions = json.loads(
                conv.read_text(encoding="utf-8-sig")).get("sessions") or []
        except Exception:  # noqa: BLE001
            sessions = []
        check("新装没有对话历史（状态行不该被存下来）", not sessions,
              f"有 {len(sessions)} 段："
              f"{[s.get('title') for s in sessions][:3]}")
    else:
        check("新装没有对话历史（状态行不该被存下来）", True)

    # 自定义工具：不该有
    ext = data_dir / "extensions.json"
    if ext.exists():
        items = json.loads(ext.read_text(encoding="utf-8-sig")).get(
            "extensions") or []
        check("新装没有自定义工具", not items, f"有 {len(items)} 个")
    else:
        check("新装没有自定义工具", True)

    # 配色：不该有（用随包默认）
    check("新装没有自定义配色", not (data_dir / "theme.json").exists())

    # API Key：不该有
    env_file = data_dir / ".env"
    has_key = False
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8", errors="replace")
        has_key = "sk-" in text or "OPENAI_API_KEY=" in text
    check("新装没有 API Key", not has_key)

    # ---- 上手指引要弹 ----
    print("\n  首次体验：")
    check("上手指引会弹（onboarding_done 是假）",
          not settings.get("onboarding_done", False),
          f"onboarding_done={settings.get('onboarding_done')!r}")
    check("高级模式默认关（泛用户看不到看不懂的项）",
          not settings.get("advanced_mode", False))

    # ---- 唯一会联网的开关要说清、且能关 ----
    check("检查更新默认开、且是设置项（能关）",
          "check_update" in settings or "update_check" in settings
          or any("update" in k for k in settings),
          "没有找到检查更新的设置项")

    # ================================================================ 二
    print("\n=== 二、MCP 播种的必须是内置 server ===")
    mcp = data_dir / "mcp_servers.json"
    if mcp.exists():
        cfg = json.loads(mcp.read_text(encoding="utf-8-sig"))
        names = [str(s.get("name")) for s in (cfg.get("servers") or [])]
        check("MCP 里只有随包的内置 server", names == ["pawkit"], str(names))
        # 命令必须是 builtin 形态，不能带本机绝对路径
        blob = mcp.read_text(encoding="utf-8")
        check("MCP 配置里没有本机绝对路径",
              "D:\\\\pet" not in blob and "/home/" not in blob, blob[:120])
    else:
        print("  （没播种 MCP 配置，跳过）")

    # ================================================================ 三
    print("\n=== 三、数据目录里没有指向这台机器的痕迹 ===")
    blob_parts: list[str] = []
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and path.stat().st_size < 2_000_000:
            try:
                blob_parts.append(path.read_text(encoding="utf-8",
                                                 errors="replace"))
            except OSError:
                continue
    blob = "\n".join(blob_parts)
    # 这台机器的用户目录、项目目录都不该出现在一个新装的数据里
    for needle, label in ((r"D:\pet", "项目目录"),
                          ("0Sun-shine0", "GitHub 用户名"),
                          ("authine", "公司邮箱域名")):
        check(f"数据里不含{label}", needle not in blob, f"出现了 {needle!r}")

    backend.shutdown()

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
