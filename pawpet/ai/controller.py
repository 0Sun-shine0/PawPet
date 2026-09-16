"""AI 模块接到 Qt/QML 的那一层。

线程模型：
    Agent 跑在后台线程里（会阻塞等待网络和用户确认），
    所有界面更新都通过 Qt 信号切回主线程，绝不在工作线程里碰 QML。

用户确认：
    工作线程发出审批请求后阻塞在一个 threading.Event 上；
    主线程收到信号把它显示给用户，用户点允许/拒绝后 set 那个 Event，
    工作线程随即被唤醒继续。这样「逐步确认」不会把界面卡住。
"""

from __future__ import annotations

import base64
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from ..config import (
    AI_DIR,
    AUDIT_FILE,
    LIVE_PREVIEW,
    ROOT,
    read_env_value,
    save_env_value,
)
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
from .agent import (
    DEFAULT_MAX_STEPS,
    MAX_MAX_STEPS,
    MIN_MAX_STEPS,
    STEP_PRESETS,
    AgentCallbacks,
    AgentRunner,
    ApprovalRequest,
    StepEvent,
    clamp_max_steps,
    system_prompt,
)
from .client import AIClient, AiError
from .mcp import MCPClient
from .tools import ToolContext, openai_tools
from .vision import ScreenCapture, downscale_png

MAX_CHAT_ITEMS = 200
MAX_SCREENSHOTS_KEPT = 3


class AiController(QObject):
    # ---------------------------------------------------------- 给 QML 的信号
    messagesChanged = Signal()
    statusChanged = Signal()
    previewChanged = Signal()
    runningChanged = Signal()
    approvalChanged = Signal()
    auditChanged = Signal()
    settingsChanged = Signal()
    memoryChanged = Signal()
    modelsChanged = Signal()
    # 步骤时间线变了（「边做边说」）
    stepsChanged = Signal()
    toastRequested = Signal(str, str)
    # 自动记忆学到了东西。和 _learnedIn 分开：
    # _learnedIn 是线程桥（工作线程 → 主线程），这个是主线程上的对外广播，
    # 界面/托盘/测试都能订阅。
    learnedSomething = Signal(str, object)

    # ------------------------------------------------- 工作线程 → 主线程的桥
    # 都带上轮次号，过期的回调（上一轮的后台线程晚回来）会被丢掉 ——
    # 不然会出现「问了新问题，它把上一轮的结果又输出一遍」。
    _statusIn = Signal(str, int)
    _eventIn = Signal(object, int)
    # (最终文本, 错误, 轮次, 收尾交代) —— 第四项是「干了几件事、文件存哪了、
    # 能不能撤回」，由执行层如实统计，界面上作为助手气泡下面的一行小字。
    _finishedIn = Signal(str, str, int, str)
    _approvalIn = Signal(str, str, str, str, bool, object)
    _modelsIn = Signal(bool, object)
    _learnedIn = Signal(str, object, int)
    _batchIn = Signal(object)

    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store

        self.capture = ScreenCapture()
        self.audit = AuditLog()
        self.audit.set_file(str(AUDIT_FILE))
        self.actions = DesktopActions(self.audit)
        self.context = ToolContext(self.capture, self.actions, store)
        self.callbacks = _QtCallbacks(self)
        self.runner: AgentRunner | None = None

        self._messages: list[dict] = []
        self._status = "空闲"
        self._running = False
        self._pending: dict | None = None
        self._approval_lock = threading.Lock()
        self._approvals: dict[str, dict] = {}
        # 合并确认：当前待决定的批次
        self._batches: dict[str, dict] = {}
        self._batch_entry: dict | None = None
        self._batch: list[dict] = []
        self._preview_rev = 0
        self._preview_path = ""
        self._models: list[str] = []
        self._mcp_clients: list[MCPClient] = []
        self._thread: threading.Thread | None = None
        self._lastLearned: dict = {}
        # 轮次号。每次 send 加一；工作线程和自动记忆线程都带着它回来，
        # 不是当前轮次的一律丢掉（见 _start_worker 里的说明）。
        self._turn = 0
        # 当前这一轮用户的原话。自动记忆要用它 —— 不能回头去消息列表里
        # 找「最后一条用户消息」，那时用户可能已经追问下一句了。
        self._turn_user_text = ""
        # 「边做边说」的时间线：最近几步的人话标签。
        # 新一轮开始时清空，不然会看到上一轮的步骤。
        self._steps: list[str] = []

        self.actions.level = str(store.settings.get("ai_level", LEVEL_CONFIRM))

        self._statusIn.connect(self._apply_status)
        self._eventIn.connect(self._apply_event)
        self._finishedIn.connect(self._apply_finished)
        self._approvalIn.connect(self._show_approval)
        self._modelsIn.connect(self._apply_models)
        self._learnedIn.connect(self._apply_learned)
        self._batchIn.connect(self._apply_batch)

        self._load_persisted_preview()
        self._greet()

    # ---------------------------------------------------------------- 启动语
    def _greet(self) -> None:
        if not self.configured:
            self._push("error", "还没有配置模型 API Key。\n"
                                "在下面的「模型设置」里填一个，或者把 key 写进 .env 的 OPENAI_API_KEY。\n"
                                "任何 OpenAI 兼容的服务都可以：OpenAI、DeepSeek、Moonshot、本地 Ollama…")
        else:
            self._push("info", f"已就绪：{self.clientLabel}\n"
                               f"当前权限：{LEVEL_LABELS.get(self.actions.level, '逐步确认')}。\n"
                               "可以直接说「帮我打开记事本写一段话」，或者「屏幕上这个报错是什么意思」。")

    # ---------------------------------------------------------------- 基础状态
    @property
    def _settings(self) -> dict:
        return self._store.settings

    def _client(self) -> AIClient:
        return AIClient(
            api_key=read_env_value("OPENAI_API_KEY"),
            model=read_env_value("OPENAI_MODEL", self._settings.get("ai_openai_model", "deepseek-flash")),
            base_url=read_env_value("OPENAI_BASE_URL", self._settings.get("ai_openai_base", "https://api.deepseek.com/v1")),
            timeout=int(self._settings.get("ai_timeout", 90)),
        )

    @Property(str, notify=settingsChanged)
    def clientLabel(self) -> str:
        """注意：PySide6 的属性名取自 Python 函数名，所以这里必须是驼峰，
        否则 QML 里写 backend.ai.clientLabel 会拿到 undefined。"""
        client = self._client()
        return f"{client.model} @ {client.base_url}"

    @Property(bool, notify=settingsChanged)
    def configured(self) -> bool:
        return self._client().configured

    @Property(str, notify=settingsChanged)
    def apiKeyHint(self) -> str:
        key = read_env_value("OPENAI_API_KEY")
        if not key:
            return "未配置"
        if len(key) <= 10:
            return "已配置（" + "*" * len(key) + "）"
        return f"已配置（{key[:6]}…{key[-4:]}）"

    @Property(str, notify=settingsChanged)
    def model(self) -> str:
        return read_env_value("OPENAI_MODEL", self._settings.get("ai_openai_model", "deepseek-flash"))

    @Property(str, notify=settingsChanged)
    def baseUrl(self) -> str:
        return read_env_value("OPENAI_BASE_URL", self._settings.get("ai_openai_base", "https://api.deepseek.com/v1"))

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(str, notify=settingsChanged)
    def level(self) -> str:
        return self.actions.level

    @level.setter
    def level(self, value: str) -> None:
        value = value if value in LEVEL_LABELS else LEVEL_CONFIRM
        if value == self.actions.level:
            return
        self.actions.level = value
        self._settings["ai_level"] = value
        self._store.save()
        self.settingsChanged.emit()
        self._push("info", f"权限已切换为「{LEVEL_LABELS[value]}」")

    @Property("QVariantList", notify=settingsChanged)
    def levelOptions(self) -> list:
        return [
            {"key": LEVEL_READ_ONLY, "label": LEVEL_LABELS[LEVEL_READ_ONLY],
             "hint": "只看屏幕，不动键鼠"},
            {"key": LEVEL_CONFIRM, "label": LEVEL_LABELS[LEVEL_CONFIRM],
             "hint": "每次点击/输入都先问你（推荐）"},
            {"key": LEVEL_AUTO, "label": LEVEL_LABELS[LEVEL_AUTO],
             "hint": "只读和键鼠自动执行，危险动作仍要确认"},
            {"key": LEVEL_FULL, "label": LEVEL_LABELS[LEVEL_FULL],
             "hint": "不再询问，包括执行命令（谨慎）"},
        ]

    @Property(bool, notify=settingsChanged)
    def autoScreenshot(self) -> bool:
        return bool(self._settings.get("ai_auto_screenshot", True))

    @autoScreenshot.setter
    def autoScreenshot(self, value: bool) -> None:
        self._settings["ai_auto_screenshot"] = bool(value)
        self._store.save()
        self.settingsChanged.emit()

    # ------------------------------------------------------------ 步数上限
    @Property(int, notify=settingsChanged)
    def maxSteps(self) -> int:
        """一轮任务里最多让模型思考多少步。一步 = 一次模型往返（可含多个工具调用）。"""
        return clamp_max_steps(self._settings.get("ai_max_steps", DEFAULT_MAX_STEPS))

    @maxSteps.setter
    def maxSteps(self, value) -> None:
        steps = clamp_max_steps(value)
        if steps == self.maxSteps:
            return
        self._settings["ai_max_steps"] = steps
        self._store.save()
        self.settingsChanged.emit()
        self._push("info", f"每轮最多执行步数已设为 {steps} 步")

    @Property("QVariantList", notify=settingsChanged)
    def stepOptions(self) -> list:
        """给界面下拉框用的选项，附带一句人话解释。"""
        return [
            {
                "value": steps,
                "label": f"{steps} 步",
                "hint": "简单任务" if steps <= 10 else
                        "日常够用（推荐）" if steps <= 20 else
                        "多步操作" if steps <= 30 else
                        "长流程" if steps <= 50 else
                        "不设实际限制，注意费用",
            }
            for steps in STEP_PRESETS
        ]

    @Property(str, notify=settingsChanged)
    def maxStepsHint(self) -> str:
        steps = self.maxSteps
        return (f"当前 {steps} 步。一步 = 模型看一次结果再决定下一步，"
                f"一步里可以同时做几个动作，所以 {steps} 步通常能完成不少操作。"
                f"可调范围 {MIN_MAX_STEPS}–{MAX_MAX_STEPS}。")

    # ------------------------------------------------------------ 现成任务
    # 「首页直接列几个能点就跑的任务」。
    # 真正的门槛不是模型能力，是用户不知道能说什么 —— 空输入框前面
    # 大部分人只会打一句「你好」。给几条具体的例子，转化率完全不同。
    @Property("QVariantList", constant=True)
    def taskTemplates(self) -> list:
        from .tasks import all_templates

        return all_templates()

    @Property("QVariantList", constant=True)
    def taskGroups(self) -> list:
        """按人群分好组的模板，界面上分栏显示。"""
        from .tasks import groups

        return groups()

    @Slot(str)
    def runTemplate(self, key: str) -> None:
        """点了现成任务就直接开跑，不用用户再打字。"""
        from .tasks import find

        template = find(key)
        if template is None:
            return
        if not self.configured:
            self.toastRequested.emit("还没配置模型", "去「AI 操作 → 设置」填一个 API Key")
            return
        if self._running:
            self.toastRequested.emit("正在忙", "等这一轮跑完再点，或者先按停止")
            return
        # 说明文字也进对话，用户回看时知道这是从模板点出来的
        self._push("info", f"📌 现成任务：{template.label}")
        self.send(template.prompt)

    # ------------------------------------------------------ 模型服务商向导
    # 泛用户卡在第一步：不知道该去哪拿 API Key、填什么地址、选什么模型。
    # 这一组属性把「人话名字 + 注册页链接 + 接口地址 + 常用模型」交给界面，
    # 点一下就自动填好、点一下就跳到注册页。
    @Property("QVariantList", constant=True)
    def modelProviders(self) -> list:
        from .providers import all_providers

        return all_providers()

    @Property("QVariantList", constant=True)
    def modelProviderGroups(self) -> list:
        from .providers import groups

        return groups()

    @Property("QVariantMap", constant=True)
    def primaryProvider(self) -> dict:
        """默认推荐的那家（DeepSeek）。

        泛用户点开界面就该看到一个「去哪拿 Key」的按钮，而不是先做一道
        七选一的选择题。其他家收在「想用别的」后面。
        """
        from .providers import primary

        return primary()

    @Property("QVariantList", constant=True)
    def otherProviders(self) -> list:
        from .providers import alternatives

        return alternatives()

    def _key_page(self) -> tuple[str, str]:
        """「去哪拿 Key」的 (显示名, 链接)。

        判定顺序有讲究：

        1. **当前接口地址不属于任何已知服务商**（等于用户还没选过）→
           一律给默认推荐那家（DeepSeek）。
           不能因为设置里恰好存着 `api.openai.com`（那是安装默认值）就把
           泛用户引导去 OpenAI —— 那家要绑信用卡、还得能上外网，
           是国内用户的第一道坎。
        2. 认得出是哪家 → 用它自己对的那一页。用户点过「想用别的服务商」
           选了 Moonshot，链接和按钮文案都要跟着换成 Kimi 的。
        3. 都没有 → 还是给默认那家，总比不给强。
        """
        from .providers import find, guess_key_help, primary

        name, url = guess_key_help(self.baseUrl)
        if url:
            return name, url
        item = find(primary().get("key") or "")
        return (item.name if item else ""), (item.signup_url if item else "")

    @Slot()
    def openKeyPage(self) -> None:
        """打开「去哪拿 Key」那一页。

        链接从服务商预设里取。单独做成一个 Slot 而不是让 QML 拼字符串：
        QML 只需要调一个动作，不需要知道 URL 长什么样。
        """
        from PySide6.QtGui import QDesktopServices

        _name, url = self._key_page()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @Property(str, notify=settingsChanged)
    def keyPageLabel(self) -> str:
        """「去拿 Key」按钮上的字，带上服务商名字。"""
        name, _url = self._key_page()
        # 「DeepSeek（深度求索）」→ 按钮上只留 DeepSeek
        short = name.split("（")[0] if name else ""
        return f"去 {short} 拿 Key" if short else "去拿 Key"

    @Property(str, notify=settingsChanged)
    def keyPageUrl(self) -> str:
        _name, url = self._key_page()
        return url

    @Slot(str)
    def applyProvider(self, key: str) -> None:
        """选中一家服务商：把接口地址和模型名填好。

        **刻意不写 key** —— key 只能用户自己粘。这里只把「填什么地址、
        用什么模型」这种他答不上来的部分准备好。
        """
        from .providers import find

        provider = find(key)
        if provider is None:
            return
        self._settings["ai_openai_base"] = provider.base_url
        if provider.model:
            self._settings["ai_openai_model"] = provider.model
        self._store.save()
        self.settingsChanged.emit()
        self.toastRequested.emit(
            "已选好" + provider.name,
            "接着点「去拿 Key」注册，把 key 粘回来就行" if provider.needs_key
            else "本地模型不需要 key，装好 Ollama 直接保存",
        )

    @Property(str, notify=settingsChanged)
    def keyHelpUrl(self) -> str:
        """「去哪拿 key」的链接。

        按当前填的接口地址反查服务商；查不到就给一个通用的对比页，
        总比让用户自己搜强。
        """
        from .providers import guess_key_help

        _name, url = guess_key_help(self.baseUrl)
        return url

    @Property(str, notify=settingsChanged)
    def keyHelpLabel(self) -> str:
        from .providers import guess_key_help

        name, url = guess_key_help(self.baseUrl)
        if not url:
            return ""
        return f"去 {name} 拿 Key"

    @Slot(str)
    def openUrl(self, url: str) -> None:
        """用系统浏览器打开链接。

        走 QDesktopServices 而不是 QML 的 Qt.openUrlExternally：
        注册页链接是从 Python 侧（服务商预设）来的，集中在一个地方处理
        少一层信任边界要操心 —— QML 里只需要传字符串。
        """
        from PySide6.QtGui import QDesktopServices

        target = (url or "").strip()
        if not target.startswith(("http://", "https://")):
            return
        QDesktopServices.openUrl(QUrl(target))


    # ------------------------------------------------------------ 跨会话记忆
    def _memory(self):
        from .memory import Memory

        try:
            return Memory.from_dict(self._store.memory)
        except Exception:  # noqa: BLE001 - 记忆坏了不该让 AI 用不了
            return Memory()

    def _memory_text(self) -> str:
        """渲染成可以塞进系统提示词的一段。没有记忆就返回空串。"""
        from .memory import format_for_prompt

        try:
            return format_for_prompt(self._memory())
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------ 知识库
    def _kb_docs(self):
        from .kb import KnowledgeBase

        try:
            # load_repaired 而不是 load：老版本导进来的 HTML 资料存的是
            # `%22%3A` 和 `<div style=...>` 这种垃圾，检索结果没法用。
            # 它会检查一遍源文件还在不在，在就按新逻辑重解析并写回 ——
            # 用户什么都不用做，下次搜索就正常了。
            return KnowledgeBase(self._store).load_repaired()
        except Exception:  # noqa: BLE001 - 知识库坏了不该让 AI 用不了
            return []

    def _kb_text(self) -> str:
        """知识库**目录**（不是正文）。没有资料就返回空串。"""
        from .kb import catalog_text

        try:
            return catalog_text(self._kb_docs())
        except Exception:  # noqa: BLE001
            return ""

    @Property(str, notify=settingsChanged)
    def knowledgeSummary(self) -> str:
        from .kb import describe

        try:
            return describe(self._kb_docs())
        except Exception as exc:  # noqa: BLE001
            return f"知识库读取失败：{exc}"

    @Property(int, notify=settingsChanged)
    def knowledgeCount(self) -> int:
        return len([doc for doc in self._kb_docs() if doc.chunks])

    @Property(int, notify=settingsChanged)
    def knowledgeChunks(self) -> int:
        return sum(doc.chunk_count for doc in self._kb_docs())

    @Slot(str, bool)
    def importKnowledge(self, path: str, recursive: bool = False) -> None:
        """界面上的「导入资料」按钮。"""
        from . import files, kb

        path = (path or "").strip()
        if not path:
            self.toastRequested.emit("没给路径", "填一个文件或文件夹的路径")
            return
        try:
            target = files.expand(path)
        except files.FileDenied as exc:
            self.toastRequested.emit("不能导入", str(exc)[:120])
            return

        book = kb.KnowledgeBase(self._store)
        existing = book.load_repaired()
        try:
            if target.is_dir():
                incoming, notes = kb.import_folder(str(target), existing,
                                                   recursive=recursive)
            else:
                doc, message = kb.import_file(str(target), existing)
                incoming = [doc] if doc else []
                notes = [] if doc else [message]
        except Exception as exc:  # noqa: BLE001
            self.toastRequested.emit("导入失败", f"{type(exc).__name__}: {exc}"[:120])
            return

        if not incoming:
            detail = "；".join(notes[:2]) if notes else "没有可导入的文本文件"
            self._push("error", f"没能导入：{detail}")
            self.toastRequested.emit("导入失败", detail[:100])
            return

        merged = book.add(incoming)
        chunks = sum(doc.chunk_count for doc in incoming)
        self.settingsChanged.emit()
        self.toastRequested.emit("已导入", f"{len(incoming)} 份资料，{chunks} 块")
        self._push("info", f"已导入 {len(incoming)} 份资料（{chunks} 块）。"
                           f"知识库现在有 {len([d for d in merged if d.chunks])} 份。\n"
                           "正文不会全塞进对话，我回答时会按需检索。")
        if notes:
            self._push("info", "；".join(notes[:3]))

    @Slot()
    def clearKnowledge(self) -> None:
        from .kb import KnowledgeBase

        count = KnowledgeBase(self._store).clear()
        self.settingsChanged.emit()
        self._push("info", f"已清空知识库（{count} 份资料）。")
        self.toastRequested.emit("知识库已清空", f"移除了 {count} 份资料")

    @Slot(str)
    def forgetKnowledge(self, name: str) -> None:
        from .kb import KnowledgeBase

        removed = KnowledgeBase(self._store).remove(name or "")
        self.settingsChanged.emit()
        if removed is None:
            self.toastRequested.emit("没找到", "知识库里没有这份资料")
        else:
            self.toastRequested.emit("已移除", removed.name[:40])
            self._push("info", f"已从知识库移除「{removed.name}」。")

    @Slot(str, result=str)
    def searchKnowledge(self, query: str) -> str:
        """界面上的试查框：让用户自己验证检索效果。"""
        from . import kb

        query = (query or "").strip()
        if not query:
            return "输入一个词试试。"
        docs = self._kb_docs()
        if not any(doc.chunks for doc in docs):
            return "知识库是空的。"
        hits = kb.search(docs, query, limit=5)
        if not hits:
            return f"没找到和「{query}」相关的内容。"
        lines = [f"找到 {len(hits)} 段：", ""]
        for index, hit in enumerate(hits, 1):
            head = hit.heading or "（无标题）"
            preview = hit.text[:110].replace("\n", " ")
            lines.append(f"{index}. 【{hit.doc} · {head}】")
            lines.append(f"   {preview}…")
        return "\n".join(lines)

    @Property(str, notify=memoryChanged)
    def memorySummary(self) -> str:
        """给界面看的可读摘要。"""
        from .memory import describe

        try:
            return describe(self._memory())
        except Exception as exc:  # noqa: BLE001
            return f"记忆读取失败：{exc}"

    @Property(int, notify=memoryChanged)
    def memoryCount(self) -> int:
        memory = self._memory()
        return len(memory.facts) + len(memory.aliases) + len(memory.tasks)

    @Property(int, notify=memoryChanged)
    def autoMemoryCount(self) -> int:
        """有多少条是小爪自己学来的。让用户知道该去审阅哪些。"""
        from .memory import count_auto

        try:
            return count_auto(self._memory())
        except Exception:  # noqa: BLE001
            return 0

    @Property(str, notify=memoryChanged)
    def autoLearnStatus(self) -> str:
        """最近一次自动学习干了什么。面板上给用户看。"""
        learned = getattr(self, "_lastLearned", None) or {}
        if not learned:
            return "还没有自动学到东西。正常聊几句，它自己会判断。"
        parts = []
        for text in learned.get("learned") or []:
            parts.append(f"· 新记住：{text}")
        for text in learned.get("updated") or []:
            parts.append(f"· 更新了：{text}")
        for text in learned.get("forgotten") or []:
            parts.append(f"· 删掉了旧记忆：{text}")
        if not parts:
            return "最近一轮没有值得记的东西。"
        return "\n".join(parts[:6])

    @Property(bool, notify=memoryChanged)
    def memoryEnabled(self) -> bool:
        return bool(self._settings.get("ai_memory_enabled", True))

    @memoryEnabled.setter
    def memoryEnabled(self, value: bool) -> None:
        self._settings["ai_memory_enabled"] = bool(value)
        self._store.save()
        self.settingsChanged.emit()
        self.memoryChanged.emit()
        self._push("info", "记忆已开启，以后不用重复交代"
                    if value else "记忆已关闭，之前记住的内容仍然保留（可在设置里清空）")

    @Slot()
    def clearMemory(self) -> None:
        from .memory import MemoryBook

        counts = MemoryBook(self._store).clear()
        total = counts.get("facts", 0) + counts.get("aliases", 0) + counts.get("tasks", 0)
        self.memoryChanged.emit()
        self._push("info", f"已清空 {total} 条记忆。" if total else "本来就没有记忆。")
        self.toastRequested.emit("记忆已清空", f"删掉了 {total} 条")

    @Slot(str)
    def forgetMemory(self, text: str) -> None:
        from .memory import MemoryBook

        removed = MemoryBook(self._store).forget(text or "")
        self.memoryChanged.emit()
        if removed is None:
            self.toastRequested.emit("没找到", "没有匹配的记忆")
        else:
            self.toastRequested.emit("已忘掉", removed.text[:40])
            self._push("info", f"已忘掉：{removed.text}")

    @Slot()
    def refreshMemory(self) -> None:
        """重新渲染记忆（界面上的刷新按钮，改完数据文件后也能用）。"""
        self.memoryChanged.emit()

    @Property(bool, notify=settingsChanged)
    def mcpEnabled(self) -> bool:
        return bool(self._settings.get("ai_mcp_enabled", False))

    @mcpEnabled.setter
    def mcpEnabled(self, value: bool) -> None:
        self._settings["ai_mcp_enabled"] = bool(value)
        self._store.save()
        self.settingsChanged.emit()

    @Property(str, notify=settingsChanged)
    def capabilityReport(self) -> str:
        lines = [self.capture.status()]
        lines.append(self.actions.status())

        # UI Automation 是「读控件而不是猜坐标」的总开关，放在显眼位置
        try:
            from . import uia

            lines.append(uia.status())
        except Exception as exc:  # noqa: BLE001
            lines.append(f"UI Automation 不可用：{exc}")

        # 文件读写的能力边界：说清楚哪些地方不给碰，
        # 用户才知道「AI 到底能看到我磁盘上的什么」
        try:
            from . import files

            home = str(Path.home())
            secret = [item.replace("[读写都禁止] ", "")
                      for item in files.describe_sensitive()
                      if item.startswith("[读写都禁止]")]
            readonly = [item.replace("[只允许读]   ", "")
                        for item in files.describe_sensitive()
                        if item.startswith("[只允许读]")]

            def shorten(items: list[str], limit: int) -> str:
                shown = [item.replace(home, "~") for item in items[:limit]]
                text = "、".join(shown)
                return text + ("…" if len(items) > limit else "")

            lines.append("文件读写：可用（文本文件可直接读）")
            lines.append(f"  不读也不写：{shorten(secret, 4)}")
            lines.append(f"  只读不写：{shorten(readonly, 3)}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"文件读写不可用：{exc}")

        try:
            import pyautogui  # noqa: F401

            lines.append("急停：把鼠标快速甩到屏幕左上角")
        except ImportError:
            pass
        return "\n".join(lines)

    # ---------------------------------------------------------------- 对话记录
    @Property("QVariantList", notify=messagesChanged)
    def messages(self) -> list:
        return self._messages

    @Property(int, notify=messagesChanged)
    def messageCount(self) -> int:
        return len(self._messages)

    @Property(str, notify=messagesChanged)
    def lastSummary(self) -> str:
        """最近一条助手回复的纯文本版。指令栏用它显示结果。"""
        for item in reversed(self._messages):
            if item.get("role") == "assistant" and (item.get("text") or "").strip():
                return item.get("plain") or item["text"].strip()
        return ""

    @Property(str, notify=messagesChanged)
    def lastSummaryHtml(self) -> str:
        """同上的富文本版，保留加粗和列表的可读性。"""
        for item in reversed(self._messages):
            if item.get("role") == "assistant" and (item.get("text") or "").strip():
                return item.get("html") or ""
        return ""

    @Property(str, notify=messagesChanged)
    def lastError(self) -> str:
        for item in reversed(self._messages):
            if item.get("role") == "error":
                return (item.get("text") or "").strip()
        return ""

    def _push(self, role: str, text: str, **extra) -> None:
        from .markdown import to_plain, to_qt_html

        item = {
            "id": f"m{int(time.time() * 1000)}{len(self._messages)}",
            "role": role,
            "text": text,
            # 助手和错误信息可能是 Markdown，转成富文本再给界面；
            # 用户自己输入的原样显示，免得他打的星号被吃掉。
            "html": to_qt_html(text) if role in ("assistant", "error") else "",
            "plain": to_plain(text) if role == "assistant" else text,
            "time": datetime.now().strftime("%H:%M:%S"),
            "tool": extra.get("tool", ""),
            "risk": extra.get("risk", ""),
            "ok": extra.get("ok", True),
            "detail": extra.get("detail", ""),
            "seconds": extra.get("seconds", 0.0),
            "image": extra.get("image", ""),
            "imageNote": extra.get("imageNote", ""),
            # 失败时给出「接下来怎么办」的一行说明。
            # 让用户看得懂卡在哪、正打算怎么绕 —— 而不是只看到一句报错。
            "recovery": extra.get("recovery", ""),
            # 「做完有交代」：干了几件事、文件存哪了、能不能撤回。
            # 只有这一轮的**最终**回答才带，中间过程不需要。
            "report": extra.get("report", ""),
        }
        self._messages.append(item)
        del self._messages[:-MAX_CHAT_ITEMS]
        self.messagesChanged.emit()

    # ---------------------------------------------------------------- 画面预览
    @Property(str, notify=previewChanged)
    def previewSource(self) -> str:
        return self._preview_path

    @Property(bool, notify=previewChanged)
    def hasPreview(self) -> bool:
        return bool(self._preview_path)

    def _load_persisted_preview(self) -> None:
        if LIVE_PREVIEW.exists():
            self._preview_path = QUrl.fromLocalFile(str(LIVE_PREVIEW)).toString()

    def _write_preview(self, png: bytes) -> str:
        if not png:
            return ""
        try:
            AI_DIR.mkdir(parents=True, exist_ok=True)
            LIVE_PREVIEW.write_bytes(png)
        except OSError:
            return ""
        self._preview_rev += 1
        return QUrl.fromLocalFile(str(LIVE_PREVIEW)).toString() + f"?v={self._preview_rev}"

    @Slot()
    def capturePreview(self) -> None:
        """只截图给界面看，不惊动模型。"""
        shot = self.capture.grab(monitor=int(self._settings.get("ai_monitor", 1)), with_preview=True)
        if not shot.ok:
            self.toastRequested.emit("截图失败", shot.message)
            return
        path = self._write_preview(downscale_png(shot.preview_png, 1100))
        if path:
            self._preview_path = path
            self.previewChanged.emit()
        self._push("info", f"{shot.message}")

    # ---------------------------------------------------------------- 审批流
    @Property("QVariantMap", notify=approvalChanged)
    def pendingApproval(self) -> dict:
        return self._pending or {}

    @Property(bool, notify=approvalChanged)
    def hasPendingApproval(self) -> bool:
        return self._pending is not None

    def _show_approval(self, request_id: str, tool: str, risk: str, summary: str,
                       question: bool = False, options: object = None) -> None:
        self._pending = {
            "id": request_id, "tool": tool, "risk": risk, "summary": summary,
            "question": bool(question),
            "options": [str(item) for item in (options or [])],
        }
        self.approvalChanged.emit()

    @Property(bool, notify=approvalChanged)
    def hasPendingQuestion(self) -> bool:
        """当前挂着的是「问你一句」还是普通审批？

        界面靠它决定显示哪张卡片：提问要一个输入框（或者几个选项按钮），
        审批要「允许 / 拒绝」。两者走的是同一条阻塞通道。
        """
        return bool(self._pending and self._pending.get("question"))

    @Slot(str)
    def answerPending(self, text: str) -> None:
        """回答小爪提出的问题（不是审批，所以内容任意）。"""
        if not self._pending:
            return
        request_id = self._pending["id"]
        with self._approval_lock:
            entry = self._approvals.get(request_id)
        if entry is not None:
            entry["answer"] = (text or "").strip()
            entry["result"] = bool(entry["answer"])
            entry["event"].set()
        # 同 resolveApproval：不管工作线程那边还在不在，卡片都要收起来
        if self._pending.get("id") == request_id:
            self._pending = None
            self.approvalChanged.emit()

    def _apply_batch(self, items: object) -> None:
        """主线程：把整批待确认操作显示出来。"""
        self._batch = list(items or [])
        self.approvalChanged.emit()

    @Slot(str, bool)
    def resolveApproval(self, request_id: str, approved: bool) -> None:
        with self._approval_lock:
            entry = self._approvals.get(request_id)
        if entry is not None:
            entry["result"] = bool(approved)
            entry["event"].set()
        # 卡片无论如何都要收起来。
        #
        # 原来这里在 entry 是 None 时直接 return，于是：工作线程等超时之后
        # 卡片会一直挂在界面上，用户点了「允许」它也不消失 —— 看起来就是
        # 界面卡死了。用户点了按钮就必须有反应。
        if self._pending and self._pending.get("id") == request_id:
            self._pending = None
            self.approvalChanged.emit()

    @Slot(bool)
    def resolvePending(self, approved: bool) -> None:
        if self._pending:
            self.resolveApproval(self._pending["id"], approved)

    # ---------------------------------------------------------------- 审计日志
    @Property("QVariantList", notify=auditChanged)
    def auditEntries(self) -> list:
        return list(reversed(self.audit.recent(50)))

    @Slot()
    def clearAudit(self) -> None:
        self.audit.clear()
        self.auditChanged.emit()

    @Slot()
    def openAuditFile(self) -> None:
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(AUDIT_FILE)))

    # ---------------------------------------------------------------- 主入口
    @Slot(str)
    def send(self, text: str) -> None:
        text = (text or "").strip()
        if not text or self._running:
            return
        self._push("user", text)
        self._start_worker(text)

    def _start_worker(self, text: str) -> None:
        self._running = True
        self.runningChanged.emit()
        self.actions.clear_stop()
        # 把这一轮用户的原话交给工具层：remember 要靠它判断
        # 「这条是用户明说要记的」还是「模型干活时顺手记的」
        self.context.recent_user_texts = [text]

        # 给这一轮编个号。
        #
        # **这是个真实的 bug 修复，不是防御性代码。** 上一轮结束时会起一个
        # 后台线程做「自动记忆」判断，它比主线程慢；用户马上追问第二句时，
        # 上一轮的后台线程可能才刚回来，于是「记住了新东西」这条提示
        # 会**插到新一轮的对话里** —— 看起来就是「我问了问题，它把上一轮的
        # 结果又输出了一遍，我的问题没有任何回答」。
        #
        # 加个轮次号，过期的回调直接丢掉。
        self._turn += 1
        turn = self._turn
        self._turn_user_text = text
        # 新一轮：上一轮的步骤时间线要清掉，否则「正在做」会串轮
        self._steps = []
        self.stepsChanged.emit()

        self._thread = threading.Thread(
            target=self._worker, args=(text, turn), name="pawpet-ai", daemon=True
        )
        self._thread.start()

    def _is_current(self, turn: int) -> bool:
        """这个回调还属于当前这一轮吗？"""
        return turn == self._turn

    def _worker(self, text: str, turn: int) -> None:
        error = ""
        final = ""
        try:
            client = self._client()
            if not client.configured:
                self._finishedIn.emit("", "没有配置 API Key", turn, "")
                return

            self.runner = AgentRunner(
                client, self.context, self.actions, self.callbacks,
                max_steps=self.maxSteps,
                memory_text=self._memory_text() if self.memoryEnabled else "",
                kb_text=self._kb_text(),
            )
            # 任务开始时先自动看一眼（界面上那个「每轮开始自动看一眼屏幕」）
            self.runner.auto_screenshot = self.autoScreenshot
            if self.memoryEnabled:
                self.runner.memory_provider = self._memory_text
            # 用户可能中途导入资料，下一轮就该在目录里看到
            self.runner.kb_provider = self._kb_text
            # 保留之前的对话上下文
            self.runner.messages = self._build_history()
            final = self.runner.run(text)
        except AiError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            report = ""
            if self.runner is not None:
                report = getattr(self.runner, "last_summary", "") or ""
            self._finishedIn.emit(final, error, turn, report)

    def _build_history(self) -> list:
        """把界面上的对话还原成模型消息，让多轮对话有上下文。

        只回放纯文本对话，工具调用的中间过程不回放 —— 那会让历史变得又长又乱。
        """
        history: list[dict] = [{
            "role": "system",
            "content": system_prompt(
                self.maxSteps,
                self._memory_text() if self.memoryEnabled else "",
                self._kb_text(),
            ),
        }]
        for item in self._messages[-12:]:
            role = item.get("role")
            text = (item.get("text") or "").strip()
            if not text:
                continue
            if role == "user":
                history.append({"role": "user", "content": text})
            elif role == "assistant":
                history.append({"role": "assistant", "content": text})
        return history

    # ------------------------------------------------------- 工作线程的回调
    def _apply_status(self, text: str, turn: int = 0) -> None:
        # 过期的状态更新会覆盖新一轮的「思考中…」，看起来像卡住了
        if not self._is_current(turn):
            return
        self._status = text
        self.statusChanged.emit()

    def _apply_event(self, event: StepEvent, turn: int = 0) -> None:
        # 上一轮的残留事件插进新一轮的对话，就是用户看到的
        # 「问了新问题却输出上一轮结果」。
        if not self._is_current(turn):
            return
        if event.kind == "assistant":
            self._push("assistant", event.text)
            return
        if event.kind == "error":
            self._push("error", event.text, tool=event.tool, risk=event.risk,
                       ok=False, detail=event.detail)
            return
        if event.kind == "tool":
            # 「边做边说」的素材：工具执行前把这一步记进时间线，
            # 状态行显示「正在做：xxx → 刚做完：yyy」。
            self._note_step(event)
            image_path = ""
            if event.image_png:
                image_path = self._write_preview(event.image_png)
                if image_path:
                    self._preview_path = image_path
                    self.previewChanged.emit()
            self._push(
                "tool",
                event.text or event.tool,
                tool=event.tool,
                risk=event.risk,
                ok=event.ok,
                detail=event.detail,
                seconds=event.seconds,
                image=image_path,
                recovery=event.recovery,
            )
            self.auditChanged.emit()
            return
        self._push("info", event.text)

    # -------------------------------------------------- 边做边说：步骤时间线
    # 用户的原话是「每步显示『正在打开浏览器 → 找到导出按钮』」。
    # 关键不是把工具名念出来，而是让他随时知道**现在在干什么**。
    # 所以这里存的是人话版的标签（工具事件里的 text 已经是
    # _human_summary 生成的人话），不是 tool 名。
    MAX_STEPS_SHOWN = 4

    def _note_step(self, event: StepEvent) -> None:
        label = (event.text or "").strip()
        if not label or event.tool == "screenshot":
            # 「看屏幕」是每轮开头自动做的事，写进时间线只会占位置
            return
        if label not in self._steps:
            self._steps.append(label)
        del self._steps[:-self.MAX_STEPS_SHOWN]
        self.stepsChanged.emit()

    @Property("QVariantList", notify=stepsChanged)
    def stepTimeline(self) -> list:
        """最近几步的人话标签，最早的在前。界面拿它画「→ 甲 → 乙」。"""
        return list(getattr(self, "_steps", []))

    @Property(str, notify=stepsChanged)
    def progressLine(self) -> str:
        """一行进度：「刚做完 A → 刚做完 B」，最后一步单独高亮。"""
        steps = getattr(self, "_steps", [])
        if not steps:
            return ""
        return " → ".join(steps)

    def _apply_finished(self, text: str, error: str, turn: int = 0,
                        report: str = "") -> None:
        if not self._is_current(turn):
            # 这一轮早被新一轮取代了：不要再动界面状态，
            # 更不要起自动记忆线程（那正是「上一轮结果插进新一轮」的来源）
            return
        self._running = False
        self._status = "空闲"
        self.runningChanged.emit()
        self.statusChanged.emit()
        if error:
            self._push("error", error)
            self.toastRequested.emit("AI 出错", error[:120])
        elif text:
            # **把最终回答落进对话。**
            #
            # 这里原来只发了个「任务结束」的提示，没有 _push —— 于是：
            # 模型如果整轮都在调工具、没吐过正文（常见于多步任务），
            # 收尾文字算出来了却**从来不显示**，用户看到的就是
            # 「跑了一堆步骤然后什么都没有了，任务没完成就结束了」。
            # 这是用户实际报过的问题。
            #
            # 但也不能无脑 push：模型在循环中间吐过正文时，那段已经作为
            # 助手消息显示过了，再 push 一遍就重复了。所以先看
            # 「最后一条助手消息」是不是同一段文本，是就跳过。
            last_assistant = ""
            for item in reversed(self._messages):
                if item.get("role") == "assistant":
                    last_assistant = (item.get("text") or "").strip()
                    break
            if last_assistant != text.strip():
                self._push("assistant", text, report=report)
            elif report:
                # 收尾文字和上一条助手消息一样（模型在循环中间已经说过），
                # 那就别重复推一条，但「交代」还是得让用户看到 ——
                # 挂到那条已有的消息上。
                for item in reversed(self._messages):
                    if item.get("role") == "assistant":
                        item["report"] = report
                        self.messagesChanged.emit()
                        break
            self.toastRequested.emit("任务结束", text[:80])

        # 任务结束后，后台判断这一轮有没有值得长期记住的东西。
        # 放在这里（而不是任务中间）有两个好处：不占用步数预算，
        # 也不拖慢用户拿到结果的时间 —— 它在后台线程里跑。
        if self.memoryEnabled:
            self._start_learning(self._turn_user_text, text, turn)

    # ------------------------------------------------------------ 自动学习
    def _start_learning(self, user_text: str, assistant_text: str,
                        turn: int = 0) -> None:
        """在后台线程里跑一次自动记忆判断。

        `user_text` **必须由调用方传进来**，不要回头去 self._messages 里找
        「最后一条用户消息」—— 用户可能已经追问下一句了，那样会把**新问题**
        当成上一轮的内容去学习，学出来的记忆是错的。
        （这个 bug 实际发生过：自动记忆拿错了文本。）
        """
        if not user_text:
            return

        thread = threading.Thread(
            target=self._learn_worker, args=(user_text, assistant_text, turn),
            name="pawpet-ai-learn", daemon=True)
        thread.start()

    def _learn_worker(self, user_text: str, assistant_text: str,
                      turn: int = 0) -> None:
        """自动学习的工作线程。

        **任何异常都吞掉。** 这一层是「顺手多学一点」，
        学不到最多是记忆少一条 —— 绝不能因为判别请求失败
        就在对话里冒一个报错出来吓用户。
        """
        try:
            from .autolearn import learn_from_turn
            from .memory import MemoryBook

            client = self._client()
            result = learn_from_turn(
                client, MemoryBook(self._store), user_text, assistant_text)
            if result.changed:
                # 切回主线程更新界面（信号是线程安全的），带上轮次号
                self._learnedIn.emit(result.summary(), result.as_dict(), turn)
        except Exception:  # noqa: BLE001 - 后台链路，静默失败
            pass

    def _apply_learned(self, summary: str, detail: object,
                       turn: int = 0) -> None:
        """自动学习完成后的界面反馈。**这个函数一定在主线程执行。**

        **刻意不往对话流里塞消息。** 每轮都冒一条「我记住了 2 条」
        会把对话刷得很吵；只在真有收获时给一个轻提示，
        详细内容让用户自己去记忆面板看。

        过期轮次的回调直接丢掉 —— 用户已经开始问下一句了，
        这时候弹一个「记住了新东西」会让他以为答错了。
        记忆本身**已经写进存储**了（_learn_worker 里做的），
        丢掉只是不提示，不会丢数据。
        """
        if not self._is_current(turn):
            return
        self._lastLearned = detail if isinstance(detail, dict) else {}
        self.memoryChanged.emit()
        # 广播给界面（QML 之外的东西也能订阅，比如托盘提示、测试）
        self.learnedSomething.emit(summary, self._lastLearned)
        self.toastRequested.emit("记住了新东西", summary)

    @Property("QVariantMap", notify=memoryChanged)
    def lastLearned(self) -> dict:
        """最近一次自动学习的结果。记忆面板用它显示「刚学到什么」。"""
        return getattr(self, "_lastLearned", {}) or {}

    # ------------------------------------------------------------ 审批桥接
    def request_approval_blocking(self, request: ApprovalRequest) -> bool:
        """在工作线程里被调用，阻塞等待用户点按钮。"""
        request_id = f"a{int(time.time() * 1000)}"
        entry = {"event": threading.Event(), "result": False, "answer": ""}
        with self._approval_lock:
            self._approvals[request_id] = entry

        self._approvalIn.emit(request_id, request.tool_name, request.risk,
                              request.summary, bool(request.question),
                              list(request.options or []))

        # 最多等 5 分钟，避免用户走开后线程永久挂着
        if not entry["event"].wait(timeout=300):
            with self._approval_lock:
                self._approvals.pop(request_id, None)
            if request.question and self._pending \
                    and self._pending.get("id") == request_id:
                self._pending = None
                self.approvalChanged.emit()
            return False

        with self._approval_lock:
            self._approvals.pop(request_id, None)
        return bool(entry["result"])

    def ask_user_blocking(self, question: str, options: list) -> str:
        """把模型的问题抛给用户，阻塞等他的回答（和审批共用一条通道）。

        超时同样按「没回答」处理 —— 用户可能关着屏幕走开了，
        这时候让工作线程一直挂着没有意义。
        """
        request_id = f"q{int(time.time() * 1000)}"
        entry = {"event": threading.Event(), "result": False, "answer": ""}
        with self._approval_lock:
            self._approvals[request_id] = entry

        self._approvalIn.emit(request_id, "ask_user", "read", question, True,
                              list(options or []))

        if not entry["event"].wait(timeout=300):
            with self._approval_lock:
                self._approvals.pop(request_id, None)
            if self._pending and self._pending.get("id") == request_id:
                self._pending = None
                self.approvalChanged.emit()
            return ""

        with self._approval_lock:
            self._approvals.pop(request_id, None)
        return str(entry.get("answer") or "")

    # ------------------------------------------------------------ 合并确认
    @Property("QVariantList", notify=approvalChanged)
    def pendingBatch(self) -> list:
        """当前待确认的批次清单，给界面画「一次问完」的卡片。"""
        return list(self._batch or [])

    @Property(bool, notify=approvalChanged)
    def hasPendingBatch(self) -> bool:
        return bool(self._batch)

    @Slot(int, bool)
    def resolveBatchItem(self, index: int, approved: bool) -> None:
        """用户在批次卡片上逐条决定允许/拒绝。"""
        entry = self._batch_entry
        if entry is None:
            return
        decisions = entry.setdefault("decisions", {})
        decisions[int(index)] = bool(approved)
        self.approvalChanged.emit()   # 让界面刷新勾选状态

    @Slot(bool)
    def resolveBatchAll(self, approved: bool) -> None:
        """一键全部允许 / 全部拒绝。"""
        entry = self._batch_entry
        if entry is None:
            return
        for item in entry.get("items") or []:
            entry.setdefault("decisions", {})[item["index"]] = bool(approved)
        entry["event"].set()

    @Slot()
    def confirmBatch(self) -> None:
        """按当前勾选提交。没勾的按**拒绝**处理（fail closed）。"""
        entry = self._batch_entry
        if entry is None:
            return
        entry["event"].set()

    def request_approval_batch_blocking(self, items: list) -> dict:
        """在工作线程里被调用，阻塞等待用户对整批做出决定。

        和单个确认的区别：这里一次把清单摆出来，用户勾完提交。
        批量比单个更容易让人犹豫，但超时策略不变（fail closed）。
        """
        batch_id = f"b{int(time.time() * 1000)}"
        payload = [item.as_dict() for item in items]
        entry = {
            "event": threading.Event(),
            "decisions": {},
            "items": payload,
            "id": batch_id,
        }
        with self._approval_lock:
            self._batches[batch_id] = entry

        self._batch_entry = entry
        self._batch = payload
        self._batchIn.emit(payload)

        if not entry["event"].wait(timeout=300):
            with self._approval_lock:
                self._batches.pop(batch_id, None)
            self._batch_entry = None
            self._batch = []
            self.approvalChanged.emit()
            return {}

        with self._approval_lock:
            self._batches.pop(batch_id, None)
        decisions = dict(entry.get("decisions") or {})
        self._batch_entry = None
        self._batch = []
        self.approvalChanged.emit()
        return decisions

    # ---------------------------------------------------------------- 控制
    @Slot()
    def stop(self) -> None:
        if self.runner is not None:
            self.runner.request_stop()
        self.actions.request_stop()
        self.actions.clear_stop()
        self._status = "正在停止…"
        self.statusChanged.emit()
        # 如果有卡住的审批，一并放行（拒绝）
        if self._pending:
            self.resolvePending(False)

    @Slot()
    def clear(self) -> None:
        self._messages.clear()
        self.messagesChanged.emit()
        if self.runner is not None:
            self.runner.reset()

    # ------------------------------------------------------------ 模型设置
    @Slot(str)
    def saveApiKey(self, key: str) -> None:
        key = (key or "").strip()
        if not key:
            self.toastRequested.emit("未保存", "API Key 不能为空")
            return
        if save_env_value("OPENAI_API_KEY", key):
            self.settingsChanged.emit()
            self._push("info", "API Key 已写入 .env（不会存进 pet_data.json）")
            self.toastRequested.emit("已保存", "API Key 已更新")
        else:
            self.toastRequested.emit("保存失败", "无法写入 .env 文件")

    @Slot(str, str)
    def saveEndpoint(self, base_url: str, model: str) -> None:
        base_url = (base_url or "").strip()
        model = (model or "").strip()
        if base_url:
            save_env_value("OPENAI_BASE_URL", base_url.rstrip("/"))
            self._settings["ai_openai_base"] = base_url.rstrip("/")
        if model:
            save_env_value("OPENAI_MODEL", model)
            self._settings["ai_openai_model"] = model
        self._store.save()
        self.settingsChanged.emit()
        self.toastRequested.emit("已保存", f"接口设置为 {model or '默认模型'}")

    @Slot()
    def testConnection(self) -> None:
        if self._running:
            return
        self._status = "正在测试连接…"
        self.statusChanged.emit()

        def probe():
            # 连接测试不占轮次：把它算作「当前轮次」，
            # 这样它的回显不会被 _is_current 丢掉。
            turn = self._turn
            ok, message = self._client().test_connection()
            self._finishedIn.emit("", "" if ok else message, turn, "")
            self._statusIn.emit("空闲", turn)
            self._eventIn.emit(StepEvent(
                kind="info" if ok else "error",
                text=("连接正常：" + message) if ok else ("连接失败：" + message),
            ), turn)

        threading.Thread(target=probe, name="pawpet-ai-test", daemon=True).start()

    @Property("QVariantList", notify=modelsChanged)
    def availableModels(self) -> list:
        return self._models

    @Slot()
    def fetchModels(self) -> None:
        def probe():
            ok, payload = self._client().list_models()
            self._modelsIn.emit(ok, payload if ok else str(payload))

        threading.Thread(target=probe, name="pawpet-ai-models", daemon=True).start()

    def _apply_models(self, ok: bool, payload) -> None:
        if not ok:
            self.toastRequested.emit("获取模型列表失败", str(payload)[:120])
            return
        self._models = list(payload)[:200]
        self.modelsChanged.emit()
        self.toastRequested.emit("已获取模型列表", f"共 {len(self._models)} 个")

    # ------------------------------------------------------------------ MCP
    @Slot(result=str)
    def mcpStatus(self) -> str:
        config_path = ROOT / "mcp_servers.json"
        if not config_path.exists():
            return "没有 mcp_servers.json"
        if not self.mcpEnabled:
            return "MCP 已关闭"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"配置读取失败：{exc}"
        servers = [s for s in (config.get("servers") or []) if s.get("enabled")]
        if not servers:
            return "配置里没有启用的 server"
        lines = []
        for spec in servers:
            connected = next((c for c in self._mcp_clients
                              if c.name == spec.get("name") and c.running), None)
            if connected is None:
                lines.append(f"{spec.get('name', '?')}：未连接")
            else:
                lines.append(f"{spec.get('name', '?')}：已连接，{len(connected.tools)} 个工具")
        return "\n".join(lines)

    @Slot()
    def connectMcp(self) -> None:
        config_path = ROOT / "mcp_servers.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError:
            self._push("error", "读不到 mcp_servers.json")
            return
        except json.JSONDecodeError as exc:
            self._push("error", f"mcp_servers.json 格式有误：{exc}")
            return

        servers = [s for s in (config.get("servers") or []) if s.get("enabled")]
        if not servers:
            self._push("info", "mcp_servers.json 里没有启用的 server。"
                               "把 enabled 改成 true 并填好 command 再试。")
            return

        for spec in servers:
            name = spec.get("name") or "unnamed"
            existing = next((c for c in self._mcp_clients if c.name == name), None)
            if existing is not None and existing.running:
                self._push("info", f"MCP {name} 已经连着了")
                continue
            client = MCPClient(
                name=name,
                command=spec.get("command") or [],
                cwd=spec.get("cwd"),
                timeout=float(spec.get("timeout", 20)),
            )
            ok, message = client.start()
            if ok:
                self._mcp_clients.append(client)
                names = ", ".join(t["name"] for t in client.tool_summaries()) or "无"
                self._push("info", f"MCP {name} 已连接：{message}\n可用工具：{names}")
            else:
                self._push("error", f"MCP {name} 连接失败：{message}")

    @Slot()
    def disconnectMcp(self) -> None:
        for client in self._mcp_clients:
            client.stop()
        self._mcp_clients.clear()
        self._push("info", "已断开所有 MCP 连接")

    def shutdown(self) -> None:
        self.stop()
        for client in self._mcp_clients:
            client.stop()
        self._mcp_clients.clear()
        if self._pending:
            self.resolvePending(False)


class _QtCallbacks(AgentCallbacks):
    """把 Agent 的回调转成 Qt 信号（自动跨线程排队到主线程）。

    每个回调都带上**轮次号**：这些回调是在工作线程里触发的，而用户可能
    已经发了下一句。不带轮次号的话，上一轮的残留事件会插进新一轮的对话里。
    轮次号从 controller 现取 —— 回调对象是复用的，不能在构造时定死。
    """

    def __init__(self, controller: AiController) -> None:
        self._c = controller

    @property
    def _turn(self) -> int:
        return self._c._turn

    def on_status(self, text: str) -> None:
        self._c._statusIn.emit(text, self._turn)

    def on_event(self, event: StepEvent) -> None:
        self._c._eventIn.emit(event, self._turn)

    def on_image(self, png: bytes, note: str) -> None:
        pass    # 图片随 tool 事件一起送出去，这里不需要单独处理

    def on_finished(self, text: str) -> None:
        pass    # 收尾统一由 _worker 的 finally 处理

    def on_error(self, text: str) -> None:
        self._c._eventIn.emit(StepEvent(kind="error", text=text), self._turn)

    def request_approval(self, request: ApprovalRequest) -> bool:
        return self._c.request_approval_blocking(request)

    def request_approval_batch(self, items: list) -> dict:
        """把整批操作一次问完，而不是逐个弹卡片。"""
        return self._c.request_approval_batch_blocking(items)

    def ask_user(self, question: str, options: list) -> str:
        return self._c.ask_user_blocking(question, options)
