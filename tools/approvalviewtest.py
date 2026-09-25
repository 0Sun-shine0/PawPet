r"""Approval-card visibility regression.

The important user-facing invariant is not only that the controller emits an
approval signal. The card containing the decision buttons must be inside the
visible right-hand viewport when the AI pauses.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "approvalviewtest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "approvalviewtest"
os.environ["OPENAI_API_KEY"] = "ui-test-key"

PASSED = 0
FAILED: list[str] = []
_keep_alive: list[object] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("小爪待确认卡片可见性回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    store = Store(SCRATCH / "pet.json", SCRATCH / "pet.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import PawPet 1.0
        Item {
            id: host
            property real pw: 908
            property real ph: 590
            width: pw
            height: ph
            AiPage { id: inner; objectName: "inner"; anchors.fill: parent }
        }
        """,
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "approvalviewtest.qml")),
    )
    host = component.create(engine.rootContext())
    if host is None:
        for error in component.errors():
            print("   ", error.toString())
        return 1
    _keep_alive.append(host)

    page = host.findChild(QObject, "inner", Qt.FindChildrenRecursively)
    scroll = page.findChild(QObject, "rightScroll", Qt.FindChildrenRecursively)
    ask = page.findChild(QObject, "askCard", Qt.FindChildrenRecursively)
    approval = page.findChild(QObject, "approvalCard", Qt.FindChildrenRecursively)
    batch = page.findChild(QObject, "batchCard", Qt.FindChildrenRecursively)
    check("待确认卡片对象齐全", all((page, scroll, ask, approval, batch)))
    if not all((page, scroll, ask, approval, batch)):
        return 1

    def pump(cycles: int = 30) -> None:
        for _ in range(cycles):
            app.processEvents()
            time.sleep(0.006)

    def at_bottom(card: QObject, minimum_height: float) -> tuple[bool, str]:
        viewport = float(scroll.property("height") or 0)
        content_height = float(scroll.property("contentHeight") or 0)
        content_y = float(scroll.property("contentY") or 0)
        card_height = float(card.property("height") or 0)
        max_y = max(0.0, content_height - viewport)
        ok = (card_height >= minimum_height
              and content_height >= viewport
              and abs(content_y - max_y) <= 2)
        detail = (f"card h={card_height:.0f}, contentY={content_y:.0f}, "
                  f"maxY={max_y:.0f}, viewport={viewport:.0f}, "
                  f"contentH={content_height:.0f}")
        return ok, detail

    host.setProperty("pw", 908)
    host.setProperty("ph", 590)
    ai = backend.ai
    ai._messages.clear()
    for index in range(12):
        ai._push("assistant", f"历史操作 {index + 1}：" + "这是一段较长的对话内容。" * 4)
    ai.messagesChanged.emit()
    pump(40)

    print("=== 提问卡片 ===")
    ai._show_approval("question-test", "ask_user", "read",
                      "请确认要把这些文件放到哪个文件夹？",
                      True, ["放到文档", "放到工作", "暂不处理"])
    pump(80)
    ok, detail = at_bottom(ask, 228)
    check("提问卡片可见", bool(ask.property("visible")))
    check("提问卡片完整落在视口", ok, detail)
    ai.answerPending("放到文档")
    pump(30)
    check("回答后提问卡片收起", not bool(ask.property("visible")))

    print("\n=== 危险审批卡片 ===")
    ai._show_approval("approval-test", "run_command", "danger",
                      "执行命令：清理临时文件")
    pump(80)
    ok, detail = at_bottom(approval, 210)
    check("审批卡片可见", bool(approval.property("visible")))
    check("审批卡片完整落在视口", ok, detail)
    ai.resolvePending(False)
    pump(30)
    check("拒绝后审批卡片收起", not bool(approval.property("visible")))

    print("\n=== 批量确认卡片 ===")
    ai._apply_batch([
        {"index": 1, "tool": "click", "risk": "confirm", "summary": "点击导出"},
        {"index": 2, "tool": "type_text", "risk": "confirm", "summary": "输入文件名"},
        {"index": 3, "tool": "press_keys", "risk": "confirm", "summary": "按回车"},
    ])
    pump(80)
    ok, detail = at_bottom(batch, 220)
    check("批量确认卡片可见", bool(batch.property("visible")))
    check("批量确认卡片完整落在视口", ok, detail)
    ai.resolveBatchAll(False)
    ai.confirmBatch()
    ai._batch = []
    ai.approvalChanged.emit()
    pump(30)
    check("提交后批量确认卡片收起", not bool(batch.property("visible")))

    backend.shutdown()
    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
