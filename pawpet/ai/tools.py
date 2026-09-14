"""给模型用的工具定义与执行器。

工具分三档风险（见 actions.Risk）：
  read    只观察，不改变任何东西
  confirm 会动你的键鼠或窗口
  danger  会影响系统（启动程序、执行命令），默认关闭

坐标约定：模型看到的是缩小后的截图，所以它给的坐标属于「截图坐标系」。
执行前一律用最后一次截图的 scale 换算回真实屏幕坐标，映射逻辑集中在
`ToolContext.to_screen()`，避免散落各处算错。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .actions import LEVEL_READ_ONLY, Risk
from .vision import ScreenCapture, downscale_png

# 视觉模型看屏幕时的输出上限，避免一口气吐太多
MAX_OBSERVATION_NOTE = 800


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    risk: str
    returns_image: bool = False


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
    }


_NUMBER = {"type": "number"}
_INT = {"type": "integer"}
_STRING = {"type": "string"}
_BOOL = {"type": "boolean"}


TOOLS: list[ToolSpec] = [
    # -------------------------------------------------------------- 观察
    ToolSpec(
        "screenshot",
        "截取屏幕并查看当前画面。这是你了解屏幕状态的唯一方式，"
        "每次操作之后都应该重新截图确认结果。返回的图片尺寸不同于真实屏幕尺寸，"
        "你给出的所有坐标都要基于返回图片的尺寸。",
        _schema({
            "monitor": {**_INT, "description": "要截取的显示器序号，从 1 开始。默认 1。"},
        }),
        Risk.READ,
        returns_image=True,
    ),
    ToolSpec(
        "screen_info",
        "获取屏幕与鼠标的当前状态：分辨率、缩放比例、鼠标位置、当前活动窗口标题。"
        "在执行点击之前先用它确认坐标系和鼠标落点。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "list_windows",
        "列出当前所有可见窗口的标题。想切换到某个程序时先用它拿到准确的标题。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "read_clipboard",
        "读取剪贴板里的文本。需要用户先复制一段内容时用它。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "wait",
        "等待若干秒。用于等程序启动、页面加载完成。",
        _schema({"seconds": {**_NUMBER, "description": "等待秒数，0 到 10 之间。"}},
                ["seconds"]),
        Risk.READ,
    ),

    # -------------------------------------------------------------- 控制
    ToolSpec(
        "move_mouse",
        "把鼠标移动到指定坐标（不点击）。坐标基于最近一次 screenshot 返回的图片尺寸。",
        _schema({
            "x": {**_INT, "description": "截图坐标系里的 x"},
            "y": {**_INT, "description": "截图坐标系里的 y"},
        }, ["x", "y"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "click",
        "在指定坐标点击鼠标。不传坐标时在当前位置点击。",
        _schema({
            "x": {**_INT, "description": "截图坐标系里的 x，可省略"},
            "y": {**_INT, "description": "截图坐标系里的 y，可省略"},
            "button": {**_STRING, "enum": ["left", "right", "middle"],
                       "description": "哪个键，默认 left"},
            "clicks": {**_INT, "description": "点击次数：1 单击，2 双击。默认 1"},
        }),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "drag",
        "按住左键从一点拖到另一点。用于拖动窗口、拖选内容。",
        _schema({
            "x1": {**_INT, "description": "起点 x"},
            "y1": {**_INT, "description": "起点 y"},
            "x2": {**_INT, "description": "终点 x"},
            "y2": {**_INT, "description": "终点 y"},
        }, ["x1", "y1", "x2", "y2"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "scroll",
        "滚动鼠标滚轮。正数向上，负数向下。",
        _schema({
            "amount": {**_INT, "description": "滚动格数，-20 到 20"},
            "x": {**_INT, "description": "可选：先移动到这个 x 再滚动"},
            "y": {**_INT, "description": "可选：先移动到这个 y 再滚动"},
        }, ["amount"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "type_text",
        "输入一段文本，会打到当前获得焦点的输入框里。"
        "点击输入框之后再调用它。支持中文。",
        _schema({"text": {**_STRING, "description": "要输入的文本"}}, ["text"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "press_keys",
        "按下按键或组合键。例如 [\"enter\"]、[\"ctrl\",\"c\"]、[\"alt\",\"tab\"]。",
        _schema({
            "keys": {"type": "array", "items": _STRING,
                     "description": "按键列表，按顺序组合。单个元素就是单键。"},
        }, ["keys"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "activate_window",
        "把标题包含指定文字的窗口切到前台。",
        _schema({"title": {**_STRING, "description": "窗口标题的一部分"}}, ["title"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "write_clipboard",
        "把文本写入剪贴板。",
        _schema({"text": {**_STRING, "description": "要复制的文本"}}, ["text"]),
        Risk.CONFIRM,
    ),

    # ---------------------------------------------------------- 小爪自己的数据
    ToolSpec(
        "add_task",
        "把小爪助手自己的待办清单里加一条待办。"
        "当用户说「记一下」「帮我记住要做某事」时用它，不要用输入框去打字。",
        _schema({
            "text": {**_STRING, "description": "待办内容"},
            "priority": {**_INT, "description": "0 普通 / 1 重要 / 2 紧急，默认 0"},
        }, ["text"]),
        Risk.READ,
    ),
    ToolSpec(
        "add_reminder",
        "给小爪助手加一条定时提醒。",
        _schema({
            "title": {**_STRING, "description": "提醒内容"},
            "time": {**_STRING, "description": "24 小时制时间，例如 09:30"},
            "repeat": {**_STRING, "enum": ["once", "daily", "weekdays", "weekly"],
                       "description": "重复方式，默认 daily"},
        }, ["title", "time"]),
        Risk.READ,
    ),
    ToolSpec(
        "save_note",
        "把一段内容存进小爪助手的便签里。适合记录长文本、会议纪要、网址集合。",
        _schema({
            "title": {**_STRING, "description": "便签标题"},
            "text": {**_STRING, "description": "便签正文"},
        }, ["title", "text"]),
        Risk.READ,
    ),

    # -------------------------------------------------------------- 系统
    ToolSpec(
        "open_app",
        "启动一个程序，或用一个网址打开浏览器。"
        "例如 \"notepad\"、\"calc\"、\"https://example.com\"。属于高危动作。",
        _schema({"target": {**_STRING, "description": "程序名或 URL"}}, ["target"]),
        Risk.DANGER,
    ),
    ToolSpec(
        "run_command",
        "执行一条命令行指令并取回输出。属于高危动作，默认需要用户逐次批准。",
        _schema({
            "command": {**_STRING, "description": "要执行的命令"},
            "timeout": {**_INT, "description": "超时秒数，默认 20"},
        }, ["command"]),
        Risk.DANGER,
    ),
]

TOOL_INDEX = {tool.name: tool for tool in TOOLS}


def openai_tools() -> list[dict]:
    """转成 chat/completions 的 tools 参数格式。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in TOOLS
    ]


class ToolContext:
    """工具执行上下文：把捕获、动作和数据层接到一起。"""

    def __init__(self, capture: ScreenCapture, actions, store, backend=None) -> None:
        self.capture = capture
        self.actions = actions
        self.store = store
        self.backend = backend        # 用来把待办同步给界面
        self.last_shot = None
        self.last_preview_png = b""

    # ------------------------------------------------------------ 坐标换算
    def to_screen(self, x, y) -> tuple[int, int]:
        """把模型坐标换成真实屏幕坐标。

        模型可能给出截图坐标系里的值，也可能（偷懒时）直接给真实屏幕坐标。
        这里用一个启发式兜底：如果换算后的点明显超出屏幕，就当作已经是真实坐标。
        """
        real_w, real_h = self.actions.screen_size()
        if self.last_shot is None:
            return (int(x), int(y))

        shot = self.last_shot
        if shot.model_width and shot.model_height:
            if 0 <= float(x) <= shot.model_width * 1.15 and 0 <= float(y) <= shot.model_height * 1.15:
                sx, sy = shot.to_screen(x, y)
            else:
                # 超出截图范围，八成给的已经是真实坐标
                sx, sy = int(x), int(y)
        else:
            sx, sy = int(x), int(y)

        if real_w and real_h:
            sx = max(0, min(real_w - 1, sx))
            sy = max(0, min(real_h - 1, sy))
        return (sx, sy)

    @staticmethod
    def describe_shot(shot) -> str:
        """告诉模型当前坐标系，显著减少它算错坐标的概率。"""
        if shot is None:
            return ""
        return (
            f"[截图信息] 图片尺寸 {shot.model_width}x{shot.model_height}，"
            f"真实屏幕 {shot.width}x{shot.height}，"
            f"缩放比例约 {shot.scale_x:.2f}。"
            f"你给出的坐标请使用 {shot.model_width}x{shot.model_height} 这套。"
        )[:MAX_OBSERVATION_NOTE]

    # ---------------------------------------------------------------- 执行
    def execute(self, name: str, arguments: dict):
        """返回 (是否成功, 给模型看的文本, 图片包或 None)。

        图片包在截图时才有，格式是
            {"preview": PNG 字节, "data_url": JPEG data URL, "note": 坐标说明}
        刻意在这里就把说明和图片绑死，而不是等 Agent 回头再去读 last_shot ——
        一轮里如果截了两次屏，延迟取会拿到同一个说明，模型就会按错坐标系点击。
        """
        spec = TOOL_INDEX.get(name)
        if spec is None:
            return False, f"没有名为 {name} 的工具", None

        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            return False, f"工具 {name} 还没有实现", None

        try:
            return handler(arguments or {})
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成可读文本回给模型
            return False, f"工具 {name} 执行出错：{exc}", None

    # -------------------------------------------------------------- 观察实现
    def _do_screenshot(self, args: dict):
        monitor = int(args.get("monitor") or 1)
        shot = self.capture.grab(monitor=monitor, with_preview=True)
        if not shot.ok:
            return False, shot.message, None
        self.last_shot = shot
        preview = downscale_png(shot.preview_png, 900)
        self.last_preview_png = preview
        bundle = {
            "preview": preview,
            "data_url": shot.data_url(),
            "note": self.describe_shot(shot),
        }
        return True, shot.message, bundle

    def _do_screen_info(self, _args: dict):
        real_w, real_h = self.actions.screen_size()
        mouse_x, mouse_y = self.actions.mouse_position()
        lines = [
            f"真实分辨率：{real_w}x{real_h}",
            f"鼠标当前位置（真实坐标）：({mouse_x}, {mouse_y})",
            f"当前活动窗口：{self.actions.active_window()}",
        ]
        if self.last_shot is not None:
            shot = self.last_shot
            model_x, model_y = shot.to_model(mouse_x, mouse_y)
            lines.append(
                f"最近截图坐标系：{shot.model_width}x{shot.model_height}"
                f"（缩放 {shot.scale_x:.2f}）"
            )
            lines.append(f"鼠标在截图坐标系里是：({model_x}, {model_y})")
        else:
            lines.append("提示：还没有截图，先调用 screenshot 再定位坐标。")
        lines.append("显示器：")
        lines.append(self.capture.describe_monitors())
        return True, "\n".join(lines), None

    def _do_list_windows(self, _args: dict):
        titles = self.actions.list_windows()
        if not titles:
            return True, "没有枚举到可见窗口", None
        return True, "可见窗口：\n" + "\n".join(f"- {t}" for t in titles), None

    def _do_read_clipboard(self, _args: dict):
        text = self.actions.clipboard_text()
        if not text:
            return True, "剪贴板是空的（或者内容不是文本）", None
        if len(text) > 3000:
            text = text[:3000] + "\n…（内容过长已截断）"
        return True, f"剪贴板内容：\n{text}", None

    def _do_wait(self, args: dict):
        result = self.actions.wait(float(args.get("seconds") or 1))
        return result.ok, result.message, None

    # -------------------------------------------------------------- 控制实现
    def _do_move_mouse(self, args: dict):
        x, y = self.to_screen(args.get("x", 0), args.get("y", 0))
        result = self.actions.move(x, y)
        return result.ok, result.message, None

    def _do_click(self, args: dict):
        if args.get("x") is None or args.get("y") is None:
            result = self.actions.click(None, None,
                                        str(args.get("button") or "left"),
                                        int(args.get("clicks") or 1))
            return result.ok, result.message, None
        x, y = self.to_screen(args.get("x"), args.get("y"))
        result = self.actions.click(x, y, str(args.get("button") or "left"),
                                    int(args.get("clicks") or 1))
        return result.ok, result.message, None

    def _do_drag(self, args: dict):
        x1, y1 = self.to_screen(args.get("x1", 0), args.get("y1", 0))
        x2, y2 = self.to_screen(args.get("x2", 0), args.get("y2", 0))
        result = self.actions.drag(x1, y1, x2, y2)
        return result.ok, result.message, None

    def _do_scroll(self, args: dict):
        if args.get("x") is None or args.get("y") is None:
            result = self.actions.scroll(int(args.get("amount") or 0))
            return result.ok, result.message, None
        x, y = self.to_screen(args.get("x"), args.get("y"))
        result = self.actions.scroll(int(args.get("amount") or 0), x, y)
        return result.ok, result.message, None

    def _do_type_text(self, args: dict):
        result = self.actions.type_text(str(args.get("text") or ""))
        return result.ok, result.message, None

    def _do_press_keys(self, args: dict):
        keys = args.get("keys") or []
        if isinstance(keys, str):
            keys = [part for part in keys.replace("+", " ").split() if part]
        result = self.actions.press_keys(list(keys))
        return result.ok, result.message, None

    def _do_activate_window(self, args: dict):
        result = self.actions.activate_window(str(args.get("title") or ""))
        return result.ok, result.message, None

    def _do_write_clipboard(self, args: dict):
        ok = self.actions.set_clipboard(str(args.get("text") or ""))
        return ok, "已写入剪贴板" if ok else "写入剪贴板失败", None

    # ---------------------------------------------------------- 小爪自己的数据
    def _do_add_task(self, args: dict):
        text = str(args.get("text") or "").strip()
        if not text:
            return False, "待办内容不能为空", None
        priority = max(0, min(2, int(args.get("priority") or 0)))
        if self.backend is not None:
            self.backend.tasks.add(text, priority)
        else:
            from ..store import new_id
            import time as _time

            self.store.tasks.append({
                "id": new_id("t"), "text": text[:200], "done": False,
                "created": _time.time(), "done_at": None,
                "priority": priority, "due": None, "pomodoros": 0,
            })
            self.store.save()
        return True, f"已加入待办：{text}", None

    def _do_add_reminder(self, args: dict):
        title = str(args.get("title") or "").strip()
        when = str(args.get("time") or "").strip()
        if not title or not when:
            return False, "提醒内容和时间都不能为空", None
        repeat = str(args.get("repeat") or "daily")
        if self.backend is not None:
            self.backend.reminders.add(title, when, repeat)
        else:
            from ..store import new_id
            import time as _time

            self.store.reminders.append({
                "id": new_id("r"), "title": title[:80], "time": when[:5],
                "date": None, "repeat": repeat, "enabled": True,
                "last_fired": None, "created": _time.time(),
            })
            self.store.save()
        return True, f"已添加提醒：{when} {title}", None

    def _do_save_note(self, args: dict):
        title = str(args.get("title") or "AI 记录").strip()
        text = str(args.get("text") or "")
        if not text.strip():
            return False, "便签内容不能为空", None
        if self.backend is not None:
            self.backend.notes.add(title, text)
        else:
            from ..store import new_id
            import time as _time

            now = _time.time()
            self.store.notes.append({
                "id": new_id("n"), "title": title[:60], "text": text,
                "created": now, "updated": now,
            })
            self.store.save()
        return True, f"已存入便签「{title}」", None

    # ---------------------------------------------------------------- 系统
    def _do_open_app(self, args: dict):
        result = self.actions.open_app(str(args.get("target") or ""))
        return result.ok, result.message, None

    def _do_run_command(self, args: dict):
        result = self.actions.run_command(
            str(args.get("command") or ""),
            int(args.get("timeout") or 20),
        )
        return result.ok, result.message, None


def describe_arguments(name: str, arguments: dict) -> str:
    """把工具参数转成界面上好读的一行说明。"""
    if not arguments:
        return ""
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)
