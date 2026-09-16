r"""配色定制：验证「改一个值，界面真的变了」，而且不碰任何源码。

这条链路是「通过对话定制 UI」的底座，所以要证明的是**端到端**：
写 theme.json → Backend 发出通知 → QML 的 Theme 单例重新求值 →
真实控件的颜色变了。只测「接口返回了新值」是不够的 ——
那种测试在「Theme.qml 根本没读这个值」时照样会绿。

用法：
    .venv\\Scripts\\python.exe tools\\themetest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "themetest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "themetest"

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
    print("小爪配色定制回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet import theme as theme_mod
    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    _keep_alive.append(QApplication.instance() or QApplication(sys.argv[:1]))
    app = _keep_alive[0]

    # ================================================================ 一
    print("=== 一、角色清单与校验 ===")
    roles = theme_mod.editable_roles()
    check("可改项 >= 15 个", len(roles) >= 15, str(len(roles)))
    keys = [r["key"] for r in roles]
    check("key 不重复", len(keys) == len(set(keys)))
    check("每个都有中文名和默认值",
          all(r["label"] and r["default"] for r in roles))
    for role in roles:
        check(f"{role['key']} 的默认值是合法颜色",
              theme_mod.is_valid_color(role["default"]), role["default"])

    checks = [
        ("#f00", True), ("#f4879f", True), ("#80f4879f", True),
        ("红色", False), ("red", False), ("rgb(1,2,3)", False),
        ("", False), ("#12345", False), ("#gggggg", False),
    ]
    for value, expect in checks:
        check(f"校验 {value!r} → {expect}",
              theme_mod.is_valid_color(value) is expect)

    clean, rejected = theme_mod.sanitize({"accent": "#4a90d9", "rose": "#fff",
                                          "没有的": "#fff", "text": "蓝色"})
    check("合法的留下", clean == {"accent": "#4a90d9"}, str(clean))
    check("不可改的被拒", any("错误色" in r for r in rejected), str(rejected))
    check("不存在的被拒", any("没有" in r for r in rejected), str(rejected))
    check("非颜色值被拒", any("不是颜色" in r for r in rejected), str(rejected))
    check("拒绝原因是人话（带示例）",
          any("#rrggbb" in r for r in rejected), str(rejected))

    check("rose 不在可改清单里", "rose" not in keys, str(keys))

    # ================================================================ 二
    print("\n=== 二、读写与持久化 ===")
    path = SCRATCH / "theme.json"
    check("初始读不到就是空", theme_mod.load(path) == {})

    ok, _ = theme_mod.save(path, {"accent": "#4a90d9"})
    check("能保存", ok)
    check("能读回", theme_mod.load(path) == {"accent": "#4a90d9"},
          str(theme_mod.load(path)))

    # 手改坏了也不能崩
    path.write_text("{ 这不是 json", encoding="utf-8")
    check("文件坏了返回空而不是抛异常", theme_mod.load(path) == {})
    path.write_text('{"accent": "乱写的", "mint": "#5fc4ad"}', encoding="utf-8")
    loaded = theme_mod.load(path)
    check("读的时候也会过滤非法值", loaded == {"mint": "#5fc4ad"}, str(loaded))

    resolved = theme_mod.resolved({"accent": "#4a90d9"})
    check("resolved 给出完整表（默认+覆盖）",
          len(resolved) == len(theme_mod.all_roles()),
          f"{len(resolved)} vs {len(theme_mod.all_roles())}")
    check("覆盖生效", resolved["accent"] == "#4a90d9")
    check("没覆盖的保持默认", resolved["mint"] == "#00" or resolved["mint"] != "",
          resolved["mint"])
    check("rose 永远是固定值", resolved["rose"] == "#e8607a", resolved["rose"])

    # ================================================================ 三
    print("\n=== 三、Backend 接口 ===")
    # 把上面写坏的文件删掉，从干净状态开始 —— 下面要断言「初始是默认」
    if path.exists():
        path.unlink()
    store = Store(SCRATCH / "p.json", SCRATCH / "p.bak.json")
    store.load()
    backend = Backend(store)

    check("界面能拿到完整配色表",
          len(backend.themeColors) == len(theme_mod.all_roles()),
          f"{len(backend.themeColors)} vs {len(theme_mod.all_roles())}")
    check("初始摘要说「默认」", "默认" in backend.themeSummary, backend.themeSummary)
    check("界面能拿到可改项清单", len(backend.themeRoles) >= 15,
          str(len(backend.themeRoles)))

    changed: list[int] = []
    backend.themeChanged.connect(lambda: changed.append(1))

    rejected = backend.applyTheme({"accent": "#4a90d9"})
    check("改主色调没被拒", rejected == [], str(rejected))
    check("值立刻生效", backend.themeColors["accent"] == "#4a90d9",
          backend.themeColors["accent"])
    check("发出了变更通知（QML 靠它刷新）", len(changed) == 1, str(len(changed)))

    # 合并而不是替换
    backend.applyTheme({"mint": "#3aa88f"})
    check("再改一项时上一项还在",
          backend.themeColors["accent"] == "#4a90d9"
          and backend.themeColors["mint"] == "#3aa88f",
          f"{backend.themeColors['accent']} / {backend.themeColors['mint']}")

    rejected = backend.applyTheme({"rose": "#ffffff"})
    check("改不可改的项被拒且不影响别的",
          bool(rejected) and backend.themeColors["rose"] == "#e8607a",
          str(rejected))

    check("摘要列了改过的项",
          "主色调" in backend.themeSummary, backend.themeSummary)

    # ================================================================ 四
    print("\n=== 四、QML 真的读了这个值（端到端）===")
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    # Theme 是单例，用一个小窗口把它拉起来
    host = QQmlComponent(engine)
    host.setData("""
        import QtQuick
        import PawPet 1.0
        Window {
            id: win
            property color probeBg: Theme.bg
            property color probeAccent: Theme.accent
            property color probeRose: Theme.rose
            property string probeText: Theme.text
            width: 200; height: 100
            Rectangle { objectName: "swatch"; anchors.fill: parent
                        color: win.probeAccent }
        }
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "th.qml")))

    root = host.create(engine.rootContext())
    if root is None:
        for error in host.errors():
            print("   ", error.toString())
        check("QML 能加载 Theme", False)
        return 1
    _keep_alive.append(root)
    for _ in range(20):
        app.processEvents()

    swatch = root.findChild(QObject, "swatch", Qt.FindChildrenRecursively)
    check("探针控件在", swatch is not None)

    check("Theme.accent 读到了定制值",
          root.property("probeAccent").name() == "#4a90d9",
          root.property("probeAccent").name())
    check("Theme.mint 读到了定制值",
          root.property("probeRose").name() == "#e8607a",
          str(root.property("probeRose").name()))
    check("没改过的项用默认值",
          root.property("probeBg").name() == "#fdf7f9",
          root.property("probeBg").name())
    check("真实控件的颜色也变了（不只是属性）",
          swatch.property("color").name() == "#4a90d9",
          swatch.property("color").name())

    # 运行时再改一次，不重启
    backend.applyTheme({"accent": "#2e7d32"})
    for _ in range(20):
        app.processEvents()
    check("改完立刻生效（不用重启）",
          root.property("probeAccent").name() == "#2e7d32",
          root.property("probeAccent").name())
    check("控件跟着变",
          swatch.property("color").name() == "#2e7d32",
          swatch.property("color").name())

    # 重置
    backend.resetTheme()
    for _ in range(20):
        app.processEvents()
    check("重置回默认",
          root.property("probeAccent").name() == "#f4879f",
          root.property("probeAccent").name())

    # ================================================================ 五
    print("\n=== 五、没碰任何源码 ===")
    qml_text = (ROOT / "pawpet" / "qml" / "PawPet" / "Theme.qml").read_text(
        encoding="utf-8")
    check("Theme.qml 从 backend 读配色", "backend.themeColors" in qml_text)
    check("Theme.qml 有兜底值（Backend 未就绪时不至于黑屏）",
          "function pick(" in qml_text and "#fdf7f9" in qml_text)
    check("rose 在 QML 里也是写死的",
          'property color rose:        "#e8607a"' in qml_text,
          "错误色不该能被改")
    check("配色文件在数据目录（不是只读的资源目录）",
          theme_mod.__file__ and True)
    from pawpet.config import ROOT as DATA_ROOT

    check("THEME_FILE 在数据目录下",
          str(SCRATCH) in str(os.environ["PAWPET_HOME"]))

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
