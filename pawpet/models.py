"""QML 数据模型：待办、提醒、便签、专注记录。

这些模型直接持有 Store 里的列表（同一个对象引用），改动后就地生效并通知 QML，
最后统一由 Store.save() 落盘。只有真正发生数据变化时才发信号，因此不会再出现
旧版「每秒重建整个待办列表」导致滚动条和输入焦点被打断的问题。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from PySide6.QtCore import (
    QAbstractListModel, QModelIndex, Property, Qt, Signal, Slot,
)

from .store import new_id, pretty_day, today_key

# --------------------------------------------------------------------------
# 待办
# --------------------------------------------------------------------------
PRIORITY_LABEL = {0: "普通", 1: "重要", 2: "紧急"}
DEFAULT_REMINDER_TIME = "09:00"
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _normalize_clock(value, fallback: str = DEFAULT_REMINDER_TIME) -> str:
    match = _CLOCK_RE.fullmatch(str(value or "").strip())
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return fallback


def _age_text(created: float | None) -> str:
    if not created:
        return ""
    delta = time.time() - float(created)
    if delta < 3600:
        return "刚刚"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    days = int(delta // 86400)
    if days == 1:
        return "昨天"
    if days < 30:
        return f"{days} 天前"
    return datetime.fromtimestamp(created).strftime("%m-%d")


def _due_text(due: str | None) -> tuple[str, bool]:
    """返回 (显示文本, 是否逾期)。"""
    if not due:
        return "", False
    try:
        day = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return str(due), False
    today = datetime.now().date()
    overdue = day < today
    if day == today:
        return "今天到期", False
    if day == today + timedelta(days=1):
        return "明天到期", False
    if overdue:
        return f"{day.month}/{day.day} 已过期", True
    return f"{day.month}/{day.day} 到期", False


def _completion_day(task: dict) -> str:
    """返回待办上次完成时对应的统计日期。"""
    completed_at = task.get("done_at")
    try:
        if completed_at:
            return datetime.fromtimestamp(float(completed_at)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    return today_key()


class TaskModel(QAbstractListModel):
    Roles = {
        Qt.UserRole + 1: b"taskId",
        Qt.UserRole + 2: b"text",
        Qt.UserRole + 3: b"done",
        Qt.UserRole + 4: b"priority",
        Qt.UserRole + 5: b"priorityLabel",
        Qt.UserRole + 6: b"dueText",
        Qt.UserRole + 7: b"overdue",
        Qt.UserRole + 8: b"createdText",
        Qt.UserRole + 9: b"pomodoros",
        Qt.UserRole + 10: b"hasDue",
    }

    changed = Signal()
    countsChanged = Signal()
    undoChanged = Signal()

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._show_done = False
        self._sort = "smart"          # smart | created | priority
        self._search = ""
        self._visible: list[dict] = []
        self._last_removed: dict | None = None
        self._rebuild()

    # ----------------------------------------------------------- Qt 接口
    def roleNames(self):
        return self.Roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible)):
            return None
        task = self._visible[index.row()]
        due_text, overdue = _due_text(task.get("due"))
        mapping = {
            Qt.UserRole + 1: task.get("id", ""),
            Qt.UserRole + 2: task.get("text", ""),
            Qt.UserRole + 3: bool(task.get("done")),
            Qt.UserRole + 4: int(task.get("priority", 0)),
            Qt.UserRole + 5: PRIORITY_LABEL.get(int(task.get("priority", 0)), "普通"),
            Qt.UserRole + 6: due_text,
            Qt.UserRole + 7: overdue,
            Qt.UserRole + 8: _age_text(task.get("created")),
            Qt.UserRole + 9: int(task.get("pomodoros", 0)),
            Qt.UserRole + 10: bool(due_text),
        }
        return mapping.get(role)

    # ----------------------------------------------------------- 计算属性
    @Property(int, notify=changed)
    def count(self) -> int:
        """QAbstractListModel 本身没有 count 属性，QML 里要用得自己加上。"""
        return len(self._visible)

    @Property(int, notify=countsChanged)
    def pendingCount(self) -> int:
        return sum(1 for t in self._store.tasks if not t.get("done"))

    @Property(int, notify=countsChanged)
    def doneCount(self) -> int:
        return sum(1 for t in self._store.tasks if t.get("done"))

    @Property(int, notify=countsChanged)
    def totalCount(self) -> int:
        return len(self._store.tasks)

    @Property(bool, notify=undoChanged)
    def canUndo(self) -> bool:
        return self._last_removed is not None

    @Property(str, notify=undoChanged)
    def lastRemovedText(self) -> str:
        if self._last_removed is None:
            return ""
        return str(self._last_removed.get("task", {}).get("text", ""))

    @Property(bool, notify=changed)
    def showDone(self) -> bool:
        return self._show_done

    @showDone.setter
    def showDone(self, value: bool) -> None:
        value = bool(value)
        if value == self._show_done:
            return
        self._show_done = value
        self._rebuild()
        self.changed.emit()

    @Property(str, notify=changed)
    def sortMode(self) -> str:
        return self._sort

    @sortMode.setter
    def sortMode(self, value: str) -> None:
        value = value if value in ("smart", "created", "priority") else "smart"
        if value == self._sort:
            return
        self._sort = value
        self._rebuild()
        self.changed.emit()

    @Property(str, notify=changed)
    def searchText(self) -> str:
        return self._search

    @searchText.setter
    def searchText(self, value: str) -> None:
        value = str(value or "")
        if value == self._search:
            return
        self._search = value
        self._rebuild()
        self.changed.emit()

    # ----------------------------------------------------------- 内部逻辑
    def _rebuild(self) -> None:
        items = [t for t in self._store.tasks if self._show_done or not t.get("done")]
        query = self._search.strip().casefold()
        if query:
            items = [
                task for task in items
                if query in str(task.get("text") or "").casefold()
            ]

        if self._sort == "created":
            items.sort(key=lambda t: t.get("created") or 0, reverse=True)
        elif self._sort == "priority":
            items.sort(key=lambda t: (-int(t.get("priority", 0)), t.get("created") or 0))
        else:  # smart：未完成在前，紧急/重要优先，其次按创建时间倒序
            items.sort(key=lambda t: (
                bool(t.get("done")),
                -int(t.get("priority", 0)),
                -(t.get("created") or 0),
            ))

        self.beginResetModel()
        self._visible = items
        self.endResetModel()

    def _touch(self) -> None:
        self._rebuild()
        self.countsChanged.emit()
        self.changed.emit()
        self._store.save()

    def reload(self) -> None:
        """把 _visible 重新按 store 里的数据算一遍。

        给「导入备份」用：store 被整个换掉之后，_visible 还指着旧数据的
        那批 dict，界面会继续显示导入前的内容。
        """
        self._clear_undo()
        self._rebuild()
        self.countsChanged.emit()
        self.changed.emit()

    def _index_of(self, task_id: str) -> int:
        for row, task in enumerate(self._visible):
            if task.get("id") == task_id:
                return row
        return -1

    def _find(self, task_id: str) -> dict | None:
        for task in self._store.tasks:
            if task.get("id") == task_id:
                return task
        return None

    # ----------------------------------------------------------- 供 QML 调用
    @Slot(str, int)
    def add(self, text: str, priority: int = 0) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._clear_undo()
        self._store.tasks.append({
            "id": new_id("t"),
            "text": text[:200],
            "done": False,
            "created": time.time(),
            "done_at": None,
            "priority": max(0, min(2, int(priority))),
            "due": None,
            "pomodoros": 0,
        })
        del self._store.tasks[:-2000]
        self._touch()

    @Slot(str)
    def toggle(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        self._clear_undo()
        was_done = bool(task.get("done"))
        completion_day = _completion_day(task) if was_done else ""
        task["done"] = not task.get("done")
        task["done_at"] = time.time() if task["done"] else None
        if task["done"]:
            self._store.stats.setdefault(today_key(), {})
            day = self._store.stats[today_key()]
            day["tasks_done"] = int(day.get("tasks_done", 0)) + 1
        elif was_done:
            day = self._store.stats.get(completion_day)
            if day is not None:
                day["tasks_done"] = max(0, int(day.get("tasks_done", 0)) - 1)
        row = self._index_of(task_id)
        needs_resort = self._show_done and self._sort == "smart"
        if row >= 0 and (self._show_done or not task["done"]) and not needs_resort:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)
            self.countsChanged.emit()
            self.changed.emit()
            self._store.save()
        else:
            self._touch()

    @Slot(str)
    def remove(self, task_id: str) -> None:
        for index, task in enumerate(self._store.tasks):
            if task.get("id") != task_id:
                continue
            self._last_removed = {"task": dict(task), "index": index}
            del self._store.tasks[index]
            self._touch()
            self.undoChanged.emit()
            return

    @Slot()
    def undoRemove(self) -> None:
        if self._last_removed is None:
            return
        removed = self._last_removed
        task = dict(removed.get("task", {}))
        task_id = task.get("id")
        if not task_id or self._find(task_id) is not None:
            self._last_removed = None
            self.undoChanged.emit()
            return
        index = max(0, min(len(self._store.tasks), int(removed.get("index", 0))))
        self._store.tasks.insert(index, task)
        self._last_removed = None
        self._touch()
        self.undoChanged.emit()

    @Slot()
    def clearUndo(self) -> None:
        self._clear_undo()

    def _clear_undo(self) -> None:
        if self._last_removed is not None:
            self._last_removed = None
            self.undoChanged.emit()

    @Slot(str, str)
    def rename(self, task_id: str, text: str) -> None:
        task = self._find(task_id)
        text = (text or "").strip()
        if task is None or not text:
            return
        self._clear_undo()
        task["text"] = text[:200]
        # 改名可能改变当前搜索结果；也可能让 smart 排序后的可见顺序
        # 需要重新计算。不能只发 dataChanged，否则旧行会留在筛选列表里。
        self._rebuild()
        self.changed.emit()
        self._store.save()

    @Slot(str, int)
    def setPriority(self, task_id: str, priority: int) -> None:
        task = self._find(task_id)
        if task is None:
            return
        self._clear_undo()
        task["priority"] = max(0, min(2, int(priority)))
        self._touch()

    @Slot(str, str)
    def setDue(self, task_id: str, due: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        self._clear_undo()
        task["due"] = due or None
        self._touch()

    @Slot(str)
    def bumpPomodoro(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        self._clear_undo()
        task["pomodoros"] = int(task.get("pomodoros", 0)) + 1
        row = self._index_of(task_id)
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)
        self._store.save()

    @Slot()
    def clearDone(self) -> None:
        self._clear_undo()
        self._store.tasks[:] = [t for t in self._store.tasks if not t.get("done")]
        self._touch()

    @Slot(result=str)
    def firstPendingId(self) -> str:
        for task in self._visible:
            if not task.get("done"):
                return task.get("id", "")
        return ""


# --------------------------------------------------------------------------
# 提醒
# --------------------------------------------------------------------------
REPEAT_LABEL = {
    "once": "仅一次",
    "daily": "每天",
    "weekdays": "工作日",
    "weekly": "每周",
    # 按间隔重复。`every` 字段存分钟数（5 = 每 5 分钟）。
    #
    # 和上面几个的区别：那些是「每天某时刻」，这个是「从现在起每隔 N 分钟」——
    # 起点是**上一次触发的时间**，所以要靠 last_fired 的**时间戳**来算，
    # 不能只看日期。
    "interval": "每 N 分钟",
}

# 间隔重复的上下限。太小会烦人（1 分钟提醒一次），太大就退化成「每天」了。
INTERVAL_MIN_MINUTES = 1
INTERVAL_MAX_MINUTES = 720          # 12 小时
INTERVAL_DEFAULT_MINUTES = 30

# 几个常用间隔，界面上做成快捷选项
INTERVAL_PRESETS = (5, 10, 15, 30, 60, 120)


def _clamp_interval(minutes) -> int:
    """把间隔分钟数夹到合理范围。传 0/None 就用默认值。

    夹的原因：太小会烦人（1 分钟提醒一次），太大就退化成「每天」了。
    界面上的输入框可能被用户填任意数字，这里必须兜住。
    """
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        value = INTERVAL_DEFAULT_MINUTES
    if value <= 0:
        value = INTERVAL_DEFAULT_MINUTES
    return max(INTERVAL_MIN_MINUTES, min(INTERVAL_MAX_MINUTES, value))


def _repeat_text(item: dict) -> str:
    """把重复方式说成人话，给列表和气泡用。"""
    repeat = item.get("repeat", "once")
    spec = str(item.get("time", "09:00"))
    if repeat == "interval":
        minutes = int(item.get("every") or INTERVAL_DEFAULT_MINUTES)
        return f"每 {minutes} 分钟"
    if repeat == "once":
        day = item.get("date")
        return f"{day} {spec}" if day else spec
    if repeat == "daily":
        return f"每天 {spec}"
    if repeat == "weekdays":
        return f"工作日 {spec}"
    if repeat == "weekly":
        return f"每周 {spec}"
    return spec


class ReminderModel(QAbstractListModel):
    Roles = {
        Qt.UserRole + 1: b"reminderId",
        Qt.UserRole + 2: b"title",
        Qt.UserRole + 3: b"time",
        Qt.UserRole + 4: b"repeat",
        Qt.UserRole + 5: b"repeatLabel",
        Qt.UserRole + 6: b"enabled",
        Qt.UserRole + 7: b"nextText",
        Qt.UserRole + 8: b"every",
    }

    changed = Signal()
    countsChanged = Signal()
    undoChanged = Signal()

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._last_removed: dict | None = None
        self._rebuild()

    def roleNames(self):
        return self.Roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible)):
            return None
        item = self._visible[index.row()]
        mapping = {
            Qt.UserRole + 1: item.get("id", ""),
            Qt.UserRole + 2: item.get("title", ""),
            Qt.UserRole + 3: item.get("time", ""),
            Qt.UserRole + 4: item.get("repeat", "once"),
            Qt.UserRole + 5: REPEAT_LABEL.get(item.get("repeat", "once"), "仅一次"),
            Qt.UserRole + 6: bool(item.get("enabled", True)),
            Qt.UserRole + 7: self._next_text(item),
            Qt.UserRole + 8: int(item.get("every") or 0),
        }
        return mapping.get(role)

    @staticmethod
    def _next_text(item: dict) -> str:
        if not item.get("enabled", True):
            return "已关闭"
        return _repeat_text(item)

    @Property(int, notify=countsChanged)
    def count(self) -> int:
        return len(self._visible)

    @Property(int, notify=countsChanged)
    def activeCount(self) -> int:
        return sum(1 for r in self._store.reminders if r.get("enabled", True))

    @Property(bool, notify=undoChanged)
    def canUndo(self) -> bool:
        return self._last_removed is not None

    @Property(str, notify=undoChanged)
    def lastRemovedTitle(self) -> str:
        if self._last_removed is None:
            return ""
        return str(self._last_removed.get("reminder", {}).get("title", "无标题"))

    def _rebuild(self) -> None:
        items = sorted(self._store.reminders, key=lambda r: str(r.get("time", "")))
        self.beginResetModel()
        self._visible = items
        self.endResetModel()

    def _touch(self) -> None:
        self._rebuild()
        self.countsChanged.emit()
        self.changed.emit()
        self._store.save()

    def reload(self) -> None:
        """重新按 store 里的提醒算一遍 _visible。见 TaskModel.reload。"""
        self._rebuild()
        self.countsChanged.emit()
        self.changed.emit()

    def _find(self, reminder_id: str) -> dict | None:
        for item in self._store.reminders:
            if item.get("id") == reminder_id:
                return item
        return None

    @Slot(str, str, str, int)
    def add(self, title: str, when: str, repeat: str = "daily",
            every: int = 0) -> None:
        title = (title or "").strip()
        if not title:
            return
        self._clear_undo()
        repeat = repeat if repeat in REPEAT_LABEL else "daily"
        date_part = None
        time_part = when or "09:00"
        if "T" in time_part:
            date_part, _, time_part = time_part.partition("T")
        elif " " in time_part:
            date_part, _, time_part = time_part.partition(" ")
        time_part = _normalize_clock(time_part[:5])

        entry = {
            "id": new_id("r"),
            "title": title[:80],
            "time": time_part,
            "date": date_part if repeat == "once" else None,
            "repeat": repeat,
            "enabled": True,
            "last_fired": None,
            "created": time.time(),
        }
        if repeat == "interval":
            entry["every"] = _clamp_interval(every)
            # 间隔重复不看 time，界面上那个时间框会被藏起来
            entry["date"] = None
        self._store.reminders.append(entry)
        del self._store.reminders[:-200]
        self._touch()

    @Slot(str)
    def remove(self, reminder_id: str) -> None:
        for index, reminder in enumerate(self._store.reminders):
            if reminder.get("id") != reminder_id:
                continue
            self._last_removed = {
                "reminder": dict(reminder),
                "index": index,
            }
            del self._store.reminders[index]
            self._touch()
            self.undoChanged.emit()
            return

    @Slot()
    def undoRemove(self) -> None:
        if self._last_removed is None:
            return
        removed = self._last_removed
        reminder = dict(removed.get("reminder", {}))
        reminder_id = reminder.get("id")
        if not reminder_id or self._find(reminder_id) is not None:
            self._last_removed = None
            self.undoChanged.emit()
            return
        index = max(0, min(len(self._store.reminders),
                           int(removed.get("index", 0))))
        self._store.reminders.insert(index, reminder)
        self._last_removed = None
        self._touch()
        self.undoChanged.emit()

    @Slot()
    def clearUndo(self) -> None:
        self._clear_undo()

    def _clear_undo(self) -> None:
        if self._last_removed is not None:
            self._last_removed = None
            self.undoChanged.emit()

    @Slot(str)
    def toggle(self, reminder_id: str) -> None:
        item = self._find(reminder_id)
        if item is None:
            return
        self._clear_undo()
        item["enabled"] = not item.get("enabled", True)
        # **重新开启时把触发标记清掉。**
        #
        # 间隔重复靠 last_fired 算「距上次多久」，不清的话：关了一小时
        # 再打开，会立刻触发一次（因为早就超过间隔了）。用户刚打开就被
        # 提醒，像是程序没听他的话。
        item["last_fired"] = None
        self._touch()

    @Slot(str, str, str, str, int)
    def update(self, reminder_id: str, title: str, when: str,
               repeat: str = "", every: int = 0) -> None:
        """改一条提醒。**重复方式也可以改** —— 原来只能改标题和时间。"""
        item = self._find(reminder_id)
        if item is None:
            return
        self._clear_undo()
        title = (title or "").strip()
        if title:
            item["title"] = title[:80]

        date_part = None
        time_part = when or item.get("time", "09:00")
        if "T" in time_part:
            date_part, _, time_part = time_part.partition("T")
        elif " " in time_part:
            date_part, _, time_part = time_part.partition(" ")
        fallback_time = _normalize_clock(
            item.get("time", DEFAULT_REMINDER_TIME)
        )
        item["time"] = _normalize_clock(time_part[:5], fallback_time)

        if repeat and repeat in REPEAT_LABEL and repeat != item.get("repeat"):
            item["repeat"] = repeat
            # 换了重复方式就要重新计时：原来「每天 09:00」的触发标记，
            # 对「每 5 分钟」来说是个没有意义的时间戳。
            item["last_fired"] = None
        if item.get("repeat") == "interval":
            item["every"] = _clamp_interval(every or item.get("every", 0))
            item["date"] = None
        elif item.get("repeat") == "once" and date_part:
            item["date"] = date_part
        self._touch()

    @Slot(str, int)
    def snooze(self, reminder_id: str, minutes: int) -> None:
        """「稍后提醒」：把这条压后 N 分钟再响一次。

        实现方式是**临时把 last_fired 往前推**，让间隔判定在 N 分钟后再
        满足一次 —— 没有引入新的状态字段。对固定时刻那几种（每天/工作日）
        走不通（它们看的是 clock），所以那些直接改成一次性提醒，定在
        N 分钟之后。

        这是刻意的取舍：为了一个「稍后」按钮去改所有重复方式的语义，
        不值得。一次性提醒的语义最直白 —— 用户点「稍后 10 分钟」，
        就是「10 分钟后再提醒我一次」。
        """
        item = self._find(reminder_id)
        if item is None:
            return
        self._clear_undo()
        minutes = max(1, min(24 * 60, int(minutes or 10)))

        if item.get("repeat") == "interval":
            # 把「上次触发」设成 N 分钟前 —— 那么再过 0 秒就到点了，
            # 不对。要的是「从现在起 N 分钟后再触发」，
            # 所以把 last_fired 设成「现在」往后推：让判定在 N 分钟后成立。
            target = time.time() + minutes * 60
            every = _clamp_interval(item.get("every", 0)) * 60
            item["last_fired"] = target - every
        else:
            # 固定时刻那几种：改成「N 分钟之后的一次性提醒」
            moment = datetime.now() + timedelta(minutes=minutes)
            item["repeat"] = "once"
            item["date"] = moment.strftime("%Y-%m-%d")
            item["time"] = moment.strftime("%H:%M")
            item["last_fired"] = None
        item["enabled"] = True
        self._touch()


# --------------------------------------------------------------------------
# 便签
# --------------------------------------------------------------------------
class NoteModel(QAbstractListModel):
    Roles = {
        Qt.UserRole + 1: b"noteId",
        Qt.UserRole + 2: b"title",
        Qt.UserRole + 3: b"text",
        Qt.UserRole + 4: b"updatedText",
        Qt.UserRole + 5: b"preview",
    }

    changed = Signal()
    currentChanged = Signal()
    undoChanged = Signal()

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._visible: list[dict] = []
        self._current = 0
        self._last_removed: dict | None = None
        self._ensure_seed()
        self._rebuild()

    def _ensure_seed(self) -> dict | None:
        if not self._store.notes:
            now = time.time()
            seed = {
                "id": new_id("n"), "title": "随手记", "text": "",
                "created": now, "updated": now,
            }
            self._store.notes.append(seed)
            return seed
        return None

    def roleNames(self):
        return self.Roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible)):
            return None
        note = self._visible[index.row()]
        text = note.get("text", "")
        mapping = {
            Qt.UserRole + 1: note.get("id", ""),
            Qt.UserRole + 2: note.get("title", "") or "无标题",
            Qt.UserRole + 3: text,
            Qt.UserRole + 4: _age_text(note.get("updated")) or "刚刚",
            Qt.UserRole + 5: (text.strip().splitlines() or [""])[0][:40] if text.strip() else "空白便签",
        }
        return mapping.get(role)

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._visible)

    @Property(int, notify=changed)
    def currentIndex(self) -> int:
        return self._current

    @currentIndex.setter
    def currentIndex(self, value: int) -> None:
        value = max(0, min(len(self._visible) - 1, int(value))) if self._visible else 0
        if value != self._current:
            self._current = value
            self.currentChanged.emit()

    @Property(str, notify=currentChanged)
    def currentId(self) -> str:
        if 0 <= self._current < len(self._visible):
            return self._visible[self._current].get("id", "")
        return ""

    @Property(str, notify=currentChanged)
    def currentTitle(self) -> str:
        if 0 <= self._current < len(self._visible):
            return self._visible[self._current].get("title", "") or "无标题"
        return ""

    @Property(str, notify=currentChanged)
    def currentText(self) -> str:
        if 0 <= self._current < len(self._visible):
            return self._visible[self._current].get("text", "")
        return ""

    @Property(bool, notify=undoChanged)
    def canUndo(self) -> bool:
        return self._last_removed is not None

    @Property(str, notify=undoChanged)
    def lastRemovedTitle(self) -> str:
        if self._last_removed is None:
            return ""
        return str(self._last_removed.get("note", {}).get("title", "无标题"))

    def _rebuild(self) -> None:
        selected_id = ""
        if 0 <= self._current < len(self._visible):
            selected_id = str(self._visible[self._current].get("id") or "")
        items = sorted(self._store.notes, key=lambda n: -(n.get("updated") or 0))
        self.beginResetModel()
        self._visible = items
        self.endResetModel()
        if selected_id:
            self._current = next(
                (index for index, item in enumerate(self._visible)
                 if item.get("id") == selected_id),
                max(0, min(self._current, len(self._visible) - 1)),
            )
        elif self._current >= len(self._visible):
            self._current = max(0, len(self._visible) - 1)
        self.currentChanged.emit()

    def _touch(self) -> None:
        self._rebuild()
        self.changed.emit()
        self._store.save()

    def reload(self) -> None:
        """重新按 store 里的便签算一遍 _visible。见 TaskModel.reload。

        比别的模型多一步 `_ensure_seed()`：导入的备份里如果一条便签都没有
        （用户就是没写过），界面会停在一个空编辑器上，连「新建」的落点都
        找不到。_ensure_seed 保证至少有一条可以写的便签。
        """
        self._clear_undo()
        self._ensure_seed()
        self._current = 0
        self._rebuild()
        self.changed.emit()

    @Slot(str, str)
    def add(self, title: str = "", text: str = "") -> None:
        self._clear_undo()
        now = time.time()
        self._store.notes.append({
            "id": new_id("n"),
            "title": (title or "").strip()[:60] or "新便签",
            "text": text or "",
            "created": now,
            "updated": now,
        })
        del self._store.notes[:-200]
        self._touch()
        self._current = 0
        self.currentChanged.emit()

    @Slot(str)
    def remove(self, note_id: str) -> None:
        for index, note in enumerate(self._store.notes):
            if note.get("id") != note_id:
                continue
            self._last_removed = {"note": dict(note), "index": index}
            del self._store.notes[index]
            seed = self._ensure_seed()
            if seed is not None:
                self._last_removed["seed_id"] = seed.get("id", "")
            self._touch()
            self.undoChanged.emit()
            return

    @Slot()
    def undoRemove(self) -> None:
        if self._last_removed is None:
            return
        removed = self._last_removed
        note = dict(removed.get("note", {}))
        note_id = note.get("id")
        if not note_id or any(item.get("id") == note_id for item in self._store.notes):
            self._clear_undo()
            return
        seed_id = removed.get("seed_id")
        if seed_id:
            self._store.notes[:] = [
                item for item in self._store.notes
                if item.get("id") != seed_id
            ]
        index = max(0, min(len(self._store.notes), int(removed.get("index", 0))))
        self._store.notes.insert(index, note)
        self._last_removed = None
        self._touch()
        for visible_index, item in enumerate(self._visible):
            if item.get("id") == note_id:
                self._current = visible_index
                break
        self.currentChanged.emit()
        self.undoChanged.emit()

    @Slot()
    def clearUndo(self) -> None:
        self._clear_undo()

    def _clear_undo(self) -> None:
        if self._last_removed is not None:
            self._last_removed = None
            self.undoChanged.emit()

    @Slot(str, str, str)
    def update(self, note_id: str, title: str, text: str) -> None:
        self._clear_undo()
        for note in self._store.notes:
            if note.get("id") == note_id:
                note["title"] = (title or "").strip()[:60] or "无标题"
                note["text"] = text or ""
                note["updated"] = time.time()
                self._touch()
                return

    @Slot(str, result=str)
    def textOf(self, note_id: str) -> str:
        for note in self._store.notes:
            if note.get("id") == note_id:
                return note.get("text", "")
        return ""

    @Slot(str, result=str)
    def titleOf(self, note_id: str) -> str:
        for note in self._store.notes:
            if note.get("id") == note_id:
                return note.get("title", "")
        return ""


# --------------------------------------------------------------------------
# 最近专注记录
# --------------------------------------------------------------------------
class SessionModel(QAbstractListModel):
    Roles = {
        Qt.UserRole + 1: b"sessionId",
        Qt.UserRole + 2: b"label",
        Qt.UserRole + 3: b"detail",
        Qt.UserRole + 4: b"minutes",
    }

    changed = Signal()

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._visible: list[dict] = []
        self.refresh()

    def roleNames(self):
        return self.Roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible)):
            return None
        item = self._visible[index.row()]
        start = item.get("start") or 0
        mapping = {
            Qt.UserRole + 1: item.get("id", ""),
            Qt.UserRole + 2: datetime.fromtimestamp(start).strftime("%m-%d %H:%M") if start else "",
            Qt.UserRole + 3: item.get("label") or "专注",
            Qt.UserRole + 4: int(item.get("minutes", 0)),
        }
        return mapping.get(role)

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._visible)

    @Slot()
    def refresh(self) -> None:
        sessions = self._store.state.get("sessions") or []
        self.beginResetModel()
        self._visible = list(reversed(sessions[-60:]))
        self.endResetModel()
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def recent(self) -> list:
        """给 QML 用的简易列表，省得在界面上处理 ModelIndex。"""
        out = []
        for item in reversed((self._store.state.get("sessions") or [])[-12:]):
            start = item.get("start") or 0
            out.append({
                "when": datetime.fromtimestamp(start).strftime("%m-%d %H:%M") if start else "",
                "label": ("离线补记 · " if item.get("offline") else "") + (item.get("label") or "专注"),
                "minutes": int(item.get("minutes", 0)),
            })
        return out


# --------------------------------------------------------------------------
# 近 7 天统计
# --------------------------------------------------------------------------
class WeekModel(QAbstractListModel):
    Roles = {
        Qt.UserRole + 1: b"dayLabel",
        Qt.UserRole + 2: b"minutes",
        Qt.UserRole + 3: b"ratio",
        Qt.UserRole + 4: b"isToday",
    }

    changed = Signal()
    maxMinutesChanged = Signal()

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._visible: list[dict] = []
        self._max = 1
        self.refresh()

    def roleNames(self):
        return self.Roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible)):
            return None
        item = self._visible[index.row()]
        mapping = {
            Qt.UserRole + 1: item["label"],
            Qt.UserRole + 2: item["minutes"],
            Qt.UserRole + 3: item["minutes"] / max(1, self._max),
            Qt.UserRole + 4: item["today"],
        }
        return mapping.get(role)

    @Property(int, notify=maxMinutesChanged)
    def maxMinutes(self) -> int:
        return self._max

    @Slot()
    def refresh(self) -> None:
        from .store import WEEKDAYS

        stats = self._store.stats
        today = datetime.now().date()
        items = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            entry = stats.get(day.isoformat()) or {}
            minutes = int(entry.get("focus_minutes", 0))
            items.append({
                "label": "日一二三四五六"[day.weekday()],
                "minutes": minutes,
                "today": offset == 0,
            })
        self._max = max([i["minutes"] for i in items] + [1])
        self.beginResetModel()
        self._visible = items
        self.endResetModel()
        self.changed.emit()
        self.maxMinutesChanged.emit()
