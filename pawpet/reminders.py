"""提醒引擎：久坐提醒（AFK 感知）+ 定时提醒。

久坐提醒的关键点是「人不在就别提醒」：用 GetLastInputInfo 判断空闲时长，
离开超过阈值就把计时重新开始；全屏打游戏/放 PPT 时也不打扰。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import QObject, Signal

from . import win32

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class ReminderEngine(QObject):
    fired = Signal(str, str, str)      # (kind, title, body)

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._last_sit_check = 0.0
        self._last_schedule_check = 0.0
        # 启动时把久坐计时重置，避免刚开机就收到「该起来走走了」
        self._store.counters["sit_since_epoch"] = time.time()

    # ------------------------------------------------------------ 久坐提醒
    @property
    def sit_active_minutes(self) -> float:
        start = float(self._store.counters.get("sit_since_epoch") or time.time())
        return max(0.0, (time.time() - start) / 60.0)

    def sit_status_text(self) -> str:
        settings = self._store.settings
        if not settings.get("sit_reminder_enabled", True):
            return "久坐提醒已关闭"
        target = int(settings.get("sit_reminder_minutes", 45))
        current = self.sit_active_minutes
        left = max(0, int(round(target - current)))
        return f"已连续活动 {int(current)} 分钟 · 还有 {left} 分钟提醒你休息"

    def reset_sit_timer(self) -> None:
        self._store.counters["sit_since_epoch"] = time.time()
        self._store.save()

    @property
    def sitProgress(self) -> float:
        settings = self._store.settings
        target = max(1, int(settings.get("sit_reminder_minutes", 45)))
        return max(0.0, min(1.0, self.sit_active_minutes / target))

    def _check_sit(self, now: float) -> None:
        if now - self._last_sit_check < 10.0:
            return
        self._last_sit_check = now
        settings = self._store.settings
        if not settings.get("sit_reminder_enabled", True):
            self._store.counters["sit_since_epoch"] = now
            return

        idle = win32.idle_seconds()
        afk_limit = max(1, int(settings.get("afk_minutes", 5))) * 60
        if idle >= afk_limit:
            # 用户离开了：说明已经休息过，计时重来
            self._store.counters["sit_since_epoch"] = now
            return

        if settings.get("quiet_when_fullscreen", True) and win32.is_quiet_context():
            self._store.counters["sit_since_epoch"] = now
            return

        target = max(1, int(settings.get("sit_reminder_minutes", 45))) * 60
        if now - float(self._store.counters.get("sit_since_epoch") or now) >= target:
            self._store.counters["sit_since_epoch"] = now
            self._store.save()
            self.fired.emit(
                "sit",
                "该起来动一动了",
                f"你已经连续坐了 {settings.get('sit_reminder_minutes', 45)} 分钟。"
                "站起来走走、看看远处，肩膀会谢谢你。",
            )

    # ------------------------------------------------------------ 定时提醒
    def _schedule_matches(self, item: dict, now: datetime) -> bool:
        if not item.get("enabled", True):
            return False
        repeat = item.get("repeat", "once")
        try:
            hour, minute = (int(x) for x in str(item.get("time", "09:00")).split(":")[:2])
        except (ValueError, TypeError):
            return False
        today = now.date()
        stamp = today.isoformat()

        if item.get("last_fired") == stamp:
            return False

        if repeat == "weekdays" and now.weekday() >= 5:
            return False
        if repeat == "weekly" and item.get("date"):
            try:
                if datetime.strptime(str(item["date"]), "%Y-%m-%d").date().weekday() != now.weekday():
                    return False
            except ValueError:
                pass
        if repeat == "once":
            day = item.get("date")
            if day:
                try:
                    if datetime.strptime(str(day), "%Y-%m-%d").date() != today:
                        return False
                except ValueError:
                    return False
            elif item.get("last_fired"):
                return False

        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (now - scheduled).total_seconds()
        # 只在到点后的 10 分钟内补触发，避免晚上开程序时把早上的提醒全补一遍
        return 0 <= delta <= 600

    def _check_schedule(self, now: float) -> None:
        if now - self._last_schedule_check < 15.0:
            return
        self._last_schedule_check = now
        moment = datetime.now()
        stamp = moment.date().isoformat()
        changed = False
        for item in self._store.reminders:
            if self._schedule_matches(item, moment):
                item["last_fired"] = stamp
                changed = True
                if item.get("repeat") == "once":
                    item["enabled"] = False
                self.fired.emit("reminder", item.get("title") or "提醒", self._describe(item))
        if changed:
            self._store.save()

    @staticmethod
    def _describe(item: dict) -> str:
        repeat = item.get("repeat", "once")
        label = {"once": "仅一次", "daily": "每天", "weekdays": "工作日", "weekly": "每周"}.get(repeat, "")
        return f"{item.get('time', '')} · {label}"

    def next_upcoming(self, limit: int = 3) -> list[str]:
        """给面板显示「接下来」的几条提醒。"""
        now = datetime.now()
        upcoming: list[tuple[float, str]] = []
        for item in self._store.reminders:
            if not item.get("enabled", True):
                continue
            try:
                hour, minute = (int(x) for x in str(item.get("time", "09:00")).split(":")[:2])
            except (ValueError, TypeError):
                continue
            repeat = item.get("repeat", "once")
            for offset in range(0, 8):
                day = now.date() + timedelta(days=offset)
                if repeat == "weekdays" and day.weekday() >= 5:
                    continue
                moment = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
                if moment <= now:
                    continue
                if repeat == "once" and item.get("date"):
                    try:
                        if datetime.strptime(str(item["date"]), "%Y-%m-%d").date() != day:
                            continue
                    except ValueError:
                        pass
                gap = (moment - now).total_seconds()
                when = "今天" if offset == 0 else ("明天" if offset == 1 else f"{moment.month}/{moment.day}")
                upcoming.append((gap, f"{when} {hour:02d}:{minute:02d}  {item.get('title', '')}"))
                break
        upcoming.sort(key=lambda x: x[0])
        return [text for _, text in upcoming[:limit]]

    def poll(self) -> None:
        now = time.time()
        self._check_sit(now)
        self._check_schedule(now)
