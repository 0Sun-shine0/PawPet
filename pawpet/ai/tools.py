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
import sys
import time
from dataclasses import dataclass

from .actions import LEVEL_READ_ONLY, Risk
from .extensions import (
    LEVEL_CODE,
    LEVEL_NOTE,
    LEVEL_RECIPE,
    Extension,
)
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
        "**注意：这个接口挑程序。** 实测记事本、计算器、画图、Edge 读不到"
        "（会明确告诉你读不到，别反复重试）；浏览器页面、Office、WPS 一般能读到；"
        "用 Tk/Qt 自绘界面的程序读不到。读不到时改用 ui_element_at + 坐标。",
        _schema({
            "window": {**_STRING, "description": "窗口标题的一部分，不区分大小写"},
            "limit": {**_INT, "description": "最多返回多少个控件，默认 60，上限 150"},
        }, ["window"]),
        Risk.READ,
    ),
    ToolSpec(
        "ui_forget_hangs",
        "清掉「已知读不到控件的窗口」记录。"
        "如果某个程序你重启过、或者它刚从卡死中恢复，之前被标记读不动，"
        "用这个清一下再试 ui_controls。平时不需要用。",
        _schema({}),
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

    # ------------------------------------------------------------ 知识库
    ToolSpec(
        "import_knowledge",
        "**把用户的资料导入知识库**，以后回答时就能引用。"
        "传一个文件或一个文件夹的路径。"
        "导入的是**文本类**资料（md/txt/代码/csv/json/日志…）。"
        "PDF、Word、Excel **不支持直接导入** —— 会明确告诉用户"
        "先另存为 txt/md。"
        "同一个文件重新导入会覆盖旧的，不会攒出两份。"
        "导入后正文**不会**全塞进对话，而是按需检索，所以可以导很多。",
        _schema({
            "path": {**_STRING, "description": "文件或文件夹路径，支持 ~ 和环境变量"},
            "recursive": {**_BOOL, "description":
                          "传文件夹时是否连子目录一起导入，默认 false"},
        }, ["path"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "search_knowledge",
        "**在知识库里检索**，拿出和问题相关的原文片段。"
        "用户问的事情可能写在导入的资料里时，**先检索再回答**，"
        "不要凭印象编。返回的是原文片段，引用时按里面的出处说明。",
        _schema({
            "query": {**_STRING, "description": "要查什么，用自然语言描述即可"},
            "limit": {**_INT, "description": "返回几块，默认 4，最多 8"},
        }, ["query"]),
        Risk.READ,
    ),
    ToolSpec(
        "list_knowledge",
        "列出知识库里已经导入了哪些资料（文件名 + 块数）。"
        "不确定用户有没有导入过某份资料时用它。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "forget_knowledge",
        "**从知识库里移除**某份资料（导错了、或者内容过期了）。"
        "传文件名的一部分即可。",
        _schema({
            "name": {**_STRING, "description": "要移除的资料名（可以是其中一部分）"},
        }, ["name"]),
        Risk.CONFIRM,
    ),

    # ------------------------------------------------------------ 本地文件
    # 有这些工具，用户就不用「打开文件 → 全选 → 复制 → 粘贴」绕一圈了。
    ToolSpec(
        "read_file",
        "**直接读本地文本文件**，不用让用户复制粘贴。"
        "适合：代码、日志、配置、txt、csv、md、json 等文本文件。"
        "二进制文件（exe、图片、压缩包）会被拒绝 —— 读出来是乱码，没意义。"
        "Word/Excel/PDF 这类**文档不是文本**，直接读会得到乱码，"
        "那种情况请让用户用对应的程序打开，或者用截图看。"
        "文件很长时会分段返回，用 start_line 翻页。",
        _schema({
            "path": {**_STRING, "description":
                     "文件路径。支持 ~ 和环境变量，比如 "
                     "%USERPROFILE%\\Desktop\\a.txt 或 ~/notes.md"},
            "start_line": {**_INT, "description": "从第几行开始读，默认 1（用于翻页）"},
            "max_lines": {**_INT, "description": "最多读多少行，默认 2000"},
        }, ["path"]),
        Risk.READ,
    ),
    ToolSpec(
        "list_dir",
        "列出一个目录里有什么（文件名 + 大小）。"
        "用户说「我桌面上那个文件」「D 盘那个报告」但不记得全名时，"
        "先用它找出来，再用 read_file 读。",
        _schema({
            "path": {**_STRING, "description":
                     "目录路径。不传就列当前目录。支持 ~ 和环境变量"},
            "pattern": {**_STRING, "description":
                        "可选，按文件名通配过滤，比如 *.csv 或 报告*"},
        }, []),
        Risk.READ,
    ),
    ToolSpec(
        "write_file",
        "**把内容写入本地文件**（新建或覆盖）。"
        "用户说「帮我保存成文件」「写个脚本放到桌面」时用。"
        "**默认不覆盖已有文件** —— 目标已存在会先报错让你确认，"
        "确认要覆盖再把 overwrite 设成 true。"
        "系统目录（Windows、Program Files）只允许读，不允许写。",
        _schema({
            "path": {**_STRING, "description": "写到哪个路径"},
            "content": {**_STRING, "description": "文件内容（纯文本，UTF-8 保存）"},
            "overwrite": {**_BOOL, "description":
                          "目标已存在时是否覆盖。默认 false（不覆盖，先报错让你确认）"},
        }, ["path", "content"]),
        Risk.CONFIRM,
    ),

    # ------------------------------------------------------------ 跨会话记忆
    # 这些工具让「记住」变成模型能主动做的事。没有它们的话，
    # 每一轮对话都得让用户从头交代一遍。
    ToolSpec(
        "remember",
        "**把关于用户的事情长期记下来**，下次打开小爪还在。"
        "适合记：常用程序、称呼习惯、排版偏好、工作流程、"
        "「以后都这样」这类明确的要求。"
        "不适合记：只对这一次有效的细节（临时坐标、本次对话的中间结果）。"
        "重复记同一件事不会产生两条，会合并成一条。",
        _schema({
            "text": {**_STRING, "description":
                     "要记的内容，一句完整的话，比如「他习惯用 WPS 而不是 Office」"},
            "category": {**_STRING, "description":
                         "分类：identity（身份）/ preference（偏好）/ "
                         "workflow（工作方式）/ environment（环境）/ other"},
            "confidence": {**_INT, "description":
                           "3=确定（用户明说的）；2=比较确定（默认）；"
                           "1=不太确定（你推测的，用的时候要再确认）"},
        }, ["text"]),
        Risk.READ,
    ),
    ToolSpec(
        "forget_memory",
        "**忘掉**一条记错或过期的记忆。"
        "当用户说「不对」「不是这样」「以后不用了」，或者你发现记忆和"
        "眼前的事实冲突时用它。传记忆内容的片段即可。",
        _schema({
            "text": {**_STRING, "description": "要忘掉的那条记忆的内容片段"},
        }, ["text"]),
        Risk.READ,
    ),
    ToolSpec(
        "recall_memory",
        "查看当前**已经记住的全部内容**。"
        "系统提示词里只列出了最重要的一部分，"
        "怀疑自己有相关记忆但没看到时，用它查全量。",
        _schema({
            "keyword": {**_STRING, "description":
                        "可选。只列出包含这个词的记忆，不传就列全部"},
        }),
        Risk.READ,
    ),
    ToolSpec(
        "note_task_state",
        "**记录一件事做到哪了**，下次开机能接着做。"
        "任务跨度比较大、这一轮做不完，或者用户说「先这样，回头再弄」时用。"
        "做完之后同样用它把状态改成 done，别让已完成的事一直挂在待办里。",
        _schema({
            "text": {**_STRING, "description": "这件事是什么，一句话"},
            "status": {**_STRING, "description":
                       "open=进行中（默认）；done=已完成；blocked=卡住了"},
            "next_step": {**_STRING, "description":
                          "可选。下一步具体该做什么，写清楚下次能直接上手"},
        }, ["text"]),
        Risk.READ,
    ),
    # -------------------------------------------------------- 造新工具
    # 「二次开发」的落地：用户反复做同一件事时，把它固化成一个工具。
    #
    # 为什么值得做：用户现在的做法是把流程写在便签里，每次让小爪读了
    # 再重新理解。同一条流程跑两次结果不会完全一样。做成工具 = 写死一次。
    #
    # 三档能力，默认只开前两档（见 extensions.py）：
    #   note   固定说法 —— 一段提示词，不碰任何东西
    #   recipe 固定流程 —— 一串**只读**步骤
    #   code   自定义代码 —— 默认关闭，开了也要每次运行单独确认
    ToolSpec(
        "propose_extension",
        "**给用户造一个新工具**。用户反复做同一类事、或者明确说"
        "「以后都这么做」「帮我记成一个固定的做法」时用它。\n"
        "这个工具只生成**草稿**，不生效 —— 还要调 install_extension "
        "让用户确认之后才算装上。\n"
        "三档能力，从安全到强大：\n"
        f"  · {LEVEL_NOTE}：固定说法。给一段固定指令，不碰文件不联网\n"
        f"  · {LEVEL_RECIPE}：固定流程。一串**只读**步骤"
        "（read_file / list_dir / regex_find / pick_lines / template / "
        "join / truncate / count）\n"
        f"  · {LEVEL_CODE}：自定义代码。默认关着，别主动提\n"
        "**优先用 recipe**，它够用而且一定安全。\n"
        "参数要声明清楚：用户需要提供什么（比如文件路径、工单内容）。",
        _schema({
            "name": {**_STRING, "description":
                     "工具名：小写字母开头，只能小写字母数字下划线，3~41 位"},
            "title": {**_STRING, "description": "中文名，界面上显示这个"},
            "description": {**_STRING, "description":
                            "什么时候该用这个工具。要具体到「用户说什么的时候」，"
                            "模型靠这句话决定要不要调它"},
            "level": {**_STRING, "description":
                      f"{LEVEL_NOTE} / {LEVEL_RECIPE} / {LEVEL_CODE}，默认 {LEVEL_RECIPE}"},
            "prompt": {**_STRING, "description":
                       f"仅 {LEVEL_NOTE} 需要：那段固定指令"},
            "steps": {
                "type": "array",
                "description": f"仅 {LEVEL_RECIPE} 需要：步骤列表，"
                               "每步形如 {\"op\": \"read_file\", \"path\": \"{path}\"}。"
                               "可以用 {参数名} 引用参数，用 {text} 引用上一步的输出",
                "items": {"type": "object"},
            },
            "code": {**_STRING, "description":
                     f"仅 {LEVEL_CODE} 需要：要运行的 Python 代码，"
                     "里面必须定义 def run(args) 并 return 结果文本"},
            "parameters": {
                "type": "object",
                "description": "这个工具要问用户拿什么，JSON Schema 形式",
            },
        }, ["name", "title", "description"]),
        Risk.READ,
    ),
    ToolSpec(
        "install_extension",
        "把 propose_extension 生成的草稿**装上去**。"
        "这会弹一张卡片让用户确认 —— 卡片上写清楚「会做什么」，"
        "用户点头之后才生效。\n"
        "用户说「不用了」「算了」就别调这个。",
        _schema({
            "draft": {"type": "object", "description":
                      "propose_extension 返回的草稿对象，原样传回来"},
        }, ["draft"]),
        Risk.CONFIRM,
    ),
    ToolSpec(
        "list_extensions",
        "列出**已经装好的**自定义工具。用户问「我有哪些自己加的工具」"
        "「之前那个整理工单的还在吗」时用它。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "remove_extension",
        "**卸掉**一个自定义工具。用户说「那个不要了」「删掉」时用它。",
        _schema({
            "name": {**_STRING, "description": "要卸掉的工具名"},
        }, ["name"]),
        Risk.READ,
    ),
    # ------------------------------------------------------------ 定制界面
    # 「通过对话改主题」的落地。
    # 为什么走数据而不是改 Theme.qml：Theme.qml 带 pragma Singleton，
    # 模块解析对同名类型是「先找到的赢」，覆盖它靠碰运气；而且打包后
    # QML 在 _MEIPASS 里、退出就删，磁盘上根本没有 .qml 可改。
    # 颜色值存在用户数据目录的 theme.json，改完立刻生效、不用重启。
    ToolSpec(
        "set_theme_color",
        "**改界面配色**。用户说「换个颜色」「主色调改成蓝的」"
        "「背景太白了我想要米黄」这类需求时用它。\n"
        "值必须是 #rrggbb 形式的十六进制颜色（比如 #4a90d9）。"
        "**不要用「蓝色」「red」这种写法** —— 会被拒绝。\n"
        "拿不准用户想要什么色号时，自己挑一个再让他看效果，"
        "改错了他说一句就能换回来（再调一次即可）。\n"
        "`list_theme_colors` 能看到当前所有可改的项和它们的值。",
        _schema({
            "colors": {
                "type": "object",
                "description": "要改的项和值，形如 {\"accent\": \"#4a90d9\"}。"
                               "一次可以改多项。",
                "additionalProperties": {"type": "string"},
            },
        }, ["colors"]),
        Risk.READ,
    ),
    ToolSpec(
        "list_theme_colors",
        "列出界面里**可以改的**配色项、它们的中文含义、当前值。"
        "用户问「界面能改哪些颜色」或者你不确定键名时用它。",
        _schema({}),
        Risk.READ,
    ),
    ToolSpec(
        "reset_theme",
        "把界面配色**恢复成默认的粉白**。用户说「改回来」「恢复原样」时用。",
        _schema({}),
        Risk.READ,
    ),
    # ------------------------------------------------------------ 停下来问人
    # 这一条是「拿不准就停一下问」的落地。
    #
    # 为什么值得单独做个工具：模型默认行为是「不管三七二十一先干」，
    # 猜错了的代价是它把文件挪错地方、把表填串行、把界面上别的东西点掉。
    # 多问一句的成本是一秒钟，猜错的成本是一次返工 —— 这笔账很划算。
    ToolSpec(
        "ask_user",
        "**信息不够或者拿不准的时候，停下来问用户**，别自己猜。"
        "什么时候该用：\n"
        "  · 要动用户的文件/数据，但不确定他想放哪、叫什么名字\n"
        "  · 屏幕上有多个看起来都对的目标，分不出该点哪个\n"
        "  · 用户的话有两种以上合理解释，选错了要重做\n"
        "  · 这一步操作不可撤销（覆盖、删除、提交），想再确认一次\n"
        "什么时候不该用：能自己看一眼就确定的（比如截图看一下），"
        "别拿它当偷懒的借口 —— 用户会觉得你什么都要问。\n"
        "问法要具体：说清你看到了什么、要用户定什么，"
        "能列选项就列（一次最多 4 个），用户点一下就能答。",
        _schema({
            "question": {**_STRING, "description":
                         "要问用户的话。说清你看到的现状 + 需要他定什么，"
                         "别只写「请问怎么办」"},
            "options": {
                "type": "array",
                "items": _STRING,
                "description": ("可选。给几个具体选项（最多 4 个），"
                                "用户点一下就能回答。不确定他想怎么选时别传，"
                                "留空的输入框更好用"),
            },
        }, ["question"]),
        Risk.READ,
    ),
]

TOOL_INDEX = {tool.name: tool for tool in TOOLS}


def openai_tools(extra: list[dict] | None = None) -> list[dict]:
    """转成 chat/completions 的 tools 参数格式。

    extra 是外面接进来的工具（目前是 MCP server 提供的），格式已经是
    OpenAI 那一套，直接拼上去就行。

    **为什么要这个参数**：内置工具列表是模块级的常量，而 MCP 工具是
    运行时才连上的、还会变。原来这里没有 extra，结果是
    `MCPClient.as_openai_tools()` 根本没人调用 —— 界面显示「已连接，
    3 个工具」，但那 3 个工具模型永远看不到也调不到。等于 MCP 是个假功能。
    """
    tools = [
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
    if extra:
        # 内置工具优先。同名的外部工具直接丢掉，不让它顶掉内置的 ——
        # 内置工具是安全边界内实现好的，外面来的同名工具不可信。
        taken = {item["function"]["name"] for item in tools}
        for item in extra:
            name = (item.get("function") or {}).get("name")
            if name and name not in taken:
                tools.append(item)
                taken.add(name)
    return tools


class ToolContext:
    """工具执行上下文：把捕获、动作和数据层接到一起。"""

    def __init__(self, capture: ScreenCapture, actions, store, backend=None) -> None:
        self.capture = capture
        self.actions = actions
        self.store = store
        self.backend = backend        # 用来把待办同步给界面
        self.last_shot = None
        self.last_preview_png = b""
        # 这一轮用户说的原话。界面层每轮开始时写进来，
        # remember 靠它判断该标 told 还是 tool。
        self.recent_user_texts: list[str] = []

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
            # 不是内置工具 —— 可能是外面接进来的 MCP 工具，
            # 也可能是用户自己造的扩展工具。
            if name.startswith("ext_"):
                return self.call_extension(name, arguments or {})
            # 名字长这样：mcp_{server名}_{工具名}
            if name.startswith("mcp_"):
                return self.call_mcp_tool(name, arguments or {})
            return False, f"没有名为 {name} 的工具", None

        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            return False, f"工具 {name} 还没有实现", None

        # 在工具内部再确认一次权限。
        #
        # Agent 主循环里已经拦过一次（_run_one），但工具也可能被别处直接调用
        # （测试、以后的别的入口），只靠上游那一层守不住 ——
        # 只读模式下 write_file 实测就能写进去。
        # 这里挡的是「不可逆且不该发生的」，所以按 blocked() 判，
        # 而不是 needs_approval()（后者会被用户的确认放行）。
        level = getattr(self.actions, "level", None)
        if level is not None and self.actions.blocked(spec.risk):
            return False, (
                f"当前是「只读」模式，不允许执行会改变状态的动作（{name}）。"
                "要写文件请先在工作台把操作权限调高。"
            ), None

        try:
            return handler(arguments or {})
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成可读文本回给模型
            return False, f"工具 {name} 执行出错：{exc}", None

    # ---------------------------------------------------------- 自定义工具
    def extension_tools(self) -> list[dict]:
        """用户装好的扩展，转成给模型的工具定义。

        每轮重新读 —— 用户会话中间装了一个，下一步就该能用。
        """
        from .extensions import as_openai_tools, load_all

        try:
            path = self.store.path.parent / "extensions.json"
            return as_openai_tools(load_all(path))
        except Exception:  # noqa: BLE001 - 扩展坏了不该让整轮用不了
            return []

    def call_extension(self, name: str, arguments: dict):
        """执行用户自定义的工具。

        三档的执行方式不同，但有一条共同的规矩：
        **只有 approved 的才执行。** 没经用户确认的定义不出现在工具清单里，
        万一手改配置塞进来一个，这里也拦住。
        """
        from .extensions import (
            LEVEL_NOTE,
            LEVEL_RECIPE,
            find_by_tool_name,
            load_all,
            run_recipe,
            save_all,
        )

        try:
            path = self.store.path.parent / "extensions.json"
            items = load_all(path)
        except Exception as exc:  # noqa: BLE001
            return False, f"读不到自定义工具：{exc}", None

        ext = find_by_tool_name(items, name)
        if ext is None:
            known = "、".join(f"ext_{i.name}" for i in items) or "（一个都没有）"
            return False, f"没有名为 {name} 的自定义工具。现有的：{known}", None
        if not ext.approved:
            return False, (f"「{ext.title}」还没经过确认，不能执行。"
                           "让用户确认一次再装。"), None

        if ext.level == LEVEL_NOTE:
            # 固定说法：这段指令当成「用户的要求」交给模型，附上参数。
            # 返回格式刻意做成被填充过的提示词 —— 模型读到就会照着做。
            filled = ext.prompt
            for key, value in (arguments or {}).items():
                filled = filled.replace("{" + key + "}", str(value))
            ok, text = True, (f"【用户的自定义要求：{ext.title}】\n{filled}\n"
                              f"（这是用户预先定好的做法，请照它办）")
        elif ext.level == LEVEL_RECIPE:
            ok, text = run_recipe(ext, arguments or {})
        elif ext.level == LEVEL_CODE:
            ok, text = self._run_extension_code(ext, arguments or {})
        else:
            return False, f"「{ext.title}」的类型未知：{ext.level}", None

        # 记一次使用次数。用户问「这个工具到底有没有用」时能看到。
        if ok:
            ext.run_count += 1
            try:
                save_all(path, items)
            except Exception:  # noqa: BLE001 - 记不上不影响这次执行
                pass
        return ok, text, None

    def _run_extension_code(self, ext, arguments: dict):
        """code 档：跑用户（或模型）写的 Python。

        **走子进程。** 理由不是「更安全」—— 它当然能在用户机器上干任何事，
        这一点在审批卡片上已经说清楚了。走子进程是为了：
        * 卡死/崩溃不带走小爪本身；
        * 能设超时；
        * 输出可控（不让它往 stdout 写脏东西影响我们的界面）。

        参数通过环境变量传，结果从 stdout 收。
        """
        import subprocess

        code = (ext.code or "").strip()
        if not code:
            return False, "这个自定义工具没有代码"

        # 拼一段收尾，把结果打到 stdout
        wrapper = (
            code
            + "\n\nimport json as _json, os as _os\n"
            "_args = _json.loads(_os.environ.get('PAWPET_EXT_ARGS') or '{}')\n"
            "if 'run' in dir():\n"
            "    _out = run(_args)\n"
            "    print('' if _out is None else _out)\n"
        )
        import os as _os

        env = dict(_os.environ)
        env["PAWPET_EXT_ARGS"] = json.dumps(arguments or {}, ensure_ascii=False)
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            result = subprocess.run(
                [sys.executable, "-c", wrapper],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, env=env,
            )
        except subprocess.TimeoutExpired:
            return False, f"「{ext.title}」跑了超过 30 秒，先掐掉了。"
        except OSError as exc:
            return False, f"跑不起来：{exc}"

        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            tail = detail[-1] if detail else "（没有错误输出）"
            return False, f"「{ext.title}」报错了：{tail}"
        return True, (result.stdout or "").strip() or "（没有输出）"

    # ---------------------------------------------------------- MCP
    def mcp_clients(self) -> list:
        """当前连着的 MCP server。

        从 backend 拿，而不是让工具层持有 MCPClient —— ToolContext 是
        每轮新建的轻量对象，连接的生命周期归 controller 管。
        """
        backend = self.backend
        controller = getattr(backend, "ai", None) if backend is not None else None
        clients = getattr(controller, "_mcp_clients", None)
        return list(clients) if clients else []

    def mcp_tools(self) -> list[dict]:
        """把所有 MCP server 的工具拼成 OpenAI 格式。

        每轮都重新取 —— 用户在会话中间连上一个 server，下一步就该看见它。
        """
        merged: list[dict] = []
        for client in self.mcp_clients():
            if not getattr(client, "running", False):
                continue
            try:
                merged.extend(client.as_openai_tools())
            except Exception:  # noqa: BLE001 - 一个 server 出问题不该拖垮整轮
                continue
        return merged

    def call_mcp_tool(self, name: str, arguments: dict):
        """把 mcp_xxx_yyy 路由给对应的 server。

        名字拆解不能简单 split("_")：server 名和工具名里都可能有下划线。
        所以拿每个已连接 server 的前缀去比，**取最长的那个匹配** ——
        否则 server 名叫 `a` 和 `a_b` 时会路由错。
        """
        best = None
        best_tool = ""
        for client in self.mcp_clients():
            if not getattr(client, "running", False):
                continue
            prefix = f"mcp_{client.name}_"
            if name.startswith(prefix) and (best is None
                                            or len(prefix) > len(f"mcp_{best.name}_")):
                best = client
                best_tool = name[len(prefix):]

        if best is None or not best_tool:
            known = ", ".join(c.name for c in self.mcp_clients()) or "（没有）"
            return False, (f"没有名为 {name} 的工具。"
                           f"当前连着的 MCP：{known}。"
                           "可能这个 server 已经断开了。"), None

        ok, text = best.call_tool(best_tool, arguments)
        if not ok:
            return False, f"MCP「{best.name}」的 {best_tool} 失败：{text}", None
        return True, text, None

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

    def _do_ui_forget_hangs(self, _args: dict):
        got, reason = self._uia()
        if got is None:
            return False, reason, None
        _client, uia_mod = got

        known = uia_mod.known_hanging_windows()
        cleared = uia_mod.forget_hanging_windows()
        if not cleared:
            return True, "当前没有被标记为「读不到控件」的窗口。", None

        lines = [f"已清掉 {cleared} 个窗口的记录："]
        for pid, handle, title in known[:10]:
            lines.append(f"  · {title or '(无标题)'}（pid={pid}）")
        lines.append("现在可以重新试 ui_controls 了。")
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

        # 读控件树可能很慢（甚至超时），给足时间但也设上限。
        # 已知读不动的窗口会在 tree() 里被熔断，立刻返回而不再等超时。
        ok, result = uia.call_with_timeout(
            lambda: client.tree(window, max_elements=limit), timeout=12.0)
        if not ok:
            return False, (f"读取「{window.title}」的控件超时。\n"
                           "这个程序可能不支持辅助功能接口"
                           "（用 Tk/Qt 自绘界面的程序常见）。\n"
                           "改用截图观察，或者用 ui_element_at 按坐标确认控件。"), None

        elements, note = result
        if not elements:
            return True, note, None

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
        title = str(args.get("title") or "")
        result = self.actions.activate_window(title)
        if result.ok:
            self._observe_app(title)
        return result.ok, result.message, None

    # ------------------------------------------------------- 被动观察常用程序
    def _observe_app(self, keyword: str, max_apps: int = 5, threshold: int = 3):
        """留意用户常切到哪些程序，用够了就记进长期记忆。

        为什么要被动学：让模型每次都用 remember 记「他用了 WPS」太浪费 ——
        那是一句废话，还占一次工具往返。而「常用程序」是有用的长期信息：
        下次它就知道该找哪个窗口，不用用户再说一遍「还是用 WPS」。

        threshold 是「用够几次才算习惯」。只切一次就记下来的话，
        偶发操作会被当成习惯，记忆很快就脏了。
        """
        keyword = (keyword or "").strip()
        if not keyword or len(keyword) > 60:
            return

        apps = self.store.memory.setdefault("app_usage", {})
        if not isinstance(apps, dict):
            apps = {}
            self.store.memory["app_usage"] = apps

        now = time.time()
        key = keyword.lower()
        entry = apps.get(key)
        if isinstance(entry, dict):
            entry["count"] = int(entry.get("count") or 0) + 1
            entry["last"] = now
            entry["label"] = keyword
        else:
            entry = {"count": 1, "last": now, "label": keyword}
            apps[key] = entry

        # 只留最常用的几个，别让这个表无限长大
        if len(apps) > 20:
            ordered = sorted(apps.items(),
                             key=lambda kv: kv[1].get("count", 0), reverse=True)
            self.store.memory["app_usage"] = dict(ordered[:20])
            apps = self.store.memory["app_usage"]

        # 到阈值了，作为一条「环境」类记忆写下来
        if entry["count"] == threshold:
            try:
                from .memory import MemoryBook

                # MemoryBook.add_fact 自己会 save，所以上面只更新计数、
                # 不单独存一次盘 —— 切个窗口写两次盘是没必要的开销。
                MemoryBook(self.store).add_fact(
                    f"常用程序：{keyword}", category="environment",
                    confidence=2, source="observed")
                return
            except Exception:  # noqa: BLE001 - 观察失败绝不能影响正在做的事
                pass

        self.store.save()

    def frequent_apps(self, limit: int = 5) -> list[tuple[str, int]]:
        """最常用的程序，(名字, 次数) 列表。"""
        apps = self.store.memory.get("app_usage")
        if not isinstance(apps, dict):
            return []
        items = []
        for entry in apps.values():
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or "").strip()
            if label:
                items.append((label, int(entry.get("count") or 0)))
        items.sort(key=lambda pair: pair[1], reverse=True)
        return items[:limit]

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

    # ------------------------------------------------------------ 知识库
    def _kb(self):
        from .kb import KnowledgeBase

        return KnowledgeBase(self.store)

    def _do_import_knowledge(self, args: dict):
        from . import files, kb

        path = str(args.get("path") or "").strip()
        if not path:
            return False, "要导入哪个文件或文件夹？给个路径", None
        recursive = bool(args.get("recursive"))

        try:
            target = files.expand(path)
        except files.FileDenied as exc:
            return False, str(exc), None

        book = self._kb()
        existing = book.load()

        if target.is_dir():
            incoming, notes = kb.import_folder(str(target), existing,
                                               recursive=recursive)
            if not incoming:
                detail = "；".join(notes[:3]) if notes else "这个目录里没有可导入的文本文件"
                return False, f"没能导入任何资料：{detail}", None
            merged = book.add(incoming)
            total_chunks = sum(doc.chunk_count for doc in incoming)
            lines = [f"已导入 {len(incoming)} 份资料，共 {total_chunks} 块。",
                     f"知识库现在有 {len([d for d in merged if d.chunks])} 份资料。",
                     "这些内容**不会**全部塞进对话，需要时用 search_knowledge 检索。"]
            if notes:
                lines.append("")
                lines.extend(f"· {note}" for note in notes[:5])
            return True, "\n".join(lines), None

        doc, message = kb.import_file(str(target), existing)
        if doc is None:
            return False, message, None
        merged = book.add([doc])
        return True, (f"{message}\n"
                      f"知识库现在有 {len([d for d in merged if d.chunks])} 份资料。"
                      "需要时用 search_knowledge 检索里面的内容。"), None

    def _do_search_knowledge(self, args: dict):
        from . import kb

        query = str(args.get("query") or "").strip()
        if not query:
            return False, "要查什么？", None
        try:
            limit = int(args.get("limit") or kb.DEFAULT_HITS)
        except (TypeError, ValueError):
            limit = kb.DEFAULT_HITS

        docs = self._kb().load()
        if not any(doc.chunks for doc in docs):
            return True, ("知识库还是空的。"
                          "可以让我把某个文件夹导入知识库 —— "
                          "用户说「把这个文件夹导入知识库」并给出路径即可。"), None

        hits = kb.search(docs, query, limit=limit)
        if not hits:
            names = "、".join(doc.name for doc in docs if doc.chunks)[:200]
            return True, (f"知识库里没有和「{query}」相关的内容。\n"
                          f"现有资料：{names}\n"
                          "可以告诉用户没找到，或者问他要不要先导入相关文件。"), None

        # 按块分配预算，总量受控 —— 检索结果是要发出去的，不能太大
        budget = max(300, kb.MAX_TOTAL_CHARS // max(1, len(hits)))
        body = "\n\n".join(hit.render(budget) for hit in hits)
        return True, (f"在知识库里找到 {len(hits)} 段相关内容：\n\n{body}\n\n"
                      "引用时说明出处（文件名 · 标题）。"
                      "如果这些还不够，可以换个说法再检索一次。"), None

    def _do_list_knowledge(self, _args: dict):
        from . import kb

        docs = self._kb().load()
        if not docs:
            return True, "知识库是空的，还没有导入任何资料。", None
        return True, kb.describe(docs), None

    def _do_forget_knowledge(self, args: dict):
        name = str(args.get("name") or "").strip()
        if not name:
            return False, "要移除哪份资料？给个文件名（可以是其中一部分）", None
        removed = self._kb().remove(name)
        if removed is None:
            return True, (f"知识库里没有叫「{name}」的资料。"
                          "可以用 list_knowledge 看看现在有哪些。"), None
        return True, f"已从知识库移除「{removed.name}」（{removed.chunk_count} 块）", None

    # ------------------------------------------------------------ 本地文件
    def _do_read_file(self, args: dict):
        from . import files

        path = str(args.get("path") or "").strip()
        if not path:
            return False, "要读哪个文件？给个路径", None

        try:
            start = int(args.get("start_line") or 1)
        except (TypeError, ValueError):
            start = 1
        try:
            lines = int(args.get("max_lines") or 2000)
        except (TypeError, ValueError):
            lines = 2000

        try:
            result = files.read_text(path, start_line=start, max_lines=lines)
        except Exception as exc:  # noqa: BLE001 - 读文件不该把整轮搞挂
            return False, f"读文件出错：{type(exc).__name__}: {exc}", None

        if not result.ok:
            return False, result.message, None
        if not result.text.strip():
            return True, result.message + "\n（文件里没有可显示的文本内容）", None
        return True, result.message + "\n\n" + result.text, None

    def _do_list_dir(self, args: dict):
        from . import files

        path = str(args.get("path") or "").strip() or "."
        pattern = str(args.get("pattern") or "").strip()
        try:
            result = files.list_dir(path, pattern=pattern)
        except Exception as exc:  # noqa: BLE001
            return False, f"列目录出错：{type(exc).__name__}: {exc}", None
        if not result.ok:
            return False, result.message, None
        return True, result.message + "\n" + result.text, None

    def _do_write_file(self, args: dict):
        from . import files

        path = str(args.get("path") or "").strip()
        if not path:
            return False, "要写到哪个路径？", None
        content = args.get("content")
        if content is None:
            return False, "没有给内容，写个空文件也没意义", None
        overwrite = bool(args.get("overwrite"))

        try:
            result = files.write_text(path, str(content), overwrite=overwrite)
        except Exception as exc:  # noqa: BLE001
            return False, f"写文件出错：{type(exc).__name__}: {exc}", None

        if not result.ok:
            return False, result.message, None
        return True, result.message, None

    # ------------------------------------------------------------ 跨会话记忆
    def _memory_book(self):
        from .memory import MemoryBook

        return MemoryBook(self.store)

    # 用户明确要求「记住」的说法。命中就说明这条是用户主动交代的，
    # 该标成 told 而不是小爪自己学的。
    _ASKED_TO_REMEMBER = (
        "记住", "记下来", "记一下", "帮我记", "别忘", "不要忘",
        "以后都", "以后别", "记住我",
    )

    def _user_asked_to_remember(self) -> bool:
        """最近一条用户消息里，用户是不是明确要求记住什么。"""
        for item in reversed(getattr(self, "recent_user_texts", []) or []):
            text = str(item or "")
            if any(marker in text for marker in self._ASKED_TO_REMEMBER):
                return True
        return False

    def _do_remember(self, args: dict):
        text = str(args.get("text") or "").strip()
        if not text:
            return False, "要记的内容不能为空", None

        category = str(args.get("category") or "other").strip().lower()
        try:
            confidence = int(args.get("confidence") or 2)
        except (TypeError, ValueError):
            confidence = 2

        try:
            from .memory import KIND_TOLD, KIND_TOOL

            # 用户明说「记住…」时，这条要标成 told —— 界面上会区分显示，
            # 用户才知道哪些是自己要求的、哪些是小爪自己学的
            kind = KIND_TOLD if self._user_asked_to_remember() else KIND_TOOL
            fact, created = self._memory_book().add_fact(
                text, category, confidence, source="ai", kind=kind)
        except ValueError as exc:
            return False, str(exc), None

        if created:
            message = f"已记住：{fact.text}"
        else:
            message = f"这条之前记过，已更新：{fact.text}"
        if fact.confidence >= 3:
            message += "\n（标记为「确定」，以后会直接按这个来）"
        elif fact.confidence <= 1:
            message += "\n（标记为「不确定」，用的时候我会再确认）"
        return True, message, None

    def _do_forget_memory(self, args: dict):
        text = str(args.get("text") or "").strip()
        if not text:
            return False, "要忘掉什么？给一段内容片段", None

        removed = self._memory_book().forget(text)
        if removed is None:
            return True, (f"没有找到和「{text}」匹配的记忆，可能本来就没记过。"
                          "可以用 recall_memory 看看现在都记了什么。"), None
        return True, f"已忘掉：{removed.text}", None

    def _do_recall_memory(self, args: dict):
        from .memory import CATEGORY_LABELS, describe

        book = self._memory_book()
        memory = book.load()
        if memory.is_empty():
            return True, "现在还没有任何记忆。", None

        keyword = str(args.get("keyword") or "").strip()
        if not keyword:
            return True, describe(memory), None

        from .memory import normalize

        needle = normalize(keyword)
        hits = [f for f in memory.facts if needle in f.key]
        aliases = [a for a in memory.aliases if needle in a.key
                   or needle in normalize(a.actual)]
        tasks = [t for t in memory.tasks if needle in t.key]

        if not (hits or aliases or tasks):
            return True, (f"没有和「{keyword}」相关的记忆。"
                          "可以用 remember 记下来。"), None

        lines = [f"和「{keyword}」相关的记忆：", ""]
        for fact in hits:
            label = CATEGORY_LABELS.get(fact.category, "其他")
            lines.append(f"  [{label}] {fact.text}")
        for alias in aliases:
            lines.append(f"  叫法：「{alias.spoken}」→「{alias.actual}」")
        for task in tasks:
            lines.append(f"  任务：[{task.status}] {task.text}")
        return True, "\n".join(lines), None

    def _do_note_task_state(self, args: dict):
        text = str(args.get("text") or "").strip()
        if not text:
            return False, "任务描述不能为空", None

        status = str(args.get("status") or "open").strip().lower()
        next_step = str(args.get("next_step") or "").strip()
        if status not in ("open", "done", "blocked"):
            status = "open"

        task = self._memory_book().note_task(text, status, next_step)
        if status == "done":
            return True, f"已把「{task.text}」标记为完成，不再挂在待办里。", None
        if status == "blocked":
            return True, (f"已记下「{task.text}」卡住了。"
                          "下次会提醒你先解决这个。"), None
        message = f"已记下进度：「{task.text}」"
        if task.next_step:
            message += f"\n下次接着做：{task.next_step}"
        return True, message, None

    # ------------------------------------------------------------ 造新工具
    def _ext_book(self):
        """拿扩展注册表。存在数据目录，和记忆/知识库一个套路。"""
        from .extensions import load_all, save_all

        path = self.store.path.parent / "extensions.json"
        return path, load_all(path), save_all

    def _do_propose_extension(self, args: dict):
        """生成草稿。**不生效** —— 生效要过 install_extension 那一步确认。"""
        import time as _time

        from .extensions import LEVEL_HINTS, LEVEL_LABELS, Extension

        level = str(args.get("level") or LEVEL_RECIPE).strip().lower()
        steps = args.get("steps")
        ext = Extension(
            name=str(args.get("name") or "").strip().lower(),
            title=str(args.get("title") or "").strip(),
            description=str(args.get("description") or "").strip(),
            level=level,
            prompt=str(args.get("prompt") or "").strip(),
            steps=[s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else [],
            parameters=args.get("parameters") if isinstance(args.get("parameters"), dict) else {},
            code=str(args.get("code") or "").strip(),
            created=_time.time(),
            updated=_time.time(),
        )

        issues = ext.problems(self._max_extension_level())
        if issues:
            return False, ("这个定义还有问题，改一下再来：\n  · "
                           + "\n  · ".join(issues)), None

        # 同名的已存在 → 算升级，不是新建
        _path, existing, _save = self._ext_book()
        old = next((item for item in existing if item.name == ext.name), None)
        if old is not None:
            ext.created = old.created or ext.created
            ext.version = int(old.version or 1) + 1

        action = "更新" if old is not None else "新建"
        message = (
            f"草稿做好了（{action}）。**还没有生效**，要给用户看过才算装上。\n\n"
            f"{ext.summary()}\n\n"
            f"接下来：跟用户说清楚「会做什么」，他同意之后调 install_extension，"
            f"把下面这个 draft 原样传回去。\n"
            f"draft = {json.dumps(ext.as_dict(), ensure_ascii=False)}"
        )
        return True, message, None

    def _do_install_extension(self, args: dict):
        from .extensions import Extension, LEVEL_LABELS, as_openai_tools, save_all

        draft = args.get("draft")
        if not isinstance(draft, dict):
            return False, "draft 要传 propose_extension 返回的那个对象", None

        ext = Extension.from_dict(draft)
        issues = ext.problems(self._max_extension_level())
        if issues:
            return False, ("这个定义不能装：\n  · " + "\n  · ".join(issues)), None

        ext.approved = True
        ext.updated = __import__("time").time()
        if not ext.created:
            ext.created = ext.updated

        path, existing, _save = self._ext_book()
        replaced = False
        for index, item in enumerate(existing):
            if item.name == ext.name:
                existing[index] = ext
                replaced = True
                break
        if not replaced:
            existing.append(ext)

        ok, message = save_all(path, existing)
        if not ok:
            return False, message, None

        total = len(as_openai_tools(existing))
        return True, (
            f"装好了：「{ext.title}」（{LEVEL_LABELS.get(ext.level, ext.level)}）。"
            f"现在一共 {total} 个自定义工具，下一步开始模型就能调用它。\n"
            "想卸掉随时说一声。"
        ), None

    def _do_list_extensions(self, args: dict):
        from .extensions import LEVEL_LABELS

        _path, items, _save = self._ext_book()
        if not items:
            return True, ("还没有自定义工具。用户反复做同一件事时，"
                          "可以用 propose_extension 给他固化一个。"), None
        lines = [f"已装 {len(items)} 个自定义工具："]
        for ext in items:
            mark = "" if ext.approved else "（未确认，不生效）"
            lines.append(f"  {ext.name}  {ext.title}"
                         f"  [{LEVEL_LABELS.get(ext.level, ext.level)}]{mark}"
                         f"  用过 {ext.run_count} 次")
            if ext.description:
                lines.append(f"      {ext.description}")
        lines.append("")
        lines.append("卸掉用 remove_extension。")
        return True, "\n".join(lines), None

    def _do_remove_extension(self, args: dict):
        from .extensions import save_all

        name = str(args.get("name") or "").strip().lower()
        if not name:
            return False, "要卸掉哪一个？给我工具名。", None
        path, items, _save = self._ext_book()
        kept = [item for item in items if item.name != name]
        if len(kept) == len(items):
            names = "、".join(item.name for item in items) or "（一个都没有）"
            return False, f"没有叫「{name}」的自定义工具。现有的：{names}", None
        ok, message = save_all(path, kept)
        if not ok:
            return False, message, None
        return True, f"已卸掉「{name}」。", None

    def _max_extension_level(self) -> str:
        """当前允许造到哪一档。

        `ai_extension_level` 默认是 recipe —— **code 档默认关着**。
        理由是「用户让 AI 造的东西在用户电脑上执行任意代码」这件事，
        对泛用户不可接受；给愿意承担的人留开关，但默认不打开。
        """
        from .extensions import MAX_LEVEL_DEFAULT

        value = str(self.store.settings.get("ai_extension_level")
                    or MAX_LEVEL_DEFAULT).strip().lower()
        if value in (LEVEL_NOTE, LEVEL_RECIPE, LEVEL_CODE):
            return value
        return MAX_LEVEL_DEFAULT

    def _do_set_theme_color(self, args: dict):
        """改界面配色。"""
        from .. import theme as theme_mod

        raw = args.get("colors")
        if not isinstance(raw, dict) or not raw:
            return False, ("要改哪些颜色？形如 {\"accent\": \"#4a90d9\"}。"
                           "用 list_theme_colors 看可改的项。"), None

        # 这一轮用户的原话 —— 界面要问用户「确认吗」时用不上，
        # 但收尾交代里要点明「你刚改了界面配色」，所以留个记录。
        clean, rejected = theme_mod.sanitize(raw)
        if not clean:
            return False, ("没有可用的改动。\n" + "\n".join(rejected)
                           + "\n颜色要写成 #rrggbb，比如 #4a90d9。"), None

        backend = self.backend
        apply = getattr(backend, "applyTheme", None)
        if not callable(apply):
            return False, "界面配色接口不可用（Backend 没接上）", None

        still_rejected = apply(clean)
        if still_rejected:
            # Backend 又拒了一次（写文件失败之类）
            return False, "改配色失败：" + "；".join(still_rejected), None

        lines = []
        for key, value in clean.items():
            role = next((r for r in theme_mod.all_roles() if r["key"] == key), None)
            lines.append(f"{role['label'] if role else key} → {value}")
        message = "已改好界面配色：" + "、".join(lines) + "\n改完立刻生效，不用重启。"
        if rejected:
            message += "\n（这些没改：" + "；".join(rejected) + "）"
        return True, message, None

    def _do_list_theme_colors(self, args: dict):
        from .. import theme as theme_mod

        backend = self.backend
        current = {}
        try:
            current = backend.themeColors if backend is not None else {}
        except Exception:  # noqa: BLE001
            current = {}

        lines = ["界面里可以改的配色项（键名 / 中文含义 / 当前值）："]
        for role in theme_mod.editable_roles():
            value = current.get(role["key"], role["default"])
            changed = (str(value).lower() != role["default"].lower())
            mark = "  ← 用户改过" if changed else ""
            lines.append(f"  {role['key']:12s} {role['label']:12s} {value}{mark}")
            if role["hint"]:
                lines.append(f"               {role['hint']}")
        lines.append("")
        lines.append("错误色（rose）固定不可改 —— 改成和背景相近用户就看不到报错了。")
        lines.append("改的时候用 set_theme_color，值要是 #rrggbb 形式。")
        return True, "\n".join(lines), None

    def _do_reset_theme(self, args: dict):
        backend = self.backend
        reset = getattr(backend, "resetTheme", None)
        if not callable(reset):
            return False, "界面配色接口不可用（Backend 没接上）", None
        reset()
        return True, "界面配色已恢复成默认的粉白。", None

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
