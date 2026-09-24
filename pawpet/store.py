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
        # pet_x / pet_y 允许是 None（还没摆过位置），所以单独放行 ——
        # 通用的 `v is not None` 会把它们过滤掉，于是每次启动都回到默认角。
        state.update({k: v for k, v in raw.items()
                      if v is not None or k in ("pet_x", "pet_y")})
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
        # 老版本没有 memory / knowledge 这两个键，补空字典让上层自己去解析
        if not isinstance(state.get("memory"), dict):
            state["memory"] = {}
        if not isinstance(state.get("knowledge"), dict):
            state["knowledge"] = {}
        state["schema"] = SCHEMA_VERSION
        return state

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
