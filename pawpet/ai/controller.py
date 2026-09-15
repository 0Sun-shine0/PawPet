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
from .agent import AgentCallbacks, AgentRunner, ApprovalRequest, StepEvent
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
    modelsChanged = Signal()
    toastRequested = Signal(str, str)

    # ------------------------------------------------- 工作线程 → 主线程的桥
    _statusIn = Signal(str)
    _eventIn = Signal(object)
    _finishedIn = Signal(str, str)
    _approvalIn = Signal(str, str, str, str)
    _modelsIn = Signal(bool, object)

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
        self._preview_rev = 0
        self._preview_path = ""
        self._models: list[str] = []
        self._mcp_clients: list[MCPClient] = []
        self._thread: threading.Thread | None = None

        self.actions.level = str(store.settings.get("ai_level", LEVEL_CONFIRM))

        self._statusIn.connect(self._apply_status)
        self._eventIn.connect(self._apply_event)
        self._finishedIn.connect(self._apply_finished)
        self._approvalIn.connect(self._show_approval)
        self._modelsIn.connect(self._apply_models)

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
            model=read_env_value("OPENAI_MODEL", self._settings.get("ai_openai_model", "gpt-4.1-mini")),
            base_url=read_env_value("OPENAI_BASE_URL", self._settings.get("ai_openai_base", "https://api.openai.com/v1")),
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
        return read_env_value("OPENAI_MODEL", self._settings.get("ai_openai_model", "gpt-4.1-mini"))

    @Property(str, notify=settingsChanged)
    def baseUrl(self) -> str:
        return read_env_value("OPENAI_BASE_URL", self._settings.get("ai_openai_base", "https://api.openai.com/v1"))

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

    def _show_approval(self, request_id: str, tool: str, risk: str, summary: str) -> None:
        self._pending = {
            "id": request_id, "tool": tool, "risk": risk, "summary": summary,
        }
        self.approvalChanged.emit()

    @Slot(str, bool)
    def resolveApproval(self, request_id: str, approved: bool) -> None:
        with self._approval_lock:
            entry = self._approvals.get(request_id)
        if entry is None:
            return
        entry["result"] = bool(approved)
        entry["event"].set()
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

        self._thread = threading.Thread(
            target=self._worker, args=(text,), name="pawpet-ai", daemon=True
        )
        self._thread.start()

    def _worker(self, text: str) -> None:
        error = ""
        final = ""
        try:
            client = self._client()
            if not client.configured:
                self._finishedIn.emit("", "没有配置 API Key")
                return

            self.runner = AgentRunner(client, self.context, self.actions, self.callbacks)
            # 保留之前的对话上下文
            self.runner.messages = self._build_history()
            final = self.runner.run(text)
        except AiError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self._finishedIn.emit(final, error)

    def _build_history(self) -> list:
        """把界面上的对话还原成模型消息，让多轮对话有上下文。

        只回放纯文本对话，工具调用的中间过程不回放 —— 那会让历史变得又长又乱。
        """
        from .agent import SYSTEM_PROMPT

        history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
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
    def _apply_status(self, text: str) -> None:
        self._status = text
        self.statusChanged.emit()

    def _apply_event(self, event: StepEvent) -> None:
        if event.kind == "assistant":
            self._push("assistant", event.text)
            return
        if event.kind == "error":
            self._push("error", event.text, tool=event.tool, risk=event.risk,
                       ok=False, detail=event.detail)
            return
        if event.kind == "tool":
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
            )
            self.auditChanged.emit()
            return
        self._push("info", event.text)

    def _apply_finished(self, text: str, error: str) -> None:
        self._running = False
        self._status = "空闲"
        self.runningChanged.emit()
        self.statusChanged.emit()
        if error:
            self._push("error", error)
            self.toastRequested.emit("AI 出错", error[:120])
        elif text:
            self.toastRequested.emit("任务结束", text[:80])

    # ------------------------------------------------------------ 审批桥接
    def request_approval_blocking(self, request: ApprovalRequest) -> bool:
        """在工作线程里被调用，阻塞等待用户点按钮。"""
        request_id = f"a{int(time.time() * 1000)}"
        entry = {"event": threading.Event(), "result": False}
        with self._approval_lock:
            self._approvals[request_id] = entry

        self._approvalIn.emit(request_id, request.tool_name, request.risk, request.summary)

        # 最多等 5 分钟，避免用户走开后线程永久挂着
        if not entry["event"].wait(timeout=300):
            with self._approval_lock:
                self._approvals.pop(request_id, None)
            return False

        with self._approval_lock:
            self._approvals.pop(request_id, None)
        return bool(entry["result"])

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
            ok, message = self._client().test_connection()
            self._finishedIn.emit("", "" if ok else message)
            self._statusIn.emit("空闲")
            self._eventIn.emit(StepEvent(
                kind="info" if ok else "error",
                text=("连接正常：" + message) if ok else ("连接失败：" + message),
            ))

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
    """把 Agent 的回调转成 Qt 信号（自动跨线程排队到主线程）。"""

    def __init__(self, controller: AiController) -> None:
        self._c = controller

    def on_status(self, text: str) -> None:
        self._c._statusIn.emit(text)

    def on_event(self, event: StepEvent) -> None:
        self._c._eventIn.emit(event)

    def on_image(self, png: bytes, note: str) -> None:
        pass    # 图片随 tool 事件一起送出去，这里不需要单独处理

    def on_finished(self, text: str) -> None:
        pass    # 收尾统一由 _worker 的 finally 处理

    def on_error(self, text: str) -> None:
        self._c._eventIn.emit(StepEvent(kind="error", text=text))

    def request_approval(self, request: ApprovalRequest) -> bool:
        return self._c.request_approval_blocking(request)
