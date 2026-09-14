"""OpenAI Responses API 的轻量 HTTP 客户端；不把密钥写入磁盘。"""
from __future__ import annotations
import json, os, urllib.request, urllib.error

class OpenAIClient:
    def __init__(self, api_key=None, model=None, base_url=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    @property
    def configured(self): return bool(self.api_key)

    def ask(self, text: str, image_data_url: str | None = None, instructions: str | None = None):
        if not self.configured:
            return {"ok": False, "error": "未配置 OPENAI_API_KEY"}
        content = [{"type": "input_text", "text": text}]
        if image_data_url: content.append({"type": "input_image", "image_url": image_data_url})
        payload = {"model": self.model, "input": [{"role": "user", "content": content}]}
        if instructions: payload["instructions"] = instructions
        req = urllib.request.Request(self.base_url + "/responses", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp: data = json.loads(resp.read().decode())
            text_out = data.get("output_text") or self._extract(data)
            return {"ok": True, "text": text_out, "raw": data}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"OpenAI HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _extract(data):
        chunks=[]
        for item in data.get("output", []):
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"): chunks.append(c.get("text", ""))
        return "\n".join(chunks)
