r"""专门验证「截图工具」这条路的历史形状。

截图工具比较特殊：它不是返回 tool 消息就完了，还会**额外追加一条
带图片的 user 消息**。这个形状和普通工具不同，容易漏测。

顺带验一下上下文膨胀：图片是 base64 塞进消息的，如果没被裁掉，
历史会大到让中转网关直接拒绝。

用法：
    .venv\Scripts\python.exe tools\screenshothist.py
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pawpet.ai.agent import MAX_HISTORY_MESSAGES, AgentRunner  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def validate(messages: list[dict]) -> str:
    seen: set[str] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if call.get("id"):
                    seen.add(call["id"])
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if not call_id or call_id not in seen:
                return f"第 {index} 条 tool({call_id}) 找不到对应的 tool call"
    return ""


class FakeCall:
    def __init__(self, call_id, name, arguments="{}"):
        self.id = call_id
        self.name = name
        self.arguments = {}
        self.raw_arguments = arguments


class FakeReply:
    def __init__(self, text="", calls=None):
        self.text = text
        self.tool_calls = calls or []

    @property
    def wants_tools(self):
        return bool(self.tool_calls)


class Callbacks:
    def on_status(self, t): pass
    def on_event(self, e): pass
    def on_image(self, p, n): pass
    def on_finished(self, t): pass
    def on_error(self, t): pass
    def request_approval(self, r): return True


class Actions:
    def clear_stop(self): pass
    def request_stop(self): pass
    def blocked(self, r): return False


class ShotClient:
    """每步都调 screenshot，并记录每次请求的历史体积。"""

    def __init__(self, steps, png_bytes=200_000):
        # 用接近真实的体积：1920x1080 的 PNG 压完大概几百 KB
        self.png = _fake_png(png_bytes)
        self.remaining = steps
        self.seen: list[list[dict]] = []
        self.problems: list[str] = []

    def chat(self, messages, tools=None):
        self.seen.append(messages)
        error = validate(messages)
        if error:
            self.problems.append(error)
        if self.remaining <= 0:
            return FakeReply(text="看完了，就这样。")
        self.remaining -= 1
        return FakeReply(calls=[FakeCall(f"shot_{self.remaining}", "screenshot")])


def _fake_png(size: int) -> bytes:
    """造一个体积接近真实的假 PNG（内容不重要，只看大小）。"""
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * max(0, size - 8)
    return raw


def message_bytes(messages) -> int:
    import json

    try:
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    print("截图路径的历史形状与体积\n")

    client = ShotClient(steps=8)
    runner = AgentRunner(client, object(), Actions(), Callbacks(), max_steps=30)

    # _run_one 对 screenshot 要返回 bundle（data_url + preview）
    def fake_run_one(call, call_id):
        data_url = "data:image/png;base64," + base64.b64encode(client.png).decode()
        runner._record_tool(call_id, call.name, "已截屏 1920x1080", ok=True)
        return {"data_url": data_url, "note": "显示器 1", "preview": client.png}

    runner._run_one = fake_run_one
    runner.run("一直看屏幕")

    print(f"   请求 {len(client.seen)} 次")
    check("每一次请求的历史都合法（含截图那条 user 消息）",
          not client.problems, str(client.problems[:2]))

    # 关键：图片必须被裁掉，否则历史会膨胀到被网关拒绝
    sizes = [message_bytes(s) for s in client.seen]
    worst = max(sizes) if sizes else 0
    print(f"   历史体积：最大 {worst / 1024 / 1024:.2f} MB，"
          f"最后一次 {sizes[-1] / 1024 / 1024:.2f} MB")
    check("历史没有被图片撑爆（< 6 MB）", worst < 6 * 1024 * 1024,
          f"实际 {worst / 1024 / 1024:.2f} MB")

    # 带图片的消息最多留 2 条
    last = client.seen[-1]
    with_image = [
        m for m in last
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]
    check("带图片的消息最多留 2 条", len(with_image) <= 2, f"实际 {len(with_image)}")
    check("历史条数没超上限", len(last) <= MAX_HISTORY_MESSAGES, f"{len(last)} 条")

    # 旧图片应该变成占位文字，而不是还在
    placeholders = [
        m for m in last
        if isinstance(m.get("content"), str) and "截图已省略" in m["content"]
    ]
    check("旧截图被换成了占位文字", len(placeholders) >= 1,
          f"找到 {len(placeholders)} 条占位")

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
