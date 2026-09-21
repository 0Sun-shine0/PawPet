r"""贴边的 QML 时序回归（跑真实宠物窗口）。

**为什么单独有这个套件。** `snaptest.py` 测的是 Python 侧的判定逻辑
（算得对不对），但「什么时候请求吸附」这段时序在 QML 里，而它才是最容易
出错的地方：

  * 拖动走的是原生 `startSystemMove()`，拿不到「松手」事件，只能靠
    「位置不再变化」倒推 —— 这个推断有个 240ms 的窗口
  * 吸附/滑出的动画本身会改位置，于是又会触发一次判断。处理不好就会
    自激：动画 → 判断 → 动画 …
  * 半藏时窗口坐标是负的（贴左边 x=-100），如果拿它去问「要不要吸附」，
    会被判成「离边缘很远」，宠物就自己解开了

这三条看代码看不出来，只能把真的窗口拉起来跑。实测确实抓到过：
滑回动画进行到一半时，位置是动画的中间值，和贴边该在的位置对不上。

用法：
    .venv\Scripts\python.exe tools\snapqmltest.py
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "snapqml"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "snapqmltest"

PASSED = 0
FAILED: list[str] = []
_keep_alive: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("小爪贴边 · QML 时序回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # **必须和正式启动一样先定样式**（见 app.py 的 QQuickStyle.setStyle）。
    # 不设的话走系统原生样式，我们那些自定义了 contentItem / indicator 的
    # 控件不被支持，每个都往 stderr 吐一条警告 —— 几百行，真正有用的
    # 输出全被埋掉，排查时很费劲。
    from PySide6.QtQuickControls2 import QQuickStyle

    QQuickStyle.setStyle("Basic")

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    _keep_alive.append(app)

    from pawpet.app import PawPetApp
    from pawpet.store import Store

    store = Store(SCRATCH / "p.json", SCRATCH / "b.json")
    store.load()

    pawapp = PawPetApp(app, store)
    _keep_alive.extend([store, pawapp])

    # **必须用 PawPetApp 自己那个 Backend，不能另建一个。**
    #
    # PawPetApp.__init__ 里会 `self._backend = Backend(store, self)`，
    # 而 QML 绑的是**那一个**。如果测试自己 `Backend(store)`，拿到的就是
    # 另一个对象：
    #   · pet_edge 存在共享的 store.state 里 —— 看起来是同步的，骗过眼睛
    #   · peek / peek_until 是实例字段 —— 两个对象各有一份，完全不同步
    # 结果就是测试读到的 peek_until 永远是初始值 0，于是「滑回时机不对」
    # 这个假象查了很久。测试里的这个坑和产品无关，但很值得记一笔。
    backend = pawapp._backend
    _keep_alive.append(backend)

    def pump(ms: int) -> None:
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.008)

    pump(1200)      # 等窗口建好、位置恢复

    win = None
    for candidate in app.topLevelWindows():
        if candidate.title() == backend.appName and candidate.isVisible():
            win = candidate
            break
    check("找到宠物窗口", win is not None)
    if win is None:
        backend.shutdown()
        return 1

    area = backend.screenAt(0, 0)
    width, height = backend._pet_size()
    print(f"  窗口 {win.width()}x{win.height()}   "
          f"屏幕可用 {area['width']}x{area['height']}\n")

    # ================================================================ 一
    print("=== 一、移到边缘附近会自己吸附 ===")
    backend.petDetach()
    pump(400)
    win.setX(area["x"] + 6)
    win.setY(area["y"] + 360)
    pump(900)       # settleTimer(240) + 吸附动画(240) 之后再宽裕一点

    check("吸附到左边", backend.petEdge == "left", repr(backend.petEdge))
    check("窗口移到贴边位置（左沿对齐）", win.x() == area["x"],
          f"x={win.x()}")
    check("垂直于边的位置保持住了", abs(win.y() - (area["y"] + 360)) <= 2,
          f"y={win.y()}")

    # ================================================================ 二
    print("\n=== 二、静置之后不会自己解开（关键）===")
    # 鼠标不在附近时，吸附 2.5 秒后会滑回半藏。这一步最容易出问题：
    # 动画把窗口从 0 移到 -100，中间那一串位置都会被当成「位置变了」，
    # 处理不当就会触发一次吸附判断 —— 而 -100 离左边缘 100px，
    # 远超吸附距离，于是贴边被解除，宠物自己弹回屏幕里。
    #
    # 记录一下 detach / snap 的调用序列：贴边「自己解开」的根因就在
    # 这两个方法的调用顺序里，光看最终状态看不出来。
    from PySide6.QtGui import QCursor

    calls: list[tuple[float, str, object]] = []
    started = time.time()
    real_detach = backend.petDetach
    real_snap = backend.petSnap

    def traced_detach():
        calls.append((time.time() - started, "detach", None))
        return real_detach()

    def traced_snap(sx, sy):
        result = real_snap(sx, sy)
        calls.append((time.time() - started, "snap",
                      (result.get("edge"), sx, sy)))
        return result

    backend.petDetach = traced_detach
    backend.petSnap = traced_snap

    # 采 8 个点（每 400ms 一个），够看出「滑出 → 滑回」的转折，
    # 又不至于把输出刷满
    trace = []
    for step in range(8):
        pump(400)
        geo = backend.petEdgeGeometry()
        trace.append((step * 0.4 + 0.4, backend.petEdge, win.x(),
                      backend._pet_peek, geo.get("x")))
    backend.petDetach = real_detach
    backend.petSnap = real_snap

    print("  跟踪（edge / x / peek / geo.x）：")
    for t, edge, x, peek, gx in trace:
        print(f"    t={t:4.1f}s  edge={edge!r:8s} x={x:5d}  "
              f"peek={str(peek):5s} geo.x={gx:5d}")

    # 滑回的过程中窗口会经过一串中间位置。那些位置离边缘很远，
    # 要是有谁拿它们去问「要不要吸附」，贴边就会被解开 ——
    # 这是这一段最需要盯住的事，所以把 detach 的调用次数也断言上。
    detach_count = sum(1 for _w, kind, _e in calls if kind == "detach")
    check("静置期间没有触发过解除贴边", detach_count == 0,
          f"detach 被调了 {detach_count} 次：{calls}")
    print(f"  鼠标现在在 ({QCursor.pos().x()}, {QCursor.pos().y()})")

    check("滑回之后仍然贴着", backend.petEdge == "left",
          repr(backend.petEdge))
    check("位置确实是半藏（比屏幕左沿更靠外）", win.x() < area["x"],
          f"x={win.x()}，应小于 {area['x']}")
    # 藏多少是**按边**定的（脸在画布中心，旋转不会把它挪到边上，
    # 所以每条边能藏多少不同）。这里不该写死「藏一半」。
    check("半藏的偏移量等于该边的藏匿比例",
          win.x() == area["x"] - int(width * backend._pet_hide_ratio),
          f"x={win.x()}，比例 {backend._pet_hide_ratio:.0%}"
          f"（应为 {area['x'] - int(width * backend._pet_hide_ratio)}）")

    # ================================================================ 三
    print("\n=== 三、从边上拖走会解除贴边 ===")
    target = area["x"] + 700
    win.setX(target)
    win.setY(area["y"] + 300)
    pump(900)
    check("离开边缘后解除贴边", backend.petEdge == "",
          repr(backend.petEdge))
    check("窗口停在拖到的位置，没被吸回去", abs(win.x() - target) <= 2,
          f"x={win.x()}，应约 {target}")

    # ================================================================ 四
    print("\n=== 四、四条边都能贴 ===")
    cases = [
        ("left", area["x"] + 6, area["y"] + 300, "左沿", area["x"]),
        ("right", area["x"] + area["width"] - width - 6, area["y"] + 300,
         "右沿", area["x"] + area["width"]),
        ("top", area["x"] + 500, area["y"] + 6, "上沿", area["y"]),
        ("bottom", area["x"] + 500,
         area["y"] + area["height"] - height - 6, "下沿",
         area["y"] + area["height"]),
    ]
    for edge, tx, ty, axis, expect in cases:
        backend.petDetach()
        pump(300)
        win.setX(tx)
        win.setY(ty)
        pump(900)
        if axis == "左沿":
            got = win.x()
        elif axis == "右沿":
            got = win.x() + win.width()
        elif axis == "上沿":
            got = win.y()
        else:
            got = win.y() + win.height()
        check(f"{edge} 边：吸附到 {axis}", backend.petEdge == edge,
              repr(backend.petEdge))
        check(f"{edge} 边：{axis} 对齐", abs(got - expect) <= 2,
              f"实测 {got}，应为 {expect}")

    # ================================================================ 五
    print("\n=== 五、重启后能按边恢复 ===")
    backend.petSnap(area["x"] + 6, area["y"] + 300)
    pump(500)
    check("存下来的是「哪条边」", store.state.get("pet_edge") == "left",
          repr(store.state.get("pet_edge")))
    saved = backend.petPosition()
    check("坐标存的是完全显示的位置，不是负数", saved[0] >= area["x"],
          str(saved))
    check("petEdgeGeometry 能报出可恢复的位置",
          backend.petEdgeGeometry().get("active") is True,
          str(backend.petEdgeGeometry()))

    # ================================================================ 六
    print("\n=== 六、姿态真的转过去了（跑真窗口才看得出来）===")
    #
    # 角度是渲染核对过的：左侧必须**反向**转，正转会把眼睛转到被藏起来
    # 的那半，露出来的是后脑勺。这里在真窗口上确认 stage 真的转了。
    stage = None
    for child in win.contentItem().childItems():
        try:
            rot = child.property("rotation")
            height_px = child.property("height")
        except Exception:  # noqa: BLE001
            continue
        if rot is not None and height_px and height_px > 100:
            stage = child
            break
    check("找到旋转容器（stage）", stage is not None,
          "找不到就没法验证旋转，检查 QML 里的结构")

    # 角度值：**左侧顺时针、右侧逆时针**，这样头顶才朝屏幕里。
    # 搞反了虽然也能看到脸，但看着像倒栽葱（用户报过这个）。
    for edge, expect in (("left", 90), ("right", -90),
                         ("top", 180), ("bottom", 0)):
        backend.petDetach()
        pump(300)
        if edge == "left":
            win.setX(area["x"] + 5)
            win.setY(area["y"] + 300)
        elif edge == "right":
            win.setX(area["x"] + area["width"] - width - 5)
            win.setY(area["y"] + 300)
        elif edge == "top":
            win.setX(area["x"] + 500)
            win.setY(area["y"] + 5)
        else:
            win.setX(area["x"] + 500)
            win.setY(area["y"] + area["height"] - height - 5)
        pump(1500)      # 吸附 + 翻身动画(420ms) + 余量

        check(f"{edge} 贴边后姿势角度是 {expect}°",
              backend.petPoseAngle == expect, str(backend.petPoseAngle))
        if stage is not None:
            got = float(stage.property("rotation"))
            # 倒挂时额外挂了晃动，所以允许几度误差
            check(f"{edge} 窗口里的 stage 真的转到了 {expect}° 附近",
                  abs(got - expect) <= 8, f"实测 {got}")

    # ================================================================ 七
    print("\n=== 七、拖走之后姿势要转回来（用户报的 bug）===")
    #
    # 用户报「有贴边没有回正」：宠物转过去了，但拖走之后不转回来，
    # 一直歪着待在屏幕中间。
    #
    # 根因是 backend 解除贴边时**一次信号都不发**，QML 收不到任何动静。
    # 这个套件第一版没覆盖到 —— 因为前面几节都是「先 detach 再重新贴」，
    # 每次都走到了吸附那条会发信号的路径。必须专门测「拖走之后停在那」。
    backend.petDetach()
    pump(300)
    win.setX(area["x"] + 5)
    win.setY(area["y"] + 300)
    pump(1500)
    check("先贴在左边、姿势转过去了", backend.petPoseAngle == 90,
          str(backend.petPoseAngle))
    if stage is not None:
        check("窗口里的 stage 也转过去了",
              abs(float(stage.property("rotation")) - 90) <= 8,
              str(stage.property("rotation")))

    target = area["x"] + 760
    win.setX(target)
    win.setY(area["y"] + 300)
    pump(1500)
    check("拖走之后不再贴边", backend.petEdge == "", repr(backend.petEdge))
    check("拖走之后角度回到 0", backend.petPoseAngle == 0,
          str(backend.petPoseAngle))
    if stage is not None:
        check("窗口里的 stage 真的转回来了（不是一直歪着）",
              abs(float(stage.property("rotation"))) <= 4,
              f"实测 {stage.property('rotation')}°")
    check("窗口停在拖到的位置，没被拽回贴边处",
          abs(win.x() - target) <= 3, f"x={win.x()}，应约 {target}")

    # ================================================================ 八
    print("\n=== 八、关掉贴边开关时宠物要回到屏幕内 ===")
    #
    # 关开关那一刻宠物正半藏在屏幕外，不特殊处理的话它会卡在半藏状态，
    # 用户以为宠物不见了。
    backend.petDetach()
    pump(300)
    win.setX(area["x"] + 5)
    win.setY(area["y"] + 300)
    pump(3200)      # 等滑回半藏
    check("已经半藏在屏幕外", win.x() < area["x"], f"x={win.x()}")

    store.settings["pet_snap_enabled"] = False
    backend._on_setting_changed("pet_snap_enabled")
    pump(1400)
    check("关掉开关后解除贴边", backend.petEdge == "", repr(backend.petEdge))
    check("关掉开关后角度归零", backend.petPoseAngle == 0,
          str(backend.petPoseAngle))
    check("关掉开关后宠物回到屏幕内（不再半藏）",
          win.x() >= area["x"]
          and win.x() + win.width() <= area["x"] + area["width"],
          f"x={win.x()}，屏幕 {area['x']}~{area['x'] + area['width']}")
    store.settings["pet_snap_enabled"] = True

    backend.shutdown()

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("  - " + item)
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
