"""数据存储：原子写入 + 备份 + 版本迁移。

旧版 pet.py 直接 open(..., "w") 覆盖写，写到一半崩溃就整个文件损坏。
这里改成「写临时文件 → fsync → os.replace」，并在每次成功保存前保留一份备份。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import BACKUP_FILE, DATA_FILE

SCHEMA_VERSION = 2

DEFAULT_TASK_MINUTES = 25
MAX_TASKS = 2000

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def default_settings() -> dict:
    return {
        "focus_minutes": 25,
        "short_break_minutes": 5,
        "long_break_minutes": 15,
        "rounds_before_long_break": 4,
        "auto_start_next": True,
        "sound_enabled": True,
        "notify_enabled": True,
        "pet_scale": 1.0,
        "pet_opacity": 1.0,
        "always_on_top": True,
        "fade_when_idle": True,
        "autostart": False,
        "move_step": 24,
        "hotkey_dashboard": "ctrl+alt+p",
        "hotkey_focus": "ctrl+alt+t",
        "hotkey_task": "ctrl+alt+n",
        "sit_reminder_minutes": 45,
        "sit_reminder_enabled": True,
        "afk_minutes": 5,
        "daily_goal_minutes": 120,
        "quiet_when_fullscreen": True,
        "clock_format": "%H:%M",
        # ---- AI 操作模块 ----
        "ai_level": "confirm",          # read_only | confirm | auto | full
        "ai_max_steps": 20,
        "ai_memory_enabled": True,      # 跨会话记忆：记住习惯和进度
        "ai_auto_screenshot": True,     # 每轮开始自动截一张给模型
        "ai_show_cursor": True,         # 截图时把鼠标位置标出来
        "ai_mcp_enabled": False,
        "ai_openai_model": "gpt-4.1-mini",
        "ai_openai_base": "https://api.openai.com/v1",
        # ---- 指令栏 ----
        "pet_click_action": "command",  # command（弹指令栏）| dashboard（开工作台）
        "command_bar_anchor": "pet",    # pet（跟着小爪）| bottom（屏幕底部居中）
        "hotkey_ask": "ctrl+alt+space",
        # ---- 宠物形象 ----
        "pet_style": "mochi",
    }


def default_state() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "pet_x": None,
        "pet_y": None,
        "tasks": [],
        "reminders": [],
        "notes": [],
        "sessions": [],
        "stats": {},
        # 跨会话记忆：用户习惯、叫法映射、上次做到哪。
        # 结构由 pawpet.ai.memory 定义，这里只占位 —— 数据层不认识业务字段，
        # 这样 memory 模块改结构时不用动 store。
        "memory": {},
        "settings": default_settings(),
        "focus": {
            "mode": "focus",           # focus | short_break | long_break
            "running": False,
            "end_epoch": 0.0,          # 运行时的墙钟截止时间（UNIX 秒）
            "remaining": 25 * 60,      # 暂停/空闲时的剩余秒数
            "round": 0,
            "total_minutes": 25,
        },
        "counters": {
            "focus_rounds": 0,
            "completed_total": 0,
            "sit_since_epoch": time.time(),
        },
    }


class Store:
    """线程安全的数据仓库。所有修改最后都要调用 save()。"""

    def __init__(self, path: Path = DATA_FILE, backup: Path = BACKUP_FILE) -> None:
        self.path = Path(path)
        self.backup_path = Path(backup)
        self._lock = threading.RLock()
        self._dirty = False
        self.state: dict = default_state()
        self.migrated_from: str | None = None
        # 主文件读失败时的原始异常信息，用来给用户一个准确的解释
        self.load_error: str = ""

    # ------------------------------------------------------------------ 读写
    def load(self) -> dict:
        with self._lock:
            raw = self._read_json(self.path)
            if raw is None:
                # 主文件确实存在却读不出来，说明它真的坏了（或者不是合法 JSON）。
                # 这时才回退到备份，并且记录下来让界面能提示用户。
                if self.path.exists():
                    self.load_error = "主数据文件无法解析"
                raw = self._read_json(self.backup_path)
                if raw is not None:
                    self.migrated_from = "backup"
            if raw is None:
                self.state = default_state()
                return self.state
            self.state = self._migrate(raw)
            return self.state

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        """读 JSON。

        用 utf-8-sig 而不是 utf-8：记事本、「另存为 UTF-8」以及很多编辑器
        都会在文件开头写一个 BOM，而 Python 的 json.load 遇到 BOM 会直接
        抛 JSONDecodeError。那样主文件就会被误判成「损坏」，程序静默回退到
        备份 —— 用户最近改的东西就凭空消失了，而且没有任何提示。
        utf-8-sig 在没有 BOM 时行为与 utf-8 完全一致，所以是纯收益。
        """
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------------------ 迁移
    def _migrate(self, raw: dict) -> dict:
        version = int(raw.get("schema") or 0)

        if version < 2:
            # v1 = 旧 pet.py 的扁平结构：tasks/note/focus_seconds/timer_mode/break_minutes
            self.migrated_from = self.migrated_from or "v1"
            state = default_state()
            settings = state["settings"]

            tasks = []
            for item in raw.get("tasks") or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                tasks.append({
                    "id": _new_id("t"),
                    "text": text[:200],
                    "done": bool(item.get("done")),
                    "created": time.time(),
                    "done_at": time.time() if item.get("done") else None,
                    "priority": 0,
                    "due": None,
                })
            state["tasks"] = tasks

            note = str(raw.get("note") or "").strip()
            if note:
                state["notes"] = [{
                    "id": _new_id("n"),
                    "title": "旧版便笺",
                    "text": note,
                    "created": time.time(),
                    "updated": time.time(),
                }]

            if raw.get("break_minutes"):
                try:
                    settings["sit_reminder_minutes"] = max(5, min(480, int(raw["break_minutes"])))
                except (TypeError, ValueError):
                    pass

            # 旧版没有墙钟，用剩余秒数恢复成「暂停中的番茄钟」
            remaining = raw.get("focus_seconds")
            if isinstance(remaining, (int, float)) and remaining > 0:
                state["focus"]["remaining"] = float(remaining)
                state["focus"]["running"] = False
                state["focus"]["total_minutes"] = max(1, int(round(remaining / 60)))

            state["pet_x"] = raw.get("pet_x")
            state["pet_y"] = raw.get("pet_y")
            return state

        # 当前版本：把缺的键补上（前向兼容新增字段）
        state = default_state()
        state.update({k: v for k, v in raw.items() if v is not None or k in ("pet_x", "pet_y")})
        merged_settings = default_settings()
        merged_settings.update(raw.get("settings") or {})
        state["settings"] = merged_settings
        merged_focus = default_state()["focus"]
        merged_focus.update(raw.get("focus") or {})
        state["focus"] = merged_focus
        merged_counters = default_state()["counters"]
        merged_counters.update(raw.get("counters") or {})
        state["counters"] = merged_counters
        for key in ("tasks", "reminders", "notes", "sessions"):
            if not isinstance(state.get(key), list):
                state[key] = []
        if not isinstance(state.get("stats"), dict):
            state["stats"] = {}
        # 老版本没有 memory 这个键，补一个空字典让上层自己去解析
        if not isinstance(state.get("memory"), dict):
            state["memory"] = {}
        state["schema"] = SCHEMA_VERSION
        return state

    # ------------------------------------------------------------------ 保存
    def save(self) -> bool:
        with self._lock:
            payload = copy.deepcopy(self.state)
        payload["schema"] = SCHEMA_VERSION
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            # 保存前留一份上一版备份
            if self.path.exists():
                try:
                    with open(self.path, "rb") as src:
                        previous = src.read()
                    if previous.strip():
                        with open(self.backup_path, "wb") as dst:
                            dst.write(previous)
                except OSError:
                    pass
            os.replace(tmp, self.path)
            self._dirty = False
            return True
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    # ------------------------------------------------------------- 属性访问
    @property
    def settings(self) -> dict:
        return self.state["settings"]

    @property
    def focus(self) -> dict:
        return self.state["focus"]

    @property
    def counters(self) -> dict:
        return self.state["counters"]

    @property
    def tasks(self) -> list:
        return self.state["tasks"]

    @property
    def reminders(self) -> list:
        return self.state["reminders"]

    @property
    def notes(self) -> list:
        return self.state["notes"]

    @property
    def stats(self) -> dict:
        return self.state["stats"]

    @property
    def memory(self) -> dict:
        """跨会话记忆的原始字典。解析和语义都在 pawpet.ai.memory 里。"""
        return self.state.setdefault("memory", {})


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _new_id(prefix: str) -> str:
    return f"{prefix}{int(time.time() * 1000):x}{os.urandom(2).hex()}"


def new_id(prefix: str) -> str:
    return _new_id(prefix)


def today_key(offset_days: int = 0) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


def pretty_day(key: str) -> str:
    try:
        day = datetime.strptime(key, "%Y-%m-%d").date()
    except ValueError:
        return key
    suffix = " · 今天" if day == date.today() else (" · 昨天" if day == date.today() - timedelta(days=1) else "")
    return f"{day.month} 月 {day.day} 日 {WEEKDAYS[day.weekday()]}{suffix}"


def humanize_gap(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时 {minutes % 60} 分钟"
    return f"{hours // 24} 天"


def clock_text(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
