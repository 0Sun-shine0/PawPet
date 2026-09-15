"""界面联调：确认「每轮最多执行步数」这个设置真的能从 QML 控件走通。

单测 agenttest 只验证了后端；这里把真实的 AiPage.qml 加载起来，
点那个下拉框，看数据有没有真的落进 settings、有没有反馈到界面。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "steptest"
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
    print("小爪「每轮最多执行步数」界面联调")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.agent import (
        DEFAULT_MAX_STEPS,
        MAX_MAX_STEPS,
        STEP_PRESETS,
    )
    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(QML_DIR / "PawPet" / "page" / "AiPage.qml")))

    def build():
        """每次重建一个干净的 AiPage，避免旧绑定干扰。"""
        page = component.create(engine.rootContext())
        if page is None:
            for error in component.errors():
                print("   QML 错误：", error.toString())
            return None
        page.setParent(engine)
        return page

    print("\n=== QML 能不能加载 ===")
    page = build()
    check("AiPage.qml 加载成功", page is not None)
    if page is None:
        return 1

    box = page.findChild(QObject, "stepBox", Qt.FindChildrenRecursively)
    check("找得到步数下拉框（objectName=stepBox）", box is not None)
    if box is None:
        return 1

    print("\n=== 初始值 ===")
    options = backend.aiStepOptions
    check("后端给出了选项列表", len(options) == len(STEP_PRESETS),
          f"实际 {len(options)} 个")
    check("选项里带 value / label / hint",
          all(set(o) >= {"value", "label", "hint"} for o in options),
          str(options)[:100])
    check("combo 的选项数和后端一致",
          box.property("count") == len(STEP_PRESETS),
          f"实际 {box.property('count')}")
    check("显示的是人话而不是字典",
          "步" in (box.property("displayText") or ""),
          str(box.property("displayText")))
    expect_index = list(STEP_PRESETS).index(DEFAULT_MAX_STEPS)
    check(f"默认选中「{DEFAULT_MAX_STEPS} 步」（第 {expect_index} 项）",
          box.property("currentIndex") == expect_index,
          f"实际 index={box.property('currentIndex')} "
          f"text={box.property('displayText')}")

    print("\n=== 用户选别的步数 ===")
    target_index = list(STEP_PRESETS).index(50)
    # 复现真实交互：选中某一项 → 控件发 activated → QML 里那段 onActivated 跑起来
    box.setProperty("currentIndex", target_index)
    box.activated.emit(target_index)
    check("下拉框选中了 50 步",
          box.property("currentIndex") == target_index,
          f"实际 {box.property('currentIndex')}")
    check("设置真的落进了 store",
          store.settings.get("ai_max_steps") == 50,
          str(store.settings.get("ai_max_steps")))
    check("后端的 maxSteps 跟着变了", backend.aiMaxSteps == 50,
          str(backend.aiMaxSteps))
    check("提示文案也更新了", "50 步" in backend.aiMaxStepsHint,
          backend.aiMaxStepsHint[:60])

    print("\n=== 重开界面要记住上次的选择 ===")
    page2 = build()
    box2 = (page2.findChild(QObject, "stepBox", Qt.FindChildrenRecursively)
            if page2 else None)
    check("重建后仍然选在 50 步",
          box2 is not None and box2.property("currentIndex") == target_index,
          f"实际 {box2.property('currentIndex') if box2 else 'None'}")

    print("\n=== 越界和脏值 ===")
    backend.aiMaxSteps = 9999
    check("太大的值被夹到上限", backend.aiMaxSteps == MAX_MAX_STEPS,
          str(backend.aiMaxSteps))
    backend.aiMaxSteps = 1
    check("太小的值被抬到下限（不会变成 0 步）", backend.aiMaxSteps >= 5,
          str(backend.aiMaxSteps))
    backend.aiMaxSteps = DEFAULT_MAX_STEPS

    print("\n=== QML 里没有写死的旧文案 ===")
    qml_text = (QML_DIR / "PawPet" / "page" / "AiPage.qml").read_text(encoding="utf-8")
    check("界面文案不再写死 20 步", "20 步" not in qml_text)
    check("控件绑到了 backend.aiMaxSteps", "backend.aiMaxSteps" in qml_text)
    check("选项绑到了 backend.aiStepOptions", "backend.aiStepOptions" in qml_text)

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
