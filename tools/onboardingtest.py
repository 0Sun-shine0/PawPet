"""上手指引 + 数据导入导出 + 检查更新的端到端接线测试。

和 qmlcheck.py 的分工：那个只检查 QML 语法/属性，这个真的把 Main.qml
加载起来，验证「同一条链路上 Python 和 QML 接对了没有」。

具体验三件事：

1. **引导窗口真的能被信号唤起。** 这是最容易出错的地方 —— QML 里的
   函数名写成 onOnboardingRequested 还是 onOnboardingRequested()，
   Python 侧 emit 的参数个数，任何一处对不上都是「装了但永远不弹」，
   而且不报错。
2. **backend 暴露给 QML 的属性名全部存在。** QML 里写 backend.xxx，
   Python 侧没有就是绑定失败 + 控制台告警，界面上表现为空白。
3. **跟真实数据文件隔离。** 全程用临时目录。

用法：
    .venv\\Scripts\\python.exe tools\\onboardingtest.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="pawpet-onboarding-"))
    app = QGuiApplication(sys.argv)

    try:
        # 数据和真实环境隔开
        from pawpet.store import Store

        store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
        store.load()

        from pawpet.backend import Backend

        backend = Backend(store)

        engine = QQmlApplicationEngine()
        engine.addImportPath(str(ROOT / "pawpet" / "qml"))
        engine.rootContext().setContextProperty("backend", backend)

        warnings: list[str] = []
        engine.warnings.connect(lambda items: warnings.extend(
            str(item.toString()) for item in items))

        section("加载 Main.qml")
        engine.load(QUrl.fromLocalFile(str(ROOT / "pawpet" / "qml" / "PawPet" / "Main.qml")))
        roots = engine.rootObjects()
        check("Main.qml 加载成功", bool(roots))
        if not roots:
            for line in warnings:
                print(f"       QML: {line}")
            return 1

        root = roots[0]

        section("引导窗口已创建但默认不显示")
        # 用 findChild 而不是 root.property("onboardingWindow")：
        # 后者要把自定义 QML 类型转成 Python 对象，PySide6 没有对应的
        # 转换器，会抛 "Can't find converter for 'Onboarding_QMLTYPE_*'"。
        # objectName 在 Main.qml 里已经设好了，findChild 更直接。
        from PySide6.QtCore import QObject

        onboarding = root.findChild(QObject, "onboardingWindow")
        check("拿得到 onboardingWindow", onboarding is not None)
        if onboarding is None:
            return 1
        check("默认不可见", onboarding.property("visible") is False,
              f"visible={onboarding.property('visible')}")
        check("起始在第 1 屏", onboarding.property("step") == 0,
              f"step={onboarding.property('step')}")

        section("backend 的属性/Slot 对 QML 可见")
        for name in ("onboardingDone", "updateCheck", "updateStatus",
                     "updateAvailable", "latestVersion", "updateNote",
                     "updateSkipped", "updateChecking"):
            value = backend.property(name) if hasattr(backend, "property") else None
            check(f"属性 {name}", value is not None, f"拿到 {value!r}")
        for name in ("showOnboarding", "finishOnboarding", "resetOnboarding",
                     "exportData", "importData", "checkUpdateIfDue",
                     "checkUpdateNow", "openUpdatePage", "skipThisVersion",
                     "resumeUpdateNotice"):
            check(f"Slot {name}", callable(getattr(backend, name, None)))

        section("信号能唤起引导（这是最容易接错的一环）")
        emitted = {"count": 0}
        backend.onboardingRequested.connect(lambda: emitted.__setitem__("count", emitted["count"] + 1))

        backend.showOnboarding()
        check("信号发出", emitted["count"] == 1, f"实际 {emitted['count']} 次")
        check("窗口变可见", onboarding.property("visible") is True,
              f"visible={onboarding.property('visible')}")

        section("三屏都能翻到底")
        # steps 是 QML 里的 JS 数组，property() 拿回来是 QJSValue，
        # 要先 toVariant() 转成 Python list 才能取长度。
        raw_steps = onboarding.property("steps")
        if hasattr(raw_steps, "toVariant"):
            raw_steps = raw_steps.toVariant()
        steps = raw_steps
        check("有 3 屏", steps is not None and len(steps) == 3,
              f"实际 {len(steps) if steps else 0}")
        if steps:
            check("每屏都有标题", all(str(s.get("title") or "") for s in steps))
            check("每屏都有正文", all(str(s.get("body") or "") for s in steps))
            check("每屏都有提示", all(str(s.get("hint") or "") for s in steps))
            check("第 3 屏明确说了可以不配 Key",
                  "不配" in str(steps[2].get("body") or ""),
                  str(steps[2].get("body") or ""))

        section("走完引导会置位（否则每次启动都弹）")
        check("初始未读", store.settings.get("onboarding_done") is False)
        onboarding.metaObject().invokeMethod(onboarding, "next")
        onboarding.metaObject().invokeMethod(onboarding, "next")
        check("翻到第 3 屏", onboarding.property("step") == 2,
              f"step={onboarding.property('step')}")
        onboarding.metaObject().invokeMethod(onboarding, "next")
        check("再 next 就关闭", onboarding.property("visible") is False)
        check("onboarding_done 置真", store.settings.get("onboarding_done") is True,
              f"实际 {store.settings.get('onboarding_done')!r}")
        check("设置已落盘", (scratch / "pet_data.json").is_file())

        section("再次启动不再打扰")
        backend.showOnboarding()
        check("手动调 still 能显示", onboarding.property("visible") is True)
        onboarding.metaObject().invokeMethod(onboarding, "finish")
        check("关掉后仍为已读", store.settings.get("onboarding_done") is True)

        section("跳过的路径也置位")
        store.settings["onboarding_done"] = False
        backend.showOnboarding()
        onboarding.metaObject().invokeMethod(onboarding, "next")
        onboarding.metaObject().invokeMethod(onboarding, "next")
        onboarding.metaObject().invokeMethod(onboarding, "next")
        check("三屏走完置真", store.settings.get("onboarding_done") is True)

        section("更新检查的默认状态")
        check("默认开启检查", store.settings.get("update_check") is True)
        check("默认没有跳过任何版本", backend.property("updateSkipped") == "",
              f"实际 {backend.property('updateSkipped')!r}")
        check("没查过时状态为空（不显示无意义文案）",
              backend.property("updateStatus") == "",
              f"实际 {backend.property('updateStatus')!r}")
        check("还没有新版时不显示更新提示",
              backend.property("updateAvailable") is False)

        section("跳过某个版本之后就不再提示它")
        backend._latest = {"version": "9.9.9", "url": "https://example.com", "note": "测试"}
        check("这时提示有新版本", backend.property("updateAvailable") is True)
        check("版本号读得到", backend.property("latestVersion") == "9.9.9")
        backend.skipThisVersion()
        check("跳过之后不再提示", backend.property("updateAvailable") is False)
        check("跳过记录已落盘",
              store.settings.get("update_skipped_version") == "9.9.9",
              f"实际 {store.settings.get('update_skipped_version')!r}")
        check("状态文案说清了是被忽略",
              "忽略" in backend.property("updateStatus"),
              backend.property("updateStatus"))
        backend.resumeUpdateNotice()
        check("恢复之后又能提示", backend.property("updateAvailable") is True)
        check("恢复后记录被清空",
              store.settings.get("update_skipped_version") == "")

        section("QML 有没有报绑定告警")
        # 只看和本次改动相关的：Theme/backend 属性绑定失败会出现在这里
        relevant = [w for w in warnings
                    if any(key in w for key in ("Onboarding", "onboarding",
                                                "update", "Update", "transfer"))]
        check("没有相关告警", not relevant,
              " / ".join(relevant[:3]))
        if warnings:
            print(f"       （总共 {len(warnings)} 条 QML 告警，前 5 条：）")
            for line in warnings[:5]:
                print(f"        {line}")

    finally:
        shutil.rmtree(scratch, ignore_errors=True)

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
