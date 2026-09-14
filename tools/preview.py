"""渲染界面截图，用来检查长什么样。

用法（必须在装了 PySide6 的 .venv 里跑）：
    .venv\\Scripts\\python.exe tools\\preview.py

会在 .cache/preview/ 下生成窗口截图。

关于平台：Qt 的 offscreen 插件在本机加载不到任何系统字体（0 个字体族），
文字会全部渲染成方块，所以默认用真实的 windows 平台。窗口会被挪到屏幕
外面再抓图，因此不会闪到你，但确实是真实渲染管线（含抗锯齿和系统字体）。
用 --platform offscreen 可以切回无头模式，只是文字会变方块。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PLATFORM = "windows"
for i, arg in enumerate(sys.argv):
    if arg == "--platform" and i + 1 < len(sys.argv):
        PLATFORM = sys.argv[i + 1]
os.environ["QT_QPA_PLATFORM"] = PLATFORM
if PLATFORM == "offscreen":
    os.environ.setdefault("QT_QUICK_BACKEND", "software")

# 抓图用的舞台坐标：挪到桌面外，既拿到真实渲染又不打扰用户
STAGE_X = -4000
STAGE_Y = 40

OUT = ROOT / ".cache" / "preview"

# 让 backend / engine 活到进程结束，否则解释器退出时 QML 绑定会引用
# 已被回收的 backend，刷出一大堆 "of null" 噪音。
_keep_alive: list = []


def main() -> int:
    from PySide6.QtCore import QObject, QEventLoop, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR, ensure_dirs
    from pawpet.store import Store

    ensure_dirs()
    OUT.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    # 关键：预览绝对不能碰真实的 pet_data.json。
    # 这里把数据落在 .cache/preview-data/ 下，每次跑都从干净的样例数据开始。
    scratch = ROOT / ".cache" / "preview-data"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    # 给点示例数据，不然列表页全是空状态，看不出效果
    backend.tasks.add("把季度复盘写完", 2)
    backend.tasks.add("回复张工的邮件", 1)
    backend.tasks.add("预约周五的牙医", 0)
    backend.tasks.add("读 20 页《重构》", 0)
    backend.tasks.add("整理下载文件夹", 0)
    backend.tasks.toggle(store.tasks[-1]["id"])
    backend.reminders.add("喝水", "10:30", "daily")
    backend.reminders.add("站起来走走", "15:00", "weekdays")
    backend.notes.update(store.notes[0]["id"], "会议要点",
                         "1. 下周三前给出接口文档\n2. 视觉稿确认后再动工\n"
                         "3. 记得同步给测试同学\n\n灵感：把常用操作放到宠物右键菜单里。")

    # 造一点历史数据，让「近 7 天」和「今日目标」有东西可画。
    # 必须在 Backend 建好之后调用 refresh()，否则模型还是旧的。
    from datetime import date, timedelta

    for offset, minutes in ((6, 45), (5, 90), (4, 30), (3, 120), (2, 75), (1, 60), (0, 85)):
        key = (date.today() - timedelta(days=offset)).isoformat()
        day = store.stats.setdefault(key, {})
        day["focus_minutes"] = minutes
        day["focus_rounds"] = max(1, minutes // 25)
        day["tasks_done"] = (offset * 2) % 5
    store.state["sessions"] = [
        {"id": f"s{i}", "start": time.time() - (i + 1) * 1800,
         "end": time.time() - i * 1800, "minutes": m, "label": "专注"}
        for i, m in enumerate((25, 25, 45, 25))
    ]
    backend.week.refresh()
    backend.sessions.refresh()
    backend.focus.notify_stats_changed()

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")))
    if not engine.rootObjects():
        print("QML 加载失败")
        return 1
    root = engine.rootObjects()[0]

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def child(name: str):
        """QML 里的窗口挂了 objectName，用 findChild 拿比 root.property() 稳。"""
        obj = root.findChild(QObject, name)
        if obj is None:
            raise RuntimeError(f"找不到 QML 对象 {name}")
        return obj

    def place(window, x: int, y: int) -> None:
        window.setProperty("x", x)
        window.setProperty("y", y)

    def shot(window, name: str) -> None:
        pump(400)
        image = window.grabWindow()
        if image.isNull():
            print(f"  x {name}: 抓图失败")
            return
        path = OUT / f"{name}.png"
        image.save(str(path))
        print(f"  v {name}: {path.name} ({image.width()}x{image.height()})")

    pet = child("petWindow")
    dash = child("dashboardWindow")
    bubble = child("bubbleWindow")

    print(f"[平台] {PLATFORM}")

    # ---------------------------------------------------------- 宠物窗口
    print("[1] 宠物窗口")
    pet.setProperty("visible", True)
    place(pet, STAGE_X, STAGE_Y)
    pump(1400)
    shot(pet, "01_pet_idle")

    backend.focus.startFocus()
    pump(1200)
    shot(pet, "02_pet_focusing")

    pet.setProperty("hovering", True)
    place(pet, STAGE_X, STAGE_Y + 60)
    pump(600)
    shot(pet, "03_pet_hover")
    pet.setProperty("hovering", False)

    backend.focus.reset()
    pump(300)

    # ---------------------------------------------------------- 工作台
    print("[2] 工作台各页")
    place(dash, STAGE_X, STAGE_Y)
    dash.setProperty("visible", True)
    pump(1400)

    for page in ("today", "focus", "tasks", "notes", "reminders", "ai", "settings"):
        dash.setProperty("currentPage", page)
        shot(dash, f"10_dashboard_{page}")

    # ---------------------------------------------------------- 气泡
    print("[3] 气泡提示")
    dash.setProperty("visible", False)
    pump(200)
    bubble.setProperty("headline", "专注完成 🎉")
    bubble.setProperty("body", "这一轮 25 分钟拿下了。站起来活动一下，喝口水。")
    bubble.setProperty("kind", "focus_done")
    place(bubble, STAGE_X + 40, STAGE_Y + 40)
    bubble.setProperty("visible", True)
    shot(bubble, "20_bubble")

    print(f"\n截图目录：{OUT}")

    # 按正确顺序拆掉 QML：先销毁 QML 对象树，再放掉 backend。
    # 反过来（或把 context property 置空）会让绑定在 backend 已经失效后
    # 重新求值，刷出一大堆 "of null" 噪音。
    for obj in engine.rootObjects():
        obj.deleteLater()
    del engine
    for _ in range(6):
        app.processEvents()
        pump(40)
    _keep_alive.extend([backend, root, app, store])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
