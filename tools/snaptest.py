r"""贴边功能回归。

覆盖四件事：
  一、吸附判定 —— 四条边都吸得对，够不着的不吸，屏幕中间不吸
  二、位置计算 —— 半藏在屏幕外、滑出时完全显示，垂直于边的坐标不被改
  三、滑出判定 —— 鼠标移到宠物所在的那一段才滑出，只是路过边缘不滑出
  四、数据一致性 —— 跨屏/换分辨率/关开关之后不会把宠物弄丢

用法：
    .venv\Scripts\python.exe tools\snaptest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "snaptest"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "snaptest"

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
    print("小爪贴边功能回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _keep_alive.append(app)

    from pawpet.backend import Backend
    from pawpet.store import Store

    store = Store(SCRATCH / "p.json", SCRATCH / "b.json")
    store.load()
    backend = Backend(store)
    _keep_alive.append(backend)

    area = backend.screenAt(0, 0)
    width, height = backend._pet_size()
    print(f"屏幕可用区域 {area['x']},{area['y']} {area['width']}x{area['height']}")
    print(f"宠物尺寸 {width}x{height}\n")

    # ================================================================ 一
    print("=== 一、四条边都吸得住 ===")
    edges = [
        ("left", area["x"] + 8, area["y"] + 400),
        ("right", area["x"] + area["width"] - width - 12, area["y"] + 400),
        ("top", area["x"] + 600, area["y"] + 6),
        ("bottom", area["x"] + 600,
         area["y"] + area["height"] - height - 9),
    ]
    got: dict[str, tuple[int, int]] = {}
    for expect, x, y in edges:
        result = backend.petSnap(x, y)
        got[expect] = (result["x"], result["y"])
        check(f"拖到{expect}边附近会吸附", result["edge"] == expect,
              f"实际 {result['edge']!r}")

    print("\n  吸附后的位置（peek=True，应该是完全显示、紧贴边）：")
    check("贴左边：左沿对齐屏幕左沿",
          got["left"][0] == area["x"], str(got["left"]))
    check("贴右边：右沿对齐屏幕右沿",
          got["right"][0] + width == area["x"] + area["width"],
          str(got["right"]))
    check("贴上边：上沿对齐屏幕上沿",
          got["top"][1] == area["y"], str(got["top"]))
    check("贴下边：下沿对齐屏幕下沿（任务栏之上）",
          got["bottom"][1] + height == area["y"] + area["height"],
          str(got["bottom"]))

    # ================================================================ 二
    print("\n=== 二、够不着的不吸、屏幕中间不吸 ===")
    far = area["x"] + max(60, backend._pet_snap_distance + 20)
    result = backend.petSnap(far, area["y"] + 400)
    check("拖到离边缘够远的地方不吸", result["edge"] == "", str(result))

    result = backend.petSnap(area["x"] + area["width"] // 2 - width // 2,
                             area["y"] + area["height"] // 2 - height // 2)
    check("拖到屏幕正中间不吸", result["edge"] == "", str(result))
    check("不吸的时候坐标原样返回",
          result["x"] == area["x"] + area["width"] // 2 - width // 2,
          str(result))

    # 吸附距离设置项真的起作用
    store.settings["pet_snap_distance"] = 120
    near120 = area["x"] + 100
    result = backend.petSnap(near120, area["y"] + 400)
    check("把吸附距离调到 120 之后，100px 也能吸上",
          result["edge"] == "left", str(result))
    store.settings["pet_snap_distance"] = 40
    result = backend.petSnap(near120, area["y"] + 400)
    check("调回 40 之后，100px 就不吸了", result["edge"] == "", str(result))

    # 关掉开关
    backend.petSnap(area["x"] + 5, area["y"] + 400)
    store.settings["pet_snap_enabled"] = False
    result = backend.petSnap(area["x"] + 5, area["y"] + 400)
    check("关掉贴边开关后不再吸附", result["edge"] == "", str(result))
    check("关掉开关会解除已有的贴边", backend.petEdge == "",
          repr(backend.petEdge))
    store.settings["pet_snap_enabled"] = True

    # ---- 拖过头（窗口有一部分到了屏幕外）----
    #
    # **这是用户报「贴边感应不灵敏」的根因。** 往边上拖的时候必然会
    # 拖过头（窗口滑出屏幕才感觉「到头了」），而距离原来用 abs() 算 ——
    # 超出 60px 就被算成「离边缘 60px」，超出 120px 就算 120px，
    # 于是越往边上拖越不吸附。
    print("\n  拖过头也应该吸上：")
    for overshoot in (5, 30, 60, 120, 300):
        backend.petDetach()
        result = backend.petSnap(area["x"] - overshoot, area["y"] + 400)
        check(f"往左拖过头 {overshoot}px 仍吸附",
              result["edge"] == "left", f"edge={result['edge']!r}")

    for edge, ox, oy in (
        ("right", area["x"] + area["width"] - width + 80, area["y"] + 300),
        ("top", area["x"] + 600, area["y"] - 80),
        ("bottom", area["x"] + 600,
         area["y"] + area["height"] - height + 80),
    ):
        backend.petDetach()
        result = backend.petSnap(ox, oy)
        check(f"往{edge}边拖过头仍吸附", result["edge"] == edge,
              f"edge={result['edge']!r}")

    # 完全拖到屏幕外（比如分辨率变化留下的坐标）也要能收回来
    backend.petDetach()
    result = backend.petSnap(-2000, area["y"] + 300)
    check("窗口完全在屏幕外时也能吸回最近的那条边",
          result["edge"] != "", str(result))

    # ================================================================ 三
    print("\n=== 三、半藏与滑出的位置 ===")
    backend.petSnap(area["x"] + 5, area["y"] + 400)
    check("贴在左边", backend.petEdge == "left", repr(backend.petEdge))

    geo_peek = backend._pet_geometry_for("left", 0, 400, peek=True)
    geo_hide = backend._pet_geometry_for("left", 0, 400, peek=False)
    check("滑出时完全在屏幕内", geo_peek[0] == area["x"], str(geo_peek))
    check("半藏时有一半以上在屏幕外",
          geo_hide[0] < area["x"] - width * 0.3, str(geo_hide))
    check("半藏的偏移量等于该边的藏匿比例",
          geo_hide[0] == area["x"] - int(width * backend._pet_hide_ratio),
          f"{geo_hide[0]}，比例 {backend._pet_hide_ratio:.0%}")
    check("半藏比滑出更靠外（贴左边是更小的 x）",
          geo_hide[0] < geo_peek[0], f"{geo_hide[0]} vs {geo_peek[0]}")

    # 垂直于边的坐标不该被改（只夹在屏幕内）
    check("贴左边时纵向位置保持用户拖到的值",
          geo_peek[1] == 400, str(geo_peek))
    out = backend._pet_geometry_for("left", 0, area["y"] + area["height"] + 500,
                                    peek=True)
    check("纵向超出屏幕时会被夹回来",
          out[1] <= area["y"] + area["height"] - height, str(out))

    for edge in ("right", "top", "bottom"):
        peek = backend._pet_geometry_for(edge, 900, 500, peek=True)
        hide = backend._pet_geometry_for(edge, 900, 500, peek=False)
        if edge == "right":
            ok = hide[0] > peek[0] and peek[0] + width == area["x"] + area["width"]
        elif edge == "top":
            ok = hide[1] < peek[1] and peek[1] == area["y"]
        else:
            ok = hide[1] > peek[1] and peek[1] + height == area["y"] + area["height"]
        check(f"{edge} 边的半藏比滑出更靠屏幕外、且滑出时贴边",
              ok, f"peek={peek} hide={hide}")

    # ================================================================ 四
    print("\n=== 四、鼠标滑出判定 ===")
    # 贴左边，宠物纵向在 400~620（含 margin 24）
    backend.petSnap(area["x"] + 5, area["y"] + 400)
    edge = backend.petEdge
    check("准备状态：贴在左边", edge == "left", repr(edge))

    win_x, win_y = backend._pet_geometry_for("left", 0, 400, peek=True)
    in_band_y = win_y + height // 2

    check("鼠标移到左边缘、且在宠物高度内 → 滑出",
          backend._pet_should_peek(area["x"] + 2, in_band_y))
    check("鼠标移到左边缘、但在宠物高度之外 → 不滑出（只是路过）",
          not backend._pet_should_peek(area["x"] + 2,
                                       win_y + height + 200))
    check("鼠标在屏幕中间 → 不滑出",
          not backend._pet_should_peek(area["x"] + 900, in_band_y))
    check("鼠标在最右边 → 不滑出（贴的是左边）",
          not backend._pet_should_peek(area["x"] + area["width"] - 2,
                                       in_band_y))

    # 换到右边再验一次，确认不是只对左边有效
    backend.petDetach()
    backend.petSnap(area["x"] + area["width"] - width - 5, area["y"] + 400)
    check("准备状态：贴在右边", backend.petEdge == "right",
          repr(backend.petEdge))
    win_x, win_y = backend._pet_geometry_for("right", 0, 400, peek=True)
    in_band_y = win_y + height // 2
    check("鼠标移到右边缘、且在宠物高度内 → 滑出",
          backend._pet_should_peek(area["x"] + area["width"] - 2, in_band_y))
    check("鼠标在左边缘 → 不滑出（贴的是右边）",
          not backend._pet_should_peek(area["x"] + 2, in_band_y))

    # 上下边：判定轴要反过来
    backend.petDetach()
    backend.petSnap(area["x"] + 600, area["y"] + 5)
    check("准备状态：贴在上边", backend.petEdge == "top",
          repr(backend.petEdge))
    win_x, win_y = backend._pet_geometry_for("top", 600, 0, peek=True)
    in_band_x = win_x + width // 2
    check("鼠标移到上边缘、且在宠物宽度内 → 滑出",
          backend._pet_should_peek(in_band_x, area["y"] + 2))
    check("鼠标在上边缘但横向离宠物很远 → 不滑出",
          not backend._pet_should_peek(win_x + width + 300, area["y"] + 2))
    check("鼠标在下边缘 → 不滑出（贴的是上边）",
          not backend._pet_should_peek(in_band_x,
                                       area["y"] + area["height"] - 2))

    backend.petDetach()
    backend.petSnap(area["x"] + 600, area["y"] + area["height"] - height - 5)
    check("准备状态：贴在下边", backend.petEdge == "bottom",
          repr(backend.petEdge))
    win_x, win_y = backend._pet_geometry_for("bottom", 600, 0, peek=True)
    in_band_x = win_x + width // 2
    check("鼠标移到下边缘、且在宠物宽度内 → 滑出",
          backend._pet_should_peek(in_band_x,
                                   area["y"] + area["height"] - 2))
    check("鼠标在上边缘 → 不滑出（贴的是下边）",
          not backend._pet_should_peek(in_band_x, area["y"] + 2))

    # 没贴边时永远不滑出
    backend.petDetach()
    check("没贴边时不会判定滑出",
          not backend._pet_should_peek(area["x"] + 2, 400))

    # ================================================================ 五
    print("\n=== 五、不会把宠物弄丢 ===")
    backend.petDetach()
    backend.petSnap(area["x"] + 5, area["y"] + 400)
    check("贴在左边（此时窗口 x 是负数）",
          backend._pet_geometry_for("left", 0, 400, peek=False)[0] < 0,
          "半藏坐标应该是负的")

    # 半藏时保存位置，不能把负坐标存进去 —— 否则用户关掉贴边之后
    # 宠物会跑到屏幕外找不回来
    backend.savePetPosition(area["x"] - width // 2, 400)
    saved = backend.petPosition()
    check("贴边时保存的是「完全显示」的坐标，不是负数",
          saved[0] >= area["x"], f"存了 {saved}")

    # 关掉贴边开关 → 位置要能回到屏幕内
    store.settings["pet_snap_enabled"] = False
    backend._on_setting_changed("pet_snap_enabled")
    check("关掉贴边后解除贴边状态", backend.petEdge == "",
          repr(backend.petEdge))
    check("关掉贴边后位置回到屏幕内",
          backend.petPosition()[0] >= area["x"], str(backend.petPosition()))
    store.settings["pet_snap_enabled"] = True

    # 贴边状态存的是「哪条边」而不是坐标 —— 换分辨率也不会丢
    backend.petSnap(area["x"] + 5, area["y"] + 400)
    check("状态里存的是边而不是坐标",
          store.state.get("pet_edge") == "left",
          repr(store.state.get("pet_edge")))
    geo = backend.petEdgeGeometry()
    check("petEdgeGeometry 报出可用的位置",
          geo["active"] and geo["edge"] == "left", str(geo))

    # 换了窗口尺寸（用户调缩放）之后位置要重算，不能还用旧尺寸的偏移
    store.settings["pet_scale"] = 2.0
    wide, high = backend._pet_size()
    check("缩放改了之后尺寸跟着变", wide != width or high != height,
          f"{wide}x{high}")
    geo2 = backend._pet_geometry_for("left", 0, 400, peek=False)
    check("换尺寸后半藏的偏移跟着重算",
          geo2[0] == area["x"] - int(wide * backend._pet_hide_ratio),
          f"{geo2[0]}，应为 {area['x'] - int(wide * backend._pet_hide_ratio)}")
    store.settings["pet_scale"] = 1.0

    # ================================================================ 六
    print("\n=== 六、贴边姿势（侧躺 / 倒挂）===")
    #
    # 姿势不只是好看：直挺挺藏一半看起来像**被切掉**，转过角度之后同一个
    # 「只露一部分」读起来是它自己趴在那儿 / 挂在那儿。
    backend.petDetach()

    for edge, expect_pose, expect_angle, expect_hang in (
        ("left", "side-left", 90, False),
        ("right", "side-right", -90, False),
        ("top", "hang", 180, True),
        ("bottom", "sit", 0, False),
    ):
        backend.petDetach()
        if edge == "left":
            backend.petSnap(area["x"] + 5, area["y"] + 300)
        elif edge == "right":
            backend.petSnap(area["x"] + area["width"] - width - 5,
                            area["y"] + 300)
        elif edge == "top":
            backend.petSnap(area["x"] + 500, area["y"] + 5)
        else:
            backend.petSnap(area["x"] + 500,
                            area["y"] + area["height"] - height - 5)
        check(f"{edge} 的姿势是 {expect_pose}",
              backend.petPose == expect_pose, repr(backend.petPose))
        check(f"{edge} 的角度是 {expect_angle}°",
              backend.petPoseAngle == expect_angle,
              str(backend.petPoseAngle))
        check(f"{edge} 的倒挂标记正确",
              backend.petHanging is expect_hang,
              str(backend.petHanging))

    # ---- 朝向：头必须朝屏幕里，不能朝屏幕外 ----
    #
    # **这条是用户报「左右的贴边反了」之后补的。**
    #
    # 之前只断言了「可见区里有眼睛」—— 但**两个方向都能让眼睛露出来**，
    # 区别只是「头朝屋里躺」还是「头朝屋外躺」（后者看着像倒栽葱）。
    # 所以那个断言抓不到朝向问题。
    #
    # 正确的判据是**头顶的落点**：把画布上方中心（头顶）按同样的角度转
    # 一遍，它必须落在可见区里。用和产品一样的旋转公式算，不另写一套。
    print("\n  朝向（头顶必须转向屏幕里）：")
    import math

    def rotated_head_top(angle: int, w: int, h: int) -> tuple[float, float]:
        """头顶（画布上方中心）绕画布中心转 angle 度之后落在哪。

        Qt 的 rotation 正值是顺时针（屏幕坐标 y 向下），
        所以在 y 向下的坐标系里用标准旋转矩阵即可：
            x' = x·cosθ − y·sinθ
            y' = x·sinθ + y·cosθ
        """
        cx, cy = w / 2.0, h / 2.0
        # 头顶：画布水平中心、垂直方向靠上（用 1/4 高度处，别贴边）
        ox, oy = 0.0, -(h / 2.0) + h * 0.25
        rad = math.radians(angle)
        return (cx + ox * math.cos(rad) - oy * math.sin(rad),
                cy + ox * math.sin(rad) + oy * math.cos(rad))

    for edge in ("left", "right", "top", "bottom"):
        # **角度和比例都要从产品读，不能用测试自己写的常量。**
        #
        # 第一版这里用的是循环里的字面量，结果把符号改错之后这几条断言
        # 照样全绿 —— 它只是在验证我自己的算术自洽，根本没测到产品。
        # 这种断言比没有更糟：它给的是虚假的安心。
        backend._pet_edge = edge
        angle = backend.petPoseAngle
        ratio = backend._pet_hide_ratio

        hx, hy = rotated_head_top(angle, width, height)
        if edge == "left":
            visible = hx >= width * ratio
            where = f"可见区从 x={int(width * ratio)} 起"
        elif edge == "right":
            visible = hx <= width * (1 - ratio)
            where = f"可见区到 x={int(width * (1 - ratio))} 为止"
        elif edge == "top":
            visible = hy >= height * ratio
            where = f"可见区从 y={int(height * ratio)} 起"
        else:
            visible = hy <= height * (1 - ratio)
            where = f"可见区到 y={int(height * (1 - ratio))} 为止"
        check(f"{edge} 边：头顶落在可见区里（头朝屋里躺）", visible,
              f"角度 {angle}°，头顶转到 ({hx:.0f}, {hy:.0f})，{where}")
    backend._pet_edge = ""

    # 左右两侧的旋转方向必须**相反**，否则一边朝里一边朝外
    backend.petDetach()
    backend.petSnap(area["x"] + 5, area["y"] + 300)
    left_angle = backend.petPoseAngle
    backend.petDetach()
    backend.petSnap(area["x"] + area["width"] - width - 5, area["y"] + 300)
    right_angle = backend.petPoseAngle
    check("左右两侧的旋转方向相反", left_angle == -right_angle,
          f"left={left_angle} right={right_angle}")
    check("左侧是顺时针（头才朝屏幕里）", left_angle > 0,
          f"left={left_angle} —— 反了的话头会朝屏幕外，看着像倒栽葱")

    # 藏匿比例必须**按边不同** —— 统一 0.5 会让四条边都只剩后脑勺
    backend.petDetach()
    ratios = {}
    for edge in ("left", "right", "top", "bottom"):
        backend._pet_edge = edge
        ratios[edge] = backend._pet_hide_ratio
    print(f"  各边藏匿比例：{ {k: f'{v:.0%}' for k, v in ratios.items()} }")
    check("藏匿比例按边区分（不是统一值）", len(set(ratios.values())) >= 2,
          str(ratios))
    check("藏得比例都在合理范围（不超过 60%）",
          all(0.0 < r <= 0.60 for r in ratios.values()),
          str(ratios))
    backend._pet_edge = ""

    # 关掉姿势开关：角度归零，但藏匿比例仍按边取值（脸要留得住）
    store.settings["pet_edge_pose"] = False
    backend.petDetach()
    backend.petSnap(area["x"] + 5, area["y"] + 300)
    check("关掉姿势开关后角度归零", backend.petPoseAngle == 0,
          str(backend.petPoseAngle))
    check("关掉姿势开关后姿势名是空", backend.petPose == "",
          repr(backend.petPose))
    check("关掉姿势开关后仍按边取藏匿比例",
          backend._pet_hide_ratio < 0.5,
          f"{backend._pet_hide_ratio:.0%}")
    store.settings["pet_edge_pose"] = True

    # ---- 解除贴边必须通知界面 ----
    #
    # **这是用户报「贴边没有回正」的根因。** 解除贴边时原来一次信号都不发，
    # QML 收不到任何动静，姿势就不转回来 —— 把宠物从边上拖走之后，
    # 它会一直歪着待在屏幕中间。
    print("\n  解除贴边要通知界面（不然姿势不转回来）：")
    signals: list[int] = []
    backend.petGeometryChanged.connect(lambda: signals.append(1))

    backend.petDetach()
    signals.clear()
    backend.petSnap(area["x"] + 5, area["y"] + 300)
    check("吸附时发了信号", len(signals) >= 1, f"{len(signals)} 次")

    signals.clear()
    backend.petDetach()
    check("解除贴边时发了信号", len(signals) >= 1,
          f"{len(signals)} 次 —— 0 次的话 QML 不知道要转回来")
    check("解除之后角度是 0", backend.petPoseAngle == 0,
          str(backend.petPoseAngle))

    # 重复解除不该反复发信号（免得界面跟着反复动画）
    signals.clear()
    backend.petDetach()
    check("已经解除了再解除不重复发信号", len(signals) == 0,
          f"{len(signals)} 次")

    # 从边上拖走的完整路径：先 detach 再 snap 到屏幕中间
    backend.petSnap(area["x"] + 5, area["y"] + 300)
    check("重新贴上", backend.petEdge == "left", repr(backend.petEdge))
    signals.clear()
    backend.petDetach()
    result = backend.petSnap(area["x"] + 800, area["y"] + 300)
    check("拖到屏幕中间不再吸附", result["edge"] == "", str(result))
    check("拖走之后角度回到 0（宠物是正的）", backend.petPoseAngle == 0,
          str(backend.petPoseAngle))
    check("拖走这条路径也发了信号", len(signals) >= 1, f"{len(signals)} 次")

    # 关掉贴边开关时，要把「完全显示」的位置记下来，界面才知道挪到哪
    backend.petDetach()
    backend.petSnap(area["x"] + 5, area["y"] + 300)
    store.settings["pet_snap_enabled"] = False
    backend._on_setting_changed("pet_snap_enabled")
    saved = backend.petPosition()
    check("关掉开关后位置记录在屏幕内（不是半藏的负坐标）",
          saved[0] >= area["x"], f"存了 {saved}")
    check("关掉开关后角度归零", backend.petPoseAngle == 0,
          str(backend.petPoseAngle))
    store.settings["pet_snap_enabled"] = True

    # ================================================================ 七
    print("\n=== 七、配置与界面接线 ===")
    from pawpet.config import (PET_DESIGN_HEIGHT, PET_DESIGN_WIDTH,
                               PET_SCALE_MAX, PET_SCALE_MIN)

    qml = (ROOT / "pawpet" / "qml" / "PawPet" / "PetWindow.qml").read_text(
        encoding="utf-8")
    check("QML 里的设计宽和 config 一致",
          f"designWidth: {PET_DESIGN_WIDTH}" in qml,
          f"config={PET_DESIGN_WIDTH}")
    check("QML 里的设计高和 config 一致",
          f"designHeight: {PET_DESIGN_HEIGHT}" in qml,
          f"config={PET_DESIGN_HEIGHT}")
    check("QML 里的缩放下限和 config 一致",
          str(PET_SCALE_MIN) in qml, f"config={PET_SCALE_MIN}")
    check("QML 里的缩放上限和 config 一致",
          str(PET_SCALE_MAX) in qml, f"config={PET_SCALE_MAX}")

    check("QML 订阅了 petGeometryChanged",
          "onPetGeometryChanged" in qml, "没订阅的话滑出不会动")
    check("QML 用「位置静止」判断拖动结束",
          "settleTimer" in qml, "原生拖动拿不到松手事件")
    check("QML 在贴边时跳过 clampToScreen",
          "if (backend.petEdge)" in qml,
          "不跳过的话贴上去就被拉回屏幕里")
    check("QML 拖动结束后先检查是否还在原位",
          "backend.petDetach()" in qml,
          "不做这步半藏的宠物会自己解开")

    settings = (ROOT / "pawpet" / "qml" / "PawPet" / "page"
                / "SettingsPage.qml").read_text(encoding="utf-8")
    check("设置页有贴边开关", "backend.petSnapEnabled" in settings)
    check("设置页有吸附距离滑块", "backend.petSnapDistance" in settings)
    check("设置页有「拉回来」的出口", "backend.petDetach()" in settings)
    check("设置页有贴边姿势开关", "backend.petEdgePose" in settings)

    # QML 侧：旋转挂在 stage 上，不是 Pet 上
    check("QML 转的是 stage 这一层",
          "rotation: win.petAngle" in qml,
          "转 Pet 会和它自己的 scale 打架，旋转中心也会偏")
    check("QML 有翻身动画", "Behavior on poseAngle" in qml)
    check("overshoot 写在 easing 下（写外面会报属性不存在）",
          "easing.overshoot" in qml, "写成 overshoot: 会 Cannot assign")
    check("倒挂时有晃动", "poseSwing" in qml)
    check("眼神按宠物坐标系换算（不然倒挂时朝反方向看）",
          "Math.cos(a)" in qml and "Math.sin(a)" in qml,
          "没有角度换算的话，转过去之后眼睛会看错方向")

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
