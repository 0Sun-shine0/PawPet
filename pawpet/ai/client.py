"""OpenAI 兼容的对话客户端（带函数调用）。

只依赖标准库。刻意不用 /responses 而是用 /chat/completions：
后者是事实标准，DeepSeek、Moonshot、通义、Ollama、OneAPI 之类全都兼容，
用户换服务商只要改 base_url 和 model。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class AiError(Exception):
    """带用户可读中文说明的异常。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class ChatReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class AIClient:
    def __init__(self, api_key: str = "", model: str = "", base_url: str = "",
                 timeout: int = 90) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "gpt-4.1-mini").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        if not self.configured:
            return f"未配置 API Key（将使用 {self.model}）"
        return f"{self.model} @ {self.base_url}"

    # ------------------------------------------------------------------ 请求
    def _post(self, path: str, payload: dict) -> dict:
        if not self.configured:
            raise AiError("没有配置 API Key。请在「AI → 模型设置」里填写，或写入 .env 的 OPENAI_API_KEY。")

        url = f"{self.base_url}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            hint = ""
            if exc.code == 401:
                hint = "（API Key 无效或已过期）"
            elif exc.code == 404:
                hint = "（地址或模型名不对，检查 base_url 是否以 /v1 结尾）"
            elif exc.code == 429:
                hint = "（触发限流或余额不足）"
            elif exc.code >= 500:
                hint = "（服务端错误，稍后重试）"
            raise AiError(f"接口返回 HTTP {exc.code}{hint}：{detail}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise AiError(f"请求超时（{self.timeout} 秒），可能是网络不通或模型响应太慢") from exc
            raise AiError(f"连接不上 {self.base_url}：{reason}") from exc
        except socket.timeout as exc:
            raise AiError(f"请求超时（{self.timeout} 秒）") from exc
        except json.JSONDecodeError as exc:
            raise AiError(f"接口返回的不是合法 JSON：{exc}") from exc
        except OSError as exc:
            # 连接被重置、DNS 失败等等。urllib 不保证都包成 URLError，
            # 这里兜住，否则后台线程会带着未捕获异常直接死掉，
            # 界面上就会一直停在「正在测试连接…」。
            raise AiError(f"网络错误：{exc}") from exc

    # -------------------------------------------------------------- 对话接口
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.2, max_tokens: int = 1600) -> ChatReply:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._post("/chat/completions", payload)
        choices = data.get("choices") or []
        if not choices:
            error = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
            raise AiError(f"接口没有返回任何结果{('：' + error) if error else ''}")

        message = choices[0].get("message") or {}
        reply = ChatReply(
            text=(message.get("content") or "").strip(),
            finish_reason=choices[0].get("finish_reason") or "",
            usage=data.get("usage") or {},
            raw=data,
        )

        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            reply.tool_calls.append(ToolCall(
                id=str(item.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
                raw_arguments=raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False),
            ))
        return reply

    # ---------------------------------------------------------------- 连通性
    def test_connection(self) -> tuple[bool, str]:
        """用最小代价验证 key / 地址 / 模型名是否都对。"""
        if not self.configured:
            return False, "没有配置 API Key"
        try:
            data = self._post("/chat/completions", {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            })
            model_used = data.get("model") or self.model
            return True, f"连接成功，模型 {model_used} 可正常调用"
        except AiError as exc:
            return False, str(exc)

    def list_models(self) -> tuple[bool, list[str] | str]:
        """拉取可用模型列表，方便用户在界面上下拉选择。"""
        if not self.configured:
            return False, "没有配置 API Key"
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            items = data.get("data") or []
            names = sorted({str(item.get("id")) for item in items if item.get("id")})
            return True, names
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}：{exc.read().decode('utf-8', 'replace')[:200]}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
