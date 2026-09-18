"""跨会话记忆：记住用户是谁、习惯怎么干活、上次做到哪。

为什么需要它
------------
没有记忆时，每一轮对话都是从零开始：用户要说清楚「用 WPS 打开」、
「上次那个表还没弄完」、「别给我用 Markdown 表格」。这些话说第二遍
就已经烦了。

记忆分三类，各有各的失效方式，所以分开存：

* **facts（用户是谁 / 有什么习惯）** —— 基本不过期，但会**被推翻**。
  所以带 confidence，记错了可以调低而不是删掉。
* **aliases（怎么称呼某个东西）** —— 同一件事的两种说法，合并即可。
* **tasks（上次做到哪）** —— 有明确的生命周期，做完就该收尾。

三条硬约束
----------
1. **有上限。** 记忆会进系统提示词，每轮都要发一遍。不设上限的话
   攒到几百条就会把上下文撑爆、把用户的钱烧掉。所以有
   MAX_FACTS / MAX_TASKS，超了按「置信度 + 新旧」淘汰。
2. **去重。** 模型很爱重复记同一件事（「用户喜欢简洁回答」记五遍）。
   写入时按规范化后的文本查重，命中就更新而不是新增。
3. **用户能看见、能删。** 偷偷记住东西是让人反感的设计。
   界面里要能列出全部记忆，也要能一键清空。

写盘走 Store 那套（临时文件 + fsync + os.replace + 备份），所以不用
再搞一套持久化，也不会写坏。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 容量上限
MAX_FACTS = 80
MAX_TASKS = 12
MAX_ALIASES = 40

# 单条文本长度上限（防止模型把一整段话塞进来）
MAX_TEXT = 160

# 注入提示词时的总字数预算。这是每轮都要发的钱，必须抠着用。
PROMPT_BUDGET = 1000

# 事实的分类。分类只影响排序和展示，不做强制校验 ——
# 模型给个没见过的分类时可以归到 other，而不是让它写失败。
CATEGORIES = ("identity", "preference", "workflow", "environment", "other")

# 判重时「短的那条至少要有多少字」才允许按前缀包含合并。
# 太短（比如「很好」「用 WPS」）会到处误命中。
MIN_CONTAIN = 6
CATEGORY_LABELS = {
    "identity": "身份",
    "preference": "偏好",
    "workflow": "工作方式",
    "environment": "环境",
    "other": "其他",
}

CONFIDENCE_HIGH = 3
CONFIDENCE_NORMAL = 2
CONFIDENCE_LOW = 1

# 一条记忆是怎么来的。分开记是为了让用户能分清
# 「它自己学的」和「我让它记的」—— 自动记忆必须可审计、可单独清理。
KIND_AUTO = "auto"      # 小爪自己判断后记的（自动学习）
KIND_TOLD = "told"      # 用户明说「记住…」
KIND_TOOL = "tool"      # 模型在任务中主动调 remember 记的
KINDS = (KIND_AUTO, KIND_TOLD, KIND_TOOL)
# 合并两条同义记忆时，来源按这个优先级取高的 —— 只升不降。
# 理由：用户明说「记住 X」是最强的意愿表达，不能被后来的自动学习
# 覆盖成「自己学的」；反过来，自动学到的东西如果之前是用户教的，
# 也不该被降级，否则界面上会显示错，用户找不到自己让它记的东西。
KIND_RANK = {KIND_TOOL: 0, KIND_AUTO: 1, KIND_TOLD: 2}
KIND_LABELS = {
    KIND_AUTO: "自己学的",
    KIND_TOLD: "你让它记的",
    KIND_TOOL: "干活时记的",
}

TASK_OPEN = "open"
TASK_DONE = "done"
TASK_BLOCKED = "blocked"
TASK_STATUSES = (TASK_OPEN, TASK_DONE, TASK_BLOCKED)
TASK_LABELS = {TASK_OPEN: "进行中", TASK_DONE: "已完成", TASK_BLOCKED: "卡住了"}


# ==========================================================================
#  规范化与去重
# ==========================================================================
_PUNCT = "。，、；：！？,;:!?.\u3000 \t\n\r"


def normalize(text: str) -> str:
    """把一句话规范化，用来判重。

    刻意只做**保守**的归一：统一空白、去掉首尾标点、转小写。
    不做同义词替换、不去词干 —— 那会把「喜欢 A 不喜欢 B」和
    「不喜欢 A 喜欢 B」判成同一条，比不去重更糟。
    """
    cleaned = " ".join(str(text or "").split())
    return cleaned.strip(_PUNCT).lower()


def _same_fact(left: str, right: str) -> bool:
    """两条规范化文本说的是不是同一件事。

    完全相等当然算。此外还要认「一句话被补充了细节」这种情况：
    模型先记「他习惯用 WPS 而不是 Office」，之后又记
    「他习惯用 WPS 而不是 Office 这个办公软件」—— 这是同一条被写得更全，
    不是两件事。

    但包含匹配要克制，否则会把
    「喜欢 A 不喜欢 B」和「不喜欢 A 喜欢 B」这种意思相反的判成一条。
    所以要求**短的那条是长的那条的前缀**，而且短的要够长
    （至少 MIN_CONTAIN 个字），避免「很好」这种短词到处误命中。
    """
    if not left or not right:
        return False
    if left == right:
        return True

    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < MIN_CONTAIN:
        return False
    # 必须是前缀：补充细节通常是往后加的，而不是插在中间
    return longer.startswith(shorter)


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _now() -> float:
    return time.time()


def _number(value, default: float = 0.0) -> float:
    """把外部数据里的数字安全地转成 float。

    存盘文件可能被手改过，里面出现 "不是数字" 是完全可能的。
    直接 float() 会抛 ValueError，而调用方在 from_dict 里 ——
    一条坏记录会把整个记忆（连带应用的启动）搞崩。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value, default: int = 0, low: int | None = None,
             high: int | None = None) -> int:
    """同上，转 int 并按需夹范围。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


# ==========================================================================
#  数据结构
# ==========================================================================
@dataclass
class Fact:
    """一条关于用户的知识。"""

    text: str = ""
    category: str = "other"
    confidence: int = CONFIDENCE_NORMAL
    created: float = 0.0
    updated: float = 0.0
    source: str = ""          # 谁写的：ai（模型主动记）| user（用户说的）
    hits: int = 0             # 被 recall 命中过几次，用于排序
    # 怎么来的：auto（小爪自己判断后记的）| told（用户明说要记）| tool（模型调了 remember）
    # 用户需要能分清「它自己学的」和「我让它记的」—— 自动记忆必须可审计
    kind: str = "tool"

    @property
    def key(self) -> str:
        return normalize(self.text)

    @property
    def is_auto(self) -> bool:
        return self.kind == KIND_AUTO

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "category": self.category,
            "confidence": int(self.confidence),
            "created": float(self.created),
            "updated": float(self.updated),
            "source": self.source,
            "hits": int(self.hits),
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Fact":
        if not isinstance(raw, dict):
            return cls()
        category = str(raw.get("category") or "other")
        kind = str(raw.get("kind") or KIND_TOOL)
        return cls(
            text=_clip(raw.get("text") or ""),
            category=category if category in CATEGORIES else "other",
            confidence=_integer(raw.get("confidence", CONFIDENCE_NORMAL),
                                CONFIDENCE_NORMAL, CONFIDENCE_LOW, CONFIDENCE_HIGH),
            created=_number(raw.get("created")),
            updated=_number(raw.get("updated")),
            source=str(raw.get("source") or ""),
            hits=_integer(raw.get("hits", 0), 0, 0),
            kind=kind if kind in KINDS else KIND_TOOL,
        )


@dataclass
class Alias:
    """用户嘴里的名字 -> 真实窗口标题的一部分。"""

    spoken: str = ""          # 用户常说的叫法，比如「那个表格」
    actual: str = ""          # 真实标题片段，比如「WPS 表格」
    created: float = 0.0
    updated: float = 0.0
    used: int = 0             # 用过几次，用得多的排前面

    @property
    def key(self) -> str:
        return normalize(self.spoken)

    def as_dict(self) -> dict:
        return {
            "spoken": self.spoken,
            "actual": self.actual,
            "created": float(self.created),
            "updated": float(self.updated),
            "used": int(self.used),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Alias":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            spoken=_clip(raw.get("spoken") or "", 60),
            actual=_clip(raw.get("actual") or "", 80),
            created=_number(raw.get("created")),
            updated=_number(raw.get("updated")),
            used=_integer(raw.get("used", 0), 0, 0),
        )


@dataclass
class TaskNote:
    """上次做到哪了。"""

    text: str = ""            # 在做什么
    status: str = TASK_OPEN
    next_step: str = ""       # 下一步该干什么
    created: float = 0.0
    updated: float = 0.0

    @property
    def key(self) -> str:
        return normalize(self.text)

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "status": self.status,
            "next_step": self.next_step,
            "created": float(self.created),
            "updated": float(self.updated),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskNote":
        if not isinstance(raw, dict):
            return cls()
        status = str(raw.get("status") or TASK_OPEN)
        return cls(
            text=_clip(raw.get("text") or ""),
            status=status if status in TASK_STATUSES else TASK_OPEN,
            next_step=_clip(raw.get("next_step") or "", 120),
            created=_number(raw.get("created")),
            updated=_number(raw.get("updated")),
        )


# ==========================================================================
#  记忆容器
# ==========================================================================
class Memory:
    """全部记忆。纯数据 + 纯逻辑，不碰界面、不碰磁盘。"""

    def __init__(self, facts=None, aliases=None, tasks=None,
                 updated: float = 0.0) -> None:
        self.facts: list[Fact] = list(facts or [])
        self.aliases: list[Alias] = list(aliases or [])
        self.tasks: list[TaskNote] = list(tasks or [])
        self.updated = float(updated or 0.0)

    # ------------------------------------------------------------ 序列化
    def as_dict(self) -> dict:
        return {
            "facts": [f.as_dict() for f in self.facts],
            "aliases": [a.as_dict() for a in self.aliases],
            "tasks": [t.as_dict() for t in self.tasks],
            "updated": float(self.updated),
        }

    @classmethod
    def from_dict(cls, raw) -> "Memory":
        """从存盘数据还原。

        **必须容错**：这份数据是外部文件，可能被手改过、可能是旧版本、
        也可能是别人写坏的。任何一条读不出来就跳过它，
        不能让一条坏记录把整个记忆（连带整个应用）搞崩。
        """
        if not isinstance(raw, dict):
            return cls()
        facts = []
        for item in raw.get("facts") or []:
            fact = Fact.from_dict(item)
            if fact.text:
                facts.append(fact)
        aliases = []
        for item in raw.get("aliases") or []:
            alias = Alias.from_dict(item)
            if alias.spoken and alias.actual:
                aliases.append(alias)
        tasks = []
        for item in raw.get("tasks") or []:
            task = TaskNote.from_dict(item)
            if task.text:
                tasks.append(task)
        return cls(facts=facts, aliases=aliases, tasks=tasks,
                   updated=_number(raw.get("updated")))

    # -------------------------------------------------------------- 统计
    def is_empty(self) -> bool:
        return not (self.facts or self.aliases or self.tasks)

    def counts(self) -> dict:
        return {
            "facts": len(self.facts),
            "aliases": len(self.aliases),
            "tasks": len(self.tasks),
        }

    # ------------------------------------------------------------ 用户事实
    def add_fact(self, text: str, category: str = "other",
                 confidence: int = CONFIDENCE_NORMAL,
                 source: str = "ai", kind: str = KIND_TOOL) -> tuple[Fact, bool]:
        """记一条事实。返回 (记录, 是不是新建的)。

        判重按规范化文本。命中已有记录时**合并**而不是新增：
        保留更长的那条文本（信息更多），置信度取高，刷新时间。
        """
        text = _clip(text)
        if not text:
            raise ValueError("记忆内容不能为空")

        category = category if category in CATEGORIES else "other"
        kind = kind if kind in KINDS else KIND_TOOL
        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            confidence = CONFIDENCE_NORMAL
        confidence = max(CONFIDENCE_LOW, min(CONFIDENCE_HIGH, confidence))

        key = normalize(text)
        now = _now()
        for existing in self.facts:
            if existing.key == key or _same_fact(existing.key, key):
                # 比长度要先归一：原文可能有尾随句号、多空格，
                # 直接比 len 会让「更啰嗦但信息更少」的那句赢。
                # 合并后存**归一化之后更长**的那句原文 ——
                # 它信息更多，而且不会把用户看到的文字改烂。
                if len(normalize(text)) > len(existing.key):
                    existing.text = text
                existing.category = category
                existing.confidence = max(existing.confidence, confidence)
                existing.updated = now
                if source:
                    existing.source = source
                # 来源只升不降，见 KIND_RANK 的说明
                if KIND_RANK.get(kind, 0) > KIND_RANK.get(existing.kind, 0):
                    existing.kind = kind
                self.updated = now
                return existing, False

        fact = Fact(text=text, category=category, confidence=confidence,
                    created=now, updated=now, source=source, kind=kind)
        self.facts.append(fact)
        self._trim_facts()
        self.updated = now
        return fact, True

    def forget(self, text: str) -> Fact | None:
        """按内容删一条事实。返回被删的那条，没有就返回 None。

        先精确匹配规范化文本，再退回子串包含匹配 ——
        用户/模型不一定记得原话是怎么写的。
        """
        key = normalize(text)
        if not key:
            return None
        for index, fact in enumerate(self.facts):
            if fact.key == key:
                self.updated = _now()
                return self.facts.pop(index)
        for index, fact in enumerate(self.facts):
            if key in fact.key or fact.key in key:
                self.updated = _now()
                return self.facts.pop(index)
        return None

    def _trim_facts(self) -> None:
        """超上限时淘汰：先扔低置信度的，同置信度扔最久没更新的。"""
        if len(self.facts) <= MAX_FACTS:
            return
        self.facts.sort(key=lambda f: (f.confidence, f.updated), reverse=True)
        del self.facts[MAX_FACTS:]

    # ------------------------------------------------------------ 叫法映射
    def add_alias(self, spoken: str, actual: str) -> tuple[Alias, bool]:
        spoken, actual = _clip(spoken, 60), _clip(actual, 80)
        if not spoken or not actual:
            raise ValueError("叫法映射两边都要有内容")

        key = normalize(spoken)
        now = _now()
        for existing in self.aliases:
            if existing.key == key:
                existing.actual = actual
                existing.updated = now
                existing.used += 1
                self.updated = now
                return existing, False

        alias = Alias(spoken=spoken, actual=actual, created=now, updated=now, used=1)
        self.aliases.append(alias)
        if len(self.aliases) > MAX_ALIASES:
            self.aliases.sort(key=lambda a: (a.used, a.updated), reverse=True)
            del self.aliases[MAX_ALIASES:]
        self.updated = now
        return alias, True

    def resolve_alias(self, spoken: str) -> str:
        """把用户嘴里的叫法换成真实标题片段。查不到就原样返回。"""
        key = normalize(spoken)
        if not key:
            return spoken
        for alias in self.aliases:
            if alias.key == key:
                return alias.actual
        for alias in self.aliases:
            if alias.key and (alias.key in key or key in alias.key):
                return alias.actual
        return spoken

    # ---------------------------------------------------------- 任务进度
    def note_task(self, text: str, status: str = TASK_OPEN,
                  next_step: str = "") -> TaskNote:
        text = _clip(text)
        if not text:
            raise ValueError("任务描述不能为空")
        status = status if status in TASK_STATUSES else TASK_OPEN

        key = normalize(text)
        now = _now()
        for existing in self.tasks:
            if existing.key == key:
                existing.status = status
                if next_step:
                    existing.next_step = _clip(next_step, 120)
                existing.updated = now
                self.updated = now
                return existing

        task = TaskNote(text=text, status=status, next_step=_clip(next_step, 120),
                        created=now, updated=now)
        self.tasks.append(task)
        # 超出上限时**优先保留没做完的**：已完成的任务留着占地方没意义，
        # 而未完成的是「下次接着做」的唯一线索，绝不能先扔它。
        # 排序键：未完成在前（False < True），同组内新的在前。
        # 然后删掉**排在后面**的那些。
        if len(self.tasks) > MAX_TASKS:
            self.tasks.sort(key=lambda t: (t.status == TASK_DONE, -t.updated))
            del self.tasks[MAX_TASKS:]
        self.updated = now
        return task

    def open_tasks(self) -> list[TaskNote]:
        return [t for t in self.tasks if t.status != TASK_DONE]

    # ------------------------------------------------------------ 清空
    def clear(self) -> dict:
        counts = self.counts()
        self.facts.clear()
        self.aliases.clear()
        self.tasks.clear()
        self.updated = _now()
        return counts


# ==========================================================================
#  注入提示词
# ==========================================================================
def _fact_line(fact: Fact) -> str:
    """一条事实怎么展示。低置信度的要标出来，免得模型当真。"""
    text = fact.text
    if fact.confidence == CONFIDENCE_HIGH:
        return f"- {text}（确定）"
    if fact.confidence == CONFIDENCE_LOW:
        return f"- {text}（不确定，需要时再确认）"
    return f"- {text}"


def count_auto(memory: Memory) -> int:
    """有多少条是自动学来的。界面用它提示用户去审阅。"""
    return sum(1 for fact in memory.facts if fact.is_auto)


def format_for_prompt(memory: Memory, budget: int = PROMPT_BUDGET) -> str:
    """把记忆渲染成一段可以塞进系统提示词的文字。

    返回空字符串表示没有记忆 —— 调用方应该整段跳过，
    不要注入一个「（暂无记忆）」的空壳，那只是白烧 token。
    """
    if memory.is_empty():
        return ""

    sections: list[str] = []
    used = 0

    # 用户事实：高置信度优先，其次新的
    if memory.facts:
        ordered = sorted(memory.facts,
                         key=lambda f: (f.confidence, f.updated), reverse=True)
        lines = []
        for fact in ordered:
            line = _fact_line(fact)
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line) + 1
        if lines:
            dropped = len(memory.facts) - len(lines)
            if dropped > 0:
                lines.append(f"（还有 {dropped} 条记忆没列出，需要时用 recall_memory 查）")
            sections.append("关于这位用户，你已经知道：\n" + "\n".join(lines))

    # 叫法映射
    if memory.aliases:
        lines = []
        for alias in sorted(memory.aliases, key=lambda a: a.used, reverse=True):
            line = f"- 他说「{alias.spoken}」时，指的是「{alias.actual}」"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line) + 1
        if lines:
            sections.append("他的习惯叫法：\n" + "\n".join(lines))

    # 没做完的事
    pending = memory.open_tasks()
    if pending:
        lines = []
        for task in sorted(pending, key=lambda t: t.updated, reverse=True):
            marker = "（卡住了）" if task.status == TASK_BLOCKED else ""
            line = f"- {task.text}{marker}"
            if task.next_step:
                line += f" —— 下一步：{task.next_step}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line) + 1
        if lines:
            sections.append("他上次还没做完的事：\n" + "\n".join(lines))

    if not sections:
        return ""

    body = "\n\n".join(sections)
    return (
        "\n\n【跨会话记忆】\n"
        f"{body}\n\n"
        "使用记忆的规矩：\n"
        "1. 这些是你之前攒下来的，**直接用**，不要重新问一遍。\n"
        "2. 和眼前看到的事实冲突时，**以眼前为准**，并且用 remember 更正记忆。\n"
        "3. 发现新的稳定习惯（常用程序、称呼、排版偏好）就用 remember 记下来；\n"
        "   只对这一次有效的细节不要记。\n"
        "4. 用户说「以后都这样」「别再用 X」时，一定要 remember。"
    )


class MemoryBook:
    """把 Memory 绑到 Store 上：读一次、改、存回。

    为什么要这一层：Store 里存的是裸字典，每次工具调用都
    「解析 -> 改 -> 序列化 -> save」最省心，也不会出现
    内存里的对象和磁盘上的数据不一致。
    """

    def __init__(self, store) -> None:
        self._store = store

    def load(self) -> Memory:
        try:
            return Memory.from_dict(self._store.memory)
        except Exception:  # noqa: BLE001 - 记忆读不出来不该让 AI 挂掉
            return Memory()

    def save(self, memory: Memory) -> None:
        """把记忆写回 Store。**合并，不是整份替换。**

        原来这里是 `self._store.state["memory"] = memory.as_dict()`，
        看着很自然，其实是个数据丢失 bug：`Memory.as_dict()` 只吐
        `facts` / `aliases` / `tasks` / `updated` 四个键，赋值又会把
        `state["memory"]` 底下**别的键整份删掉**。

        而 `state["memory"]` 是个**公共抽屉**，不止 Memory 在往里放东西 ——
        `tools.py` 的 `_observe_app` 会在同一个字典里写 `app_usage`
        （记用户常用哪些程序）。于是：

        * 计数刚够阈值、正要写下「常用程序：WPS」的那一下，
          就把整个 `app_usage` 清空了；
        * 此后 AI 每调一次 `remember` / `forget` / 记叫法，同样清一次。

        现象是「被动学常用程序」这件事基本不生效，`frequent_apps()`
        长期返回空 —— **而且全程不报错**，所以很难发现。

        `update()` 只覆盖 Memory 自己那几个键，抽屉里别人的东西不动。
        """
        raw = self._store.memory
        raw.update(memory.as_dict())
        self._store.save()

    # 常用的「读-改-写」组合
    def add_fact(self, text: str, category: str = "other",
                 confidence: int = CONFIDENCE_NORMAL,
                 source: str = "ai", kind: str = KIND_TOOL) -> tuple[Fact, bool]:
        memory = self.load()
        fact, created = memory.add_fact(text, category, confidence, source, kind)
        self.save(memory)
        return fact, created

    def forget(self, text: str) -> Fact | None:
        memory = self.load()
        removed = memory.forget(text)
        if removed is not None:
            self.save(memory)
        return removed

    def add_alias(self, spoken: str, actual: str) -> tuple[Alias, bool]:
        memory = self.load()
        alias, created = memory.add_alias(spoken, actual)
        self.save(memory)
        return alias, created

    def note_task(self, text: str, status: str = TASK_OPEN,
                  next_step: str = "") -> TaskNote:
        memory = self.load()
        task = memory.note_task(text, status, next_step)
        self.save(memory)
        return task

    def clear(self) -> dict:
        memory = self.load()
        counts = memory.clear()
        self.save(memory)
        return counts


def describe(memory: Memory) -> str:
    """给界面看的多行摘要。"""
    if memory.is_empty():
        return "还没有任何记忆。\n和 AI 聊几次之后，它会记住你的习惯、常用程序和你上次做到哪。"

    lines: list[str] = []
    counts = memory.counts()
    lines.append(f"共 {counts['facts']} 条知识、{counts['aliases']} 条叫法、"
                 f"{counts['tasks']} 条任务记录。")
    lines.append("")

    if memory.facts:
        lines.append("关于你：")
        for fact in sorted(memory.facts,
                           key=lambda f: (f.confidence, f.updated), reverse=True)[:20]:
            label = CATEGORY_LABELS.get(fact.category, "其他")
            # 标出「自己学的」，用户才知道哪些是它自动判断的、可以放心删
            origin = "·自己学的" if fact.is_auto else ""
            lines.append(f"  [{label}{origin}] {fact.text}")
        if len(memory.facts) > 20:
            lines.append(f"  …还有 {len(memory.facts) - 20} 条")
        lines.append("")

    if memory.aliases:
        lines.append("习惯叫法：")
        for alias in sorted(memory.aliases, key=lambda a: a.used, reverse=True)[:12]:
            lines.append(f"  「{alias.spoken}」→「{alias.actual}」")
        lines.append("")

    if memory.tasks:
        lines.append("任务进度：")
        for task in sorted(memory.tasks, key=lambda t: t.updated, reverse=True):
            mark = TASK_LABELS.get(task.status, task.status)
            extra = f" → 下一步：{task.next_step}" if task.next_step else ""
            lines.append(f"  [{mark}] {task.text}{extra}")

    return "\n".join(lines).strip()
