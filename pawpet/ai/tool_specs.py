"""内置 AI 工具目录。

工具执行逻辑位于 tools.py；把静态描述和 OpenAI 格式转换独立出来，
新增工具时只改这个文件，避免和屏幕、文件、MCP 执行逻辑混在一起。
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import Risk
from .extensions import LEVEL_CODE, LEVEL_NOTE, LEVEL_RECIPE


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
        "给小爪助手加一条定时提醒。"
        "用户说「每天九点提醒我打卡」用 daily；"
        "说「每 30 分钟提醒我起来看看远处」用 interval 并把 every 填 30。",
        _schema({
            "title": {**_STRING, "description": "提醒内容"},
            "time": {**_STRING,
                     "description": "24 小时制时间，例如 09:30。"
                                    "repeat 是 interval 时可以不给"},
            "repeat": {**_STRING,
                       "enum": ["once", "daily", "weekdays", "weekly",
                                "interval"],
                       "description": "重复方式，默认 daily。"
                                      "interval = 每隔 N 分钟"},
            "every": {**_INT,
                      "description": "仅 repeat=interval 时用：间隔多少分钟。"
                                     "例如「每 5 分钟」填 5，"
                                     "「每小时」填 60。范围 1~720"},
        }, ["title"]),
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
    # 三档能力（见 extensions.py）。**输入给模型的说明书里不写「code 默认
    # 关着」了 —— 那句和实际不符**（常量就是 code，见 extensions.py）。
    # 但「别主动提」保留：code 是能力最强也最需要用户自己想清楚的一档，
    # 该模型顺着用户的话提，不该它来推销。
    #   note   固定说法 —— 一段提示词，不碰任何东西
    #   recipe 固定流程 —— 一串**只读**步骤
    #   code   自定义代码 —— 安装和每次运行都要用户确认
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
        f"  · {LEVEL_CODE}：自定义代码。能力最强的一档，别主动提\n"
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
        # **CRITICAL，不是 CONFIRM。**
        #
        # 这个工具的字面承诺就是「会弹一张卡片让用户确认」，而 CONFIRM 在
        # 「自动执行」和「完全自动」两档下**不问**（actions.needs_approval）。
        # 于是当时那条链是：造草稿不问 → 安装不问 → 调用也不问 → 子进程跑
        # 任意 Python，一张卡片都不弹，和承诺完全相反。
        #
        # 换成 CRITICAL 之后它在**任何**权限档位下都要问一次。安装是一次性
        # 动作（一个工具装一次），换来的是「用户亲手过一遍这段代码要干什么」，
        # 这个代价不高。
        Risk.CRITICAL,
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
        "**卸掉**一个自定义工具。用户说「那个不要了」「删掉」时用它。"
        "卸掉的定义会进回收站，用 restore_extension 能捞回来。",
        _schema({
            "name": {**_STRING, "description": "要卸掉的工具名"},
        }, ["name"]),
        # **必须是 CONFIRM，不能是 READ。**
        #
        # 它删的是用户自己攒下来的东西（实测这台机器上有 17 个手工打磨的
        # 工具），而模型只听错一句话就能毁掉一个。原来这里是 READ ——
        # 不确认、不提示、也没有后悔药。
        Risk.CONFIRM,
    ),
    ToolSpec(
        "restore_extension",
        "从回收站把卸掉的自定义工具**捞回来**。用户说「把那个加回来」"
        "「刚才删错了」时用它。",
        _schema({
            "name": {**_STRING, "description": "要恢复的工具名"},
        }, ["name"]),
        # 恢复等于重新启用一段可能执行代码的定义，也要用户点头
        Risk.CONFIRM,
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
