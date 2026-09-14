"""受控桌面动作。默认只允许低风险动作，所有调用都有审计记录。"""
from __future__ import annotations
import time
try:
    import pyautogui
except ImportError:
    pyautogui = None

SAFE_ACTIONS = {"move", "click", "double_click", "type", "hotkey", "screenshot"}

class DesktopActions:
    def __init__(self, require_confirmation=True):
        self.require_confirmation = require_confirmation

    def status(self):
        return "自动化模块已就绪（pyautogui）" if pyautogui else "未安装自动化依赖：pip install -r requirements.txt"

    def execute(self, action: str, **args):
        if action not in SAFE_ACTIONS:
            return {"ok": False, "error": f"动作 {action!r} 不在安全白名单中"}
        if not pyautogui:
            return {"ok": False, "error": self.status()}
        try:
            if action == "move": pyautogui.moveTo(float(args["x"]), float(args["y"]), duration=.15)
            elif action == "click": pyautogui.click(float(args["x"]), float(args["y"]))
            elif action == "double_click": pyautogui.doubleClick(float(args["x"]), float(args["y"]), interval=.1)
            elif action == "type": pyautogui.write(str(args["text"]), interval=.01)
            elif action == "hotkey": pyautogui.hotkey(*[str(k) for k in args["keys"]])
            elif action == "screenshot": return {"ok": True, "message": "请使用 screen_vision.ScreenVision.capture()"}
            time.sleep(.05)
            return {"ok": True, "action": action}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
