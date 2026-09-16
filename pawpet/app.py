"""应用装配：创建 QApplication、加载 QML、挂上托盘 / 热键 / 心跳。

Python 侧只管「系统集成」这一层（托盘、全局热键、单实例、定时器）；
窗口之间的接线全部在 QML 里完成，所以这里不需要反射调用任何 QML 方法。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import win32
from .backend import Backend
from .config import (
    APP_NAME,
    APP_VERSION,
    DEBUG,
    PORTABLE,
    QML_DIR,
    ROOT,
    ensure_dirs,
    is_frozen,
    safe_print,
)
from .services import SingleInstanceServer
from .store import Store

# 心跳：50ms 足够让秒表看起来是「跳」而不是「爬」；
# FocusEngine 只在秒数真的变化时才发信号，所以这个频率几乎不花代价。
HEARTBEAT_MS = 50


def make_icon() -> QIcon:
    """用代码画托盘图标，省得依赖外部图片文件。"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ff9d6c"))
        painter.drawEllipse(0.5, 0.5, size - 1, size - 1)

        painter.setBrush(QColor("#2a1c16"))
        unit = size / 16.0
        painter.drawEllipse(int(4.6 * unit), int(6.6 * unit),
                            int(6.8 * unit), int(5.8 * unit))
        for cx, cy, r in ((3.3, 4.7, 1.45), (7.0, 3.5, 1.55), (10.7, 4.7, 1.45)):
            painter.drawEllipse(int((cx - r) * unit), int((cy - r) * unit),
                                int(2 * r * unit), int(2 * r * unit))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class PawPetApp(QObject):
    def __init__(self, app: QApplication, store: Store) -> None:
        super().__init__()
        self._app = app
        self._store = store
        self._backend = Backend(store, self)
        self._icon = make_icon()
        self._shutting_down = False
        self._qml_root = None

        self._engine = QQmlApplicationEngine()
        self._engine.addImportPath(str(QML_DIR))
        self._engine.rootContext().setContextProperty("backend", self._backend)
        self._engine.load(QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")))

        # 必须把根对象存成成员：rootObjects() 返回的临时列表被回收时，
        # 根对象有可能连带被销毁，窗口就会变成 "Internal C++ object already deleted"。
        roots = self._engine.rootObjects()
        if not roots:
            raise RuntimeError("QML 加载失败，请检查 pawpet/qml 目录下的文件")
        self._qml_root = roots[0]

        self._tray = self._build_tray()
        self._hotkeys = self._build_hotkeys()
        self._pipe = self._build_pipe()

        self._backend.quitRequested.connect(self._quit)
        self._backend.settingsChanged.connect(self._on_settings_saved)
        # 通知走两条路：QML 负责宠物旁边的气泡，这里负责系统托盘的气泡。
        self._backend.notifier().posted.connect(self._on_notification)

        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(HEARTBEAT_MS)
        self._heartbeat.timeout.connect(self._tick)
        self._heartbeat.start()

        self._slow = QTimer(self)
        self._slow.setInterval(1000)
        self._slow.timeout.connect(self._slow_tick)
        self._slow.start()

        self._greet()

    # ------------------------------------------------------------- 心跳
    def _tick(self) -> None:
        self._backend.focus.poll()

    def _slow_tick(self) -> None:
        self._backend.reminder_engine.poll()
        self._backend.update_activity()
        self._backend.refresh_dynamic()
        if self._tray is not None:
            self._tray.setToolTip(f"{APP_NAME} · {self._backend.statusLine}")

    def _greet(self) -> None:
        note = self._backend.migrationNote
        if note:
            self._backend.info("数据已升级", note)
        elif not self._store.settings.get("autostart", False):
            pending = self._backend.pendingCount
            body = f"还有 {pending} 件待办在等你。" if pending else "双击小爪打开工作台，右键有快捷菜单。"
            self._backend.info("小爪已就位", body)

    # ----------------------------------------------------------------- 托盘
    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            safe_print("[小爪] 系统托盘不可用，改为只显示宠物窗口。")
            return None

        tray = QSystemTrayIcon(self._icon, self)
        tray.setToolTip(f"{APP_NAME} {APP_VERSION}")

        menu = QMenu()
        # 托盘菜单是原生控件，用 pointSize 设字号（原生菜单按 pt 走）。
        # 字体名仍然从 qmlfont 取，别在这里另写一份 —— 托盘和界面用同一套
        # 字体，看起来才是一个软件而不是两个拼起来的。
        from .qmlfont import FONT_FAMILY

        tray_font = QFont()
        tray_font.setFamilies([item.strip() for item in FONT_FAMILY.split(",")])
        tray_font.setPointSize(9)
        menu.setFont(tray_font)

        def add(text: str, callback) -> QAction:
            action = QAction(text, menu)
            action.triggered.connect(callback)
            menu.addAction(action)
            return action

        self._tray_toggle_action = add("开始专注", self._toggle_focus)
        self._tray_ask_action = add("让小爪做事  (Ctrl+Alt+空格)",
                                    self._backend.showCommandBar)
        add("打开工作台  (Ctrl+Alt+P)", lambda: self._backend.showDashboard("focus"))
        add("快速添加待办  (Ctrl+Alt+N)", lambda: self._backend.showDashboard("tasks"))
        add("复制今日总结", self._backend.exportSummary)
        menu.addSeparator()
        self._tray_pet_action = add("隐藏小爪", self._backend.togglePet)
        add("我刚刚休息过了", self._backend.resetSitTimer)
        add("设置", lambda: self._backend.showDashboard("settings"))
        menu.addSeparator()
        add("退出小爪助手", self._quit)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()

        # 让菜单项文字跟着状态走
        self._backend.petVisibilityChanged.connect(self._sync_tray_labels)
        self._backend.clockChanged.connect(self._sync_tray_labels)
        self._tray_menu = menu
        return tray

    def _sync_tray_labels(self) -> None:
        if self._tray is None:
            return
        self._tray_pet_action.setText(self._backend.petVisibleLabel)
        self._tray_toggle_action.setText(
            "暂停专注" if self._backend.focus.running else "开始专注")

    @Slot()
    def _toggle_focus(self) -> None:
        self._backend.focus.toggle()
        self._sync_tray_labels()
        if self._tray is not None:
            state = "开始" if self._backend.focus.running else "暂停"
            self._tray.showMessage(APP_NAME, f"{state}{self._backend.focus.modeLabel}",
                                   self._icon, 1500)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._backend.togglePet()
        elif reason == QSystemTrayIcon.MiddleClick:
            self._backend.showDashboard("focus")

    # ----------------------------------------------------------------- 热键
    def _build_hotkeys(self) -> win32.HotkeyManager:
        settings = self._store.settings
        bindings = {
            "dashboard": settings.get("hotkey_dashboard", "ctrl+alt+p"),
            "focus": settings.get("hotkey_focus", "ctrl+alt+t"),
            "task": settings.get("hotkey_task", "ctrl+alt+n"),
            "ask": settings.get("hotkey_ask", "ctrl+alt+space"),
        }
        manager = win32.HotkeyManager(bindings, self._on_hotkey)
        manager.start()
        if manager.failed:
            safe_print(f"[小爪] 热键注册失败（可能被占用）：{', '.join(manager.failed)}")
        if manager.registered:
            safe_print(f"[小爪] 全局热键已注册：{', '.join(manager.registered)}")
        return manager

    def _on_hotkey(self, action: str) -> None:
        # 回调发生在热键线程里，切回主线程再碰界面
        QTimer.singleShot(0, lambda: self._handle_hotkey(action))

    def _handle_hotkey(self, action: str) -> None:
        if action == "focus":
            self._toggle_focus()
        elif action == "task":
            self._backend.showDashboard("tasks")
        elif action == "ask":
            # 最常用的入口：任何地方按一下就弹出指令栏
            self._backend.toggleCommandBar()
        else:
            self._backend.showDashboard("focus")

    # --------------------------------------------------------------- 单实例
    def _build_pipe(self) -> SingleInstanceServer:
        server = SingleInstanceServer(self._on_pipe_message)
        server.start()
        return server

    def _on_pipe_message(self, payload: str) -> None:
        command = payload.strip()
        QTimer.singleShot(0, lambda: self._handle_pipe(command))

    def _handle_pipe(self, command: str) -> None:
        self._backend.petVisible = True
        self._backend.showDashboard("tasks" if command == "task" else "focus")

    # ------------------------------------------------------------- 设置落盘
    @Slot()
    def _on_settings_saved(self) -> None:
        self._backend.save()

    # --------------------------------------------------------------- 通知
    @Slot(str, str, str)
    def _on_notification(self, kind: str, title: str, body: str) -> None:
        """托盘气泡。专注结束这类重要提醒用更长一点的显示时间。"""
        if self._tray is None:
            return
        timeout = 8000 if kind in ("focus_done", "break_done") else 5000
        self._tray.showMessage(title, body or APP_NAME, self._icon, timeout)

    # --------------------------------------------------------------- 收尾
    def shutdown(self) -> None:
        """按正确顺序拆除：先停定时器，再销毁 QML，最后才放掉数据。

        顺序反了的话，QML 绑定会在 backend 已经失效之后重新求值，
        在控制台刷出一大堆 "Cannot read property ... of null" 噪音。
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        self._heartbeat.stop()
        self._slow.stop()
        self._hotkeys.stop()
        self._pipe.stop()
        if self._tray is not None:
            self._tray.hide()
        self._backend.shutdown()
        self._backend.flush()

        # 先销毁 QML 对象树，断开所有对 backend 的绑定
        for obj in self._engine.rootObjects():
            obj.deleteLater()
        self._qml_root = None
        self._app.processEvents()

        self._app.quit()

    # 兼容旧调用名
    def _quit(self) -> None:
        self.shutdown()


def run() -> int:
    ensure_dirs()
    win32.set_process_dpi_awareness()

    # 单实例：已经有小爪在跑，就让那个实例把面板显示出来，然后自己安静退出。
    # 互斥体名字可以由 PAWPET_INSTANCE_SUFFIX 改（测试要用独立命名空间，
    # 否则会跟用户正在跑的那份撞上）。见 config.INSTANCE_NAME。
    from .config import INSTANCE_NAME

    instance = win32.SingleInstance(INSTANCE_NAME)
    if instance.already_running:
        from .services import activate_existing_window, ping_existing_instance

        for _ in range(8):
            if ping_existing_instance("show"):
                return 0
            time.sleep(0.25)
        activate_existing_window()
        return 0

    QQuickStyle.setStyle("Basic")   # 统一控件外观，自定义配色才有意义

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("PawPet")
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)   # 关掉面板也要留在托盘里

    store = Store()
    store.load()
    store.state["counters"]["sit_since_epoch"] = time.time()
    if DEBUG:
        safe_print(f"[小爪] 数据文件：{store.path}")
        safe_print(f"[小爪] 迁移来源：{store.migrated_from}")
        safe_print(f"[小爪] 待办 {len(store.tasks)} 条 / 便签 {len(store.notes)} 条 "
              f"/ 提醒 {len(store.reminders)} 条")

    try:
        pawpet = PawPetApp(app, store)
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        safe_print(f"[小爪] 启动失败，时间 {datetime.now():%H:%M:%S}")
        return 1

    code = app.exec()
    pawpet.shutdown()
    instance.release()
    return code
