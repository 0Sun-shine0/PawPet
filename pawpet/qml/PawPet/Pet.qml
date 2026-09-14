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

    readonly property color ringColor: Theme.modeColor(mode)

    // ---------------------------------------------------------------- 几何参数
    // 每套形象的细节尺寸不同，集中放这里，绘制函数只读不写
    readonly property var geo: {
        switch (style) {
        case "shiba":
            return { eyeY: 98, eyeLX: 78, eyeRX: 122, eyeW: 19, eyeH: 21,
                     eyeColor: "#3A2E28",
                     badgeX: 100, badgeY: 162, badgeR: 21,
                     tailX: 146, tailY: 104, tailW: 58, tailH: 74,
                     tailOX: 6, tailOY: 58 }
        case "penguin":
            return { eyeY: 100, eyeLX: 84, eyeRX: 116, eyeW: 15, eyeH: 17,
                     eyeColor: "#2B2B33",
                     badgeX: 100, badgeY: 172, badgeR: 21,
                     tailX: 146, tailY: 128, tailW: 56, tailH: 62,
                     tailOX: 8, tailOY: 52 }
        case "fox":
            return { eyeY: 100, eyeLX: 78, eyeRX: 122, eyeW: 19, eyeH: 21,
                     eyeColor: "#4A3327",
                     badgeX: 100, badgeY: 162, badgeR: 21,
                     tailX: 138, tailY: 92, tailW: 62, tailH: 84,
                     tailOX: 8, tailOY: 68 }
        default: // mochi
            return { eyeY: 122, eyeLX: 76, eyeRX: 124, eyeW: 21, eyeH: 23,
                     eyeColor: "#3E3548",
                     badgeX: 100, badgeY: 162, badgeR: 22,
                     tailX: 142, tailY: 116, tailW: 62, tailH: 78,
                     tailOX: 10, tailOY: 66 }
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
        y: root.bob

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
            shut: root.blinking
            shiftX: root.eyeShiftX
            shiftY: root.eyeShiftY
            eyeColor: root.geo.eyeColor
        }
        PetEye {
            x: root.geo.eyeRX - width / 2
            y: root.geo.eyeY - height / 2
            eyeW: root.geo.eyeW
            eyeH: root.geo.eyeH
            shut: root.blinking
            shiftX: root.eyeShiftX
            shiftY: root.eyeShiftY
            eyeColor: root.geo.eyeColor
        }
    }

    // ---------------------------------------------------------------- 进度环
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
            font.pixelSize: root.running ? 11 : 14
            font.bold: true
        }

        SequentialAnimation on scale {
            running: root.running
            loops: Animation.Infinite
            NumberAnimation { to: 1.07; duration: 900; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
        }
    }

    // ------------------------------------------------------- 呼吸 / 眨眼 / 摇尾
    SequentialAnimation {
        running: true
        loops: Animation.Infinite
        NumberAnimation { target: root; property: "bob"; to: -3.5; duration: 1500; easing.type: Easing.InOutSine }
        NumberAnimation { target: root; property: "bob"; to: 0; duration: 1500; easing.type: Easing.InOutSine }
    }

    SequentialAnimation {
        running: true
        loops: Animation.Infinite
        PauseAnimation { duration: 2400 }
        ScriptAction { script: root.blinking = true }
        PauseAnimation { duration: 110 }
        ScriptAction { script: root.blinking = false }
        PauseAnimation { duration: 2100 }
        ScriptAction { script: root.blinking = true }
        PauseAnimation { duration: 90 }
        ScriptAction { script: root.blinking = false }
        PauseAnimation { duration: 1700 }
    }

    SequentialAnimation {
        running: true
        loops: Animation.Infinite
        NumberAnimation { target: root; property: "tailAngle"; to: 8; duration: 1900; easing.type: Easing.InOutSine }
        NumberAnimation { target: root; property: "tailAngle"; to: -8; duration: 1900; easing.type: Easing.InOutSine }
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

    SequentialAnimation {
        id: pokeAnim
        NumberAnimation { target: bodyGroup; property: "scale"; to: 1.10; duration: 110; easing.type: Easing.OutQuad }
        NumberAnimation { target: bodyGroup; property: "scale"; to: 0.95; duration: 110; easing.type: Easing.InQuad }
        NumberAnimation { target: bodyGroup; property: "scale"; to: 1.0; duration: 220; easing.type: Easing.OutBack }
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
        ctx.beginPath()
        ctx.arc(100, 151, 7.5, 0.18 * Math.PI, 0.82 * Math.PI, false)
        ctx.stroke()
        ctx.beginPath()
        ctx.arc(100, 151, 15, 0.22 * Math.PI, 0.78 * Math.PI, false)
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
