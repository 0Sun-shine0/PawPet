"""小爪的 AI 能力：看屏幕、操作电脑、调用 MCP 工具。

模块划分：
    vision.py     屏幕捕获与缩放（含坐标映射）
    actions.py    键鼠/窗口/命令行，带分级安全与审计
    client.py     OpenAI 兼容的对话客户端（函数调用）
    tools.py      给模型的工具定义 + 执行器
    agent.py      观察-决策-执行-再观察 的主循环
    mcp.py        本地 MCP stdio server 客户端
    controller.py 接到 Qt/QML 的那一层
"""

from .actions import (
    LEVEL_AUTO,
    LEVEL_CONFIRM,
    LEVEL_FULL,
    LEVEL_LABELS,
    LEVEL_READ_ONLY,
    AuditLog,
    DesktopActions,
    Risk,
)
from .agent import AgentCallbacks, AgentRunner, ApprovalRequest, StepEvent
from .client import AIClient, AiError
from .mcp import MCPClient, MCPError
from .tools import ToolContext, openai_tools
from .vision import ScreenCapture, Shot

__all__ = [
    "AIClient", "AiError", "AgentCallbacks", "AgentRunner", "ApprovalRequest",
    "AuditLog", "DesktopActions", "LEVEL_AUTO", "LEVEL_CONFIRM", "LEVEL_FULL",
    "LEVEL_LABELS", "LEVEL_READ_ONLY", "MCPClient", "MCPError", "Risk",
    "ScreenCapture", "Shot", "StepEvent", "ToolContext", "openai_tools",
]
