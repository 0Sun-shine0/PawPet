"""对话的持久化。

为什么单独一个文件
------------------
不放 `pet_data.json`：那个文件已经 560 KB（其中知识库占 316 KB），
而 `Store.save()` 是**全量深拷贝 + 重写**。把对话塞进去，用户每改一个
设置、每记一条待办，都要连带重写几百 KB —— 而且对话本身还会一直长。

所以走独立文件 `conversations.json`，只在自己变化时写。

哪些字段存、哪些不存
--------------------
存进去的每条消息都过一遍 `_trim_message()`：

* **`image` 必须丢掉。** 它指向的是 `LIVE_PREVIEW`（`.cache/ai/screen.png`）
  —— 一个**每次截图都被覆盖**的固定路径。原样存下来的话，历史里那条
  消息会显示「当前」的截图，而不是当时的画面。这比不显示更糟：
  用户会以为 AI 当时看到的就是这些。
* **`html` 丢掉。** 它是 `text` 经 `to_qt_html()` 算出来的，加载时重算
  一次就行。存两份等于文件大一倍，而且格式改动之后旧文件里的 html
  会和新界面不一致。
* 其余（tool / risk / ok / detail / seconds / recovery / report）都留着 ——
  它们正是回看时最有用的部分：当时调了什么工具、成功没有、卡在哪。

会话切分
--------
一次「连续使用」是一个 session。什么时候开新的：

* 用户点「新对话」→ 当前这个归档，开一个空的
* 用户点「清空」→ 同上（清空在他眼里就是「从头开始」）
* 超过 SESSION_MAX_MESSAGES 条 → 自动切一个，免得单个会话长到打不开

启动时**恢复最近一个会话**继续用，而不是每次开新的 —— 用户的原话是
「反复处理同类工单」，他要的就是接着上次说。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

# 保留多少个历史会话。每个会话最多 200 条消息，所以最坏情况
# 30 × 200 = 6000 条 —— 按每条 500 字节估，约 3 MB。可以接受。
MAX_SESSIONS = 30
SESSION_MAX_MESSAGES = 200

# 会话标题从第一条用户消息里取，太长就把输入框撑坏
TITLE_CHARS = 28

SCHEMA = 1

# 这些字段不落盘，理由见模块开头
_DROP_FIELDS = ("image", "html")


@dataclass
class Session:
    """一次连续使用。"""

    id: str = ""
    started: float = 0.0
    updated: float = 0.0
    title: str = ""
    messages: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "started": self.started,
            "updated": self.updated,
            "title": self.title,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, raw) -> "Session":
        if not isinstance(raw, dict):
            return cls()
        messages = raw.get("messages")
        return cls(
            id=str(raw.get("id") or ""),
            started=float(raw.get("started") or 0.0),
            updated=float(raw.get("updated") or 0.0),
            title=str(raw.get("title") or ""),
            messages=[m for m in messages if isinstance(m, dict)]
            if isinstance(messages, list) else [],
        )

    def preview(self) -> str:
        """列表里显示的一行摘要：第一条用户消息。"""
        for item in self.messages:
            if item.get("role") == "user":
                text = " ".join(str(item.get("text") or "").split())
                if text:
                    return text[:60]
        return "（没有对话内容）"


def _trim_message(item: dict) -> dict:
    """把一条消息压成可以落盘的样子。"""
    out = {k: v for k, v in (item or {}).items() if k not in _DROP_FIELDS}
    # 只为「回看」服务的字段，去掉能让文件小很多
    if out.get("plain") == out.get("text"):
        out.pop("plain", None)
    if not out.get("detail"):
        out.pop("detail", None)
    if not out.get("recovery"):
        out.pop("recovery", None)
    if not out.get("report"):
        out.pop("report", None)
    if not out.get("imageNote"):
        out.pop("imageNote", None)
    return out


def make_title(messages: list) -> str:
    for item in messages:
        if item.get("role") == "user":
            text = " ".join(str(item.get("text") or "").split())
            if text:
                return text[:TITLE_CHARS]
    return "新对话"


def new_session(messages: list | None = None) -> Session:
    now = time.time()
    return Session(
        id=f"s{int(now * 1000)}",
        started=now,
        updated=now,
        title=make_title(messages or []),
        messages=[_trim_message(m) for m in (messages or [])],
    )


# ==========================================================================
#  读写
# ==========================================================================
def default_path(store_path) -> Path:
    """和 extensions.json 放同一个目录（数据目录）。

    用 store.path.parent 而不是 config.ROOT：测试会用临时 Store，
    这样它们天然隔离，不会写到真实数据目录里。
    """
    return Path(store_path).parent / "conversations.json"


def load(path: Path) -> list[Session]:
    """读全部会话，最近的排前面。读不到/坏了都返回空列表。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("sessions")
    if not isinstance(items, list):
        return []
    sessions = []
    for item in items:
        session = Session.from_dict(item)
        if session.id and session.messages:
            sessions.append(session)
    sessions.sort(key=lambda s: s.updated, reverse=True)
    return sessions


def save(path: Path, sessions: list[Session]) -> tuple[bool, str]:
    """原子写入。失败返回 (False, 原因)。

    和 Store.save() 一样走「临时文件 → os.replace」：直接覆写的话，
    写到一半断电就会留下半个 JSON，下次启动整个历史都没了。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 截断：只留最近的，且单会话不超过上限
        trimmed: list[Session] = []
        for session in sorted(sessions, key=lambda s: s.updated, reverse=True):
            if not session.messages:
                continue
            # **不要原地改调用方的 session 对象。** 保存是个「读」操作，
            # 顺手把调用方手里的数据截短会让人很难排查（界面上的消息
            # 会凭空少掉一截）。复制一份再截。
            trimmed.append(Session(
                id=session.id,
                started=session.started,
                updated=session.updated,
                title=session.title,
                messages=session.messages[-SESSION_MAX_MESSAGES:],
            ))
            if len(trimmed) >= MAX_SESSIONS:
                break

        payload = {
            "schema": SCHEMA,
            "sessions": [s.as_dict() for s in trimmed],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(path)
        return True, "已保存"
    except (OSError, TypeError, ValueError) as exc:
        return False, f"写不进 {path.name}：{exc}"


def total_messages(sessions: list[Session]) -> int:
    return sum(len(s.messages) for s in sessions)
