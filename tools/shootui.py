r"""视觉自检：把工作台摆成真实的样子，截图存到 .cache 里（人工看）。

自动测试能验证「高度不是 0」「没有绑定循环」，但验证不了
「粉白配色到底好不好看、对比度够不够、文字看不看得清」——
那必须用眼睛看。这个脚本负责把图准备好，8 张覆盖所有页面和新卡片。

用法：
    .venv\\Scripts\\python.exe tools\\shootui.py
输出：
    .cache/shots/*.png
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "shootui"
OUT = ROOT / ".cache" / "shots"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "shootui"

_keep_alive: list = []


def main() -> int:
    print("小爪界面截图")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    # 造一点内容，不然截图里全是空状态看不清布局
    store.settings["ai_openai_base"] = "https://api.deepseek.com/v1"
    store.settings["ai_openai_model"] = "deepseek-flash"
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(QML_DIR / "PawPet" / "Main.qml")))
    root = component.create(engine.rootContext())
    if root is None:
        for error in component.errors():
            print("   ", error.toString())
        return 1
    _keep_alive.append(root)

    def pump(times: int = 20) -> None:
        for _ in range(times):
            app.processEvents()

    pump()
    dash = root.findChild(QObject, "dashboardWindow", Qt.FindChildrenRecursively)
    _keep_alive.append(dash)
    dash.resize(1000, 660)
    dash.setProperty("visible", True)
    dash.setProperty("x", 40)
    dash.setProperty("y", 40)
    pump(40)

    def grab(name: str) -> None:
        path = OUT / f"{name}.png"
        image = dash.grabWindow()
        if image.isNull():
            print(f"    {name}: 抓图失败")
            return
        image.save(str(path))
        print(f"    {name}.png  {image.width()}x{image.height()}")

    def fake_chat() -> None:
        """往对话里塞几条内容，好看到气泡、动作卡片、收尾交代的真实样子。"""
        ai = backend.ai
        ai._messages.clear()
        ai._push("user", "帮我把下载文件夹整理一下")
        ai._push("assistant",
                 "好的，我先看一眼「下载」里有什么。")
        ai._push("tool", "看文件夹 D:\\下载", tool="list_dir", ok=True,
                 detail="共 43 项：文档 18、图片 12、压缩包 7、安装包 6",
                 seconds=0.2)
        ai._push("tool", "移动 18 个文档到「文档」", tool="move_many", ok=True,
                 seconds=1.4)
        ai._push("error", "有一张图片被占用了，没能移过去",
                 tool="move_many", ok=False,
                 recovery="跳过它，继续处理剩下的")
        ai._push("assistant",
                 "整理好了：43 个文件分成了 4 类。"
                 "有 1 张图片当时正被别的程序占用，留在了原地。",
                 report="这一轮做了 5 个操作，另有 1 个没成功。"
                        "文件位置：D:\\下载\\文档、D:\\下载\\图片。"
                        "只是移动位置，没有删除任何东西 —— "
                        "想还原的话，把子文件夹里的文件拖回「下载」根目录就行。")
        ai.messagesChanged.emit()

    # ------------------------------------------------------------ 各页面
    print("\n各页面：")
    for key in ("today", "focus", "tasks", "notes", "reminders", "settings"):
        dash.setProperty("currentPage", key)
        pump(30)
        grab(f"01-{key}")

    # 设置页滚到「界面配色」那一段单独截一张：它是新加的，
    # 默认在折叠线以下，整页截图看不到。
    dash.setProperty("currentPage", "settings")
    pump(30)
    settings_scroll = dash.findChild(QObject, "settingsScroll",
                                     Qt.FindChildrenRecursively)
    if settings_scroll is None:
        # 没有 objectName 就退而求其次：把所有 Flickable 里内容最高的那个滚下去
        for child in dash.findChildren(QObject):
            if "Flickable" in child.metaObject().className():
                content = float(child.property("contentHeight") or 0)
                viewport = float(child.property("height") or 0)
                if content > viewport * 1.4 and child.property("visible"):
                    settings_scroll = child
                    break
    if settings_scroll is not None:
        from PySide6.QtCore import QMetaObject as _QMO2
        _QMO2.invokeMethod(settings_scroll, "positionViewAtEnd")
        pump(20)
        _QMO2.invokeMethod(settings_scroll, "positionViewAtBeginning")
        pump(20)
        settings_scroll.setProperty(
            "contentY",
            float(settings_scroll.property("contentHeight")) * 0.42)
        pump(20)
        grab("01-settings-配色")
    else:
        print("    找不到设置页的滚动区，跳过配色截图")

    # AI 页：空对话（现成任务卡片）
    print("\nAI 页：")
    dash.setProperty("currentPage", "ai")
    pump(40)
    grab("02-ai-空状态")

    # AI 页：有对话（气泡 + 动作卡片 + 收尾交代）
    fake_chat()
    pump(40)
    grab("03-ai-有对话")

    # 带「收尾交代」的样子 —— 复现用户截图里那段长交代，
    # 看它会不会溢出气泡（用户截图里文字被宠物挡住了）
    fake_chat()
    pump(30)
    ai = backend.ai
    ai._messages.append({
        "id": "m-report", "role": "assistant",
        "text": "知识库内容质量看着不错。我再看看之前那份 HTML 手册导入的效果。",
        "html": "", "plain": "", "time": "12:10:00", "tool": "", "risk": "",
        "ok": True, "detail": "", "seconds": 0.0, "image": "", "imageNote": "",
        "recovery": "",
        "report": "这一轮执行了 18 个操作，另有 1 个没成功。"
                  "文件位置：~/Desktop、~/Desktop/md 文档阅读、"
                  "~/Desktop/新建 文本文档.txt 等 9 处。"
                  "只是读取，没有改动任何文件。",
    })
    ai.messagesChanged.emit()
    pump(30)
    # 把对话列表滚到底，否则新加的这条落在可视区外，截出来是空的。
    # 注意 positionViewAtEnd 是 QML 函数，Python 侧要用元对象系统调。
    from PySide6.QtCore import QMetaObject as _QMO
    chat = dash.findChild(QObject, "chat", Qt.FindChildrenRecursively)
    if chat is not None:
        _QMO.invokeMethod(chat, "positionViewAtEnd")
        pump(20)
    grab("10-带长交代")
    ai._messages.pop()
    ai.messagesChanged.emit()
    pump(20)

    # 提问卡片
    backend.ai._show_approval(
        "q1", "ask_user", "read",
        "「下载」里已经有一个叫「文档」的文件夹了，\n"
        "里面有两份文件看着不像文档。要一起搬进去吗？",
        True, ["一起搬", "只搬新的", "你看着办"])
    pump(40)
    grab("04-ai-提问")
    backend.ai.answerPending("只搬新的")
    pump(20)

    # 审批卡片
    backend.ai._show_approval(
        "a1", "run_command", "danger", "执行命令：清理临时文件")
    pump(40)
    grab("05-ai-审批")
    backend.ai.resolvePending(False)
    pump(20)

    # 合并确认卡片
    backend.ai._apply_batch([
        {"index": 1, "tool": "click", "risk": "confirm", "summary": "点击「导出」按钮"},
        {"index": 2, "tool": "type_text", "risk": "confirm", "summary": "输入文件名「六月报表」"},
        {"index": 3, "tool": "press_keys", "risk": "confirm", "summary": "按回车"},
    ])
    pump(40)
    grab("06-ai-批量确认")
    backend.ai.resolveBatchAll(False)
    backend.ai.confirmBatch()
    pump(20)

    # 历史对话面板：对话持久化之后新加的入口
    ai_page = dash.findChild(QObject, "aiPage", Qt.FindChildrenRecursively)
    if ai_page is not None:
        ai_page.setProperty("showHistory", True)
        pump(40)
        grab("11-历史对话")
        ai_page.setProperty("showHistory", False)
        pump(20)

    # 模型设置展开
    if ai_page is not None:
        ai_page.setProperty("showSettings", True)
        pump(40)
        grab("07-ai-设置展开")
        ai_page.setProperty("showSettings", False)
        pump(20)

    # 弹窗：气泡 + 指令栏
    print("\n弹窗：")
    from PySide6.QtCore import Q_ARG, QMetaObject

    bubble = root.findChild(QObject, "bubbleWindow", Qt.FindChildrenRecursively)
    if bubble is not None:
        QMetaObject.invokeMethod(
            bubble, "show", Qt.DirectConnection,
            Q_ARG("QVariant", "整理完了"),
            Q_ARG("QVariant", "43 个文件分成了 4 类，都放在「下载」下面了。"),
            Q_ARG("QVariant", "task_done"),
            Q_ARG("QVariant", 40), Q_ARG("QVariant", 460),
            Q_ARG("QVariant", 260), Q_ARG("QVariant", 260))
        pump(40)
        image = bubble.grabWindow()
        if not image.isNull():
            image.save(str(OUT / "08-气泡.png"))
            print(f"    08-气泡.png  {image.width()}x{image.height()}")

    backend.commandBarVisible = True
    pump(40)
    bar = root.findChild(QObject, "commandBar", Qt.FindChildrenRecursively)
    if bar is not None:
        bar.setProperty("x", 60)
        bar.setProperty("y", 520)
        pump(30)
        image = bar.grabWindow()
        if not image.isNull():
            image.save(str(OUT / "09-指令栏.png"))
            print(f"    09-指令栏.png  {image.width()}x{image.height()}")

    backend.shutdown()
    print(f"\n截图目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
