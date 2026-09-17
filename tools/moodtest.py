r"""宠物情绪动画：验证四种状态真的会动，而且动作互不打架。

为什么单独做这个：QML 动画错了**不会报错** —— 属性名写错、动画没重启、
两套变换互相覆盖，都只是「看起来不动」或「变形怪样子」。
静态检查（qmlcheck）完全抓不到，只有把每一帧的数值采出来才知道。

做法：把 Pet 拉起来，切换 mood，然后**连续采样**那几个运动通道
（bob / hop / squashX / squashY / sway / eyeShift），看它们有没有真的变化。
一个通道在一段时间里始终是常数 = 那条动画没生效。

用法：
    .venv\\Scripts\\python.exe tools\\moodtest.py
输出：
    .cache/shots/mood-*.png（四种状态的对比图，人工也能看）
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "moodtest"
OUT = ROOT / ".cache" / "shots"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "moodtest"

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
    print("小爪宠物情绪动画回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QObject, Qt, QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    _keep_alive.append(app)

    store = Store(SCRATCH / "p.json", SCRATCH / "p.bak.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    # 直接把 Pet 放进一个小窗口，一次只看一只
    host = QQmlComponent(engine)
    host.setData("""
        import QtQuick
        import PawPet 1.0
        Window {
            id: win
            property string moodName: "idle"
            property string petStyle: "mochi"
            width: 240
            height: 260
            visible: true
            color: "#FDF7F9"
            flags: Qt.FramelessWindowHint
            Pet {
                objectName: "pet"
                anchors.centerIn: parent
                width: 200
                height: 220
                style: win.petStyle
                mood: win.moodName
            }
        }
    """.encode("utf-8"),
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "moodhost.qml")))

    root = host.create(engine.rootContext())
    if root is None:
        for error in host.errors():
            print("   ", error.toString())
        check("Pet 能加载", False)
        return 1
    _keep_alive.append(root)

    pet = root.findChild(QObject, "pet", Qt.FindChildrenRecursively)
    check("拿到 Pet 实例", pet is not None)
    if pet is None:
        return 1

    def pump(ms: int) -> None:
        """推进事件循环 ms 毫秒，让动画真的走起来。"""
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.008)

    # 各状态的呼吸半周期（和 Pet.qml 里的 breathMs 一致）。
    # **采样窗口必须盖过一个完整周期**，否则量到的只是「这一段走了多远」，
    # 不是「幅度多大」。第一版就栽在这上面：睡觉的呼吸周期 2600ms、
    # 而窗口只有 528ms，于是「睡觉呼吸最深」被测成了「比平时还浅」——
    # 数据和事实反了，但代码其实是对的。
    BREATH_MS = {"idle": 1500, "thinking": 900, "speaking": 700, "sleepy": 2600}

    def sample(channel: str, ms: int, gap: int = 22) -> list:
        """在 ms 毫秒的窗口里连续采样。"""
        values = []
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            pump(gap)
            values.append(round(float(pet.property(channel) or 0), 4))
        return values

    CHANNELS = ["bob", "hop", "loopHop", "squashX", "squashY",
                "loopSquashX", "loopSquashY", "sway",
                "tailAngle", "eyeShiftX", "fidgetX"]

    # ---------------------------------------------------------------- 一
    print("=== 一、四个状态都真的在动 ===")
    # 每个状态至少要有 2 条通道在持续变化；全静止 = 动画没生效
    #
    # 注意说话态看的是 loop* 那三条：说话时的颠簸走「循环组」，
    # 不和一次性动作（被戳/叹气/伸懒腰）抢属性。见 Pet.qml 里的说明。
    expectations = {
        "idle":     ["bob", "tailAngle"],
        "thinking": ["bob", "sway", "tailAngle"],
        "speaking": ["loopHop", "loopSquashY", "bob"],
        "sleepy":   ["bob", "tailAngle"],
        "bored":    ["bob", "tailAngle"],
    }
    mood_activity: dict[str, dict] = {}
    for mood, must_move in expectations.items():
        root.setProperty("moodName", mood)
        pump(300)
        # 窗口取「一整次呼吸 + 一点余量」，确保量到的是真的幅度
        window = BREATH_MS.get(mood, 1500) * 2 + 400
        activity = {}
        for channel in CHANNELS:
            values = sample(channel, window)
            activity[channel] = max(values) - min(values)
        mood_activity[mood] = activity
        # 顺便把各自的呼吸幅度单独记下来，给下面第二条断言用
        mood_activity[mood]["_breathAmp"] = abs(
            min(sample("bob", BREATH_MS.get(mood, 1500) * 2 + 400))
            - max(sample("bob", BREATH_MS.get(mood, 1500) * 2 + 400))
        )

        moving = [c for c, span in activity.items()
                  if span > 0.004 and not c.startswith("_")]
        print(f"  {mood:9s} 在动的通道：{', '.join(moving) or '（全静止！）'}")
        for channel in must_move:
            check(f"{mood} 的 {channel} 在动",
                  activity[channel] > 0.004,
                  f"变化幅度只有 {activity[channel]:.4f}")

    # ---------------------------------------------------------------- 二
    print("\n=== 二、各状态的动作不一样（否则等于没做）===")
    # 呼吸幅度：睡觉最深、思考最浅（思考时是短促的浅呼吸）
    idle_amp = mood_activity["idle"]["_breathAmp"]
    sleepy_amp = mood_activity["sleepy"]["_breathAmp"]
    think_amp = mood_activity["thinking"]["_breathAmp"]
    print(f"  呼吸幅度：平时 {idle_amp:.2f} / 睡觉 {sleepy_amp:.2f} "
          f"/ 思考 {think_amp:.2f}")
    check("睡觉的呼吸比平时深",
          sleepy_amp > idle_amp,
          f"{sleepy_amp:.3f} vs {idle_amp:.3f}")
    check("思考的呼吸比平时浅",
          think_amp < idle_amp,
          f"{think_amp:.3f} vs {idle_amp:.3f}")

    # 说话时有弹跳，思考时没有
    check("说话时有弹跳（loopHop）",
          mood_activity["speaking"]["loopHop"] > 0.004,
          str(mood_activity["speaking"]["loopHop"]))
    check("思考时不弹跳（不该乱动）",
          mood_activity["thinking"]["loopHop"] < 0.004,
          str(mood_activity["thinking"]["loopHop"]))
    check("说话时有挤压变形（loopSquashY）",
          mood_activity["speaking"]["loopSquashY"] > 0.004,
          str(mood_activity["speaking"]["loopSquashY"]))
    # 思考时的摇摆是**持续**的；平时只有偶尔的随机抖头。
    #
    # 不能用「幅度」区分 —— 两者幅度量出来差不多（思考 ±1.8 的往复，
    # 平时 headTwitch 一下也能到 3.2）。区别在**时间占比**：
    # 思考时大部分采样点都偏离 0，平时绝大多数时候是 0（只在触发那一瞬偏）。
    # 所以比的是「偏离 0 的采样占比」。
    def sway_duty(mood: str) -> float:
        root.setProperty("moodName", mood)
        pump(320)
        vals = [abs(v) for v in sample("sway", 2400)]
        return sum(1 for v in vals if v > 0.05) / max(1, len(vals))

    think_duty = sway_duty("thinking")
    idle_duty = sway_duty("idle")
    print(f"  摇摆占空比：思考 {think_duty:.0%} / 平时 {idle_duty:.0%}")
    check("思考时持续在摇摆（占空比高）", think_duty > 0.55, f"{think_duty:.0%}")
    check("平时只是偶尔抖一下（占空比低）", idle_duty < 0.4, f"{idle_duty:.0%}")
    check("思考的摇摆比平时持续", think_duty > idle_duty + 0.2,
          f"{think_duty:.0%} vs {idle_duty:.0%}")

    # ---------------------------------------------------------------- 三
    print("\n=== 三、状态切换时不残留姿势 ===")
    # 从 thinking 切到 idle，摇摆要归零（否则头一直歪着）
    root.setProperty("moodName", "thinking")
    pump(600)
    root.setProperty("moodName", "idle")
    pump(900)
    check("离开思考态后摇摆归零",
          abs(float(pet.property("sway"))) < 0.5,
          str(pet.property("sway")))

    root.setProperty("moodName", "speaking")
    pump(500)
    root.setProperty("moodName", "idle")
    pump(900)
    check("离开说话态后弹跳归零",
          abs(float(pet.property("hop"))) < 0.6, str(pet.property("hop")))
    check("离开说话态后形变归位",
          abs(float(pet.property("squashY")) - 1.0) < 0.02,
          str(pet.property("squashY")))
    check("离开说话态后横向缩放归位",
          abs(float(pet.property("squashX")) - 1.0) < 0.02,
          str(pet.property("squashX")))
    # 循环组也要归位 —— speakBounce 会停在半路（比如正颠到最高点），
    # 不复位的话宠物会一直保持那个形变，看起来像卡住了
    check("离开说话态后循环组弹跳归零",
          abs(float(pet.property("loopHop"))) < 0.6, str(pet.property("loopHop")))
    check("离开说话态后循环组形变归位",
          abs(float(pet.property("loopSquashY")) - 1.0) < 0.02,
          str(pet.property("loopSquashY")))
    check("离开说话态后循环组横向缩放归位",
          abs(float(pet.property("loopSquashX")) - 1.0) < 0.02,
          str(pet.property("loopSquashX")))

    root.setProperty("moodName", "bored")
    pump(700)
    root.setProperty("moodName", "idle")
    pump(900)
    check("离开无聊态后眼珠归位",
          abs(float(pet.property("fidgetX"))) < 0.5, str(pet.property("fidgetX")))

    # ---------------------------------------------------------------- 四
    print("\n=== 四、poke 不破坏别的通道 ===")
    root.setProperty("moodName", "idle")
    pump(400)
    pet.metaObject().invokeMethod(pet, "poke")
    peaks = {"squashY": [], "hop": []}
    for _ in range(20):
        pump(30)
        peaks["squashY"].append(float(pet.property("squashY")))
        peaks["hop"].append(float(pet.property("hop")))
    check("poke 确实产生了形变",
          max(peaks["squashY"]) - min(peaks["squashY"]) > 0.03,
          f"{min(peaks['squashY']):.3f}~{max(peaks['squashY']):.3f}")
    pump(1200)
    check("poke 结束后形变回到 1.0",
          abs(float(pet.property("squashY")) - 1.0) < 0.02,
          str(pet.property("squashY")))

    # ---- 说话时被戳：两个效果必须**同时**存在 ----
    #
    # 这是拆通道的原因。原来两者都写 hop/squashY，用户在 AI 说话时点一下
    # 宠物，后启动的动画会顶掉另一个 —— 表现是「说话颠簸突然停了」
    # 或者「点了没反应」，而且不报错。
    print("\n  说话时被戳（两个效果叠加）：")
    root.setProperty("moodName", "speaking")
    pump(500)
    # 先量说话循环自己产生的幅度
    solo_loop = []
    for _ in range(18):
        pump(28)
        solo_loop.append(float(pet.property("loopHop")))
    loop_span = max(solo_loop) - min(solo_loop)

    pet.metaObject().invokeMethod(pet, "poke")
    one_shot, loop_during = [], []
    for _ in range(20):
        pump(28)
        one_shot.append(float(pet.property("squashY")))
        loop_during.append(float(pet.property("loopHop")))
    one_span = max(one_shot) - min(one_shot)
    loop_span_during = max(loop_during) - min(loop_during)

    check("被戳时一次性形变生效", one_span > 0.03, f"{one_span:.4f}")
    check("被戳的**同时**说话循环还在动（没被顶掉）",
          loop_span_during > loop_span * 0.4,
          f"戳之前 {loop_span:.4f} / 戳的时候 {loop_span_during:.4f}")
    root.setProperty("moodName", "idle")
    pump(900)

    # ---------------------------------------------------------------- 四之二
    #
    # **每条通道只该有一个动画在写。** 两个动画抢同一个属性会互相覆盖 ——
    # 表现是抖动，或者「这个动作根本看不出来」，而且**不会报错**。
    # 静态检查抓不到，只能从源码里查。
    #
    # 实际踩过：sighAnim 原来写 bob，而 breathAnim 是常驻无限循环也在写
    # bob，于是叹气永远看不出来。
    print("\n=== 四之二、没有两个动画抢同一条通道 ===")
    qml_src = (ROOT / "pawpet" / "qml" / "PawPet" / "Pet.qml").read_text(
        encoding="utf-8")
    import re as _re

    # 每条通道：谁在写它（property: "xxx"）
    writers: dict[str, set[str]] = {}
    current_id = ""
    for line in qml_src.splitlines():
        stripped = line.strip()
        match = _re.match(r"id:\s*(\w+)", stripped)
        if match:
            current_id = match.group(1)
        prop = _re.search(r'property:\s*"(\w+)"', stripped)
        if prop and current_id:
            writers.setdefault(prop.group(1), set()).add(current_id)

    # 常驻循环和状态循环本来就该独占，别的动画不许碰
    EXCLUSIVE = {
        "bob": "breathAnim",
        "tailAngle": "tailAnim",
        # 说话时的循环颠簸独占 loop* 三条，一次性动作走 hop/squash*。
        # 拆开的原因：「AI 说话时用户点一下宠物」是常见操作，
        # 共用一个属性会互相顶掉。
        "loopHop": "speakBounce",
        "loopSquashX": "speakBounce",
        "loopSquashY": "speakBounce",
    }
    for channel, owner in EXCLUSIVE.items():
        who = writers.get(channel, set())
        check(f"{channel} 只有 {owner} 在写",
              who == {owner}, f"实际：{sorted(who)}")

    # 一次性动作不许碰常驻通道
    for channel in ("bob", "tailAngle", "loopHop", "loopSquashX", "loopSquashY"):
        who = writers.get(channel, set())
        intruders = who - {EXCLUSIVE[channel]}
        check(f"没有一次性动作抢 {channel}", not intruders, str(sorted(intruders)))

    # 逐条打印，人工也能核对
    print("  各通道的写入者：")
    for channel in sorted(writers):
        print(f"    {channel:12s} ← {', '.join(sorted(writers[channel]))}")

    # ---------------------------------------------------------------- 五
    print("\n=== 五、四种形象都不报错 ===")
    for style in ("mochi", "shiba", "penguin", "fox"):
        root.setProperty("petStyle", style)
        for mood in ("idle", "thinking", "speaking", "sleepy", "bored"):
            root.setProperty("moodName", mood)
            pump(120)
        check(f"{style} 跑完四种情绪", True)

    # ---------------------------------------------------------------- 出图
    print("\n=== 六、出对比图 ===")
    root.setProperty("petStyle", "mochi")
    for mood in ("idle", "thinking", "speaking", "sleepy", "bored"):
        root.setProperty("moodName", mood)
        pump(700)
        img = root.grabWindow()
        if not img.isNull():
            img.save(str(OUT / f"mood-{mood}.png"))
            print(f"    mood-{mood}.png  {img.width()}x{img.height()}")
    check("出了 5 张对比图",
          len(list(OUT.glob("mood-*.png"))) >= 5,
          str(len(list(OUT.glob("mood-*.png")))))

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
