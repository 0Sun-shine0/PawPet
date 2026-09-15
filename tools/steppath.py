r"""复现「调到 100 但只跑 20 步」：走和双击 .cmd 完全一样的路径。

不猜，直接把真实的 AiController + 真实 QML 下拉框 + 假模型服务器接起来，
看用户那条路上到底哪一环把 100 变成了 20。

用法：
    .venv\Scripts\python.exe tools\steppath.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "steppath"
os.environ["PAWPET_HOME"] = str(SCRATCH)


class CountingModel:
    """永远回工具调用的假模型，用来数到底跑了几步。"""

    def __init__(self) -> None:
        self.requests = 0
        self.lock = threading.Lock()

    def reply(self, body: dict) -> dict:
        with self.lock:
            self.requests += 1
            index = self.requests
        return {
            "id": "x", "object": "chat.completion", "model": "fake",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": f"c{index}", "type": "function",
                        "function": {"name": "ui_windows", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def serve(model: CountingModel) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"data": [{"id": "fake"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {}
            payload = json.dumps(model.reply(body), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> int:
    print("复现：双击 .cmd 启动时，步数设置到底有没有生效\n")

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, QUrl, Qt
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.ai import controller as controller_module
    from pawpet.ai.controller import AiController
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()

    # ---------- 第一步：模拟用户「已经调到 100」（就是你现在的 json 状态）
    store.settings["ai_max_steps"] = 100
    store.save()
    print(f"1. 数据文件里 ai_max_steps = {store.settings['ai_max_steps']}")

    client = AiController(store)
    print(f"2. 控制器读出来 maxSteps  = {client.maxSteps}")

    # ---------- 第二步：加载真实 QML，看下拉框显示什么、选中哪一项
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", client)
    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(QML_DIR / "PawPet" / "page" / "AiPage.qml")))
    page = component.create(engine.rootContext())
    if page is None:
        print("   QML 创建失败：")
        for error in component.errors():
            print("     ", error.toString())
        return 1
    page.setParent(engine)

    box = page.findChild(QObject, "stepBox", Qt.FindChildrenRecursively)
    if box is None:
        print("   找不到 stepBox —— QML 里没有这个下拉框！")
        print("   => 说明你跑的是**没有这个功能的旧代码**")
        return 1

    index = box.property("currentIndex")
    options = client.stepOptions
    print(f"3. 下拉框 currentIndex   = {index}"
          f" -> 显示「{box.property('displayText')}」")
    print(f"   可选列表 = {[o['label'] for o in options]}")
    if 0 <= index < len(options):
        print(f"   这一项的真实值 = {options[index]['value']}")
    else:
        print("   currentIndex 越界！")

    # ---------- 第三步：走真实 send() 路径，数实际跑了几步
    model = CountingModel()
    server, url = serve(model)
    original = controller_module.AIClient

    def fake_client(*_args, **_kwargs):
        return original(api_key="sk-fake", model="fake",
                        base_url=url, timeout=20)

    controller_module.AIClient = fake_client
    try:
        client.actions.level = "full"
        client.send("一直查窗口列表别停")

        deadline = time.time() + 90
        while client.running and time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.02)
    finally:
        controller_module.AIClient = original
        server.shutdown()

    print(f"4. 实际发出 {model.requests} 次模型请求")
    print()
    if model.requests >= 50:
        print(f"结论：设置生效了（跑了 {model.requests} 步，远超 20）")
        verdict = 0
    elif model.requests <= 25:
        print(f"结论：**设置没生效**，只跑了 {model.requests} 步 ≈ 写死的 20")
        print("      说明这条路上的某一段还在用旧逻辑")
        verdict = 1
    else:
        print(f"结论：跑了 {model.requests} 步，介于两者之间，需要细看")
        verdict = 1

    client.shutdown()
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
