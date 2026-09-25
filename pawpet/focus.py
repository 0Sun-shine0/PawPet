"""番茄钟引擎：基于墙钟时间，关掉程序再打开也能接着走。

旧版用 time.monotonic() 只在内存里倒数，一关就全丢。这里把「截止时间」
以 UNIX 墙钟秒存进 pet_data.json，重启后自动恢复剩余时间。
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, Property, Signal, Slot

from .store import clock_text, today_key

MODE_LABELS = {
    "focus": "专注",
    "short_break": "短休息",
    "long_break": "长休息",
}


class FocusEngine(QObject):
    tick = Signal()                 # 每 250ms（仅在有变化时）
    completed = Signal(str, int)    # (结束的模式, 实际分钟数)
    modeChanged = Signal(str)

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._last_payload: tuple | None = None
        self._last_completion_check = 0.0
        self._reconcile_on_start()

    # ------------------------------------------------------------- 内部状态
    @property
    def focus(self) -> dict:
        return self._store.focus

    def _budget_seconds(self, mode: str | None = None) -> float:
        settings = self._store.settings
        mode = mode or self.focus.get("mode", "focus")
        if mode == "short_break":
            return max(1, int(settings.get("short_break_minutes", 5))) * 60
        if mode == "long_break":
            return max(1, int(settings.get("long_break_minutes", 15))) * 60
        return max(1, int(settings.get("focus_minutes", 25))) * 60

    def remaining_seconds(self) -> float:
        focus = self.focus
        if focus.get("running"):
            return max(0.0, float(focus.get("end_epoch", 0)) - time.time())
        return max(0.0, float(focus.get("remaining", 0)))

    def _reconcile_on_start(self) -> None:
        """启动时对齐：如果关机期间番茄钟已经跑完，就直接结算成已完成。"""
        focus = self.focus
        mode = focus.get("mode", "focus")
        if focus.get("running"):
            remaining = self.remaining_seconds()
            if remaining <= 0:
                focus["running"] = False
                focus["remaining"] = 0.0
                self._settle(mode, self._budget_seconds(mode) / 60.0, offline=True)
            else:
                focus["total_minutes"] = int(round(self._budget_seconds(mode) / 60))
        else:
            remaining = float(focus.get("remaining") or 0)
            if remaining <= 0:
                focus["remaining"] = self._budget_seconds(mode)
            focus["total_minutes"] = int(round(self._budget_seconds(mode) / 60))

    # --------------------------------------------------------------- 控制
    def _start(self, mode: str, seconds: float | None = None) -> None:
        focus = self.focus
        focus["mode"] = mode
        focus["total_minutes"] = int(round(self._budget_seconds(mode) / 60))
        focus["remaining"] = float(seconds if seconds is not None else self._budget_seconds(mode))
        focus["end_epoch"] = time.time() + focus["remaining"]
        focus["running"] = True
        self._last_completion_check = time.time()
        self.modeChanged.emit(mode)
        self._emit()
        self._store.save()

    @Slot()
    def startFocus(self) -> None:
        self._start("focus")

    @Slot()
    def toggle(self) -> None:
        focus = self.focus
        if focus.get("running"):
            focus["remaining"] = self.remaining_seconds()
            focus["running"] = False
            focus["end_epoch"] = 0.0
        else:
            remaining = float(focus.get("remaining") or 0)
            if remaining <= 0:
                remaining = self._budget_seconds()
            focus["remaining"] = remaining
            focus["end_epoch"] = time.time() + remaining
            focus["running"] = True
            self._last_completion_check = time.time()
        self._emit()
        self._store.save()

    @Slot()
    def reset(self) -> None:
        focus = self.focus
        focus["running"] = False
        focus["end_epoch"] = 0.0
        focus["remaining"] = self._budget_seconds()
        self._emit()
        self._store.save()

    @Slot()
    def skip(self) -> None:
        """跳过当前阶段，直接进入下一阶段（不计入统计）。"""
        focus = self.focus
        was_running = bool(focus.get("running"))
        focus["running"] = False
        next_mode = self._next_mode(focus.get("mode", "focus"), advance=False)
        focus["mode"] = next_mode
        focus["remaining"] = self._budget_seconds(next_mode)
        focus["end_epoch"] = 0.0
        focus["total_minutes"] = int(round(focus["remaining"] / 60))
        if was_running:
            self._start(next_mode)
        else:
            self.modeChanged.emit(next_mode)
            self._emit()
            self._store.save()

    @Slot(int)
    def setMinutes(self, minutes: int) -> None:
        """把当前阶段改成指定分钟数（用于 QML 里的 15/25/45/60 快捷选择）。"""
        minutes = max(1, min(240, int(minutes)))
        focus = self.focus
        settings = self._store.settings
        if focus.get("mode") == "short_break":
            settings["short_break_minutes"] = minutes
        elif focus.get("mode") == "long_break":
            settings["long_break_minutes"] = minutes
        else:
            settings["focus_minutes"] = minutes
        focus["total_minutes"] = minutes
        focus["remaining"] = minutes * 60
        if focus.get("running"):
            focus["end_epoch"] = time.time() + focus["remaining"]
        self._emit()
        self._store.save()

    # --------------------------------------------------------------- 计时
    def _next_mode(self, mode: str, advance: bool) -> str:
        settings = self._store.settings
        if mode == "focus":
            rounds = int(settings.get("rounds_before_long_break", 4)) or 4
            current = int(self.focus.get("round", 0))
            if advance:
                current += 1
                self.focus["round"] = current
            return "long_break" if current > 0 and current % rounds == 0 else "short_break"
        return "focus"

    def _settle(self, mode: str, minutes: float, offline: bool = False) -> None:
        """一个阶段自然结束：记录统计 + 决定下一个阶段。"""
        focus = self.focus
        rounded = max(1, int(round(minutes)))
        if mode == "focus":
            key = today_key()
            day = self._store.stats.setdefault(key, {})
            day["focus_minutes"] = int(day.get("focus_minutes", 0)) + rounded
            day["focus_rounds"] = int(day.get("focus_rounds", 0)) + 1
            self._store.counters["focus_rounds"] = int(self._store.counters.get("focus_rounds", 0)) + 1
            self._store.counters["completed_total"] = int(self._store.counters.get("completed_total", 0)) + 1
            self._store.state.setdefault("sessions", []).append({
                "id": f"s{int(time.time() * 1000):x}",
                "start": time.time() - rounded * 60,
                "end": time.time(),
                "minutes": rounded,
                "label": "专注",
                "offline": bool(offline),
            })
            del self._store.state["sessions"][:-500]
            task_id = str(focus.get("task_id") or "").strip()
            if task_id:
                # 先把待记账的任务 ID 和本轮结算一起落盘，再由 Backend
                # 消费。这样结算后程序立刻退出也不会让番茄数凭空少一个。
                focus["pending_task_pomodoro"] = task_id

        next_mode = self._next_mode(mode, advance=(mode == "focus"))
        focus["mode"] = next_mode
        focus["remaining"] = self._budget_seconds(next_mode)
        focus["total_minutes"] = int(round(focus["remaining"] / 60))
        focus["running"] = False
        focus["end_epoch"] = 0.0

        auto = bool(self._store.settings.get("auto_start_next", True))
        if auto:
            focus["end_epoch"] = time.time() + focus["remaining"]
            focus["running"] = True
            self._last_completion_check = time.time()

        self._store.save()
        self.modeChanged.emit(next_mode)
        self.completed.emit(mode, rounded)
        self._emit(force=True)

    @Slot(result=str)
    def takePendingTaskPomodoro(self) -> str:
        """取出并清掉待记账任务；没有待记账时返回空串。"""
        task_id = str(self.focus.get("pending_task_pomodoro") or "").strip()
        if task_id:
            self.focus["pending_task_pomodoro"] = ""
            self._store.save()
        return task_id

    def poll(self) -> None:
        """由主窗口的定时器调用；只在秒数变化或阶段结束时发出信号。"""
        focus = self.focus
        if focus.get("running"):
            remaining = self.remaining_seconds()
            if remaining <= 0:
                self._settle(focus.get("mode", "focus"), self._budget_seconds() / 60.0)
                return
            self._emit()
        else:
            # 暂停状态下设置里改了时长，也要同步过去
            pass

    def _emit(self, force: bool = False) -> None:
        focus = self.focus
        remaining = self.remaining_seconds()
        payload = (
            focus.get("mode", "focus"),
            bool(focus.get("running")),
            int(remaining),
            int(focus.get("total_minutes", 25)),
            int(focus.get("round", 0)),
        )
        if force or payload != self._last_payload:
            self._last_payload = payload
            self.tick.emit()

    # ------------------------------------------------------------ QML 接口
    @Property(str, notify=tick)
    def taskId(self) -> str:
        return str(self.focus.get("task_id") or "")

    @taskId.setter
    def taskId(self, value: str) -> None:
        task_id = str(value or "").strip()
        if task_id == self.taskId:
            return
        self.focus["task_id"] = task_id
        self._store.save()
        self.tick.emit()

    @Property(str, notify=tick)
    def taskLabel(self) -> str:
        task_id = self.taskId
        if not task_id:
            return ""
        for task in self._store.tasks:
            if task.get("id") == task_id:
                return str(task.get("text") or "")
        return "待办已删除"

    @Property(str, notify=tick)
    def mode(self) -> str:
        return self.focus.get("mode", "focus")

    @Property(str, notify=tick)
    def modeLabel(self) -> str:
        return MODE_LABELS.get(self.focus.get("mode", "focus"), "专注")

    @Property(bool, notify=tick)
    def running(self) -> bool:
        return bool(self.focus.get("running"))

    @Property(int, notify=tick)
    def remaining(self) -> int:
        return int(self.remaining_seconds())

    @Property(str, notify=tick)
    def clock(self) -> str:
        return clock_text(self.remaining_seconds())

    @Property(int, notify=tick)
    def totalSeconds(self) -> int:
        return max(1, int(self._budget_seconds()))

    @Property(float, notify=tick)
    def progress(self) -> float:
        """0.0 = 刚刚开始，1.0 = 时间到。"""
        total = max(1.0, float(self._budget_seconds()))
        return max(0.0, min(1.0, 1.0 - self.remaining_seconds() / total))

    @Property(int, notify=tick)
    def round(self) -> int:
        return int(self.focus.get("round", 0))

    @Property(str, notify=tick)
    def stateLabel(self) -> str:
        if self.running:
            return "进行中"
        if self.remaining_seconds() <= 0:
            return "已完成"
        return "已暂停"

    # ------------------------------------------------------------- 今日统计
    @Property(int, notify=tick)
    def todayMinutes(self) -> int:
        return int((self._store.stats.get(today_key()) or {}).get("focus_minutes", 0))

    @Property(int, notify=tick)
    def todayRounds(self) -> int:
        return int((self._store.stats.get(today_key()) or {}).get("focus_rounds", 0))

    @Property(int, notify=tick)
    def todayTasksDone(self) -> int:
        return int((self._store.stats.get(today_key()) or {}).get("tasks_done", 0))

    @Property(int, notify=tick)
    def dailyGoal(self) -> int:
        return max(1, int(self._store.settings.get("daily_goal_minutes", 120)))

    @Property(float, notify=tick)
    def goalProgress(self) -> float:
        return max(0.0, min(1.0, self.todayMinutes / max(1, self.dailyGoal)))

    @Property(str, notify=tick)
    def todayLine(self) -> str:
        minutes = self.todayMinutes
        hours, mins = divmod(minutes, 60)
        spent = f"{hours} 小时 {mins} 分" if hours else f"{mins} 分钟"
        return f"今天已专注 {spent} · {self.todayRounds} 轮 · 完成 {self.todayTasksDone} 件待办"

    def notify_stats_changed(self) -> None:
        self.tick.emit()
