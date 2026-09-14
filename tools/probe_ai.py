"""确认 AI 功能需要的依赖是否可用。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    print(f"解释器：{sys.executable}")
    print()
    print("AI / 桌面控制依赖：")
    missing = []
    for name, label in (
        ("mss", "屏幕捕获"),
        ("numpy", "图像数组"),
        ("cv2", "图像编码/缩放"),
        ("pyautogui", "鼠标键盘控制"),
    ):
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "?")
            print(f"  [ok]   {name:12s} {version:10s} {label}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [缺失] {name:12s} {'':10s} {label}  -> {exc}")
            missing.append(name)

    print()
    print("编码格式支持：")
    try:
        import cv2
        import numpy as np

        frame = np.zeros((100, 160, 3), dtype=np.uint8)
        frame[:, :] = (120, 90, 60)
        for ext, label in ((".png", "PNG"), (".jpg", "JPEG")):
            ok, buf = cv2.imencode(ext, frame)
            print(f"  [{'ok' if ok else '失败'}]   {label:5s} {len(buf)} 字节")
    except Exception as exc:  # noqa: BLE001
        print(f"  编码测试失败：{exc}")

    print()
    try:
        import mss

        with mss.mss() as sct:
            monitors = sct.monitors
            print(f"显示器数量（含虚拟全屏）：{len(monitors)}")
            for i, mon in enumerate(monitors):
                tag = "全部屏幕" if i == 0 else f"屏幕 {i}"
                print(f"  [{i}] {tag}: {mon['width']}x{mon['height']} @ ({mon['left']},{mon['top']})")
    except Exception as exc:  # noqa: BLE001
        print(f"屏幕枚举失败：{exc}")

    print()
    print("键盘/鼠标能力：")
    try:
        import pyautogui

        size = pyautogui.size()
        print(f"  [ok]   屏幕尺寸 {size.width}x{size.height}")
        print(f"  [ok]   当前鼠标位置 {pyautogui.position()}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [失败] {exc}")

    print()
    print("API 配置：")
    import os

    sys.path.insert(0, str(ROOT))
    from pawpet.config import load_env

    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    print(f"  OPENAI_API_KEY   = {'已配置（长度 %d）' % len(key) if key else '未配置'}")
    print(f"  OPENAI_MODEL     = {os.environ.get('OPENAI_MODEL', '(默认)')}")
    print(f"  OPENAI_BASE_URL  = {os.environ.get('OPENAI_BASE_URL', '(默认)')}")

    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
