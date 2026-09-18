"""高级模式：开关要走通三层，收起的东西要真的收起来。

这个套件存在的理由：高级模式的做法是「给卡片加一个 `visible` 绑定」，
而**绑错方向、或者绑漏一处，界面上完全看不出来** —— 它只是「少了一块」，
没有报错、没有日志。单看代码也很容易看岔。

所以这里做两件事：
  1. 真的把 AiPage / SettingsPage 加载起来，量卡片的 `visible`，
     而不是去 grep 源码里有没有那行 `visible:`
  2. 留一条**反向断言**：能被收起的东西只能是「泛用户看不懂的」，
     `执行步数` 这种有用且被别的套件依赖的控件必须始终可见。
     （没有这条的话，以后有人图省事把「模型设置」整个收进去，
     测试照样全绿 —— 而用户就再也配不了 Key 了。）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "advancedtest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
# 和别的套件隔开，免得真跑着的实例抢单例锁
os.environ["PAWPET_INSTANCE_SUFFIX"] = "advancedtest"
# 「四、五」两段要开真实窗口量布局（原因见 make_view 的注释），
# 显式指定平台插件，和 bubshot / aipreview 那批保持一致
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

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


def main() -> int:
    print("小爪「高级模式」联调")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, QUrl
    from PySide6.QtQml import QQmlEngine
    from PySide6.QtQuick import QQuickView
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store, default_settings

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    # =========================================================== 一、默认值
    print("\n=== 一、默认值：新装就是关着的 ===")

    check("默认是关着的", default_settings().get("advanced_mode") is False,
          repr(default_settings().get("advanced_mode")))

    data_path = SCRATCH / "pet_data.json"
    backup_path = SCRATCH / "pet_data.backup.json"
    store = Store(data_path, backup_path)
    store.load()
    check("加载后设置里有这个键", "advanced_mode" in store.settings,
          str(sorted(store.settings.keys())[:6]))

    backend = Backend(store)
    check("Backend 默认读到 False", backend.advanced_mode is False,
          repr(backend.advanced_mode))

    # =========================================================== 二、老数据迁移
    print("\n=== 二、老数据（没有这个键）要能平滑升级 ===")

    legacy_path = SCRATCH / "legacy.json"
    with open(legacy_path, "w", encoding="utf-8") as handle:
        json.dump({"schema": 3, "tasks": [], "settings": {"pet_style": "mochi"}},
                  handle, ensure_ascii=False)
    legacy = Store(legacy_path, SCRATCH / "legacy.backup.json")
    legacy.load()
    check("老数据自动补上默认值（关）",
          legacy.settings.get("advanced_mode") is False,
          repr(legacy.settings.get("advanced_mode")))
    check("老数据里原有的设置没被动",
          legacy.settings.get("pet_style") == "mochi",
          repr(legacy.settings.get("pet_style")))

    # 用户开过高级模式的备份，导入之后不能被默认值盖回去
    kept_path = SCRATCH / "kept.json"
    with open(kept_path, "w", encoding="utf-8") as handle:
        json.dump({"schema": 3, "settings": {"advanced_mode": True}},
                  handle, ensure_ascii=False)
    kept = Store(kept_path, SCRATCH / "kept.backup.json")
    kept.load()
    check("已存的值（开）不会被默认值盖掉",
          kept.settings.get("advanced_mode") is True,
          repr(kept.settings.get("advanced_mode")))
    kept_legacy = Store(data_path, backup_path)
    kept_legacy.load()
    check("老数据不会把别人的开关搅乱",
          kept_legacy.settings.get("advanced_mode") is False,
          repr(kept_legacy.settings.get("advanced_mode")))

    # =========================================================== 三、后端属性
    print("\n=== 三、Backend 属性：写进去、读出来、发信号 ===")

    fired: list[int] = []
    backend.settingsChanged.connect(lambda: fired.append(1))

    backend.advanced_mode = True
    check("设 True 之后读得到 True", backend.advanced_mode is True)
    check("真的落进了 store", store.settings.get("advanced_mode") is True,
          repr(store.settings.get("advanced_mode")))
    check("发了 settingsChanged 让 QML 刷新", len(fired) == 1, f"实际 {len(fired)} 次")

    before = len(fired)
    backend.advanced_mode = True
    check("设成同一个值不重复发信号（免得白刷新一轮）",
          len(fired) == before, f"又发了 {len(fired) - before} 次")

    backend.advanced_mode = False
    check("还能关回去", backend.advanced_mode is False
          and store.settings.get("advanced_mode") is False)

    hint_off = backend.advancedModeHint
    backend.advanced_mode = True
    hint_on = backend.advancedModeHint
    check("两态说明文案不一样", hint_off != hint_on,
          f"{hint_off[:20]!r} vs {hint_on[:20]!r}")
    check("关着的文案点名了被收起的功能",
          "知识库" in hint_off and "外部工具" in hint_off, hint_off[:60])
    check("开着的文案也点了名", "知识库" in hint_on and "外部工具" in hint_on,
          hint_on[:60])
    backend.advanced_mode = False

    # =========================================================== 四、AI 页显隐
    print("\n=== 四、AI 页：收起的东西真的收起来了 ===")

    def find(root, name):
        return root.findChild(QObject, name, Qt.FindChildrenRecursively)

    def make_view(name: str, body: str, width: int = 908, height: int = 590):
        """把页面装进一个**真实窗口**里再量。

        两个都是踩出来的，别省：

        1. **必须有窗口。** 直接 `QQmlComponent.create()` 出来的页面是
           个没有父尺寸的光杆 Item（实测 908x590 的容器里它是 0x0），
           布局根本不会真排一遍 —— 于是每个孩子的 `y` 都是 0、
           `height` 一直等于 `implicitHeight`，量出来的数字跟布局无关。
           （踩过：知识库卡片开和关都报 217，看着像「开关没生效」，
           其实是压根没布局过。）
        2. **窗口得挪到屏幕外。** 跑回归时别在用户脸上弹一个窗口。
        """
        qml_path = SCRATCH / f"host_{name}.qml"
        qml_path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")

        view = QQuickView()
        view.engine().addImportPath(str(QML_DIR))
        view.rootContext().setContextProperty("backend", backend)
        view.setResizeMode(QQuickView.SizeViewToRootObject)
        view.setSource(QUrl.fromLocalFile(str(qml_path)))
        if view.errors():
            for error in view.errors():
                print("   QML 错误：", error.toString())
            return None
        view.setPosition(-4000, 0)
        view.show()
        settle(view)
        return view

    def settle(view, ms: int = 200) -> None:
        """等布局落地。

        布局是**异步**的（挂在事件循环的 polish 上），而且光
        `processEvents()` 不够 —— 不给一次真正的 sync，Qt 不会跑那趟
        polish，量到的还是上一轮的数字。实测：只 processEvents 时，
        改了 visible 之后卡片 `y` 一直是旧值、右栏内容高一动不动；
        `grabWindow()` 逼一次同步之后就都对了。
        """
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        view.grabWindow()

    ai_host = make_view("ai", """
        import QtQuick
        import PawPet 1.0
        Item {
            id: host
            objectName: "aiHost"
            width: 908
            height: 590
            AiPage { id: inner; objectName: "inner"; anchors.fill: parent }
        }
    """)
    check("AiPage.qml 加载成功（908x590 真实窗口）", ai_host is not None)
    if ai_host is None:
        return 1

    page = find(ai_host, "inner")
    right_column = find(ai_host, "aiRightColumn")
    kb_card = find(ai_host, "kbCard")
    mcp_card = find(ai_host, "mcpCard")
    step_box = find(ai_host, "stepBox")
    check("找得到右栏列（aiRightColumn）", right_column is not None)
    check("找得到知识库卡片（kbCard）", kb_card is not None)
    check("找得到外部工具卡片（mcpCard）", mcp_card is not None)
    check("找得到执行步数控件（stepBox）", step_box is not None)
    if None in (right_column, kb_card, mcp_card, step_box):
        return 1

    # 知识库那块在「模型设置」展开区里面 —— 不展开的话它整段都不参与
    # 布局，量高度就没意义了。这里替用户点开。
    page.setProperty("showSettings", True)
    settle(ai_host)

    check("关着时：知识库藏着", kb_card.property("visible") is False)
    check("关着时：外部工具藏着", mcp_card.property("visible") is False)

    # ★ 反向断言。AI 页是**边改边看**才配得好的东西，
    #   把步数也藏起来，用户遇到「干到一半停了」就找不到原因了。
    #   而且 tools/steptest.py 也靠这个控件。
    check("关着时：执行步数**仍然可见**（这是有意的，别顺手藏了）",
          step_box.property("visible") is True)
    # 这条是粗查，别当成守门人：不可见项在布局里是「跳过」的，
    # `height` 会停在旧值上（实测把步数藏起来它也照样 > 0）。
    # 真正拦得住的是上面那条 visible。
    check("关着时：执行步数真的占着位置（不是 visible 为真但被压扁）",
          step_box.property("height") > 0, f"实际 {step_box.property('height')}")

    backend.advanced_mode = True
    check("打开后：知识库出现了", kb_card.property("visible") is True)
    check("打开后：外部工具出现了", mcp_card.property("visible") is True)
    check("打开后：执行步数还在这儿（本来就没藏过）",
          step_box.property("visible") is True)

    backend.advanced_mode = False
    check("再关回去：知识库又收起来了", kb_card.property("visible") is False)
    check("再关回去：外部工具又收起来了", mcp_card.property("visible") is False)

    # 收起之后不能留下一条空缝。
    #
    # **看数字的地方在这儿：量右栏的总内容高，不是量卡片自己的高。**
    # Qt 的 Layout 对不可见项是「跳过」——不排它、也不清它的 geometry。
    # 所以隐藏之后 `kbCard.height` 还停在上一次的值（不是 0），
    # 连 `y` 都是旧的，拿它们当「有没有留缝」的证据是量错了对象。
    # 能真正说明问题的是右栏的 implicitHeight：跳过的项不计入内容高。
    backend.advanced_mode = True
    settle(ai_host)
    h_open = right_column.property("implicitHeight")
    kb_h = kb_card.property("height")
    mcp_h = mcp_card.property("height")

    backend.advanced_mode = False
    settle(ai_host)
    h_closed = right_column.property("implicitHeight")

    check("打开时两张卡片确实占了高度（下面几条才有意义）",
          kb_h > 0 and mcp_h > 0, f"kb={kb_h} mcp={mcp_h}")

    delta = h_open - h_closed
    expected = kb_h + mcp_h
    check("关掉之后右栏内容变矮了（收起来的东西不占位）",
          delta > 0, f"打开 {h_open} → 关上 {h_closed}，差 {delta}")
    # 差值应当**至少**是两张卡片本身的高度。比这更小就说明有哪张卡
    # 只收了一半、留了半截在版面上；多一点是隔项间距被一起省掉了，正常。
    check("省掉的高度不小于两张卡片本身（说明是整张收走，不是收一半）",
          delta >= expected - 5, f"省 {delta}，两卡 {expected}")
    check("也没多省出一大块（说明没把别的卡片一起带走）",
          delta <= expected + 80, f"省 {delta}，两卡 {expected}")

    # =========================================================== 五、设置页
    print("\n=== 五、设置页：开关拨得动、说明跟得上 ===")

    set_host = make_view("settings", """
        import QtQuick
        import PawPet 1.0
        Item {
            id: host
            objectName: "setHost"
            width: 908
            height: 590
            SettingsPage { id: inner; objectName: "settingsPage"; anchors.fill: parent }
        }
    """)
    check("SettingsPage.qml 加载成功", set_host is not None)
    if set_host is not None:
        switch = find(set_host, "advancedSwitch")
        check("找得到高级模式开关（advancedSwitch）", switch is not None)
        if switch is not None:
            check("开关初始是关的", switch.property("checked") is False,
                  repr(switch.property("checked")))
            # 开关得真的在版面上占着位置 —— 光「存在」不够，
            # 万一它被塞进一个没尺寸的容器里，用户也点不到。
            check("开关真的占着位置（height > 0）",
                  switch.property("height") > 0,
                  f"实际 {switch.property('height')}")

            # 复现真实交互：拨过去 → 控件发 toggled → QML 里那段 onToggled 跑起来
            #
            # `toggled` 在 Qt Quick Controls 里有 `toggled()` 和
            # `toggled(bool)` 两个重载，PySide6 解析到的是**无参**那个 ——
            # 传参会直接抛 TypeError。QML 侧的绑定读的是 `checked`，
            # 不依赖信号参数，所以发无参的就够了。
            switch.setProperty("checked", True)
            switch.toggled.emit()
            check("拨开之后后端跟着开了", backend.advanced_mode is True,
                  repr(backend.advanced_mode))
            check("设置真的落进了 store",
                  store.settings.get("advanced_mode") is True,
                  repr(store.settings.get("advanced_mode")))

            switch.setProperty("checked", False)
            switch.toggled.emit()
            check("再拨回来也能关", backend.advanced_mode is False,
                  repr(backend.advanced_mode))

    # =========================================================== 六、不变量
    print("\n=== 六、别把该留的也收进去 ===")

    ai_text = (QML_DIR / "PawPet" / "page" / "AiPage.qml").read_text(encoding="utf-8")
    settings_text = (QML_DIR / "PawPet" / "page" / "SettingsPage.qml").read_text(
        encoding="utf-8")

    check("AI 页里没写死 visible: false（藏了就再也开不出来）",
          "visible: false" not in ai_text)
    check("显隐绑到的是 backend.advanced_mode",
          ai_text.count("visible: backend.advanced_mode") == 2,
          f"实际 {ai_text.count('visible: backend.advanced_mode')} 处")

    # 模型设置卡片**不能**被收到高级模式里 —— 没配 Key 的新用户
    # 全靠它填接口地址，收了他就卡在「还没有配置模型」上出不去。
    check("模型设置没被收进高级模式",
          "visible: page.showSettings || !backend.ai.configured" in ai_text)
    check("设置页有且只有一个这个开关",
          settings_text.count('objectName: "advancedSwitch"') == 1)

    # 位置也是一种过滤：越不常用的越靠后，泛用户滚不到也就不会被绊住。
    check("高级模式排在「关于」前面（越不常用的越靠后）",
          0 <= settings_text.index('title: "高级模式"')
          < settings_text.index('title: "关于"'))

    # =========================================================== 收尾
    ai_host.close()
    if set_host is not None:
        set_host.close()
    backend.shutdown()

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
