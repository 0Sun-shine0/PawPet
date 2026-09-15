"""自动记忆：小爪自己判断什么值得记，不用用户喊「记一下」。

为什么要有这一层
----------------
原来记忆只有两条入口：用户明说「记住我用 WPS」，或者模型自觉调
`remember`。两条都靠**外部指令** —— 用户不说、模型没想起来，
就什么都没记住。可真正的助手应该是：你说过一遍，它自己就记住了。

怎么做到「不喊也记」
--------------------
每轮对话结束后，拿「用户说了什么 + 小爪做了什么」去问一次模型：
这里面有没有**值得长期留着**的信息？有就写进记忆。

三个关键取舍
------------
1. **不是每轮都问。** 大部分对话（「打开记事本」「看下报错」）没有任何
   可记的东西，每轮都发一次请求是白烧钱。所以先用本地信号筛一遍：
   出现「以后都」「别」「我总是」这类词、或者用户在同一条消息里说了
   很长一段（可能在交代背景），才值得花那次请求。实测能挡掉绝大多数轮次。

2. **失败绝不影响主流程。** 这一层在后台线程跑，任何异常都吞掉 ——
   记忆学不到是小事，把用户的任务搞挂是大事。

3. **只记「稳定」的东西。** 判别标准在提示词里写死：只记跨会话仍然
   成立的偏好/身份/习惯，不记一次性的细节（临时坐标、这次的文件名）。
   记多了比记少了更糟 —— 脏记忆会污染之后的每一轮提示词。

自动学到的条目会标 `kind="auto"`，用户能在界面上看到并单独删掉。
这一点很重要：**自动记忆必须可审计**，否则用户会觉得它在偷偷记东西。
"""

from __future__ import annotations

import json
import re

from .client import AIClient, AiError
from .memory import (
    CATEGORIES,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_NORMAL,
    KIND_AUTO,
    Memory,
    MemoryBook,
    normalize,
)

# 一轮最多自动记几条。多了说明判别出了问题，宁缺毋滥。
MAX_PER_TURN = 3

# 单条记忆的长度上限（和 memory.py 保持一致）
MAX_ITEM = 120

# 现有的记忆最多带多少条给判别模型看，用来发现「说反了」的情况
MAX_KNOWN = 25


# ==========================================================================
#  本地信号：值不值得花一次请求去问
# ==========================================================================
# 「以后都这样」「别再」「我总是」这类词，出现就说明用户在交代长期偏好
_STRONG_SIGNALS = (
    "以后", "今后", "每次", "总是", "老是", "一直都", "从来不",
    "别再", "不要再", "不要用", "别用", "改用", "换成",
    "我喜欢", "我不喜欢", "我习惯", "我讨厌", "我偏好",
    "记住", "记下来", "记一下", "帮我记",
    "我的名字", "我叫", "我是做", "我的工作", "我在",
)

# 中等信号：单独出现不够，要配合足够长的句子
_WEAK_SIGNALS = (
    "习惯", "偏好", "默认", "统一", "规范", "风格", "格式",
    "一般", "通常", "平时", "我这边", "我们公司", "我们团队",
)

# 明显不用记的：纯操作指令
_SKIP_SIGNALS = (
    "打开", "关闭", "截图", "看屏幕", "点一下", "点击",
    "最小化", "最大化", "切换", "复制", "粘贴",
)

# 短于这个长度的话，基本是操作指令，不值得记
MIN_LENGTH = 6

# 长于这个长度，可能是在交代背景，值得看一眼
LONG_MESSAGE = 60


def should_extract(user_text: str) -> bool:
    """本地先筛一遍：这条消息值不值得花一次模型请求去判断。

    这个函数是整个功能的成本闸门。放宽一点会多花钱，收紧一点会漏记。
    现在的取法是「宁可漏掉几句，也不要每轮都问」。
    """
    text = (user_text or "").strip()
    if len(text) < MIN_LENGTH:
        return False

    lowered = text.lower()
    for signal in _STRONG_SIGNALS:
        if signal in text:
            return True

    # 纯操作指令直接跳过
    if any(signal in text for signal in _SKIP_SIGNALS) and not any(
            signal in lowered for signal in ("以后", "每次", "别", "习惯")):
        return False

    if any(signal in text for signal in _WEAK_SIGNALS) and len(text) >= 12:
        return True

    # 很长的消息通常是在讲背景，值得看一眼
    return len(text) >= LONG_MESSAGE


# ==========================================================================
#  判别提示词
# ==========================================================================
SYSTEM = """你负责维护一个桌宠助手的长期记忆。你的任务是从刚发生的一轮对话里，\
挑出**值得长期保留**的信息。

只记这三类：
1. **身份/背景**：他是谁、做什么工作、用什么设备或系统、在哪个团队
2. **稳定偏好**：喜欢什么样的回答、排版、语气；不喜欢什么；习惯用什么软件
3. **固定做法**：反复出现的操作习惯、约定的流程、明确的「以后都这样」

**不要记**（这些记下来只会污染以后的每一轮）：
- 一次性的细节：这次的文件名、临时坐标、当前屏幕内容、这次任务的中间结果
- 从对话里能直接看出来的、下次自然会知道的事
- 小爪自己说的话（只记**用户**表达的，不要把小爪的猜测当成事实）
- 模糊到没法用的信息：「他有点忙」「他问了问题」
- 密码、密钥、身份证号、银行卡号等敏感内容 —— **绝对不要记**

判断标准：**换一天再聊，这条信息还用得上吗？** 用不上就别记。

输出 JSON，不要写别的：
{"items": [{"text": "一句话，用第三人称，比如「他习惯用 WPS 而不是 Office」", \
"category": "identity|preference|workflow|environment|other", \
"confidence": 1-3, "forget": ""}]}

confidence 的含义：
- 3 = 用户明确说的（「以后都用 A」）
- 2 = 比较确定（从他说的话里能直接看出来）
- 1 = 你的推测（不确定，以后用的时候要先确认）

如果这轮出现了和**已有记忆**矛盾的新信息（比如以前记「他用 WPS」，
这次他说「我现在都用 Office 了」），在对应条目的 `forget` 里写上
**旧记忆的原话**，我们会把旧的删掉。

没有任何值得记的就返回 {"items": []}。宁可不记，也不要记不重要的。"""


def _existing_block(memory: Memory) -> str:
    """把现有记忆整理成一段给判别模型看，用来发现前后矛盾。"""
    facts = [f.text for f in memory.facts][:MAX_KNOWN]
    if not facts:
        return "（还没有任何记忆）"
    return "\n".join(f"- {text}" for text in facts)


def build_messages(user_text: str, assistant_text: str, memory: Memory) -> list[dict]:
    """组装判别用的对话。

    **刻意不带截图、不带工具调用过程。** 只给用户说的话和小爪的最终回复：
    判别「有没有值得记的」不需要中间过程，带上只会白烧 token。
    """
    user_part = (user_text or "").strip()[:2000]
    assistant_part = (assistant_text or "").strip()[:800]

    body = (
        f"【已有的记忆】\n{_existing_block(memory)}\n\n"
        f"【这一轮用户说的话】\n{user_part}\n\n"
        f"【小爪的回复】\n{assistant_part or '（没有文字回复）'}"
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": body},
    ]


# ==========================================================================
#  解析模型输出
# ==========================================================================
def _strip_fence(text: str) -> str:
    """模型很爱把 JSON 包在 ``` 里，先剥掉。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_items(raw_text: str) -> list[dict]:
    """从模型回复里解析出条目。解析不出来就返回空列表（不抛异常）。

    为什么不抛：这条链路在后台跑，解析失败只该导致「这次没学到东西」，
    不该把别的什么东西带崩。
    """
    cleaned = _strip_fence(raw_text)
    if not cleaned:
        return []

    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 兜底：从一堆文字里抠出第一个 JSON 对象
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []

    cleaned_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cleaned_items.append({
            "text": text[:MAX_ITEM],
            "category": str(item.get("category") or "other").strip().lower(),
            "confidence": item.get("confidence", CONFIDENCE_NORMAL),
            "forget": str(item.get("forget") or "").strip(),
        })
    return cleaned_items


def _confidence(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return CONFIDENCE_NORMAL
    return max(CONFIDENCE_LOW, min(CONFIDENCE_HIGH, number))


# ==========================================================================
#  主流程
# ==========================================================================
class LearnResult:
    """一次自动学习的结果，给界面和测试看。"""

    def __init__(self) -> None:
        self.asked = False          # 有没有真的去问模型
        self.reason = ""            # 没问的原因，或者问了之后的说明
        self.learned: list[str] = []       # 新记住的
        self.updated: list[str] = []       # 合并进已有条目的
        self.forgotten: list[str] = []     # 因为矛盾被删掉的
        self.skipped: list[str] = []       # 模型给了但被本地过滤器挡下的

    @property
    def changed(self) -> bool:
        return bool(self.learned or self.updated or self.forgotten)

    def summary(self) -> str:
        if not self.asked:
            return f"跳过（{self.reason}）"
        parts = []
        if self.learned:
            parts.append(f"新记 {len(self.learned)} 条")
        if self.updated:
            parts.append(f"更新 {len(self.updated)} 条")
        if self.forgotten:
            parts.append(f"删掉 {len(self.forgotten)} 条旧记忆")
        if self.skipped:
            parts.append(f"忽略 {len(self.skipped)} 条")
        return "、".join(parts) if parts else "这轮没有值得记的"

    def as_dict(self) -> dict:
        return {
            "asked": self.asked,
            "reason": self.reason,
            "learned": list(self.learned),
            "updated": list(self.updated),
            "forgotten": list(self.forgotten),
            "skipped": list(self.skipped),
        }


def _too_similar(text: str, memory: Memory) -> bool:
    """这条是不是已经记过了？（按规范化文本判，和 memory 里的规则一致）"""
    key = normalize(text)
    if not key:
        return True
    for fact in memory.facts:
        if fact.key == key:
            return True
        shorter, longer = (fact.key, key) if len(fact.key) <= len(key) else (key, fact.key)
        if len(shorter) >= 6 and longer.startswith(shorter):
            return True
    return False


def _looks_sensitive(text: str) -> bool:
    """粗筛敏感内容。宁可误杀，也不要让密码进记忆。

    这一层是**兜底**：主提示词里已经说了不要记敏感内容，但不能只靠
    模型的自觉 —— 记忆会进每一轮的提示词，一旦漏进去就是持续泄露。
    """
    lowered = text.lower()
    markers = (
        "password", "passwd", "密码", "口令",
        "api key", "apikey", "api_key", "secret", "token", "密钥",
        "身份证", "银行卡", "信用卡", "cvv",
        "sk-", "ghp_", "-----begin",
    )
    return any(marker in lowered for marker in markers)


def learn_from_turn(client: AIClient, book: MemoryBook, user_text: str,
                    assistant_text: str = "", force: bool = False) -> LearnResult:
    """一轮结束后自动学一次。

    `force=True` 会跳过本地信号筛选（测试和「让我现在学一下」用）。
    """
    result = LearnResult()

    if not force and not should_extract(user_text):
        result.reason = "这条消息里没有值得长期保留的信号"
        return result

    if not client.configured:
        result.reason = "没有配置模型"
        return result

    memory = book.load()
    result.asked = True

    try:
        messages = build_messages(user_text, assistant_text, memory)
        # 判别用不了多聪明，也不需要很长 —— 输出就是个小 JSON
        reply = client.chat(messages, temperature=0.0, max_tokens=700)
    except AiError as exc:
        result.reason = f"判别请求失败：{exc}"
        result.asked = False
        return result
    except Exception as exc:  # noqa: BLE001 - 后台链路，绝不外抛
        result.reason = f"{type(exc).__name__}: {exc}"
        result.asked = False
        return result

    items = parse_items(reply.text)
    if not items:
        result.reason = "这轮没有值得记的"
        return result

    for item in items[:MAX_PER_TURN + 3]:
        text = item["text"]

        if _looks_sensitive(text):
            result.skipped.append(text)
            continue

        category = item["category"]
        if category not in CATEGORIES:
            category = "other"

        # 先处理「说反了」：把旧的那条删掉
        if item.get("forget"):
            removed = memory.forget(item["forget"])
            if removed is not None:
                result.forgotten.append(removed.text)

        if _too_similar(text, memory):
            # 已经记过 —— 但置信度可能更高了，顺手更新一下
            fact, created = memory.add_fact(
                text, category, _confidence(item["confidence"]),
                source="auto", kind=KIND_AUTO)
            if not created:
                result.updated.append(fact.text)
            continue

        if len(result.learned) + len(result.updated) >= MAX_PER_TURN:
            result.skipped.append(text)
            continue

        fact, created = memory.add_fact(
            text, category, _confidence(item["confidence"]),
            source="auto", kind=KIND_AUTO)
        if created:
            result.learned.append(fact.text)
        else:
            result.updated.append(fact.text)

    if result.changed:
        book.save(memory)

    return result
