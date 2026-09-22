"""Backend：暴露给 QML 的统一接口。

QML 只能看到这个对象。它是唯一允许改数据的地方，并且每次改动都会
落到 Store，所以「界面上看到的」和「磁盘上存的」永远一致。
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from . import win32
from .ai.controller import AiController
from .ai.markdown import to_plain
from .config import APP_NAME, APP_VERSION, ROOT, THEME_FILE
from .focus import FocusEngine
from .models import NoteModel, ReminderModel, SessionModel, TaskModel, WeekModel
from .reminders import ReminderEngine
from .services import Notifier, SoundPlayer
from .store import pretty_day, today_key


def _auto_ui_scale(backend) -> float:
    """按屏幕分辨率挑一个合适的缩放。

    为什么用分辨率而不是 DPI：实测这台机器上 Windows 把显示缩放设成了
    100%（logicalDotsPerInch 恒为 96、devicePixelRatio 恒为 1.0），
    所以 DPI 拿不到「该放大多少」的信息 —— 那是用户的系统设置，
    不是我们能改的。而**逻辑像素总数**是个可靠的信号：
    逻辑可用高度越大，说明屏幕越宽敞，字体就该相应放大，
    否则会显得又小又空。

    分档刻意保守：宁可比用户想要的略小，也不要一上来糊一屏。
    """
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is None:
            return 1.0
        available = screen.availableGeometry()
        logical_height = available.height() / max(1.0, screen.devicePixelRatio())
    except Exception:  # noqa: BLE001 - 拿不到就用默认，绝不能影响启动
        return 1.0

    if logical_height >= 2000:
        return 1.4       # 4K / 大屏
    if logical_height >= 1400:
        return 1.25      # 2K
    if logical_height >= 1000:
        # 1080p 这类「够宽但不高」的屏。阈值定 1000 而不是 1100 ——
        # 1080 屏扣掉任务栏正好 1032 左右，定 1100 会漏掉它们。
        return 1.15
    return 1.0           # 小屏（比如 768p）


def _clamp(value: float, low: float, high: float) -> float:
    """夹在 [low, high] 之间。

    注意 `high` 可能小于 `low`（屏幕比窗口还窄的时候，比如窗口缩放调到
    2.4 倍放在小屏上），所以不能写成 `max(low, min(high, value))` ——
    那种写法在 high < low 时会返回 low，反而把窗口推出屏幕。这里显式
    处理：范围无效时返回 low。
    """
    if high < low:
        return low
    return max(low, min(high, value))


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
    # 用户改了界面配色。QML 的 Theme 单例订阅它，改完立刻生效、不用重启。
    themeChanged = Signal()
    # 上手指引该弹出来了。由启动流程发出，QML 的引导窗口订阅它。
    onboardingRequested = Signal()
    # 数据导入导出有了结果（成功/失败都要告诉用户，不能静默）。
    dataTransferFinished = Signal(bool, str)
    # 自动更新检查完（无论有没有新版）。QML 订阅它刷新「关于」那一块。
    updateChecked = Signal()
    # 贴边位置变了：贴上了 / 解除了 / 滑出滑回。QML 收到就移动到新位置。
    #
    # 用信号 + 只读属性而不是双向绑定：窗口的 x/y 会被用户拖动直接改写，
    # 双向绑定会被拖动打破。这里让 Python 算、QML 听，方向单一。
    petGeometryChanged = Signal()

    # --------------------------------------------------------------- 构造
    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._sound = SoundPlayer()
        self._notifier = Notifier(self)
        self._sound.enabled = bool(store.settings.get("sound_enabled", True))

        # 用户定制的界面配色。读不到就是空字典 = 全用默认值。
        from . import theme as theme_mod

        self._theme_overrides: dict = theme_mod.load(THEME_FILE)

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

        # 更新检查的在途结果。都是内存态的 —— 关掉程序就重来，
        # 没必要落盘（落盘反而会给用户「它在惦记什么」的感觉）。
        self._latest: dict = {}
        self._update_checking = False
        # 工作线程写好、主线程取走。见 _start_update_check 的说明。
        self._update_pending: dict | None = None

        self._focus.completed.connect(self._on_focus_completed)
        self._focus.tick.connect(self._on_focus_tick)
        self.reminder_engine.fired.connect(self._on_reminder_fired)
        self._reminders.changed.connect(self._refresh_upcoming)
        self._tasks.countsChanged.connect(self._on_focus_tick)
        self._ai.toastRequested.connect(self._on_ai_toast)
        self._ai.memoryChanged.connect(self.memoryChanged)

        # ---------------------------------------------------------- 贴边
        # 贴边时窗口有一半在屏幕外，所以「鼠标移过去要滑出来」这件事
        # **没法用 QML 的 HoverHandler** —— 鼠标在屏幕边缘时可能根本不在
        # 窗口范围内（窗口一半在外面），而且那个位置的鼠标事件属于别的
        # 程序。只能用全局鼠标位置轮询。
        #
        # 150ms 一次 QCursor.pos() 很轻（底层就是 GetCursorPos），而且
        # **只在贴边状态下才跑** —— 没贴边时定时器是停的，不耗电。
        self._pet_peek = False
        self._pet_peek_until = 0.0
        self._pet_hover_timer = QTimer(self)
        self._pet_hover_timer.setInterval(150)
        self._pet_hover_timer.timeout.connect(self._pet_check_peek)

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

    @staticmethod
    def _bubble_text(text: str, limit: int = 260) -> str:
        """把要显示在气泡/系统通知里的文字压成纯文本单段。

        气泡和通知都是「一句话交代」，不是文档渲染区：
        * Markdown 记号（**、#、`、- ）必须去干净 —— 用户看到这些只会
          觉得界面没做完；
        * 换行也去掉，气泡高度固定，多行会把宠物顶歪。
        两行以上时用「；」接起来，读着还是连贯的。"""
        plain = to_plain(text or "")
        if not plain:
            return ""
        lines = [ln.strip() for ln in plain.split("\n") if ln.strip()]
        joined = "；".join(lines) if len(lines) > 1 else (lines[0] if lines else "")
        joined = " ".join(joined.split())
        if len(joined) > limit:
            joined = joined[: limit - 1].rstrip() + "…"
        return joined

    def _notify(self, kind: str, title: str, body: str = "", sound: str | None = None) -> None:
        if sound and self._store.settings.get("sound_enabled", True):
            self._sound.play(sound)
        # 标题里的 emoji 留着（它承担情绪），正文一律转纯文本单段
        body = self._bubble_text(body)
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

    # ------------------------------------------------------------ 界面配色
    # 「通过对话定制主题」的落地。颜色存在数据目录的 theme.json 里，
    # Theme.qml 从这里读 —— 不碰任何源码，改完立刻生效（走属性通知），
    # 打包后也照样能用（资源目录是只读的，数据目录才写得进去）。
    @Property("QVariantMap", notify=themeChanged)
    def themeColors(self) -> dict:
        """当前生效的**完整**配色表（默认值 + 用户覆盖）。

        QML 侧按 key 取用：Theme.qml 里每个颜色都变成
        `backend.themeColors.bg || "#fdf7f9"` 这种带兜底的读法 ——
        兜底是必需的，因为 Backend 还没建好的那一瞬间 QML 可能已经在求值。
        """
        from . import theme as theme_mod

        try:
            return theme_mod.resolved(self._theme_overrides)
        except Exception:  # noqa: BLE001 - 配色坏了不该让界面起不来
            return theme_mod.default_overrides()

    @Property("QVariantList", constant=True)
    def themeRoles(self) -> list:
        """可以改的配色项（给界面/模型看的人话清单）。"""
        from . import theme as theme_mod

        return theme_mod.editable_roles()

    @Property("QVariantList", constant=True)
    def repeatOptions(self) -> list:
        """提醒的重复方式，给界面上的下拉用。

        **从 models 里读，不在这里另写一份** —— 这个项目踩过「同一个说法
        存两份」的坑（`_describe` 里硬编码过一份重复方式映射，加了新方式
        之后列表和气泡显示不一致）。

        每项是 {key, label, needsTime, needsInterval}：
          needsTime     —— 要不要填「几点」（间隔重复不需要）
          needsInterval —— 要不要填「每 N 分钟」
        """
        from .models import REPEAT_LABEL

        order = ["once", "daily", "weekdays", "weekly", "interval"]
        out = []
        for key in order:
            if key not in REPEAT_LABEL:
                continue
            out.append({
                "key": key,
                "label": REPEAT_LABEL[key],
                "needsTime": key != "interval",
                "needsInterval": key == "interval",
            })
        return out

    @Property("QVariantList", constant=True)
    def intervalPresets(self) -> list:
        """常用间隔（分钟），界面上做成快捷按钮。"""
        from .models import INTERVAL_PRESETS

        return list(INTERVAL_PRESETS)

    @Property("QVariantMap", constant=True)
    def intervalLimits(self) -> dict:
        """间隔的上下限，界面上用来卡输入范围。"""
        from .models import (
            INTERVAL_DEFAULT_MINUTES,
            INTERVAL_MAX_MINUTES,
            INTERVAL_MIN_MINUTES,
        )

        return {
            "min": INTERVAL_MIN_MINUTES,
            "max": INTERVAL_MAX_MINUTES,
            "default": INTERVAL_DEFAULT_MINUTES,
        }

    @Property(str, notify=themeChanged)
    def themeSummary(self) -> str:
        from . import theme as theme_mod

        return theme_mod.describe(self._theme_overrides)

    @Slot("QVariantMap", result="QVariantList")
    def applyTheme(self, overrides) -> list:
        """改配色。返回被拒绝的原因列表（空列表 = 全部成功）。

        合并而不是替换：用户说「把主色调改成蓝的」，只动 accent 一项，
        之前改过的别的项要留着。
        """
        from . import theme as theme_mod

        clean, rejected = theme_mod.sanitize(overrides)
        if clean:
            merged = dict(self._theme_overrides)
            merged.update(clean)
            ok, message = theme_mod.save(THEME_FILE, merged)
            if not ok:
                return [message]
            self._theme_overrides = merged
            self.themeChanged.emit()
        return rejected

    @Slot()
    def resetTheme(self) -> None:
        from . import theme as theme_mod

        theme_mod.reset(THEME_FILE)
        self._theme_overrides = {}
        self.themeChanged.emit()

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
    # 高级模式。关着（默认）时，设置面板里那些只有作者会用的东西不显示：
    # AI 页的知识库、外部工具（MCP）配置。
    #
    # 判断依据是「泛用户第一次打开会不会看不懂」——
    # 知识库要自己准备 md 文件再导入，MCP 更是要用户先知道 MCP 是什么。
    # 这两块连作者自己都关着，摆在默认界面里只是噪音。
    #
    # **注意不要顺手把「执行步数」也藏进来**：`tools/steptest.py` 靠
    # `stepBox` 这个 objectName 找控件，藏了测试会红 —— 而且步数是有
    # 实际用处的（一轮干到一半停了，用户得知道去哪儿调）。
    advanced_mode = _setting_property("advanced_mode", bool, settingsChanged)
    move_step = _setting_property("move_step", int, settingsChanged)
    hotkey_dashboard = _setting_property("hotkey_dashboard", str, settingsChanged)
    hotkey_focus = _setting_property("hotkey_focus", str, settingsChanged)
    hotkey_task = _setting_property("hotkey_task", str, settingsChanged)
    # 「点一下就跑」那张卡片被收起过没有。
    #
    # 默认展开（用户第一次用最需要它 —— 门槛不是模型能力，是他不知道
    # 能说什么）。但一旦他点了右上角的 ✕，就记住这个选择：
    # 那张卡片有 300px 高，每次开面板都挡在那里确实很占视野。
    # 收起之后输入行会出现「✨ 现成任务」按钮，随时能调回来。
    aiTemplatesHidden = _setting_property("ai_templates_hidden", bool, settingsChanged)
    # 上手指引看过了没有。详见 pawpet/qml/PawPet/Onboarding.qml 顶部的说明。
    onboardingDone = _setting_property("onboarding_done", bool, settingsChanged)
    # 检查更新。关掉之后一个网络请求都不会发 —— 设置页的文案里写明了这点。
    updateCheck = _setting_property("update_check", bool, settingsChanged)
    # ---- 贴边 ----
    # 拖到屏幕边缘附近吸附过去，并有一半藏在屏幕外；鼠标移过去滑出来。
    petSnapEnabled = _setting_property("pet_snap_enabled", bool, settingsChanged)
    petSnapDistance = _setting_property("pet_snap_distance", int, settingsChanged)
    # 贴边时换姿势（侧躺 / 倒挂）而不是直挺挺藏一半
    petEdgePose = _setting_property("pet_edge_pose", bool, settingsChanged)

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
        elif key == "pet_snap_enabled":
            # 关掉贴边时，宠物正半藏在屏幕外 —— 得先把它「完全显示」的
            # 位置记下来，再解除贴边，界面那边才知道该把窗口挪到哪。
            # 不记的话它就一直半藏在屏幕外，用户以为宠物不见了。
            if not bool(self._store.settings.get("pet_snap_enabled", True)):
                if self._pet_edge:
                    x, y = self._pet_geometry_for(self._pet_edge,
                                                  int(self._store.state.get("pet_x") or 0),
                                                  int(self._store.state.get("pet_y") or 0),
                                                  peek=True)
                    self._store.state["pet_x"] = int(x)
                    self._store.state["pet_y"] = int(y)
                self._pet_edge = ""       # setter 会发信号
                self.petGeometryChanged.emit()
        elif key in ("pet_snap_distance", "pet_snap_hide_ratio", "pet_scale"):
            # 改了吸附距离或隐藏比例，当前贴着的位置要重算一遍
            if self._pet_edge:
                self.petGeometryChanged.emit()
        elif key == "pet_edge_pose":
            # 开/关贴边姿势：角度变了，得让界面重新取一次
            if self._pet_edge:
                self.petGeometryChanged.emit()

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

    # ------------------------------------------------------- 上手指引
    @Slot()
    def showOnboarding(self) -> None:
        """把上手指引调出来。

        两个调用方：启动流程（只在没看过时调）和设置页里的「再看一次」。
        由信号驱动而不是直接控制窗口 —— Python 侧不持有 QML 窗口引用，
        和气泡、工作台保持同一种接线方式。
        """
        self.onboardingRequested.emit()

    @Slot(bool)
    def finishOnboarding(self, done: bool = True) -> None:
        """引导结束。

        `done` 目前只用于区分「走完三屏」和「中途跳过」—— 两种都要置位，
        否则用户每次启动都会被再弹一次。参数留着是为了以后想在
        「走完」时做点别的（比如自动打开工作台）不必改接口。
        """
        self._store.settings["onboarding_done"] = True
        self.settingsChanged.emit()
        self.flush()

    @Slot()
    def resetOnboarding(self) -> None:
        """设置页里的「再看一次」。

        **不动 onboarding_done。** 早先的写法是把它置回 False 再显示，
        但那样一旦用户看完直接杀进程（没走关闭流程），下次启动会又弹
        一遍 —— 「我明明看过了」比「我看不到」更烦人。
        这里只负责显示，置位的事交给 finishOnboarding。
        """
        self.onboardingRequested.emit()

    # --------------------------------------------------------- 检查更新
    """整个流程刻意分成「后台线程查」+「主线程发信号」两半。

    为什么不直接在后台线程里 emit 信号：PySide6 里信号跨线程 emit 时，
    Qt 判定连接类型靠的是 QObject 的线程亲和性，而纯 Python 线程没有
    注册到 Qt 线程体系 —— 有可能被判成 DirectConnection，于是在工作
    线程里直接去碰 QML 对象。那种崩溃是偶发的，测试很难复现。

    所以工作线程只往 self._update_pending 放结果（Python 层面赋值是
    原子的），由主线程的下一次 refresh_dynamic() 取走并发信号。
    refresh_dynamic 每秒被调一次，用户感知不到这一秒的延迟。"""

    @Property(bool, notify=updateChecked)
    def updateChecking(self) -> bool:
        return self._update_checking

    @Property(bool, notify=updateChecked)
    def updateAvailable(self) -> bool:
        if not self._latest:
            return False
        # 用户点过「跳过这个版本」的就不再提示。反复推同一个版本，
        # 最后的结果是他去设置里把整个检查功能关掉 —— 那更糟。
        skipped = str(self._store.settings.get("update_skipped_version") or "")
        return str(self._latest.get("version") or "") != skipped

    @Property(str, notify=updateChecked)
    def latestVersion(self) -> str:
        return str(self._latest.get("version") or "")

    @Property(str, notify=updateChecked)
    def updateNote(self) -> str:
        return str(self._latest.get("note") or "")

    @Property(str, notify=settingsChanged)
    def updateSkipped(self) -> str:
        """被用户跳过提示的那个版本号。空串 = 没有跳过任何版本。

        notify 挂 settingsChanged 而不是 updateChecked：它的来源是设置项，
        不是检查结果。
        """
        return str(self._store.settings.get("update_skipped_version") or "")

    @Property(str, notify=updateChecked)
    def updateStatus(self) -> str:
        """给设置页「关于」那一行用的一句话状态。"""
        if self._update_checking:
            return "正在检查…"
        if not self._latest:
            last = float(self._store.settings.get("update_last_check") or 0.0)
            if last <= 0:
                return ""       # 还没查过，不显示任何东西
            return "已是最新版本"
        version = str(self._latest.get("version") or "")
        if not self.updateAvailable:
            return f"已忽略 {version}（可在下方重新开启提示）"
        return f"有新版本 {version} 可以下载"

    @Slot()
    def checkUpdateIfDue(self) -> None:
        """启动时调。遵守「一天最多查一次」的节流，尊重用户的开关。"""
        from . import update as update_mod

        if not bool(self._store.settings.get("update_check", True)):
            return
        last = float(self._store.settings.get("update_last_check") or 0.0)
        if time.time() - last < update_mod.CHECK_INTERVAL:
            return
        self._start_update_check(manual=False)

    @Slot()
    def checkUpdateNow(self) -> None:
        """设置页里手动点「检查更新」。不看节流，也不看开关 ——
        用户主动点的动作，就算他关着自动检查也该执行。"""
        self._start_update_check(manual=True)

    def _start_update_check(self, manual: bool) -> None:
        import threading

        if self._update_checking:
            return
        self._update_checking = True
        self.updateChecked.emit()

        def worker() -> None:
            from . import update as update_mod

            try:
                result = update_mod.check(APP_VERSION)
            except Exception:  # noqa: BLE001 - 检查更新绝不能把程序搞崩
                result = {"ok": False, "found": None}
            # 只赋值，不发信号 —— 见上面那段说明
            self._update_pending = {"result": result, "manual": manual}

        threading.Thread(target=worker, name="pawpet-update", daemon=True).start()

    def _drain_update(self) -> None:
        """主线程侧：把工作线程查到的结果搬出来。见 _start_update_check 的说明。"""
        pending = self._update_pending
        if pending is None:
            return
        self._update_pending = None
        self._update_checking = False

        result = pending.get("result") or {}
        found = result.get("found")
        self._latest = found if isinstance(found, dict) else {}

        self._store.settings["update_last_check"] = time.time()
        self.updateChecked.emit()

        # 手动检查必须有回音 —— 点了按钮什么都不发生，用户会以为坏了。
        # 自动检查则一声不吭：没新版是常态，没必要每次都告诉他。
        if pending.get("manual"):
            if not result.get("ok"):
                self.info("检查更新失败", "没连上。可能是网络或代理的问题，稍后再试。")
            elif self._latest:
                self.info("有新版本",
                          f"{self._latest.get('version')} 可以下载了。设置 → 关于里有入口。")
            else:
                self.info("已是最新版本", f"当前 {APP_VERSION}，没有更新的版本。")

    @Slot()
    def openUpdatePage(self) -> None:
        from . import update as update_mod

        url = str(self._latest.get("url") or "") or update_mod.DOWNLOAD_PAGE
        QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def skipThisVersion(self) -> None:
        """「跳过这个版本」：记下版本号，之后不再为它提示。"""
        version = str(self._latest.get("version") or "")
        if not version:
            return
        self._store.settings["update_skipped_version"] = version
        self.settingsChanged.emit()
        self.flush()
        self.updateChecked.emit()

    @Slot()
    def resumeUpdateNotice(self) -> None:
        """设置页里的「重新开启提示」—— 把跳过的版本清掉。"""
        self._store.settings["update_skipped_version"] = ""
        self.settingsChanged.emit()
        self.flush()
        self.updateChecked.emit()

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

    @Property(int, notify=memoryChanged)
    def aiAutoMemoryCount(self) -> int:
        """其中有多少条是小爪自己学来的。"""
        return self.ai.autoMemoryCount

    @Property(str, notify=memoryChanged)
    def aiAutoLearnStatus(self) -> str:
        """最近一次自动学习的收获。"""
        return self.ai.autoLearnStatus

    # ------------------------------------------------------------ 知识库
    @Property(int, notify=settingsChanged)
    def aiKnowledgeCount(self) -> int:
        return self.ai.knowledgeCount

    @Property(int, notify=settingsChanged)
    def aiKnowledgeChunks(self) -> int:
        return self.ai.knowledgeChunks

    @Property(str, notify=settingsChanged)
    def aiKnowledgeSummary(self) -> str:
        return self.ai.knowledgeSummary

    @Slot(str, bool)
    def aiImportKnowledge(self, path: str, recursive: bool = False) -> None:
        self.ai.importKnowledge(path, recursive)

    @Slot()
    def aiClearKnowledge(self) -> None:
        self.ai.clearKnowledge()

    @Slot(str)
    def aiForgetKnowledge(self, name: str) -> None:
        self.ai.forgetKnowledge(name)

    @Slot(str, result=str)
    def aiSearchKnowledge(self, query: str) -> str:
        """界面上的试查框。让用户自己验证检索效果。"""
        return self.ai.searchKnowledge(query)

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

    # ------------------------------------------------------------ 界面缩放
    @Property(str, constant=True)
    def fontFamily(self) -> str:
        """主字体族（含中文回退链）。

        **字体名的唯一来源在 pawpet/qmlfont.py**，Theme.qml 通过这里读。
        以前两边各写一份，改一边就会漂移 —— 而且漂移了不会报错，
        只是界面上悄悄用回旧字体。
        """
        from .qmlfont import FONT_FAMILY

        return FONT_FAMILY

    @Property(str, constant=True)
    def fontFamilyMono(self) -> str:
        from .qmlfont import FONT_MONO

        return FONT_MONO

    @Property(str, constant=True)
    def fontFamilyLatin(self) -> str:
        from .qmlfont import FONT_LATIN

        return FONT_LATIN

    @Property(float, notify=settingsChanged)
    def uiScale(self) -> float:
        """界面整体缩放系数。

        为什么需要它：字号原来写死成 10/11/13，是按小窗口调的。
        在 1920x1080 的笔记本屏（物理约 157 DPI）上，界面物理尺寸明显偏小，
        右边还会空出一大片。所以让用户能调，默认按屏幕分辨率自动选。

        `ui_scale` 设为 0（或负数）就是「自动」。
        """
        try:
            configured = float(self._store.settings.get("ui_scale", 0.0) or 0.0)
        except (TypeError, ValueError):
            configured = 0.0

        if configured > 0:
            # 手动值夹在合理范围内，免得用户（或手改的 json）把界面搞到没法用
            return max(0.8, min(2.0, configured))

        return _auto_ui_scale(self)

    @Property(bool, notify=settingsChanged)
    def uiScaleIsAuto(self) -> bool:
        try:
            return float(self._store.settings.get("ui_scale", 0.0) or 0.0) <= 0
        except (TypeError, ValueError):
            return True

    @Property(str, notify=settingsChanged)
    def advancedModeHint(self) -> str:
        """高级模式开关下面那行说明。

        做成随开关变的动态文案，而不是写死一句「显示高级功能」——
        用户拨完开关得立刻知道**到底多了/少了什么**，不然这个开关
        对他来说就是个不知道后果的按钮。
        """
        if self.advanced_mode:
            return ("已打开 —— AI 页会多出「知识库」和「外部工具（MCP）」两块配置。"
                    "两块都在「模型设置」展开之后才能看到。")
        return ("关着的时候，AI 页不显示「知识库」和「外部工具（MCP）」的配置 ——"
                "它们要先自己准备资料、或者先知道 MCP 是什么才用得上。"
                "包里自带的那些只读小工具不受影响，照常能用。")

    @Property(str, notify=settingsChanged)
    def uiScaleHint(self) -> str:
        scale = self.uiScale
        percent = int(round(scale * 100))
        if self.uiScaleIsAuto:
            return f"自动（{percent}%）—— 按屏幕分辨率选的，觉得小就手动调大"
        return f"手动 {percent}%"

    @Slot(bool)
    def resetUiScale(self, auto: bool = True) -> None:
        """切回自动。"""
        self._store.settings["ui_scale"] = 0.0 if auto else self.uiScale
        self.settingsChanged.emit()
        self.flush()

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
        # 后台线程查到的更新结果在这里搬到主线程发信号。
        # 放这个函数里是因为它已经被每秒调一次 —— 不额外起定时器。
        self._drain_update()

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
        # **贴边时存「完全显示」的位置，不存半藏的位置。**
        #
        # 半藏时窗口坐标是负数（贴左边 x=-100）或者超出屏幕（贴右边）。
        # 存下来有两个后果：用户在设置里关掉贴边之后，宠物会照着这个坐标
        # 跑到屏幕外、找不回来；换分辨率或换显示器之后更对不上。
        # 存成完全显示的位置就没这些问题 —— 贴边状态另有 pet_edge 记着，
        # 启动时会按它重算。
        if self._pet_edge:
            x, y = self._pet_geometry_for(self._pet_edge, int(x), int(y),
                                          peek=True)
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

    # ============================================================== 贴边
    #
    # 拖到屏幕边缘附近就吸附过去，并且有一半藏在屏幕外（省地方，用户
    # 抱怨过宠物「很占视野」）；鼠标移到那条边附近自动滑出来，移开一会儿
    # 再滑回去。四条边都支持。
    #
    # 为什么位置计算放在 Python 而不是 QML：
    #   * 「包含任务栏偏移的可用区域」要用 QScreen 才算得准，QML 的 Screen
    #     附加类型只有 virtualX/desktopAvailable*；
    #   * 「鼠标是不是移到边上了」必须用全局鼠标位置 —— 贴边时窗口有一半
    #     在屏幕外，QML 收不到那个区域的鼠标事件。
    #   Python 算、QML 听，两边不会各算一套。
    _EDGES = ("left", "right", "top", "bottom")

    @property
    def _pet_edge(self) -> str:
        edge = str(self._store.state.get("pet_edge") or "")
        return edge if edge in self._EDGES else ""

    @_pet_edge.setter
    def _pet_edge(self, value: str) -> None:
        value = value if value in self._EDGES else ""
        previous = str(self._store.state.get("pet_edge") or "")
        if previous == value:
            return
        self._store.state["pet_edge"] = value
        # 贴边状态一变就重新开始（或停掉）鼠标轮询
        if value:
            self._pet_peek_until = 0.0
            if not self._pet_hover_timer.isActive():
                self._pet_hover_timer.start()
        else:
            self._pet_hover_timer.stop()
            self._pet_peek = False
            # **解除贴边必须通知界面。**
            #
            # 不通知的话 QML 收不到任何动静，姿势就不会转回来 ——
            # 用户把宠物从边上拖走之后，它会一直歪着待在屏幕中间
            # （实测：解除贴边发了 0 次信号）。这不只是难看，
            # 「歪着的宠物」还会让人以为它卡住了。
            #
            # 只在「从贴着变成不贴」时发；吸附那一步由 petSnap 统一发，
            # 免得同一个动作触发两次动画。
            if previous:
                self.petGeometryChanged.emit()

    @property
    def _pet_hide_ratio(self) -> float:
        """当前该藏多少。

        **按边取值**，不是一个全局数字 —— 脸在画布中心附近，旋转不会
        把它挪到边上，所以每条边能藏多少取决于转完之后脸的包围盒落在哪。
        统一用 0.5 的话四条边都只剩后脑勺（实测过）。

        设置里的 `pet_snap_hide_ratio` 只作为兜底：边不在表里时用它。
        """
        try:
            fallback = float(self._store.settings.get("pet_snap_hide_ratio", 0.5))
        except (TypeError, ValueError):
            fallback = 0.5
        fallback = max(0.0, min(0.85, fallback))

        _name, _angle, ratio = self._pet_pose(self._pet_edge)
        if not self._pet_edge:
            return fallback
        # 表里的值也夹一下，防止以后手改配置时写出离谱的数
        return max(0.0, min(0.85, ratio))

    @property
    def _pet_snap_distance(self) -> int:
        try:
            return max(4, min(200, int(
                self._store.settings.get("pet_snap_distance", 40))))
        except (TypeError, ValueError):
            return 40

    def _pet_size(self) -> tuple[int, int]:
        """宠物窗口当前的尺寸。

        从 store 里的缩放算，而不是问 QML 要 —— 恢复位置发生在 QML
        窗口刚建好、尺寸可能还没定下来的时候，那时候问它拿不到准数。
        """
        from .config import PET_DESIGN_HEIGHT, PET_DESIGN_WIDTH

        try:
            scale = float(self._store.settings.get("pet_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(0.6, min(2.4, scale))
        return (int(round(PET_DESIGN_WIDTH * scale)),
                int(round(PET_DESIGN_HEIGHT * scale)))

    def _pet_geometry_for(self, edge: str, anchor_x: int, anchor_y: int,
                          peek: bool) -> tuple[int, int]:
        """算出贴在某条边上时，窗口该在哪。

        `anchor_x` / `anchor_y` 是**垂直于贴边方向**的那个坐标 ——
        贴左右边时用 y，贴上下边时用 x。它在吸附后保持用户拖到的位置，
        只做屏幕范围内的夹取，这样宠物不会因为贴边而突然横移一截。
        """
        width, height = self._pet_size()
        area = self.screenAt(anchor_x, anchor_y)
        ratio = self._pet_hide_ratio

        if edge == "left":
            x = area["x"] if peek else area["x"] - int(width * ratio)
            y = _clamp(anchor_y, area["y"], area["y"] + area["height"] - height)
        elif edge == "right":
            right = area["x"] + area["width"]
            x = right - width if peek else right - width + int(width * ratio)
            y = _clamp(anchor_y, area["y"], area["y"] + area["height"] - height)
        elif edge == "top":
            y = area["y"] if peek else area["y"] - int(height * ratio)
            x = _clamp(anchor_x, area["x"], area["x"] + area["width"] - width)
        elif edge == "bottom":
            bottom = area["y"] + area["height"]
            y = bottom - height if peek else bottom - height + int(height * ratio)
            x = _clamp(anchor_x, area["x"], area["x"] + area["width"] - width)
        else:
            return (anchor_x, anchor_y)
        return (int(x), int(y))

    @Slot(int, int, result="QVariantMap")
    def petSnap(self, x: int, y: int) -> dict:
        """用户拖完松手时调这个：判断要不要吸附。

        返回 `{"edge": 边, "x": ..., "y": ...}`，没吸附时 edge 是空串、
        坐标原样返回。QML 收到之后负责把窗口移过去。
        """
        if not bool(self._store.settings.get("pet_snap_enabled", True)):
            self._pet_edge = ""
            return {"edge": "", "x": int(x), "y": int(y)}

        width, height = self._pet_size()
        area = self.screenAt(x + width // 2, y + height // 2)
        limit = self._pet_snap_distance

        # 四条边各算一下「离得多远」，取最近的。
        #
        # **距离要夹在 0 以上，不能用 abs()。** 用 abs 的话，用户把宠物
        # 拖**过头**（窗口有一部分跑到屏幕外）之后距离反而变大：
        # 超出 60px 就算成「离边缘 60px」，超出 120px 就算 120px ——
        # 于是越往边上拖越不吸附。而用户往边上拖的时候**必然拖过头**
        # （窗口滑出屏幕才感觉「到头了」），所以这条把「不灵敏」坐实了。
        #
        # 夹到 0 之后：「已经超出去了」是**最强烈**的贴边意图，
        # 距离记 0，必定吸上。
        gaps = {
            "left": max(0, x - area["x"]),
            "right": max(0, (area["x"] + area["width"]) - (x + width)),
            "top": max(0, y - area["y"]),
            "bottom": max(0, (area["y"] + area["height"]) - (y + height)),
        }
        edge = min(gaps, key=lambda key: gaps[key])
        if gaps[edge] > limit:
            # 没够着任何一条边 —— 顺便把之前的贴边状态清掉
            self._pet_edge = ""
            return {"edge": "", "x": int(x), "y": int(y)}

        self._pet_edge = edge
        # 吸上去的一瞬间是「完全显示」的：用户刚把它拖到那儿，直接藏一半
        # 会让人以为宠物不见了。等鼠标移开之后才收回去。
        self._pet_peek_until = time.time() + 2.5
        self._pet_peek = True
        nx, ny = self._pet_geometry_for(edge, x, y, peek=True)

        # **顺手把落点记进 state。** _pet_should_peek 要拿它算触发区，
        # 而 QML 那边保存位置有 700ms 的节流 —— 中间这段时间里鼠标要是
        # 移到边上，判定会用到旧坐标，触发区就偏了（表现为「鼠标移过去
        # 宠物不滑出来」）。吸附时已经知道确切落点，直接记下来最准。
        self._store.state["pet_x"] = int(nx)
        self._store.state["pet_y"] = int(ny)

        self.petGeometryChanged.emit()
        return {"edge": edge, "x": nx, "y": ny}

    @Slot()
    def petDetach(self) -> None:
        """解除贴边（用户把宠物拖离边缘、或者在设置里关了贴边）。"""
        if not self._pet_edge:
            return
        self._pet_edge = ""

    @Slot(result="QVariantMap")
    def petEdgeGeometry(self) -> dict:
        """当前贴边状态下窗口该在哪。QML 启动恢复位置时用。"""
        edge = self._pet_edge
        if not edge:
            return {"edge": "", "x": 0, "y": 0, "active": False}
        state = self._store.state
        try:
            saved_x = int(state.get("pet_x") or 0)
            saved_y = int(state.get("pet_y") or 0)
        except (TypeError, ValueError):
            saved_x = saved_y = 0
        nx, ny = self._pet_geometry_for(edge, saved_x, saved_y,
                                        peek=self._pet_peek)
        return {"edge": edge, "x": nx, "y": ny, "active": True}

    @Property(int, notify=petGeometryChanged)
    def petWindowX(self) -> int:
        return int(self.petEdgeGeometry().get("x", 0))

    @Property(int, notify=petGeometryChanged)
    def petWindowY(self) -> int:
        return int(self.petEdgeGeometry().get("y", 0))

    @Property(str, notify=petGeometryChanged)
    def petEdge(self) -> str:
        return self._pet_edge

    @Property(bool, notify=petGeometryChanged)
    def petPeek(self) -> bool:
        """贴边状态下宠物是不是滑出来了。"""
        return bool(self._pet_peek)

    # ---------------------------------------------------------- 贴边姿态
    #
    # 贴到哪条边就用哪个姿势。角度是**绕画布中心转**，所以「藏起来」这件事
    # 读起来就变了性质：直挺挺露出一半像被切掉，转过角度之后同一个「只露
    # 一部分」看起来是它自己趴在那儿 / 挂在那儿。
    #
    # ---- 四个角度全部是**渲染核对出来的** ----
    # 这块我错过两次，两次都是「推出来觉得对、渲染出来是反的」，所以下面
    # 把判断依据写清楚，别再靠推理：
    #
    # Qt 的 rotation 正值是**顺时针**（屏幕坐标 y 向下）。
    #
    # 而脸在画布中心**偏下**（眼睛 y≈122、头顶 y≈44，画布中心 y=110），
    # 所以旋转之后「头顶」和「嘴」是朝**相反**方向跑的 —— 只看「脸还在
    # 不在可见区里」判断不出朝向，必须看耳朵朝哪边：
    #
    #   left  +90°（顺时针）：头顶转到右边 → 耳朵在**上**、身体朝外 ✔
    #   left  -90°（逆时针）：头顶转到左边 → 耳朵在**下**，是倒的 ✘
    #   right -90°（逆时针）：头顶转到左边 → 耳朵在**上**、身体朝外 ✔
    #   right +90°（顺时针）：头顶转到右边 → 耳朵在**下**，是倒的 ✘
    #
    # 一句话记法：**头顶要朝屏幕内、脚朝屏幕外**，也就是「头朝屋里躺」。
    # 左右两侧的角度必然相反（一个顺时针一个逆时针）。
    #
    # ---- 藏多少也是渲染校准的，按「脸必须露出来」 ----
    # 旋转不改变脸的位置（它就在中心附近），所以统一藏 50% 会让四条边
    # 全都只露后脑勺。每条边能藏多少，取决于转完之后脸的包围盒落在哪。
    _PET_POSES: dict[str, tuple[str, int, float]] = {
        "left": ("side-left", 90, 0.42),
        "right": ("side-right", -90, 0.42),
        "top": ("hang", 180, 0.26),
        "bottom": ("sit", 0, 0.28),
    }

    # 不开「贴边换姿势」时用的藏匿比例。角度是 0，脸的包围盒没变，
    # 所以上下两条边和开着姿势时需要的比例不同：
    #   不转时脸在 y 110~157，贴上边（藏上、露下）能藏到 0.5，
    #   贴下边（藏下、露上）就只能藏到 0.28。
    _PET_FLAT_HIDE: dict[str, float] = {
        "left": 0.42,
        "right": 0.42,
        "top": 0.50,
        "bottom": 0.28,
    }

    def _pet_pose(self, edge: str) -> tuple[str, int, float]:
        """某条边的 (姿势名, 角度, 藏匿比例)。"""
        if not edge:
            return ("", 0, 0.0)
        if bool(self._store.settings.get("pet_edge_pose", True)):
            return self._PET_POSES.get(edge, ("", 0, 0.5))
        return ("", 0, self._PET_FLAT_HIDE.get(edge, 0.5))

    @Property(str, notify=petGeometryChanged)
    def petPose(self) -> str:
        """当前的贴边姿势。没贴边或没开姿势时是空串。

        返回的是「边 + 方向」而不是笼统的 "side"，因为左右两边虽然都是
        侧躺，角度是相反的 —— 测试里要能把它们区分开。
        """
        return self._pet_pose(self._pet_edge)[0]

    @Property(int, notify=petGeometryChanged)
    def petPoseAngle(self) -> int:
        """宠物要转多少度。"""
        return self._pet_pose(self._pet_edge)[1]

    @Property(bool, notify=petGeometryChanged)
    def petHanging(self) -> bool:
        """是不是倒挂着。倒挂时前端要给它加一点晃动，不然像贴纸。"""
        return self.petPose == "hang"

    def _pet_should_peek(self, pos_x: int, pos_y: int) -> bool:
        """鼠标在 (pos_x, pos_y) 时，贴边的宠物该不该滑出来。

        **抽成纯函数是为了能测。** 判定原本嵌在 _pet_check_peek 里，
        而那里面直接读 QCursor.pos() —— 测试没法把鼠标挪到屏幕边缘再
        断言，只能靠人手动试。抽出来之后传坐标就能验四条的边逻辑。

        触发区不是「整条屏幕边缘」，而是**宠物所在的那一段**：
        鼠标只是路过屏幕左边（比如去点任务栏）不该让宠物弹出来，
        那会很烦。所以除了靠边，还要求鼠标落在宠物所在的另一条轴上。
        """
        edge = self._pet_edge
        if not edge:
            return False

        width, height = self._pet_size()
        state = self._store.state
        try:
            anchor_x = int(state.get("pet_x") or 0)
            anchor_y = int(state.get("pet_y") or 0)
        except (TypeError, ValueError):
            anchor_x = anchor_y = 0

        # 用「完全显示」的位置来算触发区：滑出和滑回的判断基准一致，
        # 否则判定区会随着宠物滑进滑出而移动，产生自激（滑出→判定区变→
        # 又判定为不在→滑回→判定区变回→又滑出）。
        win_x, win_y = self._pet_geometry_for(edge, anchor_x, anchor_y,
                                              peek=True)
        area = self.screenAt(win_x + width // 2, win_y + height // 2)

        # 靠边多近算「移过去了」。比吸附距离窄一点：吸附是主动拖过去的，
        # 触发滑出是被动扫过，太宽会误触。
        reach = max(12, min(60, self._pet_snap_distance // 2))
        margin = 24          # 余量：宠物边上再多一点也算

        if edge in ("left", "right"):
            in_band = (win_y - margin) <= pos_y <= (win_y + height + margin)
            if edge == "left":
                return pos_x <= area["x"] + reach and in_band
            return (pos_x >= area["x"] + area["width"] - reach and in_band)

        # 贴上/下边时反过来：横向在宠物范围内，纵向靠边
        in_band = (win_x - margin) <= pos_x <= (win_x + width + margin)
        if edge == "top":
            return pos_y <= area["y"] + reach and in_band
        return pos_y >= area["y"] + area["height"] - reach and in_band

    def _pet_check_peek(self) -> None:
        """轮询全局鼠标，决定滑出还是滑回。"""
        edge = self._pet_edge
        if not edge:
            self._pet_hover_timer.stop()
            return

        from PySide6.QtGui import QCursor

        pos = QCursor.pos()
        near = self._pet_should_peek(pos.x(), pos.y())

        now = time.time()
        if near:
            # 鼠标在旁边——保持滑出，并不断把这个「保持到什么时候」往后推
            self._pet_peek_until = now + 0.6
            if not self._pet_peek:
                self._pet_peek = True
                self.petGeometryChanged.emit()
            return

        # 鼠标走开了。不立刻收回 —— 鼠标稍微动一下就让宠物来回弹很烦，
        # 所以多等一会儿（petSnap 里刚吸附的那 2.5 秒也是靠这个判断的）。
        if self._pet_peek and now >= self._pet_peek_until:
            self._pet_peek = False
            self.petGeometryChanged.emit()

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

    # --------------------------------------------------- 数据导出 / 导入
    """为什么走 Python 的原生对话框而不是纯 QML：

    QML 的文件对话框要 QtQuick.Dialogs 模块，它不在 Essentials 里 ——
    打包时大概率会漏，装到用户机器上就是「按钮点了没反应」。
    QFileDialog 是 QtWidgets 静态方法，app.py 本来就导入 QtWidgets
    （QApplication / QMenu / QSystemTrayIcon 都来自它），零新增依赖。

    注意两个 Slot 都是**阻塞**的：QFileDialog 的静态方法会开自己的事件
    循环，用户不点完不返回。这里不会卡死界面 —— 它跑在主线程但 Qt 会把
    事件继续分发下去，是标准做法。"""

    @Slot()
    def exportData(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from . import datatransfer as transfer
        from .store import SCHEMA_VERSION

        dialog = self._file_dialog(
            QFileDialog.AcceptSave, "导出小爪数据",
            str(ROOT / transfer.default_export_name()), "压缩包 (*.zip)",
        )
        dialog.setDefaultSuffix("zip")
        if dialog.exec() != QFileDialog.Accepted:
            return      # 用户取消了，什么都不做也不提示
        files = dialog.selectedFiles()
        if not files:
            return

        ok, message = transfer.export_bundle(
            ROOT, files[0], app_version=APP_VERSION, schema_version=SCHEMA_VERSION,
        )
        self.dataTransferFinished.emit(ok, message)
        self.info("导出完成" if ok else "导出失败", message)

    @Slot()
    def importData(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from . import datatransfer as transfer
        from .store import SCHEMA_VERSION

        dialog = self._file_dialog(
            QFileDialog.AcceptOpen, "选择小爪备份", str(ROOT), "压缩包 (*.zip)",
        )
        if dialog.exec() != QFileDialog.Accepted:
            return
        files = dialog.selectedFiles()
        if not files:
            return
        path = files[0]

        # 先看一眼包里有什么。这一步只读，不落地。
        try:
            info = transfer.inspect_bundle(path, schema_version=SCHEMA_VERSION)
        except transfer.BundleError as exc:
            self.dataTransferFinished.emit(False, str(exc))
            self.info("导入失败", str(exc))
            return

        counts = info.get("counts") or {}
        detail_lines = [
            f"来源版本：{info.get('appVersion') or '未知'}",
            f"包含文件：{'、'.join(info.get('files') or [])}",
        ]
        if counts:
            detail_lines.append(
                "内容：" + "、".join(
                    f"{counts.get(k, 0)} 项{label}"
                    for k, label in (("tasks", "待办"), ("notes", "便签"),
                                     ("reminders", "提醒"),
                                     ("knowledge", "资料"))
                    if counts.get(k)
                )
            )

        # 覆盖不可逆，必须让用户看清楚再点。这也顺带说明「会先备份」——
        # 用户知道有后路，才敢点确定。
        box = QMessageBox()
        self._keep_on_top(box)
        box.setWindowTitle("确认导入")
        box.setIcon(QMessageBox.Warning)
        box.setText("导入会用备份里的内容覆盖现在的数据。")
        box.setInformativeText(
            "\n".join(detail_lines)
            + f"\n\n现有数据会先备份到 {transfer.PRE_IMPORT_BACKUP}，"
              "导入后立刻生效，不用重启。"
        )
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Ok:
            return

        ok, message = transfer.import_bundle(ROOT, path, info)
        if ok:
            # 导入只换了磁盘上的文件。内存里还全是旧数据 —— 不重载的话，
            # 用户下一次改设置（flush）就把旧数据整份写回去，导入白做。
            message += self.reloadData()
        self.dataTransferFinished.emit(ok, message)
        self.info("导入完成" if ok else "导入失败", message)

    @Slot(result=str)
    def reloadData(self) -> str:
        """把内存里的数据换成磁盘上的（导入备份之后调）。

        返回一句给用户看的话。**必须整条链都换**，只换 store 是不够的：
        各个 Model 自己缓存了一份 _visible、AI 侧缓存了一份对话历史，
        它们都会在下次保存时把旧内容写回磁盘。

        顺序有讲究：先换 store（后面所有刷新都依赖它），再换 Model，
        最后才发信号。反过来 QML 会在数据还没换好的瞬间重算一遍绑定，
        显示的是「新列表配旧计数」这种半成品。
        """
        # 重载前的权限等级要在这里抓一份 —— 下面 self._store.load() 会把
        # settings 整个换成导入的那份，之后就取不到「用户原本的等级」了。
        level_before = self._ai.actions.level

        # ---- 1. 主数据
        # 重载前先清掉上一次读盘留下的状态：`load_error` 会一直被 store 留着
        # （它只在读失败时被赋值，成功时不清），`migrated_from` 同理。不清的话
        # 用户「原来数据坏了 → 导入一份好备份」之后，设置页还挂着
        # 「主数据文件无法解析」这条已经过期的提示。
        self._store.load_error = ""
        self._store.migrated_from = None
        self._store.load()

        # ---- 2. 界面配色（数据目录里的 theme.json）
        from . import theme as theme_mod

        try:
            self._theme_overrides = theme_mod.load(THEME_FILE)
        except Exception:  # noqa: BLE001 - 配色读不出来就用默认，不影响数据
            self._theme_overrides = {}

        # ---- 3. AI 的权限等级：可以跟着备份走，但**不会因为导入而变松**。
        #
        # 备份文件是能被别人发给你的。`ai_level` 决定 AI 能不能不问自答地
        # 操作这台机器，「完全自动」那一档等于把键盘鼠标交出去。导入要是
        # 无条件采纳文件里的等级，一个被转发的 zip 就能静默把权限拉满 ——
        # 用户只会看到「导入完成」四个字。
        #
        # 所以取「导入前」和「备份里」两者中更紧的那个。用户真想放宽，
        # 设置页上一句话就能改，而且那是他主动做的。
        from .ai import LEVEL_ORDER

        def _rank(level: str) -> int:
            # 不认识的等级按默认档算 —— 不能因为读到个野值就当成最松的
            return (LEVEL_ORDER.index(level) if level in LEVEL_ORDER
                    else LEVEL_ORDER.index("confirm"))

        level_after = str(self._store.settings.get("ai_level") or "")
        tighter = min((level_before, level_after), key=_rank)
        if tighter != level_after:
            # 写回 store：界面上的等级 = 真正生效的等级，不能只改内存里
            # 那个 actions.level，否则显示和实际对不上。
            self._store.settings["ai_level"] = tighter
        self._ai.actions.level = tighter

        # ---- 4. 各 Model 自己缓存的 _visible
        # SessionModel / WeekModel 的公开入口叫 refresh()，其余三个叫 reload()。
        self._tasks.reload()
        self._reminders.reload()
        self._notes.reload()
        self._sessions.refresh()
        self._week.refresh()

        # ---- 5. AI 侧：对话历史是启动时读进内存的副本，且有 2 秒的
        # 延迟写盘在跑，不重载的话它会把导入的对话覆盖回去。
        self._ai.reloadHistory()
        self._ai.memoryChanged.emit()

        # ---- 6. 音效开关跟着新设置走（原来只在启动时同步过一次）
        self._sound.enabled = bool(self._store.settings.get("sound_enabled", True))

        # ---- 7. 广播。settingsChanged 覆盖所有由设置派生的 Property，
        # petStyleChanged 是独立信号（宠物形象），两个都要发。
        self.settingsChanged.emit()
        self.petStyleChanged.emit()
        self.themeChanged.emit()
        self.memoryChanged.emit()
        self._refresh_upcoming()
        self.clockChanged.emit()
        self.sitChanged.emit()

        return " 已经重新载入，现在就是导入后的内容了。"

    @staticmethod
    def _keep_on_top(widget) -> None:
        """让对话框一定在宠物窗口上面。

        这不是洁癖。宠物窗口是 Qt.WindowStaysOnTopHint + 无边框的顶层
        窗口，用户开着「始终显示在最前面」时它盖在所有东西上面 ——
        文件对话框如果不置顶，就会**被宠物挡在下面**。用户点导出，
        以为没反应，其实对话框在宠物背后等他。
        """
        from PySide6.QtCore import Qt

        widget.setWindowFlag(Qt.WindowStaysOnTopHint, True)

    @classmethod
    def _file_dialog(cls, mode, title: str, directory: str, name_filter: str):
        """建一个置顶的文件对话框。

        刻意不用 QFileDialog.getSaveFileName 这类静态方法：它们不接受
        窗口标志，也就没法置顶 —— 原因见 _keep_on_top。
        """
        from PySide6.QtWidgets import QFileDialog

        dialog = QFileDialog(None, title, directory, name_filter)
        dialog.setAcceptMode(mode)
        dialog.setFileMode(QFileDialog.ExistingFile
                           if mode == QFileDialog.AcceptOpen
                           else QFileDialog.AnyFile)
        cls._keep_on_top(dialog)
        return dialog

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
