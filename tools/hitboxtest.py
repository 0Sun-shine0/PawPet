r"""真实宠物窗口的透明碰撞箱回归。

用法：
    .venv\Scripts\python.exe tools\hitboxtest.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "hitboxtest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "hitboxtest"
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def alpha_bbox(image) -> tuple[int, int, int, int] | None:
    left = image.width()
    top = image.height()
    right = bottom = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if ((image.pixel(x, y) >> 24) & 0xFF) == 0:
                continue
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if right < left or bottom < top:
        return None
    return left, top, right, bottom


def main() -> int:
    print("小爪透明碰撞箱回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, QPoint, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.hitbox import PetHitbox
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    store.settings["ai_mcp_enabled"] = False
    store.settings["onboarding_done"] = True
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")),
    )
    root = component.create(engine.rootContext())
    if root is None:
        for error in component.errors():
            print("  ", error.toString())
        return 1

    pet = root.findChild(QObject, "petWindow", Qt.FindChildrenRecursively)
    check("拿到宠物窗口", pet is not None)
    if pet is None:
        return 1

    hitbox = PetHitbox(pet, root, interval_ms=80)

    def pump(milliseconds: int) -> None:
        deadline = time.time() + milliseconds / 1000
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.008)

    def verify_shape(label: str) -> None:
        pump(260)
        # **用 refresh_now()**：这里验的是「区域算得对不对」，不是限频。
        # 走限频那条路的话，刚换完风格可能被跳过，断言就会去比一个
        # 滞后好几百毫秒的旧区域（第一版就是这么误报的）。
        hitbox.refresh_now()
        # 已经同步抓取并应用了这一帧；这里不能再等一拍，
        # 否则高 DPI 下宠物的呼吸位移会被放大成几像素的假偏差。
        image = pet.grabWindow()
        mask = pet.mask()
        bbox = alpha_bbox(image)
        full = (0, 0, int(pet.width()), int(pet.height()))
        box = mask.boundingRect().getRect()
        check(f"{label} 有有效 mask", not mask.isEmpty())
        check(f"{label} 不是完整矩形", box != full, f"实际 {box} / 窗口 {full}")
        check(f"{label} 透明角点不在碰撞区", not mask.contains(QPoint(0, 0)))
        expected = None if bbox is None else (
            bbox[0],
            bbox[1],
            bbox[2] - bbox[0] + 1,
            bbox[3] - bbox[1] + 1,
        )
        aligned = expected is not None and all(
            abs(actual - wanted) <= 2
            for actual, wanted in zip(box, expected)
        )
        check(f"{label} mask 与 alpha 范围对齐", aligned,
              f"alpha={bbox} mask={box}")

    backend.petVisible = True
    pump(700)
    check("宠物窗口已显示", pet.isVisible() and pet.isExposed())

    for style in ("mochi", "shiba", "penguin", "fox"):
        backend.pet_style = style
        verify_shape(style)

    backend.pet_scale = 2.4
    verify_shape("2.4 倍缩放")

    backend.petVisible = False
    pump(200)
    check("隐藏后清除旧 mask", pet.mask().isEmpty())

    backend.petVisible = True
    backend.pet_scale = 1.0
    verify_shape("恢复显示")

    # ================================================================ 调用次数
    #
    # 这一段盯的是「**没事不要动窗口区域**」。
    #
    # 原来 `refresh()` 每次（默认 60ms，即每秒 16 次）都无条件调
    # `setMask()`，哪怕宠物一动不动、区域和上次完全一样。在 Windows 上
    # `setMask()` 就是 `SetWindowRgn`：让窗口失效、打断绘制、**重新评估
    # 鼠标捕获**。后果有两类，都是用户直接能感觉到的：
    #
    #   · 一秒 16 次的窗口失效 —— 工作台那边跟着卡顿
    #     （实测事件循环 p95 延迟 16ms → 32ms，翻倍）
    #   · 点击的「按下」和「抬起」之间如果夹了一次 setMask，
    #     那次点击就配不成对 —— 表现是**单击没反应**
    #
    # 所以现在两条规则：区域没变就不设；鼠标按着的时候不设。
    print("\n=== 调用次数（没事不要动窗口区域）===")
    calls: list[object] = []
    original_set = hitbox._set_native_mask

    def counting_set(region) -> None:
        calls.append(region)
        original_set(region)

    hitbox._set_native_mask = counting_set  # type: ignore[method-assign]

    # 1) 频率上限：这是这次修的主要目标。
    #
    #    原来 `refresh()` 每次（默认 60ms，每秒 16 次）都无条件调
    #    `setMask()`。断言用「一段时间的总次数」而不是「某个短窗口内为 0」——
    #    后者太脆：定时器本身的周期和限频周期接近时，总会有一两次撞进来。
    calls.clear()
    started = time.monotonic()
    window_seconds = 2.0
    deadline = started + window_seconds
    while time.monotonic() < deadline:
        hitbox.refresh()
        pump(15)
    elapsed = time.monotonic() - started
    rate = len(calls) / elapsed
    check("设置区域的频率被压到每秒 6 次以内（原来是 16 次）",
          rate <= 6.0, f"{elapsed:.1f}s 里设了 {len(calls)} 次 "
                       f"→ {rate:.1f} 次/秒")
    check("确实还在更新（不是完全不更新了）", len(calls) >= 1,
          "一次都没设的话，点击区域永远停在启动那一帧")

    # 2) 离散变化走 refresh_now，不受限频（尺寸/风格切换后必须立刻生效）
    hitbox.refresh()
    pump(30)
    calls.clear()
    backend.pet_style = "fox" if backend.pet_style != "fox" else "mochi"
    backend.pet_scale = 2.4 if backend.pet_scale < 2.0 else 1.0
    pump(500)
    calls.clear()
    hitbox.refresh_now()
    check("离散变化走 refresh_now 会立刻重设（不受限频）", len(calls) >= 1,
          "尺寸/风格变了还沿用旧区域，点击位置会和画面对不上")

    # 3) 鼠标按着的时候不设（模拟点击/拖动进行中）
    calls.clear()
    hitbox.interaction_in_progress = lambda: True   # type: ignore[method-assign]
    backend.pet_style = "penguin"
    pump(500)
    hitbox.refresh()
    hitbox.refresh_now()      # 连 force 也不能破这条规则
    check("鼠标按着时不改窗口区域（force 也不行）", len(calls) == 0,
          f"实际设了 {len(calls)} 次")

    # 松开之后要恢复更新
    hitbox.interaction_in_progress = lambda: False   # type: ignore[method-assign]
    pump(100)
    calls.clear()
    hitbox.refresh_now()
    check("松开鼠标后恢复更新", len(calls) >= 1,
          "一直不更新的话，拖动之后区域就停在旧形状上了")

    hitbox._set_native_mask = original_set  # type: ignore[method-assign]

    hitbox.stop()
    root.deleteLater()
    app.processEvents()
    backend.shutdown()
    app.quit()

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
