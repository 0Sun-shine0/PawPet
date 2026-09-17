r"""MCP「用户真的能用上」回归。

背景：MCP 的接线修好过一轮（`as_openai_tools` / `call_tool` 从死代码变成
真调用），但**修完仍然是不可达的** —— 实测有四个独立缺口叠在一起：

  1. `connectMcp()` 全项目零调用，QML 里连「MCP」这个词都没有
  2. `mcp_servers.json` 只在项目根目录，打包后在数据目录里找不到
  3. 配置写 `["python", "mcp_servers/pawkit.py"]` —— 打包后没有 python.exe
  4. `pawkit.py` 没进 spec 的 datas

结果：pawkit 那 8 个工具一次都没在真实对话里出现过。测试全绿、
功能齐全、用户碰不到 —— 所以这个脚本盯的是**可达性**，不是接线。

用法：
    .venv\Scripts\python.exe tools\mcpwiringtest.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "mcpwiring"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "mcpwiring"

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
    print("小爪 MCP 可达性回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Basic")
    _keep_alive.append(QApplication.instance() or QApplication(sys.argv[:1]))

    from pawpet.ai import mcp as mcp_mod
    from pawpet.ai.mcp import MCPClient
    from pawpet.ai.tools import openai_tools
    from pawpet.backend import Backend
    from pawpet.config import MCP_CONFIG, MCP_DIR, is_frozen
    from pawpet.store import Store

    # ================================================================ 一
    print("=== 一、命令解析（缺口 3：打包后没有 python.exe）===")
    builtin = mcp_mod.builtin_command("pawkit")
    check("内置命令非空", len(builtin) >= 3, str(builtin))
    check("内置命令带 --mcp-server", "--mcp-server" in builtin, str(builtin))
    check("内置命令带 server 名", "pawkit" in builtin, str(builtin))
    if is_frozen():
        check("打包后：解释器就是自己，不带入口脚本",
              builtin[0] == sys.executable and builtin[1] == "--mcp-server",
              str(builtin))
    else:
        # 开发模式必须带上入口脚本 —— 裸解释器不认识 --mcp-server，
        # 会打印一行 usage 就退出（实测踩过：报「Try `python -h'」）
        check("开发模式：必须带 run_pawpet.py 入口",
              any(str(p).endswith("run_pawpet.py") for p in builtin),
              str(builtin))

    cases = [
        (["builtin", "pawkit"], "pawkit"),
        (["python", "mcp_servers/pawkit.py"], "pawkit"),
        (["python", r"mcp_servers\pawkit.py"], "pawkit"),
    ]
    for raw, name in cases:
        resolved = mcp_mod.resolve_command(raw)
        check(f"{raw} → 改写成内置形态",
              resolved == builtin and name in resolved, str(resolved))

    external = mcp_mod.resolve_command(["node", "my-server.js"])
    check("用户自己的 server 原样保留（不瞎改）",
          external == ["node", "my-server.js"], str(external))
    check("空配置返回空", mcp_mod.resolve_command([]) == [])
    check("None 不崩", mcp_mod.resolve_command(None) == [])
    check("builtin 缺名字返回空", mcp_mod.resolve_command(["builtin"]) == [])

    # ================================================================ 二
    print("\n=== 二、配置文件落地（缺口 2：配置在只读资源目录里）===")
    check("MCP_DIR 指向随包的 server 目录",
          (MCP_DIR / "pawkit.py").exists(),
          f"MCP_DIR={MCP_DIR}")
    check("内置 server 找得到", mcp_mod.server_path("pawkit") is not None)
    check("不存在的 server 返回 None", mcp_mod.server_path("nosuch") is None)

    # 配置在数据目录，且首启会播种
    check("MCP_CONFIG 在数据目录下（用户要能改）",
          str(SCRATCH) in str(MCP_CONFIG), str(MCP_CONFIG))

    seeded, created = mcp_mod.ensure_config(MCP_CONFIG)
    check("首启播种了配置", created and seeded.exists(), str(seeded))
    check("播种的配置里默认启用了 pawkit",
          "pawkit" in seeded.read_text(encoding="utf-8"))

    # 再调一次不该覆盖用户的修改
    seeded.write_text(json.dumps(
        {"servers": [{"name": "mine", "command": ["x"], "enabled": True}]},
        ensure_ascii=False), encoding="utf-8")
    _again, created2 = mcp_mod.ensure_config(MCP_CONFIG)
    check("已有配置不会被覆盖（用户改过的东西要留住）", not created2,
          "第二次调用竟然重写了")

    # 读配置
    servers, problem = mcp_mod.load_config(MCP_CONFIG)
    check("能读出启用的 server", len(servers) == 1 and not problem,
          f"{len(servers)} / {problem}")

    check("配置坏了给可读错误",
          mcp_mod.load_config(SCRATCH / "nosuch.json")[1] != "",
          "读不存在的文件应该给说明")

    bad = SCRATCH / "bad.json"
    bad.write_text("{ 不是 json", encoding="utf-8")
    check("JSON 坏了给可读错误", mcp_mod.load_config(bad)[1] != "")

    bad.write_text('{"servers": "不是数组"}', encoding="utf-8")
    check("结构不对给可读错误", mcp_mod.load_config(bad)[1] != "")

    bad.write_text('{"servers": [{"name":"a","enabled":false}]}',
                   encoding="utf-8")
    check("只返回 enabled 的", mcp_mod.load_config(bad)[0] == [])

    # ================================================================ 三
    print("\n=== 三、启动就自动连（缺口 1：零调用点）===")
    # 干净地重来一次，模拟全新安装
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.bak.json")
    store.load()
    check("新安装默认就开着 MCP",
          bool(store.settings.get("ai_mcp_enabled")), "默认关着的话用户看不到")

    backend = Backend(store)
    ai = backend.ai
    _keep_alive.append(backend)

    check("启动后自动连上了", len(ai._mcp_clients) >= 1,
          f"{len(ai._mcp_clients)} 个 client")
    check("连上的是 pawkit",
          any(c.name == "pawkit" for c in ai._mcp_clients),
          str([c.name for c in ai._mcp_clients]))
    check("服务器真的在跑",
          all(c.running for c in ai._mcp_clients),
          "进程没起来")

    check("界面能拿到 server 清单", len(ai.mcpServers) >= 1,
          str(len(ai.mcpServers)))
    if ai.mcpServers:
        first = ai.mcpServers[0]
        check("清单里标了已连接", first["running"], str(first))
        check("清单里有工具数", first["toolCount"] >= 6, str(first["toolCount"]))
        check("清单里有解析后的命令", "--mcp-server" in first["command"],
              first["command"])

    # ================================================================ 四
    print("\n=== 四、工具真的进得了给模型的清单（关键）===")
    context = ai.context
    mcp_tools = context.mcp_tools()
    check("context 能拼出 MCP 工具", len(mcp_tools) >= 6, str(len(mcp_tools)))

    merged = openai_tools(mcp_tools)
    names = [item["function"]["name"] for item in merged]
    mcp_names = [n for n in names if n.startswith("mcp_")]
    check("合并后 MCP 工具带前缀出现", len(mcp_names) == len(mcp_tools),
          f"{len(mcp_names)} vs {len(mcp_tools)}")
    check("内置工具没被顶掉", len(merged) == len(openai_tools()) + len(mcp_tools),
          f"{len(merged)} vs {len(openai_tools())}+{len(mcp_tools)}")

    # 真调一个：证明不是「清单里有、实际调不动」
    target = next((n for n in mcp_names if n.endswith("_calc")), "")
    check("找得到 calc 工具", bool(target), str(mcp_names[-4:]))
    if target:
        ok, text, _bundle = context.execute(target, {"expression": "8*8"})
        check("真调得动（不是只有清单）", ok and "64" in text, text[:60])

    # ================================================================ 五
    print("\n=== 五、开关是活的（缺口：只存值不生效）===")
    ai.mcpEnabled = False
    check("关掉之后立刻断开", not ai._mcp_clients, str(len(ai._mcp_clients)))
    check("关掉之后不再提供工具", context.mcp_tools() == [],
          str(len(context.mcp_tools())))

    ai.mcpEnabled = True
    check("再打开会重新连上", len(ai._mcp_clients) >= 1,
          str(len(ai._mcp_clients)))
    check("重连后工具回来了", len(context.mcp_tools()) >= 6,
          str(len(context.mcp_tools())))

    # 重复设同一个值不该乱动
    before = len(ai._mcp_clients)
    ai.mcpEnabled = True
    check("重复开不会连两遍", len(ai._mcp_clients) == before,
          f"{before} → {len(ai._mcp_clients)}")

    # ================================================================ 六
    print("\n=== 六、界面接了（缺口 1 的另一半）===")
    qml = (ROOT / "pawpet" / "qml" / "PawPet" / "page" / "AiPage.qml").read_text(
        encoding="utf-8")
    for api in ("backend.ai.mcpEnabled", "backend.ai.mcpServers",
                "backend.ai.connectMcp()", "backend.ai.disconnectMcp()",
                "backend.ai.mcpConfigPath", "backend.ai.mcpHint()"):
        check(f"QML 用了 {api}", api in qml, "界面没接")

    # ================================================================ 七
    print("\n=== 七、打包带上（缺口 4）===")
    spec = (ROOT / "build" / "pawpet.spec").read_text(encoding="utf-8")
    check("spec 的 datas 里有 mcp_servers",
          'mcp_servers' in spec, "不打进去的话装出来找不到 server")
    check("spec 只收 .py，不把 __pycache__ 塞给用户",
          'glob("*.py")' in spec,
          "整目录拷贝会把开发机上的陈旧 .pyc 一起发出去")

    # ================================================================ 八
    print("\n=== 八、子进程不会变孤儿（默认打开之后才有的风险）===")
    #
    # MCP 默认打开之后，**每个创建 Backend 的进程都会 spawn 一个 server
    # 子进程**。正常退出由 controller.shutdown() 回收，但应用被强杀、
    # 崩溃，或者某个脚本忘了调 shutdown 的时候它会一直挂着 ——
    # 实测连跑整套测试时机器上会攒下十几个。
    #
    # 所以客户端在 start() 里注册了 atexit 兜底。这里验证兜底真的注册上了
    # （真起一堆子进程再数代价太大，也不好隔离）。
    probe = MCPClient("probe", mcp_mod.builtin_command("pawkit"),
                      cwd=str(ROOT), timeout=25)
    ok_probe, msg_probe = probe.start()
    check("探针 server 起来了", ok_probe, msg_probe)
    if ok_probe:
        check("start() 时注册了 atexit 兜底",
              getattr(probe, "_atexit_done", False),
              "没注册的话强杀应用会留下孤儿进程")
        check("重复 start 不会重复注册",
              (probe._register_atexit_cleanup(),
               getattr(probe, "_atexit_done", False))[1],
              "应该幂等")
    probe.stop()

    # ================================================================ 九
    print("\n=== 九、截屏失败要给用户人话 ===")
    #
    # 实测：屏保切进来 / 正在锁屏时，BitBlt 会 100% 失败 ——
    # Windows 不允许捕获锁屏后的安全桌面。用户**天天**会遇到
    # （离开工位一会儿屏保就起来）。
    #
    # mss 原文是「Windows graphics function failed (no error provided):
    # BitBlt」—— 用户既看不懂，也猜不到该干什么。
    import pawpet.ai.vision as vision_mod

    class _Boom:
        monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]

        def grab(self, target):
            raise OSError("Windows graphics function failed "
                          "(no error provided): BitBlt")

    capture_mod = vision_mod.ScreenCapture()
    saved = getattr(vision_mod._thread_local, "device", None)
    vision_mod._thread_local.device = _Boom()
    try:
        broken = capture_mod.grab(monitor=0)
    finally:
        vision_mod._thread_local.device = saved

    check("抓不到时不报 ok", not broken.ok)
    check("不把 BitBlt 原文甩给用户", "BitBlt" not in broken.message,
          broken.message[:70])
    check("说清是「屏幕看不到」", "屏幕" in broken.message, broken.message[:70])
    check("告诉用户怎么办", "解锁" in broken.message, broken.message[:70])

    # 别的异常仍要保留原文（便于排查），不能被这句健忘掉
    class _Other:
        monitors = [{"left": 0, "top": 0, "width": 100, "height": 100}]

        def grab(self, target):
            raise ValueError("磁盘满了")

    vision_mod._thread_local.device = _Other()
    try:
        other = capture_mod.grab(monitor=0)
    finally:
        vision_mod._thread_local.device = saved
    check("其它错误保留原文（排查要用）", "磁盘满了" in other.message,
          other.message[:70])

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
