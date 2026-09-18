import QtQuick
import PawPet 1.0

/* 宠物本体。
   设计画布固定 200 x 220，外层按窗口大小整体缩放。

   一共 4 套形象，通过 style 属性切换（设置页里可以实时预览）：

     mochi    麻薯猫 —— 软糯的团子猫，头和身子连成一坨，最耐看
     shiba    柴犬   —— 奶油配橘色，立耳、卷尾、眉毛点
     penguin  企鹅   —— 圆滚滚的黑白配色，橘色喙和脚蹼
     fox      小狐狸 —— 橘色配白肚皮，大耳朵和蓬松白尖尾巴

   画法上刻意做了这几件事，也是它比旧版好看的原因：
   * 用线性渐变做出体积感，而不是纯色平涂
   * 描边是细的暖深色，不是粗黑线，避免廉价剪贴画味道
   * 每只都有头顶高光、腮红、眼睛双高光
   * 静态部分只画一次并缓存成纹理；呼吸、眨眼、摇尾全部走 GPU 变换，
     不重复光栅化，所以 60fps 也不费电 */

Item {
    id: root
    implicitWidth: 200
    implicitHeight: 220

    property string style: "mochi"
    property real bob: 0            // 呼吸位移
    property real tailAngle: 0      // 尾巴摆动角度
    property bool blinking: false
    property real ringProgress: 0   // 0..1
    property string mode: "focus"
    property bool running: false
    property bool hovering: false
    property string badgeText: "•"
    property real eyeShiftX: 0
    property real eyeShiftY: 0

    // ------------------------------------------------------------ 情绪状态
    // idle      平时
    // thinking  AI 正在干活（歪头、眼睛往上看、思考气泡）
    // speaking  AI 刚回完话（嘴张合、身体轻快地颠）
    // sleepy    很久没人理（闭眼、呼吸变深变慢、飘 Zzz）
    // bored     有一会儿没人理（眼神乱飘、小动作）
    property string mood: "idle"
    property real fidgetX: 0      // 无聊时眼珠的额外偏移
    property real fidgetY: 0

    // ---------------------------------------------------------- 运动通道
    /* 四种情绪各自需要不同的「肢体动作」，但身体是画在 Canvas 上烘焙好的，
       能动的只有整体变换。所以这里开几条互相独立的通道，动画往里面写，
       bodyGroup 统一读 —— 互不打架，也不用为了一个动作重画整个身体。

       **活泼的关键是不规则。** 原来呼吸/眨眼/摇尾都是完美周期循环，
       看久了就是机械感。所以下面除了周期动画，还加了一批随机触发的小动作
       （抽动、叹气、张望、伸懒腰）——「活」的感觉主要来自这些。

       ---- 通道为什么分成「一次性」和「循环」两组 ----
       一次性动作（被戳、叹气、伸懒腰）和状态循环（说话时的颠、思考时的摇）
       会同时发生 —— 最典型的就是「AI 正在说话，用户点了一下宠物」。
       如果两者写同一个属性，后写的会盖掉先写的，表现是抖动或某个动作
       整个失效，而且**不会报任何错**。

       所以拆成两组，在 bodyGroup 里叠加：
         hop / squashX / squashY  ← 一次性动作（poke / sigh / stretch / tinyHop）
         loopHop / loopSquashX / loopSquashY ← 状态循环（speakBounce）
       叠加之后「一边说话一边被戳」反而是最自然的效果。 */
    property real hop: 0          // 一次性：弹跳的纵向偏移
    property real squashX: 1.0    // 一次性：非等比缩放
    property real squashY: 1.0
    property real sway: 0         // 摇摆角度，叠加在 headTilt 上

    property real loopHop: 0      // 循环：说话时的颠
    property real loopSquashX: 1.0
    property real loopSquashY: 1.0

    // 呼吸
    readonly property real breathAmp: mood === "sleepy" ? -5.5
                                    : mood === "thinking" ? -2.0
                                    : mood === "speaking" ? -4.2 : -3.5
    readonly property int breathMs: mood === "sleepy" ? 2600
                                  : mood === "thinking" ? 900
                                  : mood === "speaking" ? 700 : 1500
    // 摇尾
    readonly property real tailAmp: mood === "sleepy" ? 2
                                  : (mood === "thinking" || mood === "speaking") ? 12 : 8
    readonly property int tailMs: mood === "sleepy" ? 3200
                                : (mood === "thinking" || mood === "speaking") ? 1100 : 1900
    // 视线
    readonly property real gazeX: mood === "thinking" ? 1.6 : 0
    readonly property real gazeY: mood === "thinking" ? -2.4 : 0
    // 头部倾斜：四种状态各不相同，一眼能看出它在干嘛
    readonly property real headTilt: mood === "thinking" ? 4.5
                                   : mood === "bored" ? -3.5
                                   : mood === "sleepy" ? -5.0 : 0

    readonly property color ringColor: Theme.modeColor(mode)

    // ---------------------------------------------------------------- 几何参数
    // 每套形象的细节尺寸不同，集中放这里，绘制函数只读不写
    readonly property var geo: {
        switch (style) {
        // badge（进度环）统一挪到头左上方悬浮 —— 原来在嘴的位置，
        // 说话时嘴被徽章挡着，张嘴动画白做。左上正好空着
        // （尾巴、思考气泡、Zzz 全在右边），跟思考气泡左右呼应。
        case "shiba":
            return { eyeY: 98, eyeLX: 78, eyeRX: 122, eyeW: 19, eyeH: 21,
                     eyeColor: "#3A2E28",
                     badgeX: 30, badgeY: 60, badgeR: 15,
                     tailX: 146, tailY: 104, tailW: 58, tailH: 74,
                     tailOX: 6, tailOY: 58, mouthX: 100, mouthY: 122 }
        case "penguin":
            return { eyeY: 100, eyeLX: 84, eyeRX: 116, eyeW: 15, eyeH: 17,
                     eyeColor: "#2B2B33",
                     badgeX: 32, badgeY: 56, badgeR: 16,
                     tailX: 146, tailY: 128, tailW: 56, tailH: 62,
                     tailOX: 8, tailOY: 52, mouthX: 100, mouthY: 128 }
        case "fox":
            return { eyeY: 100, eyeLX: 78, eyeRX: 122, eyeW: 19, eyeH: 21,
                     eyeColor: "#4A3327",
                     badgeX: 28, badgeY: 54, badgeR: 15,
                     tailX: 138, tailY: 92, tailW: 62, tailH: 84,
                     tailOX: 8, tailOY: 68, mouthX: 100, mouthY: 130 }
        default: // mochi
            return { eyeY: 122, eyeLX: 76, eyeRX: 124, eyeW: 21, eyeH: 23,
                     eyeColor: "#3E3548",
                     badgeX: 32, badgeY: 58, badgeR: 16,
                     tailX: 142, tailY: 116, tailW: 62, tailH: 78,
                     tailOX: 10, tailOY: 66,
                     // mouthY 是「说话时张开的嘴」贴的位置，必须跟着
                     // paintMochi 里那个 ω 的弧线走。
                     // 规律（另外三只都遵守）：弧线中心 y + 2 ——
                     //   柴犬 弧 120 → 122 ／ 狐狸 弧 128 → 130
                     // 麻薯的嘴改成并排 ω 之后弧线从 151 挪到了 157；
                     // 这里要是还留 153，张嘴就会盖住鼻子。
                     mouthX: 100, mouthY: 159 }
        }
    }

    // ---------------------------------------------------------------- 画图助手
    function ellipsePath(ctx, cx, cy, rx, ry) {
        ctx.save()
        ctx.translate(cx, cy)
        ctx.scale(Math.max(rx, 0.01), Math.max(ry, 0.01))
        ctx.beginPath()
        ctx.arc(0, 0, 1, 0, Math.PI * 2, false)
        ctx.restore()
    }

    function fillEllipse(ctx, cx, cy, rx, ry, fill) {
        ellipsePath(ctx, cx, cy, rx, ry)
        ctx.fillStyle = fill
        ctx.fill()
    }

    function strokeEllipse(ctx, cx, cy, rx, ry, stroke, width) {
        ellipsePath(ctx, cx, cy, rx, ry)
        ctx.strokeStyle = stroke
        ctx.lineWidth = width
        ctx.stroke()
    }

    function polygon(ctx, points, fill, stroke, width) {
        ctx.beginPath()
        ctx.moveTo(points[0], points[1])
        for (var i = 2; i < points.length; i += 2)
            ctx.lineTo(points[i], points[i + 1])
        ctx.closePath()
        if (fill) {
            ctx.fillStyle = fill
            ctx.fill()
        }
        if (stroke) {
            ctx.strokeStyle = stroke
            ctx.lineWidth = width || 1
            ctx.stroke()
        }
    }

    function vGradient(ctx, x, y0, y1, top, bottom) {
        var g = ctx.createLinearGradient(x, y0, x, y1)
        g.addColorStop(0, top)
        g.addColorStop(1, bottom)
        return g
    }

    // ---------------------------------------------------------------- 影子
    Rectangle {
        x: 32
        y: 197
        width: 136
        height: 17
        radius: 8.5
        color: "#1c1626"
        opacity: 0.28
        scale: root.hovering ? 1.06 : 1.0
        Behavior on scale {
            NumberAnimation { duration: Theme.animNormal; easing.type: Theme.easing }
        }
    }

    // ---------------------------------------------------------------- 尾巴
    Item {
        id: tailItem
        x: root.geo.tailX
        y: root.geo.tailY
        width: root.geo.tailW
        height: root.geo.tailH
        transform: Rotation {
            origin.x: root.geo.tailOX
            origin.y: root.geo.tailOY
            angle: root.tailAngle
        }

        Canvas {
            id: tailCanvas
            anchors.fill: parent
            antialiasing: true
            renderStrategy: Canvas.Cooperative
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.lineCap = "round"
                ctx.lineJoin = "round"
                root.paintTail(ctx, root.style, width, height)
            }
        }
    }

    // ---------------------------------------------------------------- 身体
    Item {
        id: bodyGroup
        anchors.fill: parent
        transformOrigin: Item.Bottom
        // 三条通道同时作用：呼吸（bob）+ 一次性动作（hop）+ 状态循环（loopHop）。
        // 分开写是为了让不同动画能各管一条，互不覆盖。
        y: root.bob + root.hop + root.loopHop
        rotation: root.headTilt + root.sway
        Behavior on rotation {
            NumberAnimation { duration: 420; easing.type: Theme.easing }
        }

        // 挤压拉伸。Item 自带的 scale 是等比的，做不出「压扁」的效果，
        // 所以用 Scale 变换给两个轴各自的系数。轴心放在底部中心 ——
        // 从脚底压下去才像有重量，从中心压会像飘在空中。
        //
        // 两组系数**相乘**：一次性的形变叠在循环形变上，
        // 「一边说话一边被戳」才会是两个效果同时有，而不是互相顶掉。
        transform: Scale {
            origin.x: bodyGroup.width / 2
            origin.y: bodyGroup.height
            xScale: root.squashX * root.loopSquashX
            yScale: root.squashY * root.loopSquashY
        }

        Canvas {
            id: bodyCanvas
            anchors.fill: parent
            antialiasing: true
            renderStrategy: Canvas.Cooperative
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.lineJoin = "round"
                ctx.lineCap = "round"
                root.paintBody(ctx, root.style, width, height)
            }
        }

        // ------------------------------------------------------------ 眼睛
        PetEye {
            x: root.geo.eyeLX - width / 2
            y: root.geo.eyeY - height / 2
            eyeW: root.geo.eyeW
            eyeH: root.geo.eyeH
            shut: root.blinking || root.mood === "sleepy"
            shiftX: root.eyeShiftX + root.fidgetX + root.gazeX
            shiftY: root.eyeShiftY + root.fidgetY + root.gazeY
            eyeColor: root.geo.eyeColor
        }
        PetEye {
            x: root.geo.eyeRX - width / 2
            y: root.geo.eyeY - height / 2
            eyeW: root.geo.eyeW
            eyeH: root.geo.eyeH
            shut: root.blinking || root.mood === "sleepy"
            shiftX: root.eyeShiftX + root.fidgetX + root.gazeX
            shiftY: root.eyeShiftY + root.fidgetY + root.gazeY
            eyeColor: root.geo.eyeColor
        }
    }

    // ---------------------------------------------------------------- 进度环
    // 悬浮在头左上方的小气泡徽章（位置见 geo.badgeX/Y），右下缀两个
    // 渐小的圆点引向头部 —— 跟思考气泡的引导圆点同一语言，
    // 看起来是宠物「自己的」状态，而不是一张贴在脸上的贴纸。
    Item {
        id: badge
        x: root.geo.badgeX - width / 2
        y: root.geo.badgeY - height / 2
        width: root.geo.badgeR * 2
        height: root.geo.badgeR * 2

        Canvas {
            id: ring
            anchors.fill: parent
            antialiasing: true
            renderStrategy: Canvas.Cooperative
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var cx = width / 2
                var cy = height / 2
                var r = width / 2 - 3.5

                ctx.beginPath()
                ctx.arc(cx, cy, r, 0, Math.PI * 2, false)
                var g = ctx.createLinearGradient(0, 0, 0, height)
                g.addColorStop(0, "#FFFDF7")
                g.addColorStop(1, "#EFE0C9")
                ctx.fillStyle = g
                ctx.fill()
                ctx.strokeStyle = "rgba(108, 86, 64, 0.42)"
                ctx.lineWidth = 1.6
                ctx.stroke()

                var progress = Math.max(0, Math.min(1, root.ringProgress))
                if (progress > 0.002) {
                    ctx.beginPath()
                    ctx.arc(cx, cy, r, -Math.PI / 2,
                            -Math.PI / 2 + Math.PI * 2 * progress, false)
                    ctx.strokeStyle = root.ringColor
                    ctx.lineWidth = 4.5
                    ctx.lineCap = "round"
                    ctx.stroke()
                }
            }
        }

        Text {
            anchors.centerIn: parent
            text: root.running ? Math.round(root.ringProgress * 100) + "%" : root.badgeText
            color: "#6B5140"
            font.family: root.running ? Theme.fontMono : Theme.font
            font.pixelSize: root.running ? Theme.px(10) : Theme.px(13)
            font.bold: true
        }

        // 引向头部的两个小圆点（越靠头越小），超出 Item 边界也会被画出来。
        // 位置按徽章宽高比例算：四套形象的耳朵/头轮廓都贴在徽章右下方，
        // 圆点垂直略偏右下落才刚好从耳朵和身体之间的空档里穿过去。
        //
        // **半透明色必须写 Qt.rgba()，不能写 CSS 那种字符串。**
        // `border.color: "rgba(108,86,64,0.35)"` 看着像 CSS，但 QML 的
        // color 属性只认 "#rrggbb" / "#aarrggbb" / 颜色名 —— 传字符串会报
        // 「Invalid property assignment: color expected」，**整个 Pet.qml
        // 加载失败**，宠物直接不显示，还会级联到 Dashboard / Main /
        // SettingsPage / PetWindow。
        // 这个坑踩过两次了（第二次就是这两个圆点），所以把说明贴在
        // 出事的地方，而不是隔 80 行之外。
        Rectangle {
            width: 6.5
            height: 6.5
            radius: 3.25
            x: badge.width * 0.59
            y: badge.height + 3
            color: "#FFFDF7"
            border.width: 1
            border.color: Qt.rgba(108 / 255, 86 / 255, 64 / 255, 0.35)
        }
        Rectangle {
            width: 4.5
            height: 4.5
            radius: 2.25
            x: badge.width * 0.72
            y: badge.height + 12
            color: "#FFFDF7"
            border.width: 1
            border.color: Qt.rgba(108 / 255, 86 / 255, 64 / 255, 0.35)
        }

        SequentialAnimation on scale {
            running: root.running
            loops: Animation.Infinite
            NumberAnimation { to: 1.07; duration: 900; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
        }
    }

    // ---------------------------------------------------------------- 状态覆盖层
    // 身体是画进 Canvas 再缓存的静态图，状态小件全部走 GPU 变换叠在上面，
    // 不触发身体重绘，所以一直动也不费电。

    // 说话时张合的嘴：盖在画好的嘴上做张合，比重画整个身体便宜得多
    Item {
        id: mouthOverlay
        visible: root.mood === "speaking"
        x: root.geo.mouthX
        y: root.geo.mouthY

        // 张嘴时露出的口腔：深色内圈 + 舌头色，比一个纯色方块像嘴
        Rectangle {
            anchors.centerIn: parent
            width: 15
            height: 4
            radius: height / 2
            color: "#8A4A52"
            SequentialAnimation on height {
                running: mouthOverlay.visible
                loops: Animation.Infinite
                NumberAnimation { to: 11; duration: 120; easing.type: Easing.InOutQuad }
                NumberAnimation { to: 3; duration: 140; easing.type: Easing.InOutQuad }
            }
        }
        Rectangle {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: 1
            width: 8
            height: 3
            radius: 1.5
            color: "#C97A82"
            opacity: 0.85
            SequentialAnimation on opacity {
                running: mouthOverlay.visible
                loops: Animation.Infinite
                NumberAnimation { to: 0.95; duration: 120 }
                NumberAnimation { to: 0.25; duration: 140 }
            }
        }
    }

    // 思考气泡：三个点轮流跳 + 整体轻轻上浮
    Item {
        id: thinkBubble
        visible: root.mood === "thinking"
        x: 136
        y: 20
        width: 48
        height: 28

        // 轻轻飘：站桩似的挂着会显得很呆
        SequentialAnimation on y {
            running: thinkBubble.visible
            loops: Animation.Infinite
            NumberAnimation { to: 15; duration: 1400; easing.type: Easing.InOutSine }
            NumberAnimation { to: 20; duration: 1400; easing.type: Easing.InOutSine }
        }
        SequentialAnimation on opacity {
            running: thinkBubble.visible
            loops: Animation.Infinite
            NumberAnimation { to: 0.72; duration: 1100; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 1100; easing.type: Easing.InOutSine }
        }

        // 引向头顶的小圆点
        //
        // **注意 Qt.rgba() 是函数，不能写成字符串。**
        // `color: "rgba(108,86,64,0.35)"` 看着像 CSS，但 QML 的 color 属性
        // 只认 "#rrggbb" / "#aarrggbb" / 颜色名 —— 传字符串会直接报
        // 「Invalid property assignment: color expected」，整个 Pet.qml
        // 加载失败，还会级联到 Dashboard / Main / SettingsPage / PetWindow。
        Rectangle { width: 5; height: 5; radius: 2.5; x: -10; y: 31
                    color: "#FFFDF7"; border.width: 1
                    border.color: Qt.rgba(108 / 255, 86 / 255, 64 / 255, 0.35) }
        Rectangle { width: 8; height: 8; radius: 4; x: -4; y: 23
                    color: "#FFFDF7"; border.width: 1
                    border.color: Qt.rgba(108 / 255, 86 / 255, 64 / 255, 0.35) }

        Rectangle {
            anchors.fill: parent
            radius: 14
            color: "#FFFDF7"
            border.width: 1.2
            border.color: Qt.rgba(108 / 255, 86 / 255, 64 / 255, 0.35)
        }
        Row {
            anchors.centerIn: parent
            spacing: 5
            Repeater {
                model: 3
                Rectangle {
                    width: 6.5
                    height: 6.5
                    radius: 3.25
                    color: "#8A7A6A"
                    SequentialAnimation on scale {
                        running: thinkBubble.visible
                        loops: Animation.Infinite
                        PauseAnimation { duration: index * 150 }
                        NumberAnimation { to: 1.45; duration: 190; easing.type: Easing.OutQuad }
                        NumberAnimation { to: 1.0; duration: 210; easing.type: Easing.InQuad }
                        PauseAnimation { duration: 420 + (2 - index) * 150 }
                    }
                }
            }
        }
    }

    // 无聊的省略号气泡：表示它在「唉……」
    // 用省略号而不是文字，是因为它只是情绪，不该像在跟用户说话
    Item {
        id: boredBubble
        visible: root.mood === "bored"
        x: 138
        y: 34
        width: 40
        height: 22

        SequentialAnimation on y {
            running: boredBubble.visible
            loops: Animation.Infinite
            NumberAnimation { to: 38; duration: 1800; easing.type: Easing.InOutSine }
            NumberAnimation { to: 34; duration: 1800; easing.type: Easing.InOutSine }
        }

        Rectangle {
            width: 5; height: 5; radius: 2.5; x: -6; y: 24
            color: "#FFFAF3"; border.width: 1
            border.color: Qt.rgba(130 / 255, 115 / 255, 100 / 255, 0.3)
        }
        Rectangle {
            anchors.fill: parent
            radius: 11
            color: "#FFFAF3"
            border.width: 1
            border.color: Qt.rgba(130 / 255, 115 / 255, 100 / 255, 0.3)
        }
        Row {
            anchors.centerIn: parent
            spacing: 3.5
            Repeater {
                model: 3
                Rectangle {
                    width: 4.5
                    height: 4.5
                    radius: 2.25
                    color: "#A2948A"
                    // 一个一个亮起来，像话说到一半没劲了
                    SequentialAnimation on opacity {
                        running: boredBubble.visible
                        loops: Animation.Infinite
                        PauseAnimation { duration: index * 420 }
                        NumberAnimation { to: 1.0; duration: 260 }
                        NumberAnimation { to: 0.25; duration: 900 }
                        PauseAnimation { duration: 420 }
                    }
                }
            }
        }
    }

    // 打盹的 Zzz：三个 z 错开往上飘，透明度走进走出
    Item {
        id: sleepZs
        visible: root.mood === "sleepy"
        Repeater {
            model: 3
            delegate: Item {
                id: zItem
                property real t: 0
                Text {
                    text: "z"
                    color: "#8E84A8"
                    font.family: Theme.font
                    // 越飘越大，所以带着 index 一起过 Theme.px()。
                    // 直接写 `13 + index * 5` 的话不吃界面缩放 ——
                    // 用户把界面调大，这一串 z 还是原来那么小。
                    font.pixelSize: Theme.px(13 + index * 5)
                    font.bold: true
                    opacity: Math.sin(Math.min(1, zItem.t) * Math.PI) * 0.9
                    x: 134 + index * 13 + zItem.t * 10
                    y: 66 - zItem.t * 46
                }
                SequentialAnimation on t {
                    running: sleepZs.visible
                    loops: Animation.Infinite
                    PauseAnimation { duration: index * 650 }
                    NumberAnimation { to: 1; duration: 2300; easing.type: Easing.InOutSine }
                    PropertyAction { target: zItem; property: "t"; value: 0 }
                }
            }
        }
    }

    // ================================================== 动作层：呼吸/眨眼/摇尾
    SequentialAnimation {
        id: breathAnim
        running: true
        loops: Animation.Infinite
        NumberAnimation { target: root; property: "bob"; to: root.breathAmp; duration: root.breathMs; easing.type: Easing.InOutSine }
        NumberAnimation { target: root; property: "bob"; to: 0; duration: root.breathMs; easing.type: Easing.InOutSine }
    }

    // 眨眼：**间隔随机**。
    //
    // 原来是一段写死的定长序列（2400/2100/1700），每次循环一模一样 ——
    // 盯着看几秒就能发现是机械重复，很假。真人眨眼是不规律的，
    // 所以改成每轮现抽一个间隔。
    Timer {
        id: blinkTimer
        repeat: true
        running: root.mood !== "sleepy"
        interval: 1600 + Math.random() * 3200
        onTriggered: {
            interval = 1600 + Math.random() * 3200
            // 偶尔来个连眨（两下），更像活的
            if (Math.random() < 0.22)
                doubleBlink.restart()
            else
                singleBlink.restart()
        }
    }
    SequentialAnimation {
        id: singleBlink
        ScriptAction { script: root.blinking = true }
        PauseAnimation { duration: 105 }
        ScriptAction { script: root.blinking = false }
    }
    SequentialAnimation {
        id: doubleBlink
        ScriptAction { script: root.blinking = true }
        PauseAnimation { duration: 95 }
        ScriptAction { script: root.blinking = false }
        PauseAnimation { duration: 130 }
        ScriptAction { script: root.blinking = true }
        PauseAnimation { duration: 90 }
        ScriptAction { script: root.blinking = false }
    }

    SequentialAnimation {
        id: tailAnim
        running: true
        loops: Animation.Infinite
        NumberAnimation { target: root; property: "tailAngle"; to: root.tailAmp; duration: root.tailMs; easing.type: Easing.InOutSine }
        NumberAnimation { target: root; property: "tailAngle"; to: -root.tailAmp; duration: root.tailMs; easing.type: Easing.InOutSine }
    }

    // ==================================================== 动作层：说话
    // 一边说一边轻快地颠：起跳时拉长、落地时压扁（挤压拉伸原理）。
    // 光上下动会很僵，加上形变才有弹性的感觉。
    //
    // 写 loopHop / loopSquash*，**不写 hop / squash*** —— 后者归一次性动作
    // （poke / sigh / stretch）。用户完全可能在 AI 说话时点一下宠物，
    // 两者同时发生是常态，共用一个属性就会互相顶掉。
    SequentialAnimation {
        id: speakBounce
        running: root.mood === "speaking"
        loops: Animation.Infinite
        ParallelAnimation {
            NumberAnimation { target: root; property: "loopHop"; to: -4.5; duration: 190; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "loopSquashY"; to: 1.05; duration: 190 }
            NumberAnimation { target: root; property: "loopSquashX"; to: 0.965; duration: 190 }
        }
        ParallelAnimation {
            NumberAnimation { target: root; property: "loopHop"; to: 0; duration: 210; easing.type: Easing.InQuad }
            NumberAnimation { target: root; property: "loopSquashY"; to: 0.955; duration: 210 }
            NumberAnimation { target: root; property: "loopSquashX"; to: 1.035; duration: 210 }
        }
        ParallelAnimation {
            NumberAnimation { target: root; property: "loopSquashY"; to: 1.0; duration: 260; easing.type: Easing.OutBack }
            NumberAnimation { target: root; property: "loopSquashX"; to: 1.0; duration: 260; easing.type: Easing.OutBack }
        }
        PauseAnimation { duration: 90 }
    }

    // ==================================================== 动作层：思考
    // 轻轻左右摇摆，像在琢磨。幅度很小（±1.8°）—— 大了会像喝醉。
    SequentialAnimation {
        id: thinkSway
        running: root.mood === "thinking"
        loops: Animation.Infinite
        NumberAnimation { target: root; property: "sway"; to: 1.8; duration: 1500; easing.type: Easing.InOutSine }
        NumberAnimation { target: root; property: "sway"; to: -1.8; duration: 1500; easing.type: Easing.InOutSine }
    }

    // ==================================================== 动作层：打盹
    // 睡着了也会偶尔动一下（做梦/换姿势），一动不动反而像死机。
    Timer {
        id: sleepTwitchTimer
        repeat: true
        running: root.mood === "sleepy"
        interval: 4200 + Math.random() * 6000
        onTriggered: {
            interval = 4200 + Math.random() * 6000
            sleepTwitch.restart()
        }
    }
    SequentialAnimation {
        id: sleepTwitch
        NumberAnimation { target: root; property: "sway"; to: 2.4; duration: 100; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "sway"; to: -0.6; duration: 220; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "sway"; to: 0; duration: 420; easing.type: Easing.OutElastic }
    }

    // ==================================================== 动作层：无聊
    // 无聊时眼神乱飘 + 偶尔叹口气 + 偶尔扭头张望。
    Behavior on fidgetX { NumberAnimation { duration: 300; easing.type: Easing.InOutQuad } }
    Behavior on fidgetY { NumberAnimation { duration: 300; easing.type: Easing.InOutQuad } }

    Timer {
        id: boredTimer
        repeat: true
        running: root.mood === "bored"
        interval: 1800 + Math.random() * 2400
        onTriggered: {
            interval = 1800 + Math.random() * 2400
            var roll = Math.random()
            if (roll < 0.55) {
                // 眼神乱飘
                root.fidgetX = (Math.random() * 6) - 3
                root.fidgetY = (Math.random() * 2.4) - 1.2
            } else if (roll < 0.8) {
                sighAnim.restart()          // 叹气
            } else {
                lookAroundAnim.restart()    // 扭头张望
            }
        }
    }

    // 叹气：慢慢压扁再慢慢回弹，像泄了气
    //
    // **注意这里写的是 hop 不是 bob。** bob 归常驻的 breathAnim 管
    // （无限循环），两个动画同时写同一个属性会互相覆盖 —— 表现出来就是
    // 抖动或者「叹气根本看不出来」。每条通道只归一个动画写。
    SequentialAnimation {
        id: sighAnim
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 0.93; duration: 520; easing.type: Easing.InOutQuad }
            NumberAnimation { target: root; property: "squashX"; to: 1.055; duration: 520; easing.type: Easing.InOutQuad }
            NumberAnimation { target: root; property: "hop"; to: 1.5; duration: 520; easing.type: Easing.InOutQuad }
        }
        PauseAnimation { duration: 240 }
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 1.0; duration: 820; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "squashX"; to: 1.0; duration: 820; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "hop"; to: 0; duration: 820; easing.type: Easing.OutQuad }
        }
    }

    // 扭头张望：头转过去停一下再转回来，像在看别处
    SequentialAnimation {
        id: lookAroundAnim
        PauseAnimation { duration: 260 }
        NumberAnimation { target: root; property: "sway"; to: 7.5; duration: 620; easing.type: Easing.OutBack }
        PauseAnimation { duration: 900 }
        NumberAnimation { target: root; property: "sway"; to: 0; duration: 700; easing.type: Easing.InOutQuad }
    }

    // ==================================================== 动作层：通用小动作
    // 平时（idle）也要有点小动静，否则就是个静止贴图。
    // **间隔随机**是重点：固定节奏会被看出是循环。
    Timer {
        id: fidgetTimer
        repeat: true
        running: root.mood === "idle"
        interval: 6000 + Math.random() * 8000
        onTriggered: {
            interval = 6000 + Math.random() * 8000
            if (Math.random() < 0.45)
                tinyHop.restart()
            else
                headTwitch.restart()
        }
    }
    // 轻微一跳
    SequentialAnimation {
        id: tinyHop
        NumberAnimation { target: root; property: "hop"; to: -3.5; duration: 150; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "hop"; to: 0; duration: 300; easing.type: Easing.OutBounce }
    }
    // 抖一下头（像甩掉什么东西）
    SequentialAnimation {
        id: headTwitch
        NumberAnimation { target: root; property: "sway"; to: 3.2; duration: 110; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "sway"; to: -1.4; duration: 160; easing.type: Easing.InOutQuad }
        NumberAnimation { target: root; property: "sway"; to: 0; duration: 320; easing.type: Easing.OutElastic }
    }

    // 睡醒伸懒腰：拉长 + 抬起来，再慢慢落回
    SequentialAnimation {
        id: stretchAnim
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 1.10; duration: 420; easing.type: Easing.OutBack }
            NumberAnimation { target: root; property: "squashX"; to: 0.925; duration: 420; easing.type: Easing.OutBack }
            NumberAnimation { target: root; property: "hop"; to: -7; duration: 420; easing.type: Easing.OutBack }
        }
        PauseAnimation { duration: 260 }
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 1.0; duration: 500; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "squashX"; to: 1.0; duration: 500; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "hop"; to: 0; duration: 560; easing.type: Easing.OutBounce }
        }
    }

    // 记住上一个状态，用来判断「刚睡醒」这类转变。
    // 注意：QML 不允许同名信号处理器写两遍，所以状态切换的全部处理
    // 都放在下面**这一个** onMoodChanged 里。
    property string previousMood: "idle"

    onMoodChanged: {
        // 离开某个状态时，把它留下的姿势收干净 —— 否则会出现
        // 「已经不无聊了但头还歪着」这种残留。
        if (mood !== "bored" && mood !== "thinking" && mood !== "sleepy")
            sway = 0
        if (mood !== "bored") {
            fidgetX = 0
            fidgetY = 0
        }
        if (mood !== "speaking") {
            hop = 0
            squashX = 1.0
            squashY = 1.0
            // 循环组也要归位 —— 离开说话态时 speakBounce 会停，
            // 但它停在半路（比如正颠到最高点），不复位的话宠物会一直
            // 保持着那个形变，看起来像卡住了。
            loopHop = 0
            loopSquashX = 1.0
            loopSquashY = 1.0
        }
        // 睡醒了伸个懒腰
        if (mood === "idle" && previousMood === "sleepy")
            stretchAnim.restart()

        // 让新的呼吸/摇尾参数立刻生效，不用等当前这一拍跑完
        breathAnim.restart()
        tailAnim.restart()

        previousMood = mood
    }

    onStyleChanged: {
        bodyCanvas.requestPaint()
        tailCanvas.requestPaint()
        ring.requestPaint()
    }
    onRingProgressChanged: ring.requestPaint()
    onModeChanged: ring.requestPaint()
    onRunningChanged: ring.requestPaint()

    function poke() {
        pokeAnim.restart()
    }

    // 被戳一下：猛地一缩再弹回来。
    // 走 squash/hop 通道而不是直接动 bodyGroup.scale —— 那样会和
    // Scale 变换叠加成两套缩放，poke 的同时如果在说话就会变形成怪样子。
    SequentialAnimation {
        id: pokeAnim
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 0.86; duration: 90; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "squashX"; to: 1.12; duration: 90; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "hop"; to: 3; duration: 90; easing.type: Easing.OutQuad }
        }
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 1.06; duration: 150; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "squashX"; to: 0.965; duration: 150; easing.type: Easing.OutQuad }
            NumberAnimation { target: root; property: "hop"; to: -4.5; duration: 150; easing.type: Easing.OutQuad }
        }
        ParallelAnimation {
            NumberAnimation { target: root; property: "squashY"; to: 1.0; duration: 360; easing.type: Easing.OutElastic }
            NumberAnimation { target: root; property: "squashX"; to: 1.0; duration: 360; easing.type: Easing.OutElastic }
            NumberAnimation { target: root; property: "hop"; to: 0; duration: 360; easing.type: Easing.OutBounce }
        }
    }

    // ====================================================================
    //  绘制实现
    // ====================================================================

    function paintTail(ctx, styleName, w, h) {
        if (styleName === "shiba") {
            // 柴犬的卷尾：贴着屁股卷一圈，不要卷得太大太圆，
            // 否则会变成一个甜甜圈挂在旁边。
            ctx.beginPath()
            ctx.moveTo(4, h * 0.90)
            ctx.bezierCurveTo(w * 0.66, h * 0.96, w * 0.92, h * 0.56, w * 0.62, h * 0.26)
            ctx.bezierCurveTo(w * 0.40, h * 0.05, w * 0.10, h * 0.14, w * 0.10, h * 0.44)
            ctx.strokeStyle = "#6E5546"
            ctx.lineWidth = 21
            ctx.stroke()
            ctx.strokeStyle = "#F0A868"
            ctx.lineWidth = 17
            ctx.stroke()
            // 卷曲内侧的浅色，让尾巴有层次
            ctx.beginPath()
            ctx.moveTo(w * 0.30, h * 0.38)
            ctx.bezierCurveTo(w * 0.22, h * 0.50, w * 0.34, h * 0.64, w * 0.50, h * 0.62)
            ctx.strokeStyle = "#FFF1DC"
            ctx.lineWidth = 7
            ctx.stroke()
            return
        }

        if (styleName === "penguin") {
            // 企鹅的小尾羽
            ctx.beginPath()
            ctx.moveTo(8, h * 0.66)
            ctx.quadraticCurveTo(w * 0.62, h * 0.88, w * 0.88, h * 0.52)
            ctx.strokeStyle = "#22222B"
            ctx.lineWidth = 17
            ctx.stroke()
            ctx.strokeStyle = "#3C3C4A"
            ctx.lineWidth = 10
            ctx.stroke()
            return
        }

        if (styleName === "fox") {
            // 狐狸尾巴：从屁股向右下方甩出去再翘起来，白尖在末端。
            // 关键是尾巴不能太粗，不然白色尖会变成一朵云。
            ctx.beginPath()
            ctx.moveTo(4, h * 0.72)
            ctx.bezierCurveTo(w * 0.62, h * 0.92, w * 0.98, h * 0.62, w * 0.80, h * 0.26)
            ctx.strokeStyle = "#7A4B2E"
            ctx.lineWidth = 27
            ctx.stroke()
            ctx.strokeStyle = "#F0913F"
            ctx.lineWidth = 23
            ctx.stroke()
            // 尾巴末端往上的白尖
            ctx.beginPath()
            ctx.moveTo(w * 0.86, h * 0.42)
            ctx.bezierCurveTo(w * 0.94, h * 0.28, w * 0.84, h * 0.12, w * 0.66, h * 0.14)
            ctx.strokeStyle = "#7A4B2E"
            ctx.lineWidth = 24
            ctx.stroke()
            ctx.strokeStyle = "#FFF6EC"
            ctx.lineWidth = 20
            ctx.stroke()
            return
        }

        // mochi：短短一条小卷尾
        ctx.beginPath()
        ctx.moveTo(6, h * 0.82)
        ctx.bezierCurveTo(w * 0.72, h * 0.88, w * 0.94, h * 0.42, w * 0.52, h * 0.20)
        ctx.strokeStyle = "#6E5E6B"
        ctx.lineWidth = 21
        ctx.stroke()
        ctx.strokeStyle = "#F4E3D0"
        ctx.lineWidth = 16
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(w * 0.54, h * 0.21)
        ctx.lineTo(w * 0.42, h * 0.10)
        ctx.strokeStyle = "#6E5E6B"
        ctx.lineWidth = 14
        ctx.stroke()
        ctx.strokeStyle = "#F4E3D0"
        ctx.lineWidth = 9
        ctx.stroke()
    }

    function paintBody(ctx, styleName, w, h) {
        if (styleName === "shiba")
            paintShiba(ctx)
        else if (styleName === "penguin")
            paintPenguin(ctx)
        else if (styleName === "fox")
            paintFox(ctx)
        else
            paintMochi(ctx)
    }

    // ------------------------------------------------------------ 麻薯猫
    function paintMochi(ctx) {
        var ink = "#6E5E6B"

        // 耳朵（先画，压在身体后面）
        polygon(ctx, [44, 94, 52, 46, 90, 78], "#F3C4B0", ink, 2.4)
        polygon(ctx, [110, 78, 148, 46, 156, 94], "#F3C4B0", ink, 2.4)
        polygon(ctx, [56, 86, 62, 60, 84, 80], "#FFDCD0", null, 0)
        polygon(ctx, [116, 80, 138, 60, 144, 86], "#FFDCD0", null, 0)

        // 头和身子连成一坨，这是麻薯猫的关键
        ctx.beginPath()
        ctx.moveTo(36, 130)
        ctx.bezierCurveTo(36, 82, 62, 64, 100, 64)
        ctx.bezierCurveTo(138, 64, 164, 82, 164, 130)
        ctx.bezierCurveTo(164, 174, 138, 194, 100, 194)
        ctx.bezierCurveTo(62, 194, 36, 174, 36, 130)
        ctx.closePath()
        ctx.fillStyle = vGradient(ctx, 100, 64, 194, "#FFFCF6", "#F1DCC3")
        ctx.fill()
        ctx.strokeStyle = ink
        ctx.lineWidth = 2.4
        ctx.stroke()

        // 头顶高光
        fillEllipse(ctx, 72, 86, 18, 9, "rgba(255,255,255,0.8)")

        // 肚皮
        fillEllipse(ctx, 100, 154, 39, 35, "rgba(255,255,255,0.6)")

        // 前爪
        fillEllipse(ctx, 66, 185, 18, 11, "#FFF7EA")
        strokeEllipse(ctx, 66, 185, 18, 11, "rgba(110,94,107,0.32)", 1.6)
        fillEllipse(ctx, 134, 185, 18, 11, "#FFF7EA")
        strokeEllipse(ctx, 134, 185, 18, 11, "rgba(110,94,107,0.32)", 1.6)

        // 腮红
        fillEllipse(ctx, 52, 142, 12, 7.5, "rgba(246,174,178,0.68)")
        fillEllipse(ctx, 148, 142, 12, 7.5, "rgba(246,174,178,0.68)")

        // 鼻子 + 嘴
        polygon(ctx, [94, 145, 106, 145, 100, 152], "#D9767E", null, 0)
        ctx.strokeStyle = "#9C7A80"
        ctx.lineWidth = 2.1
        // 从鼻尖引一小段下来，再分成左右两瓣 —— 就是猫嘴那个「ω」。
        //
        // **这里原来是两个同心弧**（圆心都是 (100,151)，半径 7.5 和 15）：
        // 大弧套小弧，看着像信号格或者双彩虹，不像嘴 —— 四只里只有麻薯
        // 是这么画的，所以它显得比柴犬、狐狸差。
        //
        // 现在跟柴犬/狐狸用同一套公式：竖线从鼻尖到嘴，两瓣圆心在
        // 鼻尖左右各偏 5、半径 6.5（它们用的是偏 5 / 半径 6，
        // 麻薯脸更大一点，等比放大一丝）。
        ctx.beginPath()
        ctx.moveTo(100, 152)
        ctx.lineTo(100, 157)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(94.5, 157, 6.5, 0, 0.9 * Math.PI, false)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(105.5, 157, 6.5, 0.1 * Math.PI, Math.PI, false)
        ctx.stroke()

        // 胡须
        ctx.strokeStyle = "rgba(140,120,130,0.5)"
        ctx.lineWidth = 1.4
        ctx.beginPath(); ctx.moveTo(60, 142); ctx.lineTo(38, 137); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(60, 149); ctx.lineTo(38, 149); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(140, 142); ctx.lineTo(162, 137); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(140, 149); ctx.lineTo(162, 149); ctx.stroke()
    }

    // -------------------------------------------------------------- 柴犬
    function paintShiba(ctx) {
        var ink = "#6E5546"

        // 耳朵
        polygon(ctx, [50, 86, 56, 40, 92, 72], "#F0A868", ink, 2.4)
        polygon(ctx, [108, 72, 144, 40, 150, 86], "#F0A868", ink, 2.4)
        polygon(ctx, [61, 78, 65, 52, 85, 73], "#FFE0C4", null, 0)
        polygon(ctx, [115, 73, 135, 52, 139, 78], "#FFE0C4", null, 0)

        // 身体
        ctx.beginPath()
        ctx.moveTo(54, 150)
        ctx.bezierCurveTo(54, 112, 72, 98, 100, 98)
        ctx.bezierCurveTo(128, 98, 146, 112, 146, 150)
        ctx.bezierCurveTo(146, 180, 126, 192, 100, 192)
        ctx.bezierCurveTo(74, 192, 54, 180, 54, 150)
        ctx.closePath()
        ctx.fillStyle = vGradient(ctx, 100, 98, 192, "#FFE9C9", "#F5D0A0")
        ctx.fill()
        ctx.strokeStyle = ink
        ctx.lineWidth = 2.4
        ctx.stroke()

        // 白色胸毛
        fillEllipse(ctx, 100, 168, 30, 24, "rgba(255,252,246,0.92)")

        // 前爪
        fillEllipse(ctx, 72, 185, 17, 11, "#FFF9F0")
        strokeEllipse(ctx, 72, 185, 17, 11, "rgba(110,85,70,0.3)", 1.6)
        fillEllipse(ctx, 128, 185, 17, 11, "#FFF9F0")
        strokeEllipse(ctx, 128, 185, 17, 11, "rgba(110,85,70,0.3)", 1.6)

        // 头：先描边底色，再用渐变填充
        fillEllipse(ctx, 100, 94, 58, 52, ink)
        fillEllipse(ctx, 100, 93, 55.5, 49.5,
                    vGradient(ctx, 100, 44, 142, "#FFF3DE", "#F8D8AC"))

        // 头顶橘色（用裁剪圆弧实现，避免 ctx.ellipse 在部分版本上不可用）
        ctx.save()
        ctx.beginPath()
        ctx.moveTo(100, 43)
        ctx.bezierCurveTo(126, 43, 144, 55, 152, 76)
        ctx.lineTo(48, 76)
        ctx.bezierCurveTo(56, 55, 74, 43, 100, 43)
        ctx.closePath()
        ctx.fillStyle = "#F0A868"
        ctx.fill()
        ctx.restore()

        // 口鼻白区
        fillEllipse(ctx, 100, 118, 34, 25, "rgba(255,252,247,0.97)")

        // 眉毛点 —— 柴犬的灵魂
        fillEllipse(ctx, 76, 82, 6.5, 5, "rgba(255,253,248,0.95)")
        fillEllipse(ctx, 124, 82, 6.5, 5, "rgba(255,253,248,0.95)")

        // 腮红
        fillEllipse(ctx, 55, 112, 11, 7, "rgba(242,166,160,0.55)")
        fillEllipse(ctx, 145, 112, 11, 7, "rgba(242,166,160,0.55)")

        // 鼻子 + 嘴
        fillEllipse(ctx, 100, 108, 8.5, 6.5, "#4B3A32")
        fillEllipse(ctx, 97.4, 106.4, 2.6, 2, "rgba(255,255,255,0.6)")
        ctx.strokeStyle = "#8A6A58"
        ctx.lineWidth = 2.1
        ctx.beginPath()
        ctx.moveTo(100, 115)
        ctx.lineTo(100, 120)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(95, 120, 6, 0, 0.9 * Math.PI, false)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(105, 120, 6, 0.1 * Math.PI, Math.PI, false)
        ctx.stroke()

        // 胡须
        ctx.strokeStyle = "rgba(150,120,95,0.42)"
        ctx.lineWidth = 1.3
        ctx.beginPath(); ctx.moveTo(66, 116); ctx.lineTo(46, 112); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(66, 123); ctx.lineTo(46, 123); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(134, 116); ctx.lineTo(154, 112); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(134, 123); ctx.lineTo(154, 123); ctx.stroke()
    }

    // -------------------------------------------------------------- 企鹅
    function paintPenguin(ctx) {
        var ink = "#22222B"

        // 脚蹼
        fillEllipse(ctx, 74, 189, 20, 10, "#F2A03C")
        strokeEllipse(ctx, 74, 189, 20, 10, "rgba(150,95,25,0.45)", 1.4)
        fillEllipse(ctx, 126, 189, 20, 10, "#F2A03C")
        strokeEllipse(ctx, 126, 189, 20, 10, "rgba(150,95,25,0.45)", 1.4)

        // 翅膀
        ctx.save()
        ctx.translate(48, 148)
        ctx.rotate(-0.2)
        fillEllipse(ctx, 0, 0, 15, 33, "#33333F")
        strokeEllipse(ctx, 0, 0, 15, 33, ink, 2.2)
        ctx.restore()
        ctx.save()
        ctx.translate(152, 148)
        ctx.rotate(0.2)
        fillEllipse(ctx, 0, 0, 15, 33, "#33333F")
        strokeEllipse(ctx, 0, 0, 15, 33, ink, 2.2)
        ctx.restore()

        // 身体
        ctx.beginPath()
        ctx.moveTo(100, 42)
        ctx.bezierCurveTo(150, 42, 164, 96, 162, 140)
        ctx.bezierCurveTo(160, 178, 132, 192, 100, 192)
        ctx.bezierCurveTo(68, 192, 40, 178, 38, 140)
        ctx.bezierCurveTo(36, 96, 50, 42, 100, 42)
        ctx.closePath()
        ctx.fillStyle = vGradient(ctx, 100, 42, 192, "#4A4A5A", "#25252F")
        ctx.fill()
        ctx.strokeStyle = ink
        ctx.lineWidth = 2.4
        ctx.stroke()

        // 白肚皮
        ctx.beginPath()
        ctx.moveTo(100, 146)
        ctx.bezierCurveTo(128, 146, 138, 162, 136, 174)
        ctx.bezierCurveTo(132, 188, 116, 192, 100, 192)
        ctx.bezierCurveTo(84, 192, 68, 188, 64, 174)
        ctx.bezierCurveTo(62, 162, 72, 146, 100, 146)
        ctx.closePath()
        ctx.fillStyle = vGradient(ctx, 100, 146, 192, "#FFFFFF", "#E6E6F0")
        ctx.fill()

        // 白色脸罩
        ctx.beginPath()
        ctx.moveTo(100, 68)
        ctx.bezierCurveTo(128, 68, 138, 86, 136, 102)
        ctx.bezierCurveTo(134, 120, 118, 130, 100, 130)
        ctx.bezierCurveTo(82, 130, 66, 120, 64, 102)
        ctx.bezierCurveTo(62, 86, 72, 68, 100, 68)
        ctx.closePath()
        ctx.fillStyle = "#FBFBFE"
        ctx.fill()

        // 头顶高光
        fillEllipse(ctx, 74, 60, 18, 8, "rgba(255,255,255,0.13)")

        // 腮红
        fillEllipse(ctx, 66, 114, 10, 6.5, "rgba(245,169,174,0.6)")
        fillEllipse(ctx, 134, 114, 10, 6.5, "rgba(245,169,174,0.6)")

        // 喙
        polygon(ctx, [90, 112, 110, 112, 100, 124], "#F2A03C", "#C97C22", 1.6)
        ctx.beginPath()
        ctx.moveTo(92.5, 115.5)
        ctx.lineTo(107.5, 115.5)
        ctx.strokeStyle = "rgba(160,100,25,0.5)"
        ctx.lineWidth = 1.2
        ctx.stroke()
    }

    // ------------------------------------------------------------ 小狐狸
    function paintFox(ctx) {
        var ink = "#7A4B2E"

        // 大耳朵
        polygon(ctx, [44, 90, 50, 30, 92, 72], "#F0913F", ink, 2.4)
        polygon(ctx, [108, 72, 150, 30, 156, 90], "#F0913F", ink, 2.4)
        polygon(ctx, [56, 80, 60, 46, 84, 74], "#FFE3CE", null, 0)
        polygon(ctx, [116, 74, 140, 46, 144, 80], "#FFE3CE", null, 0)
        // 耳尖深色
        polygon(ctx, [50, 30, 63, 47, 44, 56], "#5E3A24", null, 0)
        polygon(ctx, [150, 30, 156, 56, 137, 47], "#5E3A24", null, 0)

        // 身体
        ctx.beginPath()
        ctx.moveTo(54, 150)
        ctx.bezierCurveTo(54, 112, 72, 98, 100, 98)
        ctx.bezierCurveTo(128, 98, 146, 112, 146, 150)
        ctx.bezierCurveTo(146, 180, 126, 192, 100, 192)
        ctx.bezierCurveTo(74, 192, 54, 180, 54, 150)
        ctx.closePath()
        ctx.fillStyle = vGradient(ctx, 100, 98, 192, "#FFC089", "#EE8F3C")
        ctx.fill()
        ctx.strokeStyle = ink
        ctx.lineWidth = 2.4
        ctx.stroke()

        // 白肚皮
        fillEllipse(ctx, 100, 167, 30, 26, "rgba(255,250,243,0.95)")

        // 前爪
        fillEllipse(ctx, 72, 185, 17, 11, "#FFF7EC")
        strokeEllipse(ctx, 72, 185, 17, 11, "rgba(122,75,46,0.3)", 1.6)
        fillEllipse(ctx, 128, 185, 17, 11, "#FFF7EC")
        strokeEllipse(ctx, 128, 185, 17, 11, "rgba(122,75,46,0.3)", 1.6)

        // 头
        fillEllipse(ctx, 100, 94, 58, 52, ink)
        fillEllipse(ctx, 100, 93, 55.5, 49.5,
                    vGradient(ctx, 100, 44, 144, "#FFC089", "#EE8F3C"))

        // 脸下半部白色
        ctx.beginPath()
        ctx.moveTo(100, 84)
        ctx.bezierCurveTo(126, 84, 138, 100, 136, 116)
        ctx.bezierCurveTo(132, 136, 118, 143, 100, 143)
        ctx.bezierCurveTo(82, 143, 68, 136, 64, 116)
        ctx.bezierCurveTo(62, 100, 74, 84, 100, 84)
        ctx.closePath()
        ctx.fillStyle = "rgba(255,250,244,0.96)"
        ctx.fill()

        // 腮红
        fillEllipse(ctx, 55, 114, 11, 7, "rgba(244,167,156,0.55)")
        fillEllipse(ctx, 145, 114, 11, 7, "rgba(244,167,156,0.55)")

        // 鼻子 + 嘴
        fillEllipse(ctx, 100, 118, 8, 6, "#4A3327")
        fillEllipse(ctx, 97.5, 116.4, 2.4, 1.9, "rgba(255,255,255,0.55)")
        ctx.strokeStyle = "#8A6247"
        ctx.lineWidth = 2.1
        ctx.beginPath()
        ctx.moveTo(100, 124)
        ctx.lineTo(100, 128)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(95, 128, 6, 0, 0.9 * Math.PI, false)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(105, 128, 6, 0.1 * Math.PI, Math.PI, false)
        ctx.stroke()

        // 胡须
        ctx.strokeStyle = "rgba(150,100,60,0.42)"
        ctx.lineWidth = 1.3
        ctx.beginPath(); ctx.moveTo(66, 120); ctx.lineTo(46, 116); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(66, 127); ctx.lineTo(46, 127); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(134, 120); ctx.lineTo(154, 116); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(134, 127); ctx.lineTo(154, 127); ctx.stroke()
    }
}
