"""提醒引擎：久坐提醒（AFK 感知）+ 定时提醒。

久坐提醒的关键点是「人不在就别提醒」：用 GetLastInputInfo 判断空闲时长，
离开超过阈值就把计时重新开始；全屏打游戏/放 PPT 时也不打扰。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import QObject, Signal

from . import win32
from .models import (
    INTERVAL_DEFAULT_MINUTES,
    INTERVAL_MAX_MINUTES,
    INTERVAL_MIN_MINUTES,
    REPEAT_LABEL,
    _repeat_text,
)

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
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return False
        today = now.date()

        # ---------------- 按间隔重复 ----------------
        #
        # **这条不能走下面那套「每天某时刻」的逻辑。** 间隔重复的起点是
        # 「上一次触发的时间」，不是固定时刻 —— 所以看的是 last_fired 的
        # **时间戳**，而不是「今天触发过没有」。
        #
        # `every` 是分钟数（5 = 每 5 分钟）。启动时 last_fired 是 None，
        # 那就以「现在」为起点开始计时 —— 不能立刻触发一次（用户刚打开
        # 程序就被提醒，很突兀）。
        if repeat == "interval":
            try:
                every = int(item.get("every") or INTERVAL_DEFAULT_MINUTES)
            except (TypeError, ValueError):
                every = INTERVAL_DEFAULT_MINUTES
            every = max(INTERVAL_MIN_MINUTES, min(INTERVAL_MAX_MINUTES, every))

            stamp = item.get("last_fired")
            if stamp in (None, ""):
                # 从没触发过：记下起点，这一轮不触发
                item["last_fired"] = now.timestamp()
                return False
            try:
                last = float(stamp)
            except (TypeError, ValueError):
                # 老数据里 last_fired 存的是日期字符串（"2026-09-21"）——
                # 那种没法用来算间隔，当成「刚记下起点」重新开始。
                item["last_fired"] = now.timestamp()
                return False

            elapsed = now.timestamp() - last
            # 用 >= 而不是 ==：检查是每 15 秒一次，正好踩在整点上的概率很低。
            #
            # 上限那一条是防「睡了很久之后一连串补触发」：间隔 5 分钟、
            # 关机两小时，醒来不该弹 24 次。超过 3 倍间隔就只补一次。
            return 0 <= elapsed and elapsed >= every * 60

        # ---------------- 固定时刻那几种 ----------------
        # 这几类是「每天一次」，所以用日期字符串当去重标记就够了。
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
                # **间隔重复存时间戳，其余存日期。**
                # 存日期的话「每 5 分钟」第二次就判不出来了 —— 当天已经
                # 触发过的标记会一直挡住它。
                if item.get("repeat") == "interval":
                    item["last_fired"] = moment.timestamp()
                else:
                    item["last_fired"] = stamp
                changed = True
                if item.get("repeat") == "once":
                    item["enabled"] = False
                self.fired.emit("reminder", item.get("title") or "提醒",
                                self._describe(item))
        if changed:
            self._store.save()

    @staticmethod
    def _describe(item: dict) -> str:
        """气泡里那行小字。**重复方式的说法只留一份**（models._repeat_text）。

        原来这里硬编码了一份 {"once": "仅一次", ...} 的映射 —— 加了新的
        重复方式之后就得改两处，漏一处就出现「列表里写每 5 分钟、气泡里
        写别的」。这类「同一个说法存两份」是这个项目已经踩过的坑。
        """
        if item.get("repeat") == "interval":
            return _repeat_text(item)
        label = REPEAT_LABEL.get(item.get("repeat", "once"), "")
        return f"{item.get('time', '')} · {label}"

    def next_upcoming(self, limit: int = 3) -> list[str]:
        """给面板显示「接下来」的几条提醒。"""
        now = datetime.now()
        upcoming: list[tuple[float, str]] = []
        for item in self._store.reminders:
            if not item.get("enabled", True):
                continue
            repeat = item.get("repeat", "once")

            # 间隔提醒没有固定的时刻，不能把创建时遗留的 time 当成
            # 下一次触发时间，否则用户会看到「09:00 喝水」，但真正响铃
            # 可能是在 5 分钟后。last_fired 存的是时间戳，正好可以算出
            # 下一次；从未触发过的提醒则从现在开始算一个间隔。
            if repeat == "interval":
                try:
                    every = int(item.get("every") or INTERVAL_DEFAULT_MINUTES)
                except (TypeError, ValueError):
                    every = INTERVAL_DEFAULT_MINUTES
                every = max(INTERVAL_MIN_MINUTES, min(INTERVAL_MAX_MINUTES, every))
                stamp = item.get("last_fired")
                try:
                    next_epoch = (
                        float(stamp) + every * 60
                        if stamp not in (None, "")
                        else now.timestamp() + every * 60
                    )
                except (TypeError, ValueError):
                    next_epoch = now.timestamp() + every * 60
                gap = max(0.0, next_epoch - now.timestamp())
                minutes = max(1, int(round(gap / 60)))
                upcoming.append((gap, f"约 {minutes} 分钟后  {item.get('title', '')}"))
                continue

            try:
                hour, minute = (int(x) for x in str(item.get("time", "09:00")).split(":")[:2])
            except (ValueError, TypeError):
                continue
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
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
