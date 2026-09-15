"""Backend：暴露给 QML 的统一接口。

QML 只能看到这个对象。它是唯一允许改数据的地方，并且每次改动都会
落到 Store，所以「界面上看到的」和「磁盘上存的」永远一致。
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from . import win32
from .ai.controller import AiController
from .config import APP_NAME, APP_VERSION, ROOT
from .focus import FocusEngine
from .models import NoteModel, ReminderModel, SessionModel, TaskModel, WeekModel
from .reminders import ReminderEngine
from .services import Notifier, SoundPlayer
from .store import pretty_day, today_key


def _setting_property(key: str, qtype, notify):
    """按设置项生成 Qt Property，让 QML 里的绑定能自动刷新。"""

    def getter(self):
        value = self._store.settings.get(key)
        if value is None:
            return qtype()
        try:
            return qtype(value)
        except (TypeError, ValueError):
            return qtype()

    def setter(self, value):
        current = self._store.settings.get(key)
        if isinstance(value, float) and isinstance(current, (int, float)):
            if abs(float(value) - float(current)) < 1e-6:
                return
        elif current == value:
            return
        self._store.settings[key] = value
        self._on_setting_changed(key)

    return Property(qtype, getter, setter, notify=notify)


class Backend(QObject):
    settingsChanged = Signal()
    petStyleChanged = Signal()
    dashboardVisibilityChanged = Signal()
    petVisibilityChanged = Signal()
    commandBarVisibilityChanged = Signal()
    bubbleRequested = Signal(str, str, str)     # (kind, title, body)
    showDashboardRequested = Signal(str)       # (page)
    hideDashboardRequested = Signal()
    quitRequested = Signal()
    clockChanged = Signal()
    sitChanged = Signal()
    upcomingChanged = Signal()

    # --------------------------------------------------------------- 构造
    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._sound = SoundPlayer()
        self._notifier = Notifier(self)
        self._sound.enabled = bool(store.settings.get("sound_enabled", True))

        self._tasks = TaskModel(store, self)
        self._reminders = ReminderModel(store, self)
        self._notes = NoteModel(store, self)
        self._sessions = SessionModel(store, self)
        self._week = WeekModel(store, self)
        self._focus = FocusEngine(store, self)
        self.reminder_engine = ReminderEngine(store, self)

        # AI 操作模块。它需要能反过来往待办/便签里写东西，所以把 self 传给它。
        self._ai = AiController(store, self)
        self._ai.context.backend = self

        self._last_clock = ""
        self._upcoming: list[str] = []
        self._last_save = 0.0
        self._dashboard_visible = False
        self._command_bar_visible = False
        self._pet_visible = True
        self._user_away = False

        self._focus.completed.connect(self._on_focus_completed)
        self._focus.tick.connect(self._on_focus_tick)
        self.reminder_engine.fired.connect(self._on_reminder_fired)
        self._reminders.changed.connect(self._refresh_upcoming)
        self._tasks.countsChanged.connect(self._on_focus_tick)
        self._ai.toastRequested.connect(self._on_ai_toast)
        self._ai.memoryChanged.connect(self.memoryChanged)

        self._refresh_upcoming()

    # ------------------------------------------------------- 子对象（给 QML）
    @Property(QObject, constant=True)
    def focus(self) -> QObject:
        return self._focus

    @Property(QObject, constant=True)
    def tasks(self) -> QObject:
        return self._tasks

    @Property(QObject, constant=True)
    def reminders(self) -> QObject:
        return self._reminders

    @Property(QObject, constant=True)
    def notes(self) -> QObject:
        return self._notes

    @Property(QObject, constant=True)
    def sessions(self) -> QObject:
        return self._sessions

    @Property(QObject, constant=True)
    def week(self) -> QObject:
        return self._week

    @Property(QObject, constant=True)
    def ai(self) -> QObject:
        return self._ai

    # --------------------------------------------------------------- 信号
    def notifier(self) -> Notifier:
        return self._notifier

    def _notify(self, kind: str, title: str, body: str = "", sound: str | None = None) -> None:
        if sound and self._store.settings.get("sound_enabled", True):
            self._sound.play(sound)
        self.bubbleRequested.emit(kind, title, body)
        if self._store.settings.get("notify_enabled", True):
            self._notifier.post(kind, title, body)

    def _on_focus_completed(self, mode: str, minutes: int) -> None:
        self._sessions.refresh()
        self._week.refresh()
        if mode == "focus":
            self._notify("focus_done", "专注完成 🎉",
                         f"这一轮 {minutes} 分钟拿下了。站起来活动一下，喝口水。", "focus_done")
        else:
            self._notify("break_done", "休息结束", "准备好了就继续下一轮专注吧。", "break_done")
        self.clockChanged.emit()

    def _on_focus_tick(self) -> None:
        self.clockChanged.emit()

    def _on_reminder_fired(self, kind: str, title: str, body: str) -> None:
        self._notify(kind, title, body, "sit" if kind == "sit" else "reminder")
        self._refresh_upcoming()
        self.sitChanged.emit()

    @Slot(str, str)
    def _on_ai_toast(self, title: str, body: str) -> None:
        """AI 的提示也用气泡弹出来，不然用户不知道它干完活了。"""
        self._notify("info", title, body, "task_done" if "结束" in title else None)

    def shutdown(self) -> None:
        """退出前收尾：停掉 AI 线程和 MCP 子进程。"""
        try:
            self._ai.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_upcoming(self) -> None:
        try:
            upcoming = self.reminder_engine.next_upcoming(4)
        except Exception:  # noqa: BLE001 - 展示用数据，算不出来也不该影响主流程
            upcoming = []
        if upcoming != self._upcoming:
            self._upcoming = upcoming
            self.upcomingChanged.emit()

    # ------------------------------------------------------------- 设置项
    pet_scale = _setting_property("pet_scale", float, settingsChanged)
    pet_opacity = _setting_property("pet_opacity", float, settingsChanged)
    pet_style = _setting_property("pet_style", str, settingsChanged)
    always_on_top = _setting_property("always_on_top", bool, settingsChanged)
    fade_when_idle = _setting_property("fade_when_idle", bool, settingsChanged)
    focus_minutes = _setting_property("focus_minutes", int, settingsChanged)
    short_break_minutes = _setting_property("short_break_minutes", int, settingsChanged)
    long_break_minutes = _setting_property("long_break_minutes", int, settingsChanged)
    rounds_before_long_break = _setting_property("rounds_before_long_break", int, settingsChanged)
    auto_start_next = _setting_property("auto_start_next", bool, settingsChanged)
    sound_enabled = _setting_property("sound_enabled", bool, settingsChanged)
    notify_enabled = _setting_property("notify_enabled", bool, settingsChanged)
    sit_reminder_enabled = _setting_property("sit_reminder_enabled", bool, settingsChanged)
    sit_reminder_minutes = _setting_property("sit_reminder_minutes", int, settingsChanged)
    afk_minutes = _setting_property("afk_minutes", int, settingsChanged)
    daily_goal_minutes = _setting_property("daily_goal_minutes", int, settingsChanged)
    quiet_when_fullscreen = _setting_property("quiet_when_fullscreen", bool, settingsChanged)
    autostart = _setting_property("autostart", bool, settingsChanged)
    move_step = _setting_property("move_step", int, settingsChanged)
    hotkey_dashboard = _setting_property("hotkey_dashboard", str, settingsChanged)
    hotkey_focus = _setting_property("hotkey_focus", str, settingsChanged)
    hotkey_task = _setting_property("hotkey_task", str, settingsChanged)

    def _on_setting_changed(self, key: str) -> None:
        if key == "sound_enabled":
            self._sound.enabled = bool(self._store.settings.get("sound_enabled", True))
        elif key == "autostart":
            # 以注册表里的真实状态为准，避免界面显示和系统实际不一致
            actual = win32.set_autostart(bool(self._store.settings.get("autostart", False)))
            self._store.settings["autostart"] = actual
        elif key in ("sit_reminder_enabled", "sit_reminder_minutes", "afk_minutes"):
            self.reminder_engine.reset_sit_timer()
            self.sitChanged.emit()

        if key in ("pet_scale", "pet_opacity", "always_on_top", "fade_when_idle"):
            self.petStyleChanged.emit()
        self.settingsChanged.emit()
        self.flush()

    # ------------------------------------------------------------- 数据落盘
    def save(self) -> None:
        """带节流的保存：拖动宠物时每帧都写盘是没必要的。"""
        now = time.time()
        if now - self._last_save < 0.35:
            return
        self._last_save = now
        self._store.save()

    def flush(self) -> None:
        self._last_save = time.time()
        self._store.save()

    # ------------------------------------------------------------ 基础信息
    @Property(str, constant=True)
    def appName(self) -> str:
        return APP_NAME

    @Property(str, constant=True)
    def version(self) -> str:
        return APP_VERSION

    @Property(str, notify=clockChanged)
    def clockText(self) -> str:
        return datetime.now().strftime("%H:%M")

    @Property(str, notify=clockChanged)
    def dateText(self) -> str:
        return pretty_day(today_key())

    @Property(str, notify=clockChanged)
    def greeting(self) -> str:
        hour = datetime.now().hour
        if hour < 6:
            return "夜深了"
        if hour < 11:
            return "早上好"
        if hour < 14:
            return "中午好"
        if hour < 18:
            return "下午好"
        return "晚上好"

    @Property(str, notify=clockChanged)
    def statusLine(self) -> str:
        pending = self._tasks.pendingCount
        if self._focus.running:
            head = f"{self._focus.modeLabel}中 {self._focus.clock}"
        elif self._focus.progress > 0.001:
            head = f"{self._focus.modeLabel}已暂停 {self._focus.clock}"
        else:
            head = "空闲中"
        return f"{head} · {pending} 件待办 · 今日专注 {self._focus.todayMinutes} 分钟"

    @Property(str, notify=clockChanged)
    def todayLine(self) -> str:
        return self._focus.todayLine

    @Property(str, notify=sitChanged)
    def sitStatus(self) -> str:
        return self.reminder_engine.sit_status_text()

    @Property(float, notify=clockChanged)
    def sitProgress(self) -> float:
        return self.reminder_engine.sitProgress

    @Property(list, notify=upcomingChanged)
    def upcoming(self) -> list:
        return self._upcoming

    @Property(int, notify=clockChanged)
    def pendingCount(self) -> int:
        return self._tasks.pendingCount

    @Property(str, notify=clockChanged)
    def petBadge(self) -> str:
        pending = self._tasks.pendingCount
        return str(pending) if pending else "✓"

    @Property(bool, notify=settingsChanged)
    def soundAvailable(self) -> bool:
        return self._sound.available

    # ------------------------------------------------- 窗口可见性（QML 绑定）
    @Property(bool, notify=dashboardVisibilityChanged)
    def dashboardVisible(self) -> bool:
        return self._dashboard_visible

    @dashboardVisible.setter
    def dashboardVisible(self, value: bool) -> None:
        value = bool(value)
        if value != self._dashboard_visible:
            self._dashboard_visible = value
            self.dashboardVisibilityChanged.emit()

    @Property(bool, notify=petVisibilityChanged)
    def petVisible(self) -> bool:
        return self._pet_visible

    @petVisible.setter
    def petVisible(self, value: bool) -> None:
        value = bool(value)
        if value != self._pet_visible:
            self._pet_visible = value
            self.petVisibilityChanged.emit()

    # ------------------------------------------------- 指令栏（QML 绑定）
    @Property(bool, notify=commandBarVisibilityChanged)
    def commandBarVisible(self) -> bool:
        return self._command_bar_visible

    @commandBarVisible.setter
    def commandBarVisible(self, value: bool) -> None:
        value = bool(value)
        if value != self._command_bar_visible:
            self._command_bar_visible = value
            self.commandBarVisibilityChanged.emit()
        if value:
            # 指令栏和小爪是同一条路径上的东西，弹它的时候小爪必须在
            self.petVisible = True

    @Property(str, notify=settingsChanged)
    def commandBarAnchor(self) -> str:
        anchor = str(self._store.settings.get("command_bar_anchor", "pet"))
        return anchor if anchor in ("pet", "bottom") else "pet"

    @commandBarAnchor.setter
    def commandBarAnchor(self, value: str) -> None:
        value = value if value in ("pet", "bottom") else "pet"
        if value == self._store.settings.get("command_bar_anchor"):
            return
        self._store.settings["command_bar_anchor"] = value
        self.settingsChanged.emit()
        self.flush()

    pet_click_action = _setting_property("pet_click_action", str, settingsChanged)
    hotkey_ask = _setting_property("hotkey_ask", str, settingsChanged)

    # 「每轮最多执行步数」的唯一数据源在 AiController 里（它还要夹范围、
    # 写进提示词、并在界面日志里说一声），所以这里只做转发，别用
    # _setting_property 另存一份，否则两边会各说各话。
    @Property(int, notify=settingsChanged)
    def aiMaxSteps(self) -> int:
        return self.ai.maxSteps

    @aiMaxSteps.setter
    def aiMaxSteps(self, value) -> None:
        self.ai.maxSteps = value

    @Property("QVariantList", notify=settingsChanged)
    def aiStepOptions(self) -> list:
        return self.ai.stepOptions

    @Property(str, notify=settingsChanged)
    def aiMaxStepsHint(self) -> str:
        return self.ai.maxStepsHint

    # ------------------------------------------------------------ 跨会话记忆
    memoryChanged = Signal()

    @Property(bool, notify=memoryChanged)
    def aiMemoryEnabled(self) -> bool:
        return self.ai.memoryEnabled

    @aiMemoryEnabled.setter
    def aiMemoryEnabled(self, value: bool) -> None:
        self.ai.memoryEnabled = value

    @Property(str, notify=memoryChanged)
    def aiMemorySummary(self) -> str:
        return self.ai.memorySummary

    @Property(int, notify=memoryChanged)
    def aiMemoryCount(self) -> int:
        return self.ai.memoryCount

    @Slot()
    def aiClearMemory(self) -> None:
        self.ai.clearMemory()

    @Slot(str)
    def aiForgetMemory(self, text: str) -> None:
        self.ai.forgetMemory(text)

    @Slot(result=str)
    def aiFrequentApps(self) -> str:
        """最常用的几个程序，一行一个。给设置页展示用。"""
        try:
            pairs = self._ai.context.frequent_apps(6)
        except Exception:  # noqa: BLE001
            return ""
        if not pairs:
            return ""
        return "\n".join(f"{name}（{count} 次）" for name, count in pairs)

    @Property(str, notify=settingsChanged)
    def buildInfo(self) -> str:
        """当前**实际在跑**的代码是什么版本。

        为什么需要这个：改了代码之后如果跑的还是旧进程或旧的打包 exe，
        界面上完全看不出来 —— 会以为是代码没写对，其实是根本没加载。
        这个字符串把「跑的是源码还是打包版」「关键设置当前是多少」
        直接摆出来，一眼就能分辨。
        """
        import sys as _sys

        frozen = getattr(_sys, "frozen", False)
        source = "打包版 exe" if frozen else "源码"
        try:
            executable = _sys.executable or ""
        except Exception:  # noqa: BLE001
            executable = ""

        lines = [f"版本：{APP_VERSION}（{source}）"]
        if executable:
            lines.append(f"解释器：{executable}")
        lines.append(f"执行步数：{self.ai.maxSteps} 步")
        lines.append("记忆：" + ("已开启" if self.ai.memoryEnabled else "已关闭"))
        try:
            from .ai import uia

            lines.append("界面元素：可用" if uia.available() else "界面元素：不可用")
        except Exception:  # noqa: BLE001
            lines.append("界面元素：不可用")
        return "\n".join(lines)

    @Slot()
    def showCommandBar(self) -> None:
        self.commandBarVisible = True

    @Slot()
    def hideCommandBar(self) -> None:
        self.commandBarVisible = False

    @Slot()
    def toggleCommandBar(self) -> None:
        self.commandBarVisible = not self._command_bar_visible

    @Slot(str)
    def ask(self, text: str = "") -> None:
        """外部（热键、托盘、命令行）让 AI 干活的统一入口。"""
        text = (text or "").strip()
        if not text:
            self.showCommandBar()
            return
        self.commandBarVisible = True
        self._ai.send(text)

    # ------------------------------------------------------- 小爪的点击行为
    @Slot()
    def handlePetClick(self) -> None:
        """单击小爪。默认弹指令栏，这是日常用得最多的动作。"""
        action = str(self._store.settings.get("pet_click_action", "command"))
        if action == "dashboard":
            self.showDashboard("focus")
        else:
            self.toggleCommandBar()

    @Slot()
    def handlePetDoubleClick(self) -> None:
        """双击永远是「打开工作台」，不受单击设置影响。"""
        self.showDashboard("focus")

    @Property(str, notify=petVisibilityChanged)
    def petVisibleLabel(self) -> str:
        return "隐藏小爪" if self._pet_visible else "显示小爪"

    @Property(bool, notify=petStyleChanged)
    def petAlwaysOnTop(self) -> bool:
        return bool(self._store.settings.get("always_on_top", True))

    @Property(bool, notify=clockChanged)
    def petFaded(self) -> bool:
        """鼠标长时间不动时让小爪变淡，别挡着看东西。"""
        if not self._store.settings.get("fade_when_idle", True):
            return False
        return self._user_away

    def update_activity(self) -> None:
        away = win32.idle_seconds() >= 20.0
        if away != self._user_away:
            self._user_away = away
            self.clockChanged.emit()

    def refresh_dynamic(self) -> None:
        """每秒刷新一次「会随时间变化」的展示字段。"""
        self.clockChanged.emit()
        self.sitChanged.emit()
        self._refresh_upcoming()

    @Property(str, notify=settingsChanged)
    def dataPath(self) -> str:
        return str(self._store.path)

    @Property(str, notify=settingsChanged)
    def hotkeySummary(self) -> str:
        parts = [spec.upper() for spec in
                 (self.hotkey_dashboard, self.hotkey_focus, self.hotkey_task) if spec]
        return " / ".join(parts) if parts else "未设置"

    @Property(str, notify=settingsChanged)
    def migrationNote(self) -> str:
        source = self._store.migrated_from
        if source == "v1":
            return "已从旧版 tkinter 数据自动升级：待办、便签和休息间隔都保留了下来。"
        if source == "backup":
            detail = self._store.load_error or "主数据文件损坏"
            return (f"{detail}，已自动从备份恢复。"
                    "如果发现少了最近改的内容，可以打开数据文件夹检查 pet_data.json。")
        return ""

    # ------------------------------------------------------------ QML 动作
    @Slot(float, float)
    def savePetPosition(self, x: float, y: float) -> None:
        self._store.state["pet_x"] = int(x)
        self._store.state["pet_y"] = int(y)
        self.save()

    @Slot(result="QVariantList")
    def petPosition(self):
        return [self._store.state.get("pet_x"), self._store.state.get("pet_y")]

    @Slot(int, int, result="QVariantMap")
    def screenAt(self, x: int, y: int) -> dict:
        """返回包含 (x, y) 的那块屏幕的可用区域。

        QML 的 Screen 附加类型只有 virtualX/virtualY/desktopAvailable*，
        拿不到带任务栏偏移的 availableGeometry，所以这里用 QScreen 算准。
        """
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(QPoint(int(x), int(y)))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return {"x": 0, "y": 0, "width": 1280, "height": 720}
        rect = screen.availableGeometry()
        return {
            "x": rect.x(),
            "y": rect.y(),
            "width": rect.width(),
            "height": rect.height(),
        }

    @Slot(str)
    def showDashboard(self, page: str = "focus") -> None:
        self._dashboard_visible = True
        self.dashboardVisibilityChanged.emit()
        self.showDashboardRequested.emit(page or "focus")

    @Slot()
    def hideDashboard(self) -> None:
        self.dashboardVisible = False
        self.hideDashboardRequested.emit()

    @Slot()
    def togglePet(self) -> None:
        self.petVisible = not self._pet_visible

    @Slot()
    def quit(self) -> None:
        self.flush()
        self.quitRequested.emit()

    @Slot(str, str)
    def notify(self, title: str, body: str = "") -> None:
        self._notify("info", title, body, "reminder")

    @Slot(str, str)
    def info(self, title: str, body: str = "") -> None:
        self._notify("info", title, body)

    @Slot()
    def testNotification(self) -> None:
        self._notify("info", "这是一条测试提醒",
                     "看到这句话，说明气泡、声音和托盘通知都通了。", "reminder")

    @Slot()
    def resetSitTimer(self) -> None:
        self.reminder_engine.reset_sit_timer()
        self.sitChanged.emit()
        self.clockChanged.emit()

    @Slot(str)
    def copyText(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text or "")

    @Slot(result=str)
    def clipboardText(self) -> str:
        clipboard = QGuiApplication.clipboard()
        return clipboard.text() if clipboard is not None else ""

    @Slot()
    def newNote(self) -> None:
        self._notes.add("新便签", "")

    @Slot(str, str, str)
    def saveNote(self, note_id: str, title: str, text: str) -> None:
        self._notes.update(note_id, title, text)

    @Slot(str)
    def deleteNote(self, note_id: str) -> None:
        self._notes.remove(note_id)

    @Slot()
    def openDataFolder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT)))

    @Slot()
    def openReadme(self) -> None:
        target = ROOT / "README.md"
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def exportSummary(self) -> None:
        """把今日总结复制到剪贴板 —— 贴到日记或群里直接用。"""
        lines = [f"小爪助手 · {self.dateText}", "", self._focus.todayLine]
        pending = [t for t in self._store.tasks if not t.get("done")]
        done = [t for t in self._store.tasks if t.get("done")]
        if done:
            lines += ["", f"已完成（{len(done)}）："]
            lines += [f"  ✓ {t['text']}" for t in done[-10:]]
        if pending:
            lines += ["", f"待办（{len(pending)}）："]
            lines += [f"  · {t['text']}" for t in pending[:10]]
        if self._upcoming:
            lines += ["", "接下来："]
            lines += [f"  ⏰ {item}" for item in self._upcoming]
        self.copyText("\n".join(lines))
        self._notify("info", "今日总结已复制到剪贴板", "直接粘贴到任何地方就能用。", "task_done")

    @Slot(result=str)
    def agentStatus(self) -> str:
        parts = []
        try:
            import mss  # noqa: F401
            import numpy  # noqa: F401
            import cv2  # noqa: F401

            parts.append("视觉模块：已就绪（mss + OpenCV + NumPy）")
        except ImportError:
            parts.append("视觉模块：未安装")
        try:
            import pyautogui  # noqa: F401

            parts.append("自动化模块：已就绪（pyautogui）")
        except ImportError:
            parts.append("自动化模块：未安装")

        # UI Automation 是「读控件而不是猜坐标」的关键，单独报出来
        try:
            from .ai import uia

            parts.append("界面元素：" + uia.status())
        except Exception as exc:  # noqa: BLE001
            parts.append(f"界面元素：不可用（{exc}）")

        key = os.environ.get("OPENAI_API_KEY", "").strip()
        parts.append("AI 模型：" + ("已配置" if key else "未配置（在 .env 里填 OPENAI_API_KEY 后重启）"))
        return "\n".join(parts)

    @Slot(result=str)
    def runtimeInfo(self) -> str:
        import sys

        return (f"Python {sys.version.split()[0]} · 数据文件 {self._store.path.name} · "
                f"进程 {os.getpid()}")

    @Slot()
    def restart(self) -> None:
        """重启自己（改完全局热键之后需要重新注册）。"""
        self.flush()
        import sys

        try:
            subprocess.Popen([sys.executable, str(ROOT / "run_pawpet.py")],
                             cwd=str(ROOT), close_fds=True)
        except OSError:
            pass
        self.quit()
