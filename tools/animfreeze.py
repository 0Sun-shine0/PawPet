r"""把界面上的动画和定时器**冻住**，让「连续两帧逐像素相同」成立。

## 为什么要有这个共享模块

这段逻辑原来是 `densityshots.py` 的私有函数。但它解决的问题是**通用的**：
任何「渲染 → 抓图 → 比像素」的工具都会被动画干扰。

实测代价：`pxdiff.py`（写得更早，没有这段）在设置页的噪声地板
**双峰到 63.59%**，触发了它自己的「容差没有判别力」断言 —— 而根因
就是四个宠物预览一直在呼吸/眨眼。

**所以抽成一份，两边共用。** 复制第二份一定会漂
（本项目已经吃过「手写清单会漏」的亏）。

## 冻的是什么

来源是 `Pet.qml`：

  · `breathAnim { running: true; loops: Animation.Infinite }`
    —— **永远在呼吸**（`NumberAnimation` 动 `bob`）
  · `blinkTimer` —— 随机 1600~4800ms 眨一次眼（动 `blinking`）
  · 还有摇尾、thinking / bored / sleepy / idle 各自的定时器

**设置页有 4 个宠物预览**，所以那一页受影响最大。

## 三个坑（都实测踩过）

### 一、`findChildren(QObject)` **看不到 Pet 子树**

实测对比（设置页）：

| 遍历方式 | 对象数 | 有 `tailAngle` 的 | `CanvasItem` |
|---|---|---|---|
| `dash.findChildren(QObject)` | 3097 | **0** | 1 |
| 视觉树 `childItems()` | 1963 | **4** | 13 |

`findChildren` 返回的对象**更多**，却**漏掉了整个 Pet 子树** ——
所以「返回得多」不等于「覆盖全」。这里走**视觉树**。

### 二、`property("running")` 读出 `False`，动画却真的在跑

那 4 个 Pet 的 `bob` 实测是 `-3.37`（正在呼吸中），
但它们身上 8 个动画对象的 `running` **全部读成 `False`**。

所以**不判断、无条件 `stop()`**（`QMetaObject.invokeMethod`）。

### 三、只停动画不够，还要**归位**

停掉动画只是不再变化；`bob` 会**停在半口气的位置**（实测 `-3.37`）。
静止值必须显式归位，否则不同档位/不同次渲染的图会落在
**不同的呼吸相位**上 —— 那正是假的布局差异。
"""

from __future__ import annotations

from typing import Any, Iterator

# 被动画驱动的属性 → 静止值
REST_VALUES: dict[str, Any] = {
    "bob": 0.0, "tailAngle": 0.0, "sway": 0.0,
    "hop": 0.0, "loopHop": 0.0,
    "squashX": 1.0, "squashY": 1.0,
    "loopSquashX": 1.0, "loopSquashY": 1.0,
    "blinking": False,
}


def walk_visual(root_item) -> Iterator[Any]:
    """遍历**视觉树**（`childItems()`）。

    为什么不用 `findChildren(QObject)` —— 见模块 docstring 的坑一。
    """
    stack = [root_item]
    while stack:
        item = stack.pop()
        if item is None:
            continue
        yield item
        try:
            stack.extend(item.childItems())
        except Exception:  # noqa: BLE001
            continue


def qobject_tree(node) -> Iterator[Any]:
    """从某节点出发遍历它的 QObject 子树（动画/定时器挂在这里）。"""
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        yield cur
        try:
            stack.extend(cur.children())
        except Exception:  # noqa: BLE001
            continue


def freeze(root, *, passes: int = 2) -> dict:
    """把 `root` 下的动画/定时器全停掉，并把被驱动的属性归位。

    `root` 传 `dash.property("contentItem")`（或任意 `QQuickItem`）。

    **跑两遍**（`passes=2`）是有意的：冻结本身会触发重绘，
    而重绘可能让某些 `running: true` 的动画重新起来。
    第二遍之后才稳定 —— 实测 `densityshots.py` 就是这么收敛到
    「20 张全部两帧逐像素相同」的。

    返回统计信息，便于调用方报到报告里（**不静默**）。
    """
    from PySide6.QtCore import QMetaObject

    stopped = 0
    reset: set[str] = set()

    for _ in range(max(1, passes)):
        for item in walk_visual(root):
            # ---- 停这个节点上的动画/定时器（走它的 QObject 子树）----
            for node in qobject_tree(item):
                try:
                    cls = node.metaObject().className()
                except Exception:  # noqa: BLE001
                    continue
                if "Animation" in cls:
                    try:
                        QMetaObject.invokeMethod(node, "stop")
                        stopped += 1
                    except Exception:  # noqa: BLE001
                        pass
                elif cls.startswith("QQuickTimer") or cls == "QQmlTimer":
                    try:
                        QMetaObject.invokeMethod(node, "stop")
                        stopped += 1
                    except Exception:  # noqa: BLE001
                        pass
            # ---- 归位被动画驱动的属性 ----
            for prop, value in REST_VALUES.items():
                try:
                    if item.property(prop) is not None:
                        if item.setProperty(prop, value):
                            reset.add(prop)
                except Exception:  # noqa: BLE001
                    pass

    return {"stopped": stopped, "reset": sorted(reset)}
