"""数据存储：原子写入 + 备份 + 版本迁移。

旧版 pet.py 直接 open(..., "w") 覆盖写，写到一半崩溃就整个文件损坏。
这里改成「写临时文件 → fsync → os.replace」，并在每次成功保存前保留一份备份。
"""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
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
        # 界面整体缩放。0 = 自动（按屏幕分辨率选）。
        # 高分屏上字号原来显得太小，用户能自己调大。
        "ui_scale": 0.0,
        "always_on_top": True,
        "fade_when_idle": True,
        "autostart": False,
        # 高级模式。默认关 —— 关着的时候设置面板只显示泛用户看得懂的东西
        # （AI 页的知识库、外部工具 MCP 配置会藏起来）。
        # 判断口径是「第一次打开会不会看不懂」，不是「我用不用得上」。
        "advanced_mode": False,
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
        # 外部工具（MCP）默认打开。
        #
        # 随包带了一个 pawkit server，给的是「现在几点 / 算个数 / 生成密码 /
        # 算文件校验值」这类**纯计算或只读**的工具，没有任何写操作 ——
        # 默认关着的话，这套东西用户永远看不到，等于白做。
        #
        # 想关随时能在设置里关掉；老用户的设置不会被这行影响
        # （_migrate 是「默认值打底、用户已存的覆盖」）。
        "ai_mcp_enabled": True,
        # 默认模型给 DeepSeek，不给 OpenAI。
        #
        # 这是**泛用户第一次打开时的值**，而 OpenAI 对国内用户是双重门槛：
        # 要能上外网、要绑信用卡。DeepSeek 手机号注册就能用、国内直连、
        # 单价也低 —— 默认值选错了，用户第一步就走不下去。
        #
        # 注意 _migrate 是「默认值打底、用户已存的覆盖」，所以改这里
        # **不会**动到老用户已经配好的地址。
        "ai_openai_model": "deepseek-flash",
        "ai_openai_base": "https://api.deepseek.com/v1",
        # ---- 指令栏 ----
        "pet_click_action": "command",  # command（弹指令栏）| dashboard（开工作台）
        "command_bar_anchor": "pet",    # pet（跟着小爪）| bottom（屏幕底部居中）
        "hotkey_ask": "ctrl+alt+space",
        # ---- 宠物形象 ----
        "pet_style": "mochi",
        # ---- 贴边 ----
        # 拖到屏幕边缘附近时吸附过去，并有一半藏在屏幕外；鼠标移到那条边
        # 附近自动滑出来，移开再滑回去。
        #
        # 默认开：桌面宠物常驻在屏幕上，贴边是省地方的主要手段，而用户
        # 抱怨过「很占视野」。想固定摆在某处的人可以在设置里关掉。
        "pet_snap_enabled": True,
        # 拖到离边缘多近时触发吸附。做成设置项是因为「多近算近」很个人：
        # 有人喜欢一拖就吸（宽），有人嫌误触（窄）。
        "pet_snap_distance": 40,
        # 吸附后藏在屏幕外的比例。0.5 = 露一半。
        #
        # 不暴露到界面上：这是一个「调好了就不用动」的视觉参数，
        # 多一个滑块只会让设置页更难看懂。要改就改这里。
        "pet_snap_hide_ratio": 0.5,
        # 贴边时换个姿势（侧躺 / 倒挂），而不是直挺挺地藏一半。
        #
        # 这不只是好看：直挺挺藏一半看起来像**被切掉了**，而转个角度之后
        # 同一个「只露一部分」读起来是「故意趴在那儿」，更可爱也更不占地方。
        # 旋转是纯图形变换，宠物自带的呼吸、眨眼、摇尾全都继续生效。
        "pet_edge_pose": True,
        # ---- 首次启动引导 ----
        # 引导只该出现一次。用设置项而不是「检测数据文件是否为空」：
        # 老用户升级上来时数据文件是满的，但引导从没看过 —— 按数据判空
        # 会让他们永远看不到（或者反过来，删掉数据就重新弹）。
        # 设置项语义明确：用户点过「开始用」或「跳过」就置真，不再打扰。
        # 设置页里可以手动再打开一次。
        "onboarding_done": False,
        # ---- 检查更新 ----
        # 默认开。这是唯一一个会因为小爪自身而上网的设置项，所以要能关，
        # 而且文案里必须说清「只发了什么」—— 桌面宠物用户对偷偷联网敏感。
        # 检查请求只带版本号，不带任何用户数据。
        "update_check": True,
        # 上次检查的时间戳。用来做「一天最多查一次」的节流。
        "update_last_check": 0.0,
        # 上次检查是否成功。旧版本没有这个字段，默认按历史成功处理；
        # 新用户在时间戳为 0 时不会显示状态。
        "update_last_check_ok": True,
        # 用户点过「跳过这个版本」的那个版本号。
        # 存下来是为了不反复提醒同一个版本 —— 被同一个弹窗烦第三次的用户
        # 会去关掉整个检查功能，那比没这功能还糟。
        "update_skipped_version": "",
    }


def default_state() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "pet_x": None,
        "pet_y": None,
        # 贴边的状态："" | "left" | "right" | "top" | "bottom"。
        #
        # **存边缘而不是存坐标**：贴边时窗口有一半在屏幕外，存下来的坐标
        # 是个负数或者超出屏幕的值，下次启动直接用它反而会出问题
        # （换分辨率、换显示器就更对不上）。存「贴的是哪条边」，
        # 启动时按当前的屏幕尺寸重新算位置，换显示器也不会错。
        "pet_edge": "",
        "tasks": [],
        "reminders": [],
        "notes": [],
        "sessions": [],
        "stats": {},
        # 跨会话记忆：用户习惯、叫法映射、上次做到哪。
        # 结构由 pawpet.ai.memory 定义，这里只占位 —— 数据层不认识业务字段，
        # 这样 memory 模块改结构时不用动 store。
        "memory": {},
        # 知识库：用户导入的资料（切块后的文本）。
        # 同样只占位，结构在 pawpet.ai.kb 里定义。
        "knowledge": {},
        "settings": default_settings(),
        "focus": {
            "mode": "focus",           # focus | short_break | long_break
            "running": False,
            "end_epoch": 0.0,          # 运行时的墙钟截止时间（UNIX 秒）
            "remaining": 25 * 60,      # 暂停/空闲时的剩余秒数
            "round": 0,
            "total_minutes": 25,
            # 当前专注轮关联的待办。空串表示不关联。
            "task_id": "",
            # 专注完成后待记入的番茄，先落盘再由 Backend 消费，
            # 这样程序恰好在结算后退出也不会丢统计。
            "pending_task_pomodoro": "",
        },
        "counters": {
            "focus_rounds": 0,
            "completed_total": 0,
            "sit_since_epoch": time.time(),
        },
    }


def _safe_int(value, default: int | None = 0, low: int | None = None,
              high: int | None = None) -> int | None:
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = default
    if result is None:
        return None
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _safe_float(value, default: float = 0.0, low: float | None = None,
                high: float | None = None) -> float:
    try:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _safe_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "是", "开"}:
            return True
        if text in {"0", "false", "no", "off", "否", "关"}:
            return False
    return default


def _safe_text(value, default: str = "", limit: int | None = None) -> str:
    if isinstance(value, bool):
        return default
    if not isinstance(value, (str, int, float)):
        return default
    text = str(value).strip()
    if not text:
        return default
    return text[:limit] if limit is not None else text


def _safe_timestamp(value, default: float | None = None) -> float | None:
    result = _safe_float(value, float("nan"))
    if not math.isfinite(result) or result < 0 or result > 4102444800:
        return default
    return result


def _safe_date(value: object) -> str | None:
    text = _safe_text(value, "")
    if not text:
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _safe_clock(value: object, default: str = "09:00") -> str:
    text = _safe_text(value, "")
    if not text:
        return default
    parts = text.split(":", 1)
    if len(parts) != 2:
        return default
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return f"{hour:02d}:{minute:02d}"


def _safe_schema(value: object) -> tuple[int, bool]:
    if value is None:
        return 0, False
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError
        version = int(value)
    except (TypeError, ValueError, OverflowError):
        return SCHEMA_VERSION, True
    if version < 0:
        return SCHEMA_VERSION, True
    return version, False


def _different(raw, normalized) -> bool:
    try:
        return raw != normalized
    except Exception:  # noqa: BLE001
        return True


def _normalize_settings(raw_settings: object) -> tuple[dict, bool]:
    repaired = raw_settings is not None and not isinstance(raw_settings, dict)
    defaults = default_settings()
    settings = dict(defaults)
    if isinstance(raw_settings, dict):
        settings.update(raw_settings)

    int_limits = {
        "focus_minutes": (1, 240),
        "short_break_minutes": (1, 240),
        "long_break_minutes": (1, 240),
        "rounds_before_long_break": (1, 12),
        "move_step": (1, 200),
        "sit_reminder_minutes": (5, 480),
        "afk_minutes": (1, 120),
        "daily_goal_minutes": (1, 1440),
        "ai_max_steps": (1, 100),
        "pet_snap_distance": (1, 300),
    }
    float_limits = {
        "pet_scale": (0.6, 2.4),
        "pet_opacity": (0.05, 1.0),
        "ui_scale": (0.0, 3.0),
        "pet_snap_hide_ratio": (0.0, 0.95),
        "update_last_check": (0.0, 4102444800.0),
    }
    enum_values = {
        "ai_level": {"read_only", "confirm", "auto", "full"},
        "pet_click_action": {"command", "dashboard"},
        "command_bar_anchor": {"pet", "bottom"},
        "ai_extension_level": {"note", "recipe", "code"},
    }
    extra_ints = {
        "ai_timeout": (5, 600, 90),
        "ai_monitor": (1, 16, 1),
    }

    for key, default in defaults.items():
        if key not in settings:
            continue
        raw_value = settings[key]
        if isinstance(default, bool):
            normalized = _safe_bool(raw_value, default)
        elif isinstance(default, int):
            low, high = int_limits.get(key, (None, None))
            normalized = _safe_int(raw_value, default, low, high)
        elif isinstance(default, float):
            low, high = float_limits.get(key, (None, None))
            normalized = _safe_float(raw_value, default, low, high)
        else:
            normalized = _safe_text(raw_value, default)
        if key in enum_values and normalized not in enum_values[key]:
            normalized = default
        if _different(raw_value, normalized):
            repaired = True
        settings[key] = normalized

    for key, (low, high, default) in extra_ints.items():
        if key not in settings:
            continue
        raw_value = settings[key]
        normalized = _safe_int(raw_value, default, low, high)
        if _different(raw_value, normalized):
            repaired = True
        settings[key] = normalized

    return settings, repaired


def _normalize_task(raw: dict, now: float, used: set[str]) -> tuple[dict | None, bool]:
    repaired = False
    item = dict(raw)
    raw_id = raw.get("id")
    task_id = _safe_text(raw_id, "")
    if not task_id or task_id in used:
        task_id = _new_id("t")
        repaired = True
    elif _different(raw_id, task_id):
        repaired = True
    used.add(task_id)

    raw_text = raw.get("text")
    text = _safe_text(raw_text, "", 200)
    if not text:
        return None, True
    if _different(raw_text, text):
        repaired = True

    done = _safe_bool(raw.get("done"), False)
    if "done" in raw and _different(raw.get("done"), done):
        repaired = True
    created = _safe_timestamp(raw.get("created"), now)
    if "created" in raw and _different(raw.get("created"), created):
        repaired = True
    priority = _safe_int(raw.get("priority"), 0, 0, 2)
    if "priority" in raw and _different(raw.get("priority"), priority):
        repaired = True
    pomodoros = _safe_int(raw.get("pomodoros"), 0, 0, 999999)
    if "pomodoros" in raw and _different(raw.get("pomodoros"), pomodoros):
        repaired = True
    if done:
        done_at = _safe_timestamp(raw.get("done_at"), created)
    else:
        done_at = None
    if "done_at" in raw and _different(raw.get("done_at"), done_at):
        repaired = True
    due = _safe_date(raw.get("due"))
    if "due" in raw and _different(raw.get("due"), due):
        repaired = True

    item.update({
        "id": task_id,
        "text": text,
        "done": done,
        "created": created,
        "done_at": done_at,
        "priority": priority,
        "due": due,
        "pomodoros": pomodoros,
    })
    return item, repaired


def _normalize_tasks(raw_items: object, now: float) -> tuple[list, bool]:
    if not isinstance(raw_items, list):
        return [], raw_items is not None
    repaired = False
    used: set[str] = set()
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            repaired = True
            continue
        item, item_repaired = _normalize_task(raw, now, used)
        repaired = repaired or item_repaired
        if item is not None:
            items.append(item)
    if len(items) > MAX_TASKS:
        items = items[-MAX_TASKS:]
        repaired = True
    return items, repaired


def _normalize_reminder(raw: dict, now: float, used: set[str]) -> tuple[dict, bool]:
    repaired = False
    item = dict(raw)
    raw_id = raw.get("id")
    reminder_id = _safe_text(raw_id, "")
    if not reminder_id or reminder_id in used:
        reminder_id = _new_id("r")
        repaired = True
    elif _different(raw_id, reminder_id):
        repaired = True
    used.add(reminder_id)

    title = _safe_text(raw.get("title"), "提醒", 80)
    if "title" in raw and _different(raw.get("title"), title):
        repaired = True
    clock = _safe_clock(raw.get("time"), "09:00")
    if "time" in raw and _different(raw.get("time"), clock):
        repaired = True
    repeat = _safe_text(raw.get("repeat"), "once")
    if repeat not in {"once", "daily", "weekdays", "weekly", "interval"}:
        repeat = "once"
        repaired = True
    enabled = _safe_bool(raw.get("enabled"), True)
    if "enabled" in raw and _different(raw.get("enabled"), enabled):
        repaired = True
    created = _safe_timestamp(raw.get("created"), now)
    if "created" in raw and _different(raw.get("created"), created):
        repaired = True
    date_value = _safe_date(raw.get("date"))
    if repeat not in {"once", "weekly"}:
        date_value = None
    if "date" in raw and _different(raw.get("date"), date_value):
        repaired = True
    if repeat == "interval":
        last_fired = _safe_timestamp(raw.get("last_fired"), None)
        every = _safe_int(raw.get("every"), 30, 1, 720)
    else:
        last_fired = _safe_date(raw.get("last_fired"))
        every = 0
    if "last_fired" in raw and _different(raw.get("last_fired"), last_fired):
        repaired = True
    if "every" in raw and _different(raw.get("every"), every):
        repaired = True

    item.update({
        "id": reminder_id,
        "title": title,
        "time": clock,
        "date": date_value,
        "repeat": repeat,
        "enabled": enabled,
        "last_fired": last_fired,
        "created": created,
        "every": every,
    })
    return item, repaired


def _normalize_reminders(raw_items: object, now: float) -> tuple[list, bool]:
    if not isinstance(raw_items, list):
        return [], raw_items is not None
    repaired = False
    used: set[str] = set()
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            repaired = True
            continue
        item, item_repaired = _normalize_reminder(raw, now, used)
        repaired = repaired or item_repaired
        items.append(item)
    if len(items) > 200:
        items = items[-200:]
        repaired = True
    return items, repaired


def _normalize_note(raw: dict, now: float, used: set[str]) -> tuple[dict, bool]:
    repaired = False
    item = dict(raw)
    raw_id = raw.get("id")
    note_id = _safe_text(raw_id, "")
    if not note_id or note_id in used:
        note_id = _new_id("n")
        repaired = True
    elif _different(raw_id, note_id):
        repaired = True
    used.add(note_id)

    title = _safe_text(raw.get("title"), "无标题", 60)
    text = _safe_text(raw.get("text"), "")
    created = _safe_timestamp(raw.get("created"), now)
    updated = _safe_timestamp(raw.get("updated"), created)
    for key, normalized in (("title", title), ("text", text),
                            ("created", created), ("updated", updated)):
        if key in raw and _different(raw.get(key), normalized):
            repaired = True
    item.update({
        "id": note_id,
        "title": title,
        "text": text,
        "created": created,
        "updated": updated,
    })
    return item, repaired


def _normalize_notes(raw_items: object, now: float) -> tuple[list, bool]:
    if not isinstance(raw_items, list):
        return [], raw_items is not None
    repaired = False
    used: set[str] = set()
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            repaired = True
            continue
        item, item_repaired = _normalize_note(raw, now, used)
        repaired = repaired or item_repaired
        items.append(item)
    if len(items) > 200:
        items = items[-200:]
        repaired = True
    return items, repaired


def _normalize_session(raw: dict, now: float, used: set[str]) -> tuple[dict, bool]:
    repaired = False
    item = dict(raw)
    raw_id = raw.get("id")
    session_id = _safe_text(raw_id, "")
    if not session_id or session_id in used:
        session_id = _new_id("s")
        repaired = True
    elif _different(raw_id, session_id):
        repaired = True
    used.add(session_id)
    start = _safe_timestamp(raw.get("start"), 0.0)
    end = _safe_timestamp(raw.get("end"), start or now)
    minutes = _safe_int(raw.get("minutes"), 0, 0, 1440)
    label = _safe_text(raw.get("label"), "专注", 80)
    offline = _safe_bool(raw.get("offline"), False)
    for key, normalized in (("start", start), ("end", end),
                            ("minutes", minutes), ("label", label),
                            ("offline", offline)):
        if key in raw and _different(raw.get(key), normalized):
            repaired = True
    item.update({
        "id": session_id,
        "start": start,
        "end": end,
        "minutes": minutes,
        "label": label,
        "offline": offline,
    })
    return item, repaired


def _normalize_sessions(raw_items: object, now: float) -> tuple[list, bool]:
    if not isinstance(raw_items, list):
        return [], raw_items is not None
    repaired = False
    used: set[str] = set()
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            repaired = True
            continue
        item, item_repaired = _normalize_session(raw, now, used)
        repaired = repaired or item_repaired
        items.append(item)
    if len(items) > 500:
        items = items[-500:]
        repaired = True
    return items, repaired


def _normalize_stats(raw_stats: object) -> tuple[dict, bool]:
    if not isinstance(raw_stats, dict):
        return {}, raw_stats is not None
    repaired = False
    stats = {}
    for raw_day, raw_entry in raw_stats.items():
        day = _safe_text(raw_day, "")
        if not day:
            repaired = True
            continue
        if not isinstance(raw_entry, dict):
            stats[day] = {}
            repaired = True
            continue
        entry = dict(raw_entry)
        for key in ("focus_minutes", "focus_rounds", "tasks_done"):
            if key not in entry:
                continue
            normalized = _safe_int(entry[key], 0, 0, 100000000)
            if _different(entry[key], normalized):
                repaired = True
            entry[key] = normalized
        stats[day] = entry
        if _different(raw_day, day):
            repaired = True
    return stats, repaired


class Store:
    """线程安全的数据仓库。所有修改最后都要调用 save()。"""

    def __init__(self, path: Path = DATA_FILE, backup: Path = BACKUP_FILE) -> None:
        self.path = Path(path)
        self.backup_path = Path(backup)
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self.state: dict = default_state()
        self.migrated_from: str | None = None
        self.repaired: bool = False
        self._preserve_backup_once: bool = False
        # 主文件读失败时的原始异常信息，用来给用户一个准确的解释
        self.load_error: str = ""
        # 主文件无法解析时，会在保存前把原文留成一个独立快照，避免
        # 「没有备份 → 启动后第一次自动保存 → 唯一证据被覆盖」。
        self.corrupt_path: Path | None = None

    # ------------------------------------------------------------------ 读写
    def load(self) -> dict:
        with self._lock:
            self.repaired = False
            self._preserve_backup_once = False
            self.migrated_from = None
            self.load_error = ""
            self.corrupt_path = None
            raw = self._read_json(self.path)
            if raw is None:
                # 主文件确实存在却读不出来，说明它真的坏了（或者不是合法 JSON）。
                # 这时才回退到备份，并且记录下来让界面能提示用户。
                if self.path.exists():
                    self.load_error = "主数据文件无法解析"
                    self.corrupt_path = self._preserve_corrupt_file()
                    self._preserve_backup_once = True
                elif self.backup_path.exists():
                    self.load_error = "主数据文件不存在"
                raw = self._read_json(self.backup_path)
                if raw is not None:
                    self.migrated_from = "backup"
            if raw is None:
                self.state = default_state()
                return self.state
            self.state = self._migrate(raw)
            if self.repaired:
                self._preserve_backup_once = True
            return self.state

    def _preserve_corrupt_file(self) -> Path | None:
        """保存一份无法解析的主文件原文，避免后续 save() 覆盖证据。"""
        if not self.path.is_file():
            return None

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = self.path.stem or self.path.name
        suffix = self.path.suffix
        for index in range(1000):
            extra = "" if index == 0 else f"-{index}"
            candidate = self.path.with_name(
                f"{stem}.corrupt-{stamp}{extra}{suffix}"
            )
            try:
                with self.path.open("rb") as source, candidate.open("xb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                return candidate
            except FileExistsError:
                continue
            except OSError:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
        return None

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
        version, invalid_schema = _safe_schema(raw.get("schema"))
        self.repaired = invalid_schema

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
        # pet_x / pet_y 允许是 None（还没摆过位置），所以单独放行 ——
        # 通用的 `v is not None` 会把它们过滤掉，于是每次启动都回到默认角。
        state.update({k: v for k, v in raw.items()
                      if v is not None or k in ("pet_x", "pet_y")})
        raw_settings = raw.get("settings")
        merged_settings, settings_repaired = _normalize_settings(raw_settings)
        self.repaired = self.repaired or settings_repaired
        state["settings"] = merged_settings
        merged_focus = default_state()["focus"]
        raw_focus = raw.get("focus")
        if isinstance(raw_focus, dict):
            merged_focus.update(raw_focus)
        elif raw_focus is not None:
            self.repaired = True
        state["focus"] = merged_focus
        merged_counters = default_state()["counters"]
        raw_counters = raw.get("counters")
        if isinstance(raw_counters, dict):
            merged_counters.update(raw_counters)
        elif raw_counters is not None:
            self.repaired = True
        state["counters"] = merged_counters
        # 老版本没有 memory / knowledge 这两个键，补空字典让上层自己去解析
        if not isinstance(state.get("memory"), dict):
            state["memory"] = {}
            if raw.get("memory") is not None:
                self.repaired = True
        if not isinstance(state.get("knowledge"), dict):
            state["knowledge"] = {}
            if raw.get("knowledge") is not None:
                self.repaired = True

        state, state_repaired = self._normalize_state(state, raw)
        self.repaired = self.repaired or state_repaired
        state["schema"] = SCHEMA_VERSION
        return state

    def _normalize_state(self, state: dict, raw: dict | None = None) -> tuple[dict, bool]:
        source = raw if isinstance(raw, dict) else state
        now = time.time()
        repaired = False

        tasks, changed = _normalize_tasks(source.get("tasks"), now)
        state["tasks"] = tasks
        repaired = repaired or changed
        reminders, changed = _normalize_reminders(source.get("reminders"), now)
        state["reminders"] = reminders
        repaired = repaired or changed
        notes, changed = _normalize_notes(source.get("notes"), now)
        state["notes"] = notes
        repaired = repaired or changed
        sessions, changed = _normalize_sessions(source.get("sessions"), now)
        state["sessions"] = sessions
        repaired = repaired or changed
        stats, changed = _normalize_stats(source.get("stats"))
        state["stats"] = stats
        repaired = repaired or changed

        settings = state.get("settings")
        if not isinstance(settings, dict):
            settings = default_settings()
            repaired = True
        state["settings"], changed = _normalize_settings(settings)
        repaired = repaired or changed

        focus = dict(default_state()["focus"])
        raw_focus = source.get("focus")
        if isinstance(raw_focus, dict):
            focus.update(raw_focus)
        elif raw_focus is not None:
            repaired = True
        mode = _safe_text(focus.get("mode"), "focus")
        if mode not in {"focus", "short_break", "long_break"}:
            mode = "focus"
            repaired = True
        running = _safe_bool(focus.get("running"), False)
        if "running" in focus and _different(focus.get("running"), running):
            repaired = True
        remaining_default = max(1, _safe_int(
            state["settings"].get("focus_minutes"), 25, 1, 240)) * 60.0
        remaining = _safe_float(focus.get("remaining"), remaining_default, 0.0, 7 * 24 * 3600.0)
        total_minutes = _safe_int(focus.get("total_minutes"), 25, 1, 240)
        round_number = _safe_int(focus.get("round"), 0, 0, 100000000)
        end_epoch = _safe_timestamp(focus.get("end_epoch"), 0.0) or 0.0
        raw_end = focus.get("end_epoch")
        if running and (not raw_end or end_epoch <= 0):
            running = False
            end_epoch = 0.0
            repaired = True
        task_id = _safe_text(focus.get("task_id"), "")
        pending = _safe_text(focus.get("pending_task_pomodoro"), "")
        for key, normalized in (
            ("mode", mode), ("remaining", remaining),
            ("total_minutes", total_minutes), ("round", round_number),
            ("end_epoch", end_epoch), ("task_id", task_id),
            ("pending_task_pomodoro", pending),
        ):
            if key in focus and _different(focus.get(key), normalized):
                repaired = True
        focus.update({
            "mode": mode,
            "running": running,
            "end_epoch": end_epoch,
            "remaining": remaining,
            "round": round_number,
            "total_minutes": total_minutes,
            "task_id": task_id,
            "pending_task_pomodoro": pending,
        })
        state["focus"] = focus

        counters = dict(default_state()["counters"])
        raw_counters = source.get("counters")
        if isinstance(raw_counters, dict):
            counters.update(raw_counters)
        elif raw_counters is not None:
            repaired = True
        for key, default, high in (
            ("focus_rounds", 0, 100000000),
            ("completed_total", 0, 100000000),
        ):
            normalized = _safe_int(counters.get(key), default, 0, high)
            if key in counters and _different(counters.get(key), normalized):
                repaired = True
            counters[key] = normalized
        sit_since = _safe_timestamp(counters.get("sit_since_epoch"), now) or now
        if "sit_since_epoch" in counters and _different(
                counters.get("sit_since_epoch"), sit_since):
            repaired = True
        counters["sit_since_epoch"] = sit_since
        state["counters"] = counters

        for key in ("pet_x", "pet_y"):
            value = state.get(key)
            if value is None:
                continue
            normalized = _safe_int(value, None, -100000, 100000)
            if _different(value, normalized):
                repaired = True
            state[key] = normalized
        edge = _safe_text(state.get("pet_edge"), "")
        if edge not in {"", "left", "right", "top", "bottom"}:
            edge = ""
            repaired = True
        if "pet_edge" in state and _different(state.get("pet_edge"), edge):
            repaired = True
        state["pet_edge"] = edge
        return state, repaired

    # ------------------------------------------------------------------ 保存
    #
    # 关于「要不要加节流 / debounce」——**评估过，不需要，别再加回来**。
    #
    # 这里原本有个 `self._dirty` 标志，设了但从来没人读，是个死字段，
    # 已删。留着它比没有更糟：它看起来像个「脏了才写」的节流开关，
    # 后来的人会以为它在工作。（写这份注释之前我就这么以为过。）
    #
    # 删掉它的依据是量出来的，不是感觉：
    #   * 一次 save() 约 27ms，且**与文件大小无关** ——
    #     557KB 端到端 27.3ms，23KB 端到端 26.7ms。缩小 24 倍只快 0.6ms。
    #   * 大头是「新建文件」这个动作本身（22.85ms），不是字节数。
    #     覆盖一个已存在的文件只要 4.51ms。差出来的 ~18ms 是杀软对新建
    #     文件的实时扫描 —— os.replace 会把 tmp 移走，所以下次 save()
    #     面对的仍然是新文件，每次都吃一次扫描。fsync 不是瓶颈。
    #   * 空闲 20 秒，save() 调用 0 次。番茄钟 / 提醒 / 切窗口全是用户触发。
    #   * 唯一真正高频的写者是 AI 对话历史，而它已经做了 2 秒防抖
    #     （见 ai/controller.py 的 _touch_history / _flush_history），
    #     而且写的是独立文件，根本不走这里。
    #
    # 所以节流省的是 0 次调用，拆文件省的是 0.6ms。debounce 还会带来一个
    # 真实的新风险：崩在 flush 之前就丢数据。拿 0.6ms 换这个是亏的。
    def save(self) -> bool:
        with self._save_lock:
            with self._lock:
                payload = copy.deepcopy(self.state)
                preserve_backup = self._preserve_backup_once
            payload["schema"] = SCHEMA_VERSION
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                # 保存前留一份上一版备份
                if self.path.exists() and not preserve_backup:
                    try:
                        with open(self.path, "rb") as src:
                            previous = src.read()
                        if previous.strip():
                            with open(self.backup_path, "wb") as dst:
                                dst.write(previous)
                    except OSError:
                        pass
                os.replace(tmp, self.path)
                with self._lock:
                    self._preserve_backup_once = False
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

    @property
    def knowledge(self) -> dict:
        """知识库的原始字典。解析和检索都在 pawpet.ai.kb 里。"""
        return self.state.setdefault("knowledge", {})


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
