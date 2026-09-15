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
    # ------------------------------------------------------- 界面元素（UIA）
    # 这一组是「准确率和速度的总开关」：不再靠像素猜坐标，而是直接问
    # Windows 那个坐标上到底是什么控件、叫什么名字、能不能点。
    ToolSpec(
        "ui_element_at",
        "查看屏幕上某个坐标上**实际是什么控件**。"
        "在点击之前用它确认目标，比单纯看截图可靠得多 —— "
        "它会返回控件的类型（按钮/输入框/链接…）、名字、精确位置、以及能不能点。"
        "坐标用真实屏幕坐标（和 screen_info 返回的坐标系一致）。",
        _schema({
            "x": {**_INT, "description": "真实屏幕 x 坐标"},
            "y": {**_INT, "description": "真实屏幕 y 坐标"},
        }, ["x", "y"]),
        Risk.READ,
    ),
    ToolSpec(
        "ui_focused",
        "查看当前**键盘焦点在哪个控件**上，以及它的名字和位置。"
        "要在输入框里打字之前，用这个确认焦点对不对。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "ui_windows",
        "列出当前打开的窗口（标题 + 句柄）。比截图更直接，用来确认目标程序是否已打开。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "ui_controls",
        "读取某个窗口内部的控件列表（控件名、类型、精确位置、可用操作）。"
        "拿到清单后就能按名字定位，不必再靠坐标。"
        "注意：浏览器、Office、WPS 这类程序支持得很好；"
        "而用 Tk/Qt 绘制的程序可能读不到（会返回说明而不是卡住）。",
        _schema({
            "window": {**_STRING, "description": "窗口标题的一部分，不区分大小写"},
            "limit": {**_INT, "description": "最多返回多少个控件，默认 60，上限 150"},
        }, ["window"]),
        Risk.READ,
    ),
    ToolSpec(
        "ui_click",
        "**按控件名字点击**，直接触发这个控件，不移动鼠标、不需要坐标。"
        "比坐标点击可靠得多，也不会因为窗口位置变化而点错。"
        "先用 ui_controls 拿到控件清单，再用这里给的名字。",
        _schema({
            "window": {**_STRING, "description": "控件所在窗口标题的一部分"},
            "name": {**_STRING, "description": "控件的名字（可以是其中的一部分）"},
            "type": {**_STRING, "description": "可选的控件类型，如 Button、MenuItem。用于消歧"},
        }, ["window", "name"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "ui_set_text",
        "**按控件名字直接设置输入框的内容**，不模拟键盘。"
        "不受输入法、焦点、键盘布局影响，也不会因为窗口失焦而丢字 —— "
        "输入中文时尤其推荐。",
        _schema({
            "window": {**_STRING, "description": "输入框所在窗口标题的一部分"},
            "name": {**_STRING, "description": "输入框的名字（可以是其中的一部分）"},
            "text": {**_STRING, "description": "要填入的内容"},
        }, ["window", "name", "text"]),
        Risk.CONFIRM,
    ),

    # -------------------------------------------------------------- 观察
    ToolSpec(
        "screenshot",
        "截取屏幕并查看当前画面。用它了解**界面长什么样、有没有报错弹窗**；"
        "但要定位控件、获取精确坐标时，优先用 ui_element_at / ui_controls，"
        "那比看图猜坐标准确得多。返回的图片尺寸不同于真实屏幕尺寸，"
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

    # ==================================================================
    #  界面元素（UI Automation）
    #
    #  这一组的价值：把「看图猜坐标」换成「直接问系统这个控件是什么」。
    #  UIA 会卡死（见 uia.py 的说明），所以每个调用都包在
    #  call_with_timeout 里，超时就给出说明而不是挂住整个对话。
    # ==================================================================
    def _uia(self):
        """惰性取 UIA 客户端。不可用时返回 (None, 原因)。"""
        try:
            from . import uia
        except Exception as exc:  # noqa: BLE001
            return None, f"UI Automation 模块加载失败：{exc}"
        try:
            client = uia.get_client()
        except Exception as exc:  # noqa: BLE001
            return None, f"UI Automation 初始化失败：{exc}"
        if not client.available:
            return None, client.error or "UI Automation 不可用"
        return (client, uia), ""

    def _do_ui_element_at(self, args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        client, uia = got

        x = int(args.get("x") or 0)
        y = int(args.get("y") or 0)

        ok, result = uia.call_with_timeout(
            lambda: client.element_at(x, y), timeout=5.0)
        if not ok:
            return False, str(result), None
        if result is None:
            return True, (f"({x}, {y}) 上没有读到控件信息。 "
                          "可能那里是空白区域，或者目标程序不提供辅助功能信息。"), None

        element = result
        lines = [
            f"坐标 ({x}, {y}) 上是一个 **{element.type_name}** 控件。",
            f"名称：{element.name or '(没有名称)'}",
            f"位置：({element.left}, {element.top})，尺寸 {element.width}x{element.height}",
            f"中心点：{element.center}",
        ]
        if element.automation_id:
            lines.append(f"标识：{element.automation_id}")
        if not element.is_enabled:
            lines.append("状态：**已禁用**（点了也不会生效）")
        if element.patterns:
            names = [uia.PATTERN_NAMES.get(p, str(p)) for p in element.patterns]
            lines.append("可用操作：" + "、".join(names))
            if uia.PATTERN_INVOKE in element.patterns:
                lines.append("→ 这个控件可以直接点击（可用 ui_click 按名字点它）")
        else:
            lines.append("可用操作：（没有可直接调用的操作，可能只是个容器）")
        return True, "\n".join(lines), None

    def _do_ui_focused(self, _args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        client, uia = got

        ok, result = uia.call_with_timeout(lambda: client.focused(), timeout=5.0)
        if not ok:
            return False, str(result), None
        if result is None:
            return True, "当前没有控件拥有键盘焦点（可能焦点在系统级界面上）。", None

        element = result
        lines = [
            f"当前焦点在 **{element.type_name}** 上。",
            f"名称：{element.name or '(没有名称)'}",
            f"位置：({element.left}, {element.top})，尺寸 {element.width}x{element.height}",
        ]
        if element.automation_id:
            lines.append(f"标识：{element.automation_id}")

        # 支持 Value 模式的话顺手把当前内容读出来 —— 模型常想知道
        if uia.PATTERN_VALUE in element.patterns:
            value_ok, value = uia.call_with_timeout(
                lambda: client.read_value(element), timeout=3.0)
            if value_ok and value:
                shown = value if len(value) <= 200 else value[:200] + "…"
                lines.append(f"当前内容：{shown}")
            elif value_ok:
                lines.append("当前内容：（空）")
        return True, "\n".join(lines), None

    def _do_ui_windows(self, _args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        client, _uia_mod = got

        windows = client.windows()
        if not windows:
            return True, "没有找到有标题的可见窗口。", None

        lines = [f"当前有 {len(windows)} 个打开的窗口："]
        for index, item in enumerate(windows[:40], 1):
            lines.append(f"{index:2d}. {item.title}")
        if len(windows) > 40:
            lines.append(f"…还有 {len(windows) - 40} 个")
        lines.append("")
        lines.append("要对某个窗口操作控件时，把标题里的一段传给 "
                     "ui_controls / ui_click / ui_set_text。")
        return True, "\n".join(lines), None

    def _do_ui_controls(self, args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        client, uia = got

        keyword = str(args.get("window") or "").strip()
        if not keyword:
            return False, "需要指定窗口标题的一部分", None

        limit = max(1, min(150, int(args.get("limit") or 60)))

        window = client.find_window(keyword)
        if window is None:
            available = [w.title for w in client.windows()][:15]
            return False, (f"没找到标题含「{keyword}」的窗口。\n"
                           f"当前打开的窗口有：{'；'.join(available)}"), None

        # 读控件树可能很慢（甚至超时），给足时间但也设上限
        ok, result = uia.call_with_timeout(
            lambda: client.tree(window, max_elements=limit), timeout=12.0)
        if not ok:
            return False, (f"读取「{window.title}」的控件超时。\n"
                           "这个程序可能不支持辅助功能接口"
                           "（用 Tk/Qt 自绘界面的程序常见）。\n"
                           "改用截图观察，或者用 ui_element_at 按坐标确认控件。"), None

        elements, note = result
        if not elements:
            return True, (f"{note}\n"
                          "这个窗口没有提供可读的控件信息（界面可能是自绘的）。\n"
                          "建议改用截图观察，或者用 ui_element_at 逐点确认。"), None

        lines = [note, ""]
        for element in elements:
            lines.append("  " + element.summary())

        actionable = [e for e in elements if e.patterns]
        if actionable:
            lines.append("")
            lines.append(f"其中 {len(actionable)} 个可以**按名字直接操作**，例如：")
            for element in actionable[:6]:
                lines.append(f"  · {element.name!r}（{element.type_name}）")
            lines.append("用 ui_click / ui_set_text 传这些名字即可，不需要坐标。")
        return True, "\n".join(lines), None

    def _find_control(self, client, uia, keyword: str, name: str,
                      type_filter: str = ""):
        """在窗口里找一个控件。返回 (元素, 错误说明)。"""
        window = client.find_window(keyword)
        if window is None:
            available = [w.title for w in client.windows()][:12]
            return None, (f"没找到标题含「{keyword}」的窗口。"
                          f"当前窗口：{'；'.join(available)}")

        ok, result = uia.call_with_timeout(
            lambda: client.tree(window, max_elements=150, interesting_only=False),
            timeout=12.0)
        if not ok:
            return None, (f"读取「{window.title}」的控件超时，"
                          "可能是这个程序不支持辅助功能接口。改用坐标点击。")

        elements, _note = result
        if not elements:
            return None, (f"「{window.title}」没有提供可读的控件"
                          "（界面可能是自绘的）。改用截图 + 坐标点击。")

        element = client.find_in(elements, name=name, control_type=type_filter)
        if element is None:
            candidates = [e for e in elements if e.patterns][:10]
            listing = "；".join(f"{e.name!r}({e.type_name})" for e in candidates)
            suffix = f"、类型为 {type_filter}" if type_filter else ""
            return None, (f"在「{window.title}」里没找到名字含「{name}」{suffix} 的控件。\n"
                          f"可操作的控件有：{listing or '（没有）'}")
        return element, ""

    def _do_ui_click(self, args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        client, uia = got

        keyword = str(args.get("window") or "").strip()
        name = str(args.get("name") or "").strip()
        type_filter = str(args.get("type") or "").strip()
        if not keyword or not name:
            return False, "需要同时给出窗口和控件名字", None

        element, error = self._find_control(client, uia, keyword, name, type_filter)
        if element is None:
            return False, error, None

        # 优先用 Invoke（不移动鼠标，最可靠）
        fallback_reason = ""
        if uia.PATTERN_INVOKE in element.patterns:
            ok, message = uia.call_with_timeout(
                lambda: client.invoke(element), timeout=6.0)
            if ok:
                return True, f"{message}（用的是控件接口，不是模拟鼠标）", None
            fallback_reason = str(message)
        elif uia.PATTERN_SELECTION_ITEM in element.patterns:
            ok, message = uia.call_with_timeout(
                lambda: client.select(element), timeout=6.0)
            if ok:
                return True, f"{message}（用的是控件接口）", None
            fallback_reason = str(message)
        else:
            fallback_reason = f"「{element.name}」不支持直接触发"

        # 退回坐标点击。注意：这个坐标是**从控件读出来的**，比看图猜准得多。
        cx, cy = element.center
        if cx <= 0 and cy <= 0:
            return False, f"{fallback_reason}，而且它没有有效的位置信息", None
        result = self.actions.click(cx, cy)
        if result.ok:
            return True, (f"{fallback_reason}，已改用坐标点击 ({cx}, {cy}) —— "
                          "这个坐标是从控件读出来的，比看图猜准确。"), None
        return False, f"{fallback_reason}；坐标点击也失败：{result.message}", None

    def _do_ui_set_text(self, args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        client, uia = got

        keyword = str(args.get("window") or "").strip()
        name = str(args.get("name") or "").strip()
        text = str(args.get("text") or "")
        if not keyword or not name:
            return False, "需要同时给出窗口和控件名字", None

        element, error = self._find_control(client, uia, keyword, name, "Edit")
        if element is None:
            # 有些输入框的控件类型不是 Edit，放宽类型再找一次
            element, error = self._find_control(client, uia, keyword, name)
        if element is None:
            return False, error, None

        if uia.PATTERN_VALUE not in element.patterns:
            return False, (f"「{element.name}」不支持直接设值（不是标准输入框）。"
                           "可以先 ui_click 聚焦它，再用 type_text 输入。"), None

        ok, message = uia.call_with_timeout(
            lambda: client.set_value(element, text), timeout=6.0)
        if not ok:
            return False, f"设置内容失败：{message}", None

        # 回读确认，避免「以为写进去了其实没有」
        verify_ok, actual = uia.call_with_timeout(
            lambda: client.read_value(element), timeout=3.0)
        if verify_ok and actual == text:
            return True, f"{message}，已回读确认内容正确。", None
        if verify_ok:
            return True, (f"{message}。注意：回读到的是 {actual!r}，"
                          "和期望不完全一致，可能控件对内容做了格式化。"), None
        return True, f"{message}（未能回读确认）。", None

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
