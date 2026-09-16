r"""界面缩放自测。

背景：用户反馈「字太小、右边空太多」。查下来不是 DPI bug ——
那台机器上 Windows 直接把显示缩放设成了 100%（logicalDotsPerInch 恒为
96、devicePixelRatio 恒为 1.0），所以 DPI 拿不到「该放大多少」的信息。
而原来的字号写死成 10/11/13，是按小窗口调的，在 1920x1080 的
笔记本屏上就偏小。

改法：所有字号和间距乘一个用户可调的系数，默认按**分辨率**自动选。

用法：
    .venv\\Scripts\\python.exe tools\\uiscaletest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "uiscale"
os.environ["PAWPET_HOME"] = str(SCRATCH)

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
    print("小爪界面缩放自测\n")

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()
    backend = Backend(store)

    # ================================================== 一、取值与夹范围
    print("=== 一、缩放取值与边界 ===")

    check("默认是自动", backend.uiScaleIsAuto, str(backend.uiScaleIsAuto))
    auto = backend.uiScale
    check(f"自动值在合理范围内（{auto}）", 0.8 <= auto <= 1.4, str(auto))
    check("自动值随分辨率走（这台机器不是 1.0）",
          abs(auto - 1.0) > 0.01,
          f"{auto} —— 如果是 1.0，说明阈值没覆盖到这台机器")
    check("提示文案说明了是自动",
          "自动" in backend.uiScaleHint, backend.uiScaleHint)

    store.settings["ui_scale"] = 1.5
    backend.settingsChanged.emit()
    check("手动值生效", abs(backend.uiScale - 1.5) < 1e-6, str(backend.uiScale))
    check("手动时 isAuto 为假", not backend.uiScaleIsAuto)
    check("提示说明了是手动", "手动" in backend.uiScaleHint,
          backend.uiScaleHint)

    store.settings["ui_scale"] = 99.0
    backend.settingsChanged.emit()
    check("过大被夹到 2.0", abs(backend.uiScale - 2.0) < 1e-6,
          str(backend.uiScale))

    store.settings["ui_scale"] = 0.01
    backend.settingsChanged.emit()
    check("过小被抬到 0.8", abs(backend.uiScale - 0.8) < 1e-6,
          str(backend.uiScale))

    store.settings["ui_scale"] = "不是数字"
    backend.settingsChanged.emit()
    check("脏值退回自动", backend.uiScaleIsAuto, str(backend.uiScale))

    store.settings["ui_scale"] = 0.0
    backend.settingsChanged.emit()
    check("0 表示自动", backend.uiScaleIsAuto)

    # ================================================== 二、QML 真的用上
    print("\n=== 二、QML 真的用上了缩放 ===")

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    def make(name):
        component = QQmlComponent(engine, QUrl.fromLocalFile(
            str(QML_DIR / "PawPet" / name)))
        page = component.create(engine.rootContext())
        errors = [e.toString() for e in component.errors()
                  if "Cannot read property" not in e.toString()
                  and "is not a type" not in e.toString()]
        return page, errors

    theme_page, errors = make("Theme.qml")
    check("Theme.qml 能加载", theme_page is not None, str(errors[:1]))

    # 直接问 QML 里的 Theme 单体：字号是不是跟着 backend.uiScale 变
    from PySide6.QtQml import qmlRegisterSingletonInstance  # noqa: F401

    probe = QQmlComponent(engine)
    probe.setData(b"""
        import QtQuick
        import PawPet 1.0
        QtObject {
            property int tiny: Theme.fsTiny
            property int small: Theme.fsSmall
            property int body: Theme.fsBody
            property int title: Theme.fsTitle
            property int gap: Theme.gap
            property real scale: Theme.scale
            property int px100: Theme.px(100)
        }
    """, QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "probe.qml")))
    obj = probe.create(engine.rootContext())
    check("能读到 Theme 的换算结果", obj is not None,
          str([e.toString() for e in probe.errors()][:1]))

    if obj is not None:
        for name in ("tiny", "small", "body", "title", "gap"):
            base = {"tiny": 11, "small": 12, "body": 14, "title": 17,
                    "gap": 12}[name]
            got = obj.property(name)
            want = round(base * auto)
            check(f"Theme.{name} = {got}（基准 {base} × {auto} = {want}）",
                  got == want, f"实际 {got}")

        check("px() 换算正确", obj.property("px100") == round(100 * auto),
              str(obj.property("px100")))
        check("Theme.scale 跟着 backend 走",
              abs(obj.property("scale") - auto) < 1e-6,
              str(obj.property("scale")))

        # 改设置，QML 应该跟着变
        store.settings["ui_scale"] = 1.6
        backend.settingsChanged.emit()
        app.processEvents()
        check("改缩放后 Theme 立即跟着变（绑定生效）",
              obj.property("scale") > auto + 0.1,
              f"改后 {obj.property('scale')}，改前 {auto}")

    # 字号下限：再小的缩放也不能让字小到读不了
    store.settings["ui_scale"] = 0.8
    backend.settingsChanged.emit()
    app.processEvents()
    if obj is not None:
        check("最小缩放下 fsTiny 仍 >= 8px（还能看清）",
              obj.property("tiny") >= 8, str(obj.property("tiny")))

    # ================================================== 三、设置页有入口
    print("\n=== 三、设置页有调节入口 ===")

    settings_page, errors = make("page/SettingsPage.qml")
    check("设置页能加载", settings_page is not None, str(errors[:1]))

    text = (QML_DIR / "PawPet" / "page" / "SettingsPage.qml").read_text(
        encoding="utf-8")
    check("设置页里有「界面大小」这一项", "界面大小" in text)
    check("滑杆绑的是 backend.uiScale", "backend.uiScale" in text)
    check("有「恢复自动」按钮", "恢复自动" in text)

    theme_text = (QML_DIR / "PawPet" / "Theme.qml").read_text(encoding="utf-8")
    check("Theme 里有 scale", "readonly property real scale" in theme_text)
    check("Theme 字号全部走 px() 换算",
          theme_text.count("px(") >= 10, str(theme_text.count("px(")))
    check("字号基准值被调大了（tiny 从 10 提到 11）",
          "px(11)" in theme_text, "基准值没动")

    # 整个界面不该再有写死的字号
    hardcoded = []
    for path in (QML_DIR / "PawPet").rglob("*.qml"):
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("font.pixelSize:") and "Theme." not in line:
                hardcoded.append(f"{path.name}:{number}")
    check("没有写死的 font.pixelSize",
          not hardcoded, str(hardcoded[:6]))

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
