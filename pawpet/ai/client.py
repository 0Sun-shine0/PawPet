"""OpenAI 兼容的对话客户端（带函数调用）。

只依赖标准库。刻意不用 /responses 而是用 /chat/completions：
后者是事实标准，DeepSeek、Moonshot、通义、Ollama、OneAPI 之类全都兼容，
用户换服务商只要改 base_url 和 model。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
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
    _RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
    _MAX_RETRIES = 2

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "",
                 timeout: int = 90) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "gpt-4.1-mini").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
        try:
            self.timeout = max(1.0, float(timeout))
        except (TypeError, ValueError):
            self.timeout = 90.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        if not self.configured:
            return f"未配置 API Key（将使用 {self.model}）"
        warning = "（警告：当前地址不是 HTTPS，API Key 可能被窃听）" \
            if self._uses_insecure_http() else ""
        return f"{self.model} @ {self.base_url}{warning}"

    def _uses_insecure_http(self) -> bool:
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "http":
            return False
        return (parsed.hostname or "").lower() not in {
            "localhost", "127.0.0.1", "::1",
        }

    def _validate_base_url(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AiError("模型地址必须是完整的 http(s) URL")
        if self._uses_insecure_http() and os.environ.get(
                "PAWPET_ALLOW_INSECURE_HTTP") != "1":
            raise AiError(
                "为了保护 API Key，非本机地址必须使用 HTTPS。"
                "如确认局域网服务可信，可设置 PAWPET_ALLOW_INSECURE_HTTP=1。"
            )

    def _safe_error(self, text: str) -> str:
        if self.api_key:
            return text.replace(self.api_key, "***")
        return text

    @staticmethod
    def _retryable_network_error(exc: BaseException) -> bool:
        if isinstance(exc, urllib.error.URLError):
            return True
        return isinstance(exc, (
            socket.timeout,
            TimeoutError,
            ConnectionResetError,
            ConnectionAbortedError,
            ConnectionRefusedError,
        ))

    @classmethod
    def _wait_before_retry(cls, attempt: int) -> None:
        # 0.5s、1s，最多只允许内部调用方要求的两次重试。
        time.sleep(min(0.5 * (2 ** attempt), 2.0))

    @staticmethod
    def _timeout_text(timeout: float) -> str:
        return f"{timeout:g}"

    def _request_json(self, request: urllib.request.Request, *,
                      timeout: float, retries: int = 0) -> dict:
        """执行一次 JSON 请求；重试只由显式调用方开启。"""
        timeout = max(1.0, float(timeout))
        retries = max(0, min(int(retries), self._MAX_RETRIES))
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:600]
                if exc.code in self._RETRYABLE_HTTP and attempt < retries:
                    self._wait_before_retry(attempt)
                    continue
                hint = ""
                if exc.code == 401:
                    hint = "（API Key 无效或已过期）"
                elif exc.code == 404:
                    hint = "（地址或模型名不对，检查 base_url 是否以 /v1 结尾）"
                elif exc.code == 429:
                    hint = "（触发限流或余额不足）"
                elif exc.code >= 500:
                    hint = "（服务端错误，稍后重试）"
                raise AiError(self._safe_error(
                    f"接口返回 HTTP {exc.code}{hint}：{detail}")) from exc
            except urllib.error.URLError as exc:
                if self._retryable_network_error(exc) and attempt < retries:
                    self._wait_before_retry(attempt)
                    continue
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, socket.timeout):
                    label = self._timeout_text(timeout)
                    raise AiError(
                        f"请求超时（{label} 秒），可能是网络不通或模型响应太慢"
                    ) from exc
                raise AiError(self._safe_error(
                    f"连接不上 {self.base_url}：{reason}")) from exc
            except (socket.timeout, TimeoutError) as exc:
                if attempt < retries:
                    self._wait_before_retry(attempt)
                    continue
                label = self._timeout_text(timeout)
                raise AiError(f"请求超时（{label} 秒）") from exc
            except json.JSONDecodeError as exc:
                raise AiError(f"接口返回的不是合法 JSON：{exc}") from exc
            except OSError as exc:
                if self._retryable_network_error(exc) and attempt < retries:
                    self._wait_before_retry(attempt)
                    continue
                # 连接被重置、DNS 失败等等。urllib 不保证都包成 URLError，
                # 这里兜住，否则后台线程会带着未捕获异常直接死掉。
                raise AiError(self._safe_error(f"网络错误：{exc}")) from exc
        raise AiError("请求失败")  # 仅为静态分析兜底，不应到达

    # ------------------------------------------------------------------ 请求
    def _post(self, path: str, payload: dict, *, timeout: float | None = None,
              retries: int = 0) -> dict:
        if not self.configured:
            raise AiError("没有配置 API Key。请在「AI → 模型设置」里填写，或写入 .env 的 OPENAI_API_KEY。")

        self._validate_base_url()
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
        return self._request_json(
            request,
            timeout=self.timeout if timeout is None else timeout,
            retries=retries,
        )

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
            }, timeout=min(self.timeout, 15.0), retries=1)
            model_used = data.get("model") or self.model
            return True, f"连接成功，模型 {model_used} 可正常调用"
        except AiError as exc:
            return False, str(exc)

    def list_models(self) -> tuple[bool, list[str] | str]:
        """拉取可用模型列表，方便用户在界面上下拉选择。"""
        if not self.configured:
            return False, "没有配置 API Key"
        try:
            self._validate_base_url()
        except AiError as exc:
            return False, str(exc)
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        try:
            data = self._request_json(
                request,
                timeout=min(self.timeout, 30.0),
                retries=1,
            )
            items = data.get("data") or []
            names = sorted({str(item.get("id")) for item in items if item.get("id")})
            return True, names
        except AiError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 - provider 返回结构可能不标准
            return False, self._safe_error(str(exc))
