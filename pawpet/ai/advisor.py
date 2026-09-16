r"""失败自纠：一步失败之后，判断该重试、该换路、还是该停下来问人。

问题
----
原来工具失败时，回给模型的只有一句原始错误（「UI Automation 查询超时」），
没有任何**怎么办**。模型只能自己猜，猜错了就变成：

* **瞎重复**：同一个操作失败三次还在试，白烧步数和钱
* **卡死**：读不到控件也不知道退到截图，就一直读
* **假装成功**：换个说法把没做成的事说成做成了

这个模块做的事很具体：**失败之后，给模型一句可执行的下一步。**

怎么判断（不靠模型，靠规则）
----------------------------
失败信息其实是可分类的，每类都有对应的正确退路：

| 失败类型 | 该怎么办 |
| --- | --- |
| 读控件超时 | 这个程序不支持辅助功能 —— 换截图 + 坐标，别重试 |
| 没找到窗口/控件 | 窗口可能关了或改名了 —— 先重新枚举，不要原地重试 |
| 被权限拦下 | 这是安全策略，重试没用 —— 告诉用户去调权限 |
| 用户拒绝 | 别再试同一个东西 —— 问用户想怎么办 |
| 窗口在动 | 截图/读控件时界面在变化 —— 等一下再来 |
| 参数不对 | 改参数重试（这一类**才**值得重试） |

**关键区别：只有「参数不对 / 网络抖动」值得重试，其余都该换路。**
模型天然倾向于重试同一个动作，所以这里要明确把它推离那条路。

另外还有三道闸
--------------
1. **同一件事失败 2 次** → 提醒它按系统提示词的要求停下来换路
2. **同一个调用（工具+参数）失败 3 次** → **直接拦掉**，不再执行 ——
   这是硬止损，防止它把 20 步全花在同一个失败动作上
3. **同一个工具累计失败 5 次** → 提示很可能这条路整体走不通

这一层是纯规则的：不花 token、不增加延迟、行为可预测。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

# ------------------------------------------------------------------ 判定阈值
SAME_ACTION_WARN = 2      # 同一个动作失败多少次开始提醒换路
SAME_ACTION_BLOCK = 3     # 同一个调用失败多少次直接拦掉
SAME_TOOL_WARN = 5        # 同一个工具累计失败多少次提示整体换路

# ---------------------------------------------------------------- 失败类型
KIND_TIMEOUT = "timeout"          # 超时
KIND_NOT_FOUND = "not_found"      # 目标不存在
KIND_PERMISSION = "permission"    # 权限/策略
KIND_DENIED = "denied"            # 用户拒绝
KIND_TRANSIENT = "transient"      # 界面在动，等一下就好
KIND_BAD_ARGUMENT = "bad_arg"     # 参数不对
KIND_MISSING_DEP = "missing_dep"  # 依赖没装
KIND_UNKNOWN = "unknown"          # 认不出来


@dataclass
class Advice:
    """一条给模型的恢复建议。"""

    kind: str = KIND_UNKNOWN
    label: str = ""            # 给界面看的一行短标签
    hint: str = ""             # 给模型看的具体下一步
    retryable: bool = False    # 值得原样重试吗
    escalate: bool = False     # 该停下来问用户吗
    blocked: bool = False      # 是否已经被止损拦下（工具根本没执行）
    repeats: int = 0           # 同一个动作到目前失败了几次
    lesson: str = ""           # 可选的、值得记进长期记忆的教训
    tally: str = ""            # 简短计数尾巴，每次失败都会带上

    def as_text(self) -> str:
        return self.hint or self.label


# ==========================================================================
#  分类规则
# ==========================================================================
# 顺序有意义：先匹配更具体的。
# 每条是 (正则, 类型)；用 search，所以不用写全匹配。
_RULES: list[tuple[str, str]] = [
    # 权限/策略（要在 timeout 之前 —— 「只读模式下不允许」里没有超时字样，
    # 但「已被安全策略拦截」必须优先于其它）
    (r"只读|已被安全策略拦截|不允许执行|permission|权限", KIND_PERMISSION),
    (r"用户拒绝|用户已拒绝|拒绝了", KIND_DENIED),
    (r"没有安装|未安装|缺少运行依赖|ImportError|No module named",
     KIND_MISSING_DEP),
    # 超时类的表述很杂，一并覆盖
    (r"超时|timed?\s*out|没有响应|无响应|未在时限内|放弃", KIND_TIMEOUT),
    # 找不到目标
    (r"没找到|找不到|没有找到|没有这个|不存在|未知工具|not\s*found|"
     r"没有读到|读不到", KIND_NOT_FOUND),
    # 参数/用法错误
    (r"参数|不能为空|需要指定|格式|不合法|非法|invalid|缺少", KIND_BAD_ARGUMENT),
    # 界面在动
    (r"正在|变化|忙|稍后|稍等|重试一下|busy", KIND_TRANSIENT),
]

# 每类失败对应的「下一步」。这是这个模块的核心价值：
# 把「失败了」变成「接下来这么做」。
_HINTS = {
    KIND_TIMEOUT: (
        "**不要再重试同一个操作。** 超时说明这个程序不响应辅助功能接口，"
        "重试还是一样。改用截图看画面，然后用坐标点击：\n"
        "  1. screenshot 看清当前画面\n"
        "  2. 目测目标位置，用 ui_click 传 x/y 点击\n"
        "  3. 点完再 screenshot 确认结果"
    ),
    KIND_NOT_FOUND: (
        "目标可能已经变了（窗口关了、改名了、控件重建了）。"
        "**先重新确认现状再动手**，不要用同样的参数重试：\n"
        "  · 窗口相关 → 先 ui_windows 看现在有哪些窗口\n"
        "  · 控件相关 → 先 screenshot 看界面现在长什么样\n"
        "  · 拿到新信息之后再决定下一步"
    ),
    KIND_PERMISSION: (
        "这是安全策略拦下的，**重试没有用**。"
        "直接告诉用户：当前「只读」模式下不允许这个操作，"
        "需要他在工作台把「操作权限」调高。然后停下来等他回答。"
    ),
    KIND_DENIED: (
        "用户拒绝了这个操作，**不要换个说法再试一次**。"
        "问他希望怎么处理，或者换一个他可能接受的方式，并说明区别。"
    ),
    KIND_TRANSIENT: (
        "界面当时正在变化。**等一下再试**：用 wait 等 1-2 秒，"
        "然后重新 screenshot 确认状态，再决定下一步。"
    ),
    KIND_BAD_ARGUMENT: (
        "参数不对，这一类**值得改对再试一次**：\n"
        "  · 检查参数名和取值是否符合工具说明\n"
        "  · 坐标要落在屏幕范围内\n"
        "  · 需要的参数是不是漏了"
    ),
    KIND_MISSING_DEP: (
        "缺少依赖，重试没有用。告诉用户缺什么、怎么装，然后停下来。"
    ),
    KIND_UNKNOWN: (
        "这条路没走通。**换一个思路**，不要用同样的参数重试：\n"
        "  · 先 screenshot 看清当前状态\n"
        "  · 或者改用别的工具达到同样目的\n"
        "  · 连续两次都不行就停下来告诉用户卡在哪"
    ),
}

# 哪些类型值得原样重试。**只有这两类。** 其余重试都是浪费步数。
_RETRYABLE = {KIND_BAD_ARGUMENT, KIND_TRANSIENT}

# 哪些类型应该停下来问用户
_ESCALATE = {KIND_PERMISSION, KIND_DENIED, KIND_MISSING_DEP}


def classify(text: str) -> str:
    """判断失败属于哪一类。认不出来就返回 unknown。"""
    body = str(text or "")
    if not body.strip():
        return KIND_UNKNOWN
    for pattern, kind in _RULES:
        if re.search(pattern, body, re.IGNORECASE):
            return kind
    return KIND_UNKNOWN


LABELS = {
    KIND_TIMEOUT: "读不到，改用坐标",
    KIND_NOT_FOUND: "目标变了，先重新确认",
    KIND_PERMISSION: "被权限拦下",
    KIND_DENIED: "用户拒绝了",
    KIND_TRANSIENT: "界面在动，等一下",
    KIND_BAD_ARGUMENT: "参数不对，改一下",
    KIND_MISSING_DEP: "缺依赖",
    KIND_UNKNOWN: "换条路",
}


# ==========================================================================
#  重复检测
# ==========================================================================
def action_key(tool: str, arguments) -> str:
    """把一次调用压成可比较的键，用来识别「同一件事」。

    参数会被排序后再序列化，所以 {"a":1,"b":2} 和 {"b":2,"a":1} 算同一个。
    """
    try:
        payload = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(arguments)
    return f"{tool}|{payload}"


@dataclass
class Failure:
    """一次失败的记录。"""

    tool: str = ""
    key: str = ""
    kind: str = KIND_UNKNOWN
    detail: str = ""
    at: float = 0.0


@dataclass
class Advisor:
    """一轮任务里的失败跟踪 + 建议。

    每个 AgentRunner 持有一个，任务开始时清空。
    """

    failures: list[Failure] = field(default_factory=list)
    # 是否已经就「同一个动作反复失败」提醒过，避免刷屏
    _warned_keys: set[str] = field(default_factory=set)
    _warned_tools: set[str] = field(default_factory=set)

    # ------------------------------------------------------------ 记账
    def reset(self) -> None:
        self.failures.clear()
        self._warned_keys.clear()
        self._warned_tools.clear()

    def count_key(self, key: str) -> int:
        return sum(1 for item in self.failures if item.key == key)

    def count_tool(self, tool: str) -> int:
        return sum(1 for item in self.failures if item.tool == tool)

    def distinct_kinds(self) -> set[str]:
        return {item.kind for item in self.failures}

    # ------------------------------------------------------ 执行前：止损
    def before_call(self, tool: str, arguments) -> Advice | None:
        """执行之前先问一句：这个调用是不是已经失败太多次了？

        返回非 None 就表示**不要执行**，直接把这条建议回给模型。
        这是硬止损 —— 光靠提示词劝不住模型反复试同一个动作。
        """
        key = action_key(tool, arguments)
        repeats = self.count_key(key)
        if repeats < SAME_ACTION_BLOCK:
            return None
        return Advice(
            kind=KIND_UNKNOWN,
            label="这个动作已经失败多次",
            hint=(
                f"**停一下。** 完全相同的调用（{tool}）你已经试了 {repeats} 次，"
                "每次都失败，所以这次没有执行。\n"
                "再试一次结果还是一样。请换一个做法：\n"
                "  · 先 screenshot 看清当前状态，再想别的办法\n"
                "  · 或者换一个工具达到同样目的\n"
                "  · 确实做不到就停下来，直接告诉用户卡在哪一步、为什么"
            ),
            retryable=False,
            escalate=True,
            blocked=True,
            repeats=repeats,
        )

    # ------------------------------------------------------ 执行后：建议
    def after_failure(self, tool: str, arguments, detail: str) -> Advice:
        """工具失败之后，给出下一步建议。"""
        key = action_key(tool, arguments)
        kind = classify(detail)

        self.failures.append(Failure(
            tool=tool, key=key, kind=kind, detail=str(detail or "")[:400],
            at=time.time(),
        ))

        repeats = self.count_key(key)
        tool_failures = self.count_tool(tool)

        advice = Advice(
            kind=kind,
            label=LABELS.get(kind, LABELS[KIND_UNKNOWN]),
            hint=_HINTS.get(kind, _HINTS[KIND_UNKNOWN]),
            retryable=kind in _RETRYABLE,
            escalate=kind in _ESCALATE,
            repeats=repeats,
        )

        notes: list[str] = []

        # 同一个动作反复失败 —— 这是最常见也最烧钱的情况
        if repeats >= SAME_ACTION_WARN and key not in self._warned_keys:
            self._warned_keys.add(key)
            notes.append(
                f"⚠️ 同一个操作（{tool}）已经失败 {repeats} 次。"
                "按规矩不要再来第三次，换路。"
            )

        # 同一个工具整体不通（也是一次性提醒，避免刷屏）
        if tool_failures >= SAME_TOOL_WARN and tool not in self._warned_tools:
            self._warned_tools.add(tool)
            notes.append(
                f"⚠️ {tool} 这条路累计失败了 {tool_failures} 次，"
                "很可能整体走不通，别再依赖它了。"
            )

        # 多种不同的失败混在一起 —— 说明环境本身有问题，不是在试错
        if len(self.distinct_kinds()) >= 3 and repeats == 1:
            notes.append(
                "这一步和之前的失败原因都不一样，可能不是操作问题而是环境问题。"
                "先 screenshot 看清整体情况，再决定要不要继续。"
            )

        # 每次失败都带上当前的总次数，让模型始终知道自己在第几次 ——
        # 一次性警告容易被后面的话冲掉，这个短的会一直在。
        tally = f"（这一步已失败 {repeats} 次"
        if tool_failures > repeats:
            tally += f"，{tool} 累计失败 {tool_failures} 次"
        tally += "）"
        advice.tally = tally

        if notes:
            advice.hint = advice.hint + "\n\n" + "\n".join(notes)

        # 前两次失败里，超时这类还可以再给一次机会（等一等再试）；
        # 到第三次就必须换路了
        if repeats >= SAME_ACTION_WARN:
            advice.retryable = False

        advice.lesson = self._lesson(kind, tool)
        return advice

    @staticmethod
    def _lesson(kind: str, tool: str) -> str:
        """从这次失败里能学到什么（可选，供上层记录/展示）。

        只挑**跨会话仍然成立**的推测，不记一次性的东西。
        """
        if kind == KIND_TIMEOUT and tool.startswith("ui_"):
            return "这个程序读不到控件，得用截图 + 坐标"
        if kind == KIND_MISSING_DEP:
            return "某个依赖没装"
        return ""

    # ------------------------------------------------------------ 收尾
    def summary(self) -> str:
        """给界面/日志用的失败概览。"""
        if not self.failures:
            return ""
        counts: dict[str, int] = {}
        for item in self.failures:
            counts[item.tool] = counts.get(item.tool, 0) + 1
        parts = [f"{tool}×{n}" for tool, n in
                 sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]
        return f"本轮失败 {len(self.failures)} 次：" + "、".join(parts[:5])


# ==========================================================================
#  给用户看的话
# ==========================================================================
# _HINTS 是写给**模型**的（带 Markdown 强调、带「调用什么工具」的具体指令），
# 直接甩给用户既看不懂也没法执行。这里另外写一套：
#   不报错误码 → 说清是什么情况
#   不甩技术名词 → 说清用户现在该做什么
#   不说「失败了」→ 说「你做完这一步我就接着干」
_USER_MESSAGES = {
    KIND_PERMISSION: (
        "有一件事被权限挡住了 —— 现在的「操作权限」级别不允许动你的键鼠。"
        "你去工作台把权限调高一点，或者直接跟我说「允许」，我就接着做。"
    ),
    KIND_DENIED: (
        "你拒绝的那一步我跳过了，后面能做的都做完了。"
        "要是想换个方式做，跟我说一句就行。"
    ),
    KIND_MISSING_DEP: (
        "有个功能需要的组件没装上，我这边做不了这一步。"
        "要我告诉你是哪个组件、怎么装吗？"
    ),
    KIND_TIMEOUT: (
        "有个程序一直没响应，读不到它里面的内容。"
        "我换成「看截图认位置」的办法了 —— 如果那也不行，"
        "多半是这个软件不让别的程序读它，那就得你自己点一下。"
    ),
    KIND_NOT_FOUND: (
        "要找的东西不在了 —— 窗口可能被关掉或者改过名字。"
        "你把那个窗口重新打开，我接着做。"
    ),
    KIND_TRANSIENT: (
        "刚才界面正在变化，没抓准时机。"
        "等它稳定下来我再试一次就行。"
    ),
    KIND_BAD_ARGUMENT: (
        "有个操作我用错了方式，已经换了个写法。"
        "如果还是不行，你告诉我正确的做法。"
    ),
    KIND_UNKNOWN: (
        "有一件事我没做成，卡住了。"
        "要我再说清楚是卡在哪一步吗？"
    ),
}


def user_facing_failure(kind: str, detail: str = "") -> str:
    """把一次失败翻成用户能看懂、并且知道下一步做什么的一句话。

    这是「失败说人话」的实现：界面上**不出现**错误码、异常类名、
    工具名这些东西。用户关心的是「现在轮到我做什么」。
    """
    message = _USER_MESSAGES.get(kind) or _USER_MESSAGES[KIND_UNKNOWN]

    # 缺依赖是个例外：具体缺什么对用户是有用信息，但要把英文包名
    # 从一堆异常文本里挑出来，不能整段贴给用户。
    if kind == KIND_MISSING_DEP:
        name = ""
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", str(detail or ""))
        if match:
            name = match.group(1)
        else:
            match = re.search(r"没有安装[：: ]*([^\s，。]+)", str(detail or ""))
            if match:
                name = match.group(1)
        if name:
            return f"少了「{name}」这个组件，我这边装不了这一步。要我告诉你怎么装吗？"
    return message
