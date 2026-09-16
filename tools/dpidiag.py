"""验证高 DPI 缩放的修法。

用户的屏幕是 1920x1080 但物理 157 DPI（约 1.64 倍屏），
而程序按 100% 渲染 —— 所以界面物理尺寸只有应有的六成。

这个脚本对比几种设置下 Qt 认为的缩放比：
  1. 什么都不做（当前行为）
  2. 只设 rounding policy
  3. roundness + 显式 QT_SCALE_FACTOR
  4. 只设 QT_SCALE_FACTOR

每种都要在**独立进程**里跑 —— Qt 的 DPI 设置在 QApplication 之后改不了。

用法：
    .venv\\Scripts\\python.exe tools\\dpidiag.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = [
    ("1. 当前行为（什么都不设）", {}),
    ("2. RoundPreferFloor", {
        "PAWPET_TEST_ROUNDING": "floor",
    }),
    ("3. RoundPreferFloor + 显式 1.5", {
        "PAWPET_TEST_ROUNDING": "floor",
        "QT_SCALE_FACTOR": "1.5",
    }),
    ("4. 只设 QT_SCALE_FACTOR=1.5", {
        "QT_SCALE_FACTOR": "1.5",
    }),
    ("5. RoundPreferFloor + 显式 1.25", {
        "PAWPET_TEST_ROUNDING": "floor",
        "QT_SCALE_FACTOR": "1.25",
    }),
]

CHILD = r'''
import os, sys
sys.path.insert(0, r"{root}")
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

policy = os.environ.get("PAWPET_TEST_ROUNDING")
if policy == "floor":
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.RoundPreferFloor)

app = QApplication(sys.argv[:1])
screen = app.primaryScreen()
print("devicePixelRatio=", screen.devicePixelRatio())
print("logicalDpi=", screen.logicalDotsPerInch())
geo = screen.geometry()
avail = screen.availableGeometry()
print("geometry=", geo.width(), "x", geo.height())
print("available=", avail.width(), "x", avail.height())
print("qt_scale_factor=", os.environ.get("QT_SCALE_FACTOR", "(未设)"))
# Qt 认为的「逻辑像素」尺寸 —— 界面就是按这个排版的
print("logical_size=", int(avail.width() / screen.devicePixelRatio()),
      "x", int(avail.height() / screen.devicePixelRatio()))
'''


def main() -> int:
    exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    print("高 DPI 缩放对照实验")
    print("（屏幕物理 157 DPI / 逻辑 96 DPI，所以「应有」缩放约 1.64）\n")

    for label, env_extra in CASES:
        env = dict(os.environ)
        env.pop("QT_SCALE_FACTOR", None)
        env.pop("PAWPET_TEST_ROUNDING", None)
        env.update({k: v for k, v in env_extra.items()})
        env["PYTHONIOENCODING"] = "utf-8"
        print(f"--- {label} ---")
        try:
            result = subprocess.run(
                [exe, "-c", CHILD.format(root=str(ROOT))],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=60,
                cwd=str(ROOT),
            )
            for line in (result.stdout or "").strip().splitlines():
                print("   ", line)
            if result.returncode != 0:
                tail = (result.stderr or "").strip().splitlines()
                print("    [失败]", tail[-1] if tail else "")
        except subprocess.TimeoutExpired:
            print("    超时")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
