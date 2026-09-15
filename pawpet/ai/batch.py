r"""合并确认：把一轮里的多个待确认操作合成**一次**询问。

为什么需要它
------------
模型一轮可以同时调多个工具（比如「点保存 → 输文件名 → 按回车」）。
按原来的做法，这三个动作会**各弹一次**确认卡片 —— 用户要点三次。
动作越多越烦，最后用户干脆把权限调到「完全自动」，反而更不安全。

所以这里把同一轮里的多个待确认操作**攒起来问一次**：
> 小爪想连着做 3 个操作：
>   1. 点击「保存」
>   2. 在「文件名」里输入 周报
>   3. 按 Enter
> [全部允许]  [全部拒绝]

为什么这样是安全的
------------------
合并确认**减少了询问次数，但不减少信息量** —— 用户看到的仍然是完整的
动作清单，只是从三张卡变成一张。真正的风险不是「少问了几次」，
而是「批准的动作里混进了没细看的危险项」，所以：

* 危险等级为 danger 的操作**不参与合并**，仍然单独问 ——
  执行命令这种事不该藏在批量的第 7 条里
* 只读操作本来就不问，也不会进批次
* 用户拒绝时默认**整批拒绝**，不会只放过一半
* 清单最多列几条，超了就只显示前几条加「还有 N 个」

超时策略：批次比单个操作更容易让用户犹豫，但也不能无限等，
所以沿用同样的超时，超时按拒绝处理（fail closed）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 一个批次最多合并多少个操作。
# 再大用户也看不过来，而且「一次批准十个动作」的后果太扩散。
MAX_BATCH = 6

# 清单里最多显示几条，超出的折叠成「还有 N 个」
MAX_LISTED = 5


@dataclass
class BatchItem:
    """批次里的一个操作。"""

    index: int = 0
    tool: str = ""
    risk: str = "confirm"
    summary: str = ""       # 人话描述，给用户看

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "tool": self.tool,
            "risk": self.risk,
            "summary": self.summary,
        }


@dataclass
class BatchResult:
    """一次合并确认的结果。"""

    approved: list[int] = field(default_factory=list)
    denied: list[int] = field(default_factory=list)
    timed_out: bool = False

    @property
    def all_approved(self) -> bool:
        return bool(self.approved) and not self.denied

    @property
    def all_denied(self) -> bool:
        return bool(self.denied) and not self.approved


def build_title(items: list[BatchItem], danger_count: int = 0) -> str:
    """给用户看的一句话标题。"""
    count = len(items)
    if count == 1:
        return "小爪想做 1 个操作"
    return f"小爪想连着做 {count} 个操作"


def build_summary(items: list[BatchItem]) -> str:
    """把批次渲染成一张清单。

    清单要**完整**（用户批的就是这些），但太长就折叠。
    """
    lines = []
    for item in items[:MAX_LISTED]:
        lines.append(f"{item.index}. {item.summary or item.tool}")
    remaining = len(items) - MAX_LISTED
    if remaining > 0:
        lines.append(f"…还有 {remaining} 个")
    return "\n".join(lines)


def should_merge(requests: list) -> bool:
    """这些待确认请求该不该合并成一次询问。

    只有**一个**的时候就别包装成批次了 —— 直接问更直白。
    """
    return len(requests) > 1


def collect(items: list[BatchItem], decisions: dict,
            timeout: bool = False) -> BatchResult:
    """把用户的逐条决定汇总成批次结果。

    decisions 里没有的操作按**未批准**处理（fail closed）——
    宁可少做一个动作，也不要凭「没说不」就当他同意了。
    """
    result = BatchResult(timed_out=timeout)
    for item in items:
        if decisions.get(item.index) is True:
            result.approved.append(item.index)
        else:
            result.denied.append(item.index)
    return result
