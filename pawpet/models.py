"""QML 数据模型：待办、提醒、便签、专注记录。

这些模型直接持有 Store 里的列表（同一个对象引用），改动后就地生效并通知 QML，
最后统一由 Store.save() 落盘。只有真正发生数据变化时才发信号，因此不会再出现
旧版「每秒重建整个待办列表」导致滚动条和输入焦点被打断的问题。
"""

from __future__ import annotations

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

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._show_done = False
        self._sort = "smart"          # smart | created | priority
        self._visible: list[dict] = []
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
    @Property(int, notify=countsChanged)
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

    # ----------------------------------------------------------- 内部逻辑
    def _rebuild(self) -> None:
        items = [t for t in self._store.tasks if self._show_done or not t.get("done")]

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
        task["done"] = not task.get("done")
        task["done_at"] = time.time() if task["done"] else None
        if task["done"]:
            self._store.stats.setdefault(today_key(), {})
            day = self._store.stats[today_key()]
            day["tasks_done"] = int(day.get("tasks_done", 0)) + 1
        row = self._index_of(task_id)
        if row >= 0 and (self._show_done or not task["done"]):
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)
            self.countsChanged.emit()
            self.changed.emit()
            self._store.save()
        else:
            self._touch()

    @Slot(str)
    def remove(self, task_id: str) -> None:
        before = len(self._store.tasks)
        self._store.tasks[:] = [t for t in self._store.tasks if t.get("id") != task_id]
        if len(self._store.tasks) != before:
            self._touch()

    @Slot(str, str)
    def rename(self, task_id: str, text: str) -> None:
        task = self._find(task_id)
        text = (text or "").strip()
        if task is None or not text:
            return
        task["text"] = text[:200]
        row = self._index_of(task_id)
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)
        self.changed.emit()
        self._store.save()

    @Slot(str, int)
    def setPriority(self, task_id: str, priority: int) -> None:
        task = self._find(task_id)
        if task is None:
            return
        task["priority"] = max(0, min(2, int(priority)))
        self._touch()

    @Slot(str, str)
    def setDue(self, task_id: str, due: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        task["due"] = due or None
        self._touch()

    @Slot(str)
    def bumpPomodoro(self, task_id: str) -> None:
        task = self._find(task_id)
        if task is None:
            return
        task["pomodoros"] = int(task.get("pomodoros", 0)) + 1
        row = self._index_of(task_id)
        if row >= 0:
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx)
        self._store.save()

    @Slot()
    def clearDone(self) -> None:
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
}


class ReminderModel(QAbstractListModel):
    Roles = {
        Qt.UserRole + 1: b"reminderId",
        Qt.UserRole + 2: b"title",
        Qt.UserRole + 3: b"time",
        Qt.UserRole + 4: b"repeat",
        Qt.UserRole + 5: b"repeatLabel",
        Qt.UserRole + 6: b"enabled",
        Qt.UserRole + 7: b"nextText",
    }

    changed = Signal()
    countsChanged = Signal()

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
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
        }
        return mapping.get(role)

    @staticmethod
    def _next_text(item: dict) -> str:
        if not item.get("enabled", True):
            return "已关闭"
        spec = str(item.get("time", "09:00"))
        repeat = item.get("repeat", "once")
        if repeat == "once":
            day = item.get("date")
            return f"{day} {spec}" if day else spec
        return f"每天 {spec}" if repeat == "daily" else (
            f"工作日 {spec}" if repeat == "weekdays" else f"每周 {spec}")

    @Property(int, notify=countsChanged)
    def count(self) -> int:
        return len(self._visible)

    @Property(int, notify=countsChanged)
    def activeCount(self) -> int:
        return sum(1 for r in self._store.reminders if r.get("enabled", True))

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

    def _find(self, reminder_id: str) -> dict | None:
        for item in self._store.reminders:
            if item.get("id") == reminder_id:
                return item
        return None

    @Slot(str, str, str)
    def add(self, title: str, when: str, repeat: str = "daily") -> None:
        title = (title or "").strip()
        if not title:
            return
        repeat = repeat if repeat in REPEAT_LABEL else "daily"
        date_part = None
        time_part = when or "09:00"
        if "T" in time_part:
            date_part, _, time_part = time_part.partition("T")
        elif " " in time_part:
            date_part, _, time_part = time_part.partition(" ")
        time_part = time_part[:5] or "09:00"
        self._store.reminders.append({
            "id": new_id("r"),
            "title": title[:80],
            "time": time_part,
            "date": date_part if repeat == "once" else None,
            "repeat": repeat,
            "enabled": True,
            "last_fired": None,
            "created": time.time(),
        })
        del self._store.reminders[:-200]
        self._touch()

    @Slot(str)
    def remove(self, reminder_id: str) -> None:
        before = len(self._store.reminders)
        self._store.reminders[:] = [r for r in self._store.reminders if r.get("id") != reminder_id]
        if len(self._store.reminders) != before:
            self._touch()

    @Slot(str)
    def toggle(self, reminder_id: str) -> None:
        item = self._find(reminder_id)
        if item is None:
            return
        item["enabled"] = not item.get("enabled", True)
        item["last_fired"] = None
        self._touch()

    @Slot(str, str, str)
    def update(self, reminder_id: str, title: str, when: str) -> None:
        item = self._find(reminder_id)
        if item is None:
            return
        title = (title or "").strip()
        if title:
            item["title"] = title[:80]
        date_part = None
        time_part = when or item.get("time", "09:00")
        if "T" in time_part:
            date_part, _, time_part = time_part.partition("T")
        elif " " in time_part:
            date_part, _, time_part = time_part.partition(" ")
        item["time"] = time_part[:5] or "09:00"
        if item.get("repeat") == "once" and date_part:
            item["date"] = date_part
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

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._visible: list[dict] = []
        self._current = 0
        self._ensure_seed()
        self._rebuild()

    def _ensure_seed(self) -> None:
        if not self._store.notes:
            now = time.time()
            self._store.notes.append({
                "id": new_id("n"), "title": "随手记", "text": "",
                "created": now, "updated": now,
            })

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

    def _rebuild(self) -> None:
        items = sorted(self._store.notes, key=lambda n: -(n.get("updated") or 0))
        self.beginResetModel()
        self._visible = items
        self.endResetModel()
        if self._current >= len(self._visible):
            self._current = max(0, len(self._visible) - 1)
        self.currentChanged.emit()

    def _touch(self) -> None:
        self._rebuild()
        self.changed.emit()
        self._store.save()

    @Slot(str, str)
    def add(self, title: str = "", text: str = "") -> None:
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
        before = len(self._store.notes)
        self._store.notes[:] = [n for n in self._store.notes if n.get("id") != note_id]
        if len(self._store.notes) == before:
            return
        self._ensure_seed()
        self._touch()

    @Slot(str, str, str)
    def update(self, note_id: str, title: str, text: str) -> None:
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
