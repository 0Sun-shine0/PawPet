r"""「第一次配模型」向导的回归测试。

背景：用户明确说「key 你可以给一个超链接直接进行跳转」「主要给 deepseek 的
api 网址」。也就是泛用户不该被问「你要用哪家模型」——默认 DeepSeek，
界面给他一个显眼的外链跳到官方创建页，他只要复制粘贴。

这个脚本盯住三件事：
  1. 服务商预设数据完整、链接是 http(s)、默认那家是 DeepSeek；
  2. 「去拿 Key」的链接会随接口地址变（用户换了一家，链接也要跟着换）；
  3. 界面结构上，没配好模型时向导必须**自动展开**（以前要点「设置」按钮，
     泛用户根本不知道那个按钮存在）。

用法：
    .venv\\Scripts\\python.exe tools\\setuptest2.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "setuptest2"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "setuptest2"

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


# ==========================================================================
def test_provider_data() -> None:
    print("\n=== 一、服务商预设数据 ===")
    from pawpet.ai.providers import (
        PRIMARY_KEY,
        all_providers,
        alternatives,
        find,
        groups,
        primary,
    )

    items = all_providers()
    check("至少有 5 家可选", len(items) >= 5, str(len(items)))
    check("默认推的那家是 deepseek", PRIMARY_KEY == "deepseek", PRIMARY_KEY)

    head = primary()
    check("默认那家的名字里有 DeepSeek", "DeepSeek" in head.get("name", ""),
          head.get("name"))
    check("默认那家指向官方创建 API Key 那一页",
          head.get("signupUrl") == "https://platform.deepseek.com/api_keys",
          head.get("signupUrl"))
    check("默认那家的接口地址是 deepseek 的",
          "deepseek.com" in head.get("baseUrl", ""), head.get("baseUrl"))
    check("默认那家有默认模型", bool(head.get("model")), head.get("model"))
    check("默认那家写了要不要先充值", bool(head.get("freeHint")))
    check("默认那家有价格链接",
          any("pricing" in link["url"] for link in head.get("links", [])),
          str(head.get("links")))

    others = alternatives()
    check("其他家不含默认那家",
          all(item["key"] != PRIMARY_KEY for item in others), str(len(others)))
    check("默认 + 其他 = 全部",
          len(others) + 1 == len(items), f"{len(others)} + 1 vs {len(items)}")

    # 每一家都要自洽
    for item in items:
        key = item["key"]
        if item["needsKey"]:
            check(f"{key} 有注册页链接",
                  item["signupUrl"].startswith("https://"), item["signupUrl"])
            check(f"{key} 有默认模型", bool(item["model"]), item["model"])
        check(f"{key} 的接口地址是 http(s)",
              item["baseUrl"].startswith("http"), item["baseUrl"])
        check(f"{key} 写了要不要先充值", bool(item["freeHint"]))
        check(f"{key} 有分组标签", bool(item["groupLabel"]), item["groupLabel"])
        for link in item["links"]:
            check(f"{key} 的「{link['label']}」链接是 http(s)",
                  link["url"].startswith("https://"), link["url"])

    check("find 认得出 ollama", find("ollama") is not None)
    check("find 认不出就返回 None", find("没有这家") is None)
    check("分组至少两组", len(groups()) >= 2, str(len(groups())))


def test_link_follows_provider() -> None:
    print("\n=== 二、「去拿 Key」的链接跟着接口地址走 ===")
    from pawpet.ai.providers import guess_key_help

    cases = [
        ("https://api.deepseek.com/v1", "platform.deepseek.com"),
        ("https://api.moonshot.cn/v1", "moonshot.cn"),
        ("https://open.bigmodel.cn/api/paas/v4", "bigmodel.cn"),
        ("http://127.0.0.1:11434/v1", "ollama.com"),
    ]
    for base, expect_host in cases:
        name, url = guess_key_help(base)
        check(f"{base} → 认出 {name or '（空）'}",
              expect_host in url, f"得到 {url!r}")

    # 认不出来时不能瞎给一个链接
    name, url = guess_key_help("https://自己搭的.example/v1")
    check("认不出的地址不瞎给链接", url == "", f"得到 {url!r}")

    check("空地址不崩", guess_key_help("") == ("", ""))


def test_controller_wiring() -> None:
    print("\n=== 三、控制器接线 ===")
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.providers import find
    from pawpet.backend import Backend
    from pawpet.store import Store

    app = QApplication.instance() or QApplication(sys.argv[:1])
    scratch = SCRATCH / "ctl"
    scratch.mkdir(parents=True, exist_ok=True)
    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    # 模拟**全新安装**：把接口地址清掉。
    #
    # Store 的默认值里带着 api.openai.com（老版本的默认），留着它就会
    # 被判成「用户选了 OpenAI」，于是按钮指向 OpenAI 而不是推荐的
    # DeepSeek。这正是泛用户第一次打开时的状态。
    store.settings.pop("ai_openai_base", None)
    store.settings.pop("ai_openai_model", None)
    backend = Backend(store)

    check("界面能拿到默认那家", bool(backend.ai.primaryProvider),
          str(backend.ai.primaryProvider)[:60])
    check("界面能拿到其他家", len(backend.ai.otherProviders) >= 4,
          str(len(backend.ai.otherProviders)))
    check("按钮文案带上了服务商名",
          "DeepSeek" in backend.ai.keyPageLabel, backend.ai.keyPageLabel)
    check("按钮链接是 DeepSeek 的创建页",
          "platform.deepseek.com" in backend.ai.keyPageUrl,
          backend.ai.keyPageUrl)

    # 换一家，链接要跟着换
    backend.ai.applyProvider("moonshot")
    check("选中 Moonshot 后接口地址变了",
          "moonshot" in backend.ai.baseUrl, backend.ai.baseUrl)
    check("选中 Moonshot 后模型名也变了", bool(backend.ai.model), backend.ai.model)
    check("选中 Moonshot 后按钮链接跟着变",
          "moonshot" in backend.ai.keyPageUrl, backend.ai.keyPageUrl)
    check("按钮文案也跟着变",
          "Kimi" in backend.ai.keyPageLabel or "月之暗面" in backend.ai.keyPageLabel,
          backend.ai.keyPageLabel)

    # 换回 DeepSeek
    backend.ai.applyProvider("deepseek")
    check("换回 DeepSeek 正常",
          "deepseek" in backend.ai.baseUrl, backend.ai.baseUrl)

    # 不存在的 key 不能把设置搞乱
    before = backend.ai.baseUrl
    backend.ai.applyProvider("根本没有这家")
    check("无效 key 不动设置", backend.ai.baseUrl == before, backend.ai.baseUrl)

    # 本地模型不需要 key
    backend.ai.applyProvider("ollama")
    item = find("ollama")
    check("Ollama 的接口地址是本地", "127.0.0.1" in backend.ai.baseUrl,
          backend.ai.baseUrl)
    check("Ollama 标了不需要 key", item is not None and not item.needs_key)

    # openUrl 只放行 http(s)
    called: list[str] = []
    import pawpet.ai.providers as providers

    original = providers.find
    try:
        from PySide6.QtGui import QDesktopServices
        real_open = QDesktopServices.openUrl
        QDesktopServices.openUrl = staticmethod(
            lambda url: called.append(url.toString()) or True)
        backend.ai.openUrl("https://example.com/x")
        backend.ai.openUrl("javascript:alert(1)")
        backend.ai.openUrl("file:///C:/Windows/System32/calc.exe")
        backend.ai.openUrl("")
        QDesktopServices.openUrl = real_open
    finally:
        providers.find = original

    check("http(s) 链接会打开", called == ["https://example.com/x"], str(called))
    check("非 http(s) 一律拒绝（javascript: / file: 都不行）",
          len(called) == 1, str(called))

    backend.shutdown()


def test_ui_visible() -> None:
    print("\n=== 四、界面：没配好时向导必须自动展开 ===")
    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication(sys.argv[:1])

    scratch = SCRATCH / "ui"
    scratch.mkdir(parents=True, exist_ok=True)
    store = Store(scratch / "p.json", scratch / "p.bak.json")
    store.load()
    # 确保是「没配好」状态
    store.settings.pop("ai_openai_base", None)
    store.settings.pop("ai_openai_model", None)
    # apiKey 来自 .env，这里把它指向一个不存在的文件
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    host = QQmlComponent(engine)
    host.setData("""
        import QtQuick
        import PawPet 1.0
        Item {
            width: 1000; height: 660
            AiPage { objectName: "inner"; anchors.fill: parent }
        }
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "su2.qml")))
    root = host.create(engine.rootContext())
    if root is None:
        for error in host.errors():
            print("   ", error.toString())
        check("AiPage 能加载", False)
        return
    _keep_alive.append(root)
    for _ in range(25):
        app.processEvents()

    page = root.findChild(QObject, "inner", Qt.FindChildrenRecursively)
    check("AiPage 在", page is not None)
    if page is not None:
        # 「设置」开关默认是关的，但没配好模型时模型设置卡必须显示
        check("showSettings 默认关", page.property("showSettings") is False)
        check("「其他服务商」默认收起",
              page.property("showProviders") is False)

    qml = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "pawpet" / "qml" / "PawPet" / "page" / "AiPage.qml",
            ROOT / "pawpet" / "qml" / "PawPet" / "AiSettingsPanel.qml",
        )
    )
    check("模型设置卡的可见性带了「没配好就展开」",
          "page.showSettings || !backend.ai.configured" in qml,
          "泛用户找不到那个「设置」按钮")
    check("向导里有「去拿 Key」按钮",
          "backend.ai.openKeyPage()" in qml)
    check("向导提到会打开浏览器",
          "会打开浏览器" in qml)
    check("向导说明了 key 存在哪",
          ".env" in qml and "不会上传" in qml)
    check("其他服务商收在开关后面", "page.showProviders" in qml)
    check("key 输入框支持回车保存", "onAccepted" in qml)
    check("保存按钮会顺手测一次连接", "saveApiKey" in qml and "testConnection" in qml)

    backend.shutdown()


def main() -> int:
    print("小爪「第一次配模型」向导回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # Qt 应用必须**在最前面建一次**，而且必须是 QApplication。
    #
    # 踩过的坑：Backend 里会建 Qt 对象（定时器、QScreen 查询），
    # 没有 QApplication 时直接段错误（Windows 上是 access violation，
    # 连 traceback 都不打，只看到 exit code -1073741819）。
    # 也不能先建 QCoreApplication 再建 QApplication —— 一个进程里
    # 只能有一个 Q*Application 实例，类型不对同样崩。
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    QQuickStyle.setStyle("Basic")
    _keep_alive.append(QApplication.instance() or QApplication(sys.argv[:1]))

    test_provider_data()
    test_link_follows_provider()
    test_controller_wiring()
    test_ui_visible()

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
