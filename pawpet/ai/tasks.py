"""现成任务模板 —— 「点一下就跑」，不用用户自己想怎么问。

为什么要这个
------------
这个功能的真正门槛不是模型能力，是**用户不知道该说什么**。
第一次打开界面看到空白的输入框，绝大多数人只会打一句「你好」然后关掉。
给几个具体的例子，用户才会意识到「原来这个能帮我干这个」。

图片里那张需求写得比我清楚，直接照抄它的分类：
    首页直接列几个能点就跑的任务
    分人群模板（学生：找文件/转格式/压作业；上班党：跨系统搬数据/填表/做汇总）

设计上的几条规矩
----------------
* **每条都要能真的跑完。** 模板是提示词，写得含糊就会得到一段
  「我可以帮你做 X」的空话。所以每条都写清「做什么 + 做完怎么交代」。
* **不留占位符。** 不用「把 {文件夹} 里的文件整理一下」这种 ——
  用户点了还得自己补，那还不如直接打字。改成能直接执行的版本，
  需要路径的地方让 AI 自己问，或者用当前桌面这种确定的位置。
* **按人群分，但不强绑。** 「学生」「上班党」只是分组标签，
  谁都可以点任意一条。标签的作用是让用户快速对号入座。
"""

from __future__ import annotations

from dataclasses import dataclass

# 分组 key。界面上按这个顺序排。
GROUP_STUDENT = "student"
GROUP_WORK = "work"
GROUP_EVERYDAY = "everyday"

GROUP_LABELS = {
    GROUP_STUDENT: "学生",
    GROUP_WORK: "上班党",
    GROUP_EVERYDAY: "日常",
}

# 图标用 emoji，不引额外字体
_GROUP_ICONS = {
    GROUP_STUDENT: "🎒",
    GROUP_WORK: "💼",
    GROUP_EVERYDAY: "🏠",
}


@dataclass(frozen=True)
class TaskTemplate:
    """一条可以点了就跑的现成任务。"""

    key: str
    group: str
    label: str          # 按钮上的短标题
    hint: str           # 按钮下面一句更具体的说明
    prompt: str         # 真正发给模型的话

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "group": self.group,
            "groupLabel": GROUP_LABELS.get(self.group, self.group),
            "groupIcon": _GROUP_ICONS.get(self.group, "🐾"),
            "label": self.label,
            "hint": self.hint,
            "prompt": self.prompt,
        }


# ==========================================================================
#  模板清单
# ==========================================================================
# 每条 prompt 的写法：先说做什么，再说交付什么。
# 「交付什么」这一句很关键 —— 没有它模型干完就沉默了，
# 用户不知道到底做成了没有。
TEMPLATES: tuple[TaskTemplate, ...] = (
    # ------------------------------------------------------------ 学生
    TaskTemplate(
        key="sort_folder",
        group=GROUP_STUDENT,
        label="帮我整理这个文件夹",
        hint="按类型分好类，告诉你都动了哪些文件",
        prompt=(
            "帮我整理「下载」文件夹。先列出里面有什么，按类型归类"
            "（文档、图片、压缩包、安装包、其他），在下载文件夹里建好对应的子文件夹再移进去。"
            "重名的不要覆盖，加个序号。"
            "做完告诉我：一共整理了多少个文件、分成了哪几类、有没有不确定放哪的。"
        ),
    ),
    TaskTemplate(
        key="screenshot_to_text",
        group=GROUP_STUDENT,
        label="把这张截图转成文字",
        hint="先看一眼屏幕，把上面的文字提取出来",
        prompt=(
            "看一下我现在的屏幕，把上面的文字提取出来。"
            "如果是讲义、课件或题目，按原本的段落和层级整理，别丢内容。"
            "直接把整理好的文字发给我，方便我复制。"
        ),
    ),
    TaskTemplate(
        key="summarize_table",
        group=GROUP_STUDENT,
        label="这张表按部门汇总",
        hint="看着屏幕上的表格，算出各组小计",
        prompt=(
            "屏幕上如果有一张表格，按里面的分组字段（部门/班级/类别这类）汇总，"
            "算出每一组的小计和总计。"
            "如果没有表格，先告诉我屏幕上是什么，别自己编数据。"
        ),
    ),
    TaskTemplate(
        key="find_file",
        group=GROUP_STUDENT,
        label="找找我那份作业",
        hint="按关键词全盘找，列出改过时间的先后",
        prompt=(
            "帮我在电脑里找文件。先问我一句要找什么关键词，"
            "然后在我的用户目录下搜索文件名匹配的文件，"
            "按最后修改时间从新到旧列出最相关的 10 个，带上完整路径。"
        ),
    ),
    TaskTemplate(
        key="zip_homework",
        group=GROUP_STUDENT,
        label="把作业打包压缩",
        hint="收齐一个文件夹里的材料，压成一个包",
        prompt=(
            "帮我把作业打包。先问我作业在哪个文件夹、压缩包叫什么名字，"
            "然后把这个文件夹里的内容打包成一个压缩包放在同级目录。"
            "做完告诉我压缩包的完整路径和大小。"
        ),
    ),
    # ------------------------------------------------------------ 上班党
    TaskTemplate(
        key="move_data",
        group=GROUP_WORK,
        label="跨系统搬数据",
        hint="一边读一边填，读完核对再提交",
        prompt=(
            "帮我跨系统搬数据。先看一眼我的屏幕，告诉我你看到的两个系统分别是什么，"
            "然后我们确认要搬哪些字段。确认之后：从源系统逐条读数，"
            "在目标系统里填进去，每填几条就核对一次，别填串行。"
            "全部弄完告诉我搬了多少条、有没有对不上的。"
        ),
    ),
    TaskTemplate(
        key="fill_form",
        group=GROUP_WORK,
        label="帮我填这张表",
        hint="看着屏幕上的表单逐项填，不确定的先问你",
        prompt=(
            "屏幕上有一张表单要填。先看一眼屏幕，把表单上有哪些字段念给我听，"
            "然后我告诉你每个字段填什么。填的时候一项一项来，"
            "遇到你没把握的字段先停下来问我，别自己猜。"
        ),
    ),
    TaskTemplate(
        key="make_summary",
        group=GROUP_WORK,
        label="把这些做成汇总",
        hint="把屏幕上的数据汇总成一份小结",
        prompt=(
            "看一下我屏幕上的数据（表格、清单、报表都行），做一份汇总。"
            "先告诉我数据是什么、有多少条，然后给出：总量、分类小计、"
            "最突出的三项。汇总文字直接发给我，别写文件除非我要求。"
        ),
    ),
    TaskTemplate(
        key="organize_desktop",
        group=GROUP_WORK,
        label="桌面乱得没法看了",
        hint="把桌面按用途分堆，不删任何东西",
        prompt=(
            "我的桌面太乱了，帮我收拾一下。先数一数桌面上有多少项、都是什么类型，"
            "然后按用途建几个文件夹（比如「待处理」「参考资料」「安装包」）分类放进去。"
            "**只移动，绝不删除**。做完告诉我每个文件夹里放了多少个。"
        ),
    ),
    # ------------------------------------------------------------ 日常
    TaskTemplate(
        key="look_screen",
        group=GROUP_EVERYDAY,
        label="看看我屏幕上是什么",
        hint="截一张，用大白话说明",
        prompt=(
            "看一下我的屏幕，用大白话告诉我现在是什么情况，"
            "有没有什么需要注意的。别念界面元素，说我关心的。"
        ),
    ),
    TaskTemplate(
        key="explain_error",
        group=GROUP_EVERYDAY,
        label="这报错什么意思",
        hint="解释原因，给出你能直接做的下一步",
        prompt=(
            "屏幕上如果有报错或者异常提示，用普通人能懂的话解释："
            "这是什么问题、为什么会这样、我能做什么。"
            "如果能直接帮我处理掉，就说明你打算怎么做再动手。"
        ),
    ),
    TaskTemplate(
        key="to_todo",
        group=GROUP_EVERYDAY,
        label="整理成待办",
        hint="把屏幕上的事逐条加进待办清单",
        prompt=(
            "看看屏幕上提到的待办事项，逐条整理出来加进我的待办清单里。"
            "同名的不重复添加。做完告诉我加了几条、分别是什么。"
        ),
    ),
    TaskTemplate(
        key="translate_screen",
        group=GROUP_EVERYDAY,
        label="翻译屏幕上的内容",
        hint="把主要外文翻成中文",
        prompt=(
            "把屏幕上主要的外文内容翻译成中文，保持原来的结构，"
            "专有名词后面用括号附上原文。直接发给我。"
        ),
    ),
)


def all_templates() -> list[dict]:
    """给界面用的完整清单（已转成 QML 能吃的 dict）。"""
    return [item.as_dict() for item in TEMPLATES]


def groups() -> list[dict]:
    """按人群分组，界面上分栏显示。"""
    buckets: dict[str, list[dict]] = {}
    for item in TEMPLATES:
        buckets.setdefault(item.group, []).append(item.as_dict())
    return [
        {
            "key": group,
            "label": GROUP_LABELS[group],
            "icon": _GROUP_ICONS[group],
            "items": buckets[group],
        }
        for group in (GROUP_STUDENT, GROUP_WORK, GROUP_EVERYDAY)
        if buckets.get(group)
    ]


def find(key: str) -> TaskTemplate | None:
    for item in TEMPLATES:
        if item.key == key:
            return item
    return None
