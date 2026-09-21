import QtQuick
import QtQuick.Controls
import PawPet 1.0

/* 桌宠窗口：无边框 + 背景透明 + 置顶，不在任务栏占位置。
   位置会记住；鼠标悬停时眼睛看向光标；右键出快捷菜单。 */
Window {
    id: win

    readonly property real designWidth: 200
    readonly property real designHeight: 220
    readonly property real petScale: Math.max(0.6, Math.min(2.4, backend.pet_scale))

    width: Math.round(designWidth * petScale)
    height: Math.round(designHeight * petScale)
    minimumWidth: 120
    minimumHeight: 132

    visible: backend.petVisible
    color: "transparent"
    title: backend.appName
    flags: Qt.FramelessWindowHint | Qt.Tool
           | (backend.petAlwaysOnTop ? Qt.WindowStaysOnTopHint : 0)

    // 鼠标长时间不动时自动变淡，不挡视线；鼠标一回来立刻恢复
    opacity: backend.pet_opacity * (backend.petFaded ? 0.4 : 1.0)
    Behavior on opacity {
        NumberAnimation { duration: Theme.animSlow; easing.type: Theme.easing }
    }

    property bool hovering: false
    property bool positionReady: false

    // ------------------------------------------------------------ 宠物情绪状态
    // idle | thinking | speaking | sleepy | bored，按优先级取。
    // 数据来源：backend.ai.running（AI 在干活）、消息列表（刚回完话）、
    // 以及一个空闲秒表（多久没人理它了）。
    property int idleSeconds: 0
    readonly property string petMood: {
        if (backend.ai.running)
            return "thinking"
        if (speakTimer.running)
            return "speaking"
        if (win.hovering)
            return "idle"
        if (idleSeconds >= 240)
            return "sleepy"
        if (idleSeconds >= 45)
            return "bored"
        return "idle"
    }

    Timer {
        id: idleTimer
        interval: 1000
        repeat: true
        running: true
        onTriggered: {
            if (win.hovering || backend.ai.running || speakTimer.running)
                win.idleSeconds = 0
            else
                win.idleSeconds += 1
        }
    }

    // AI 回话后让宠物「说」一会儿，时长跟着回答长度走
    Timer {
        id: speakTimer
        repeat: false
    }

    Connections {
        target: backend.ai
        function onMessagesChanged() {
            win.idleSeconds = 0
            var msgs = backend.ai.messages
            if (!msgs || msgs.length === 0)
                return
            var last = msgs[msgs.length - 1]
            // 只认「这一轮已经结束后的回答」；任务中间的助手消息
            // 由 thinking 状态管，不在这里抢
            if (last.role === "assistant" && !backend.ai.running) {
                speakTimer.interval = Math.min(6000, 1400 + String(last.text || "").length * 35)
                speakTimer.restart()
                pet.poke()
            }
        }
        function onRunningChanged() {
            win.idleSeconds = 0
            // 开始干活时精神一下
            if (backend.ai.running)
                pet.poke()
        }
    }

    // -------------------------------------------------------------- 状态
    function clampToScreen() {
        // **贴边时不做这个夹取。** 贴边的定义就是「有一半在屏幕外」，
        // 而下面这个夹取会把窗口拉回可见范围 —— 两者直接冲突，
        // 表现是宠物刚贴上去就被弹回屏幕里。
        if (backend.petEdge)
            return
        var area = backend.screenAt(win.x, win.y)
        if (!area || area.width <= 0)
            return
        var nx = Math.max(area.x - win.width * 0.3,
                          Math.min(area.x + area.width - win.width * 0.7, win.x))
        var ny = Math.max(area.y - win.height * 0.2,
                          Math.min(area.y + area.height - win.height * 0.5, win.y))
        if (Math.round(nx) !== win.x)
            win.x = Math.round(nx)
        if (Math.round(ny) !== win.y)
            win.y = Math.round(ny)
    }

    // ================================================================ 贴边
    //
    // 拖到屏幕边缘附近就吸附过去，并且有一半藏在屏幕外；鼠标移到那条边
    // 附近自动滑出来，移开一会儿再滑回去。四条边都支持。
    //
    // 位置由 backend 算（它才拿得到带任务栏偏移的可用区域和全局鼠标
    // 位置），这里只负责「什么时候请求」和「怎么移过去」。
    //
    // ---- 为什么拖动结束要靠「位置静止」来判断 ----
    // 拖动走的是 startSystemMove()，交给 Windows 原生处理（流畅、跟手、
    // 能跨屏）。代价是拖动期间 QML 收不到任何事件，也拿不到「松手」这个
    // 时刻 —— 原生拖动会一直占着消息循环直到用户放开鼠标。
    // 所以只能反过来推：位置不再变化了，就认为拖完了。
    property bool edgeAnimating: false

    ParallelAnimation {
        id: slideAnim
        property real toX: 0
        property real toY: 0
        NumberAnimation {
            target: win; property: "x"; to: slideAnim.toX
            duration: 240; easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: win; property: "y"; to: slideAnim.toY
            duration: 240; easing.type: Easing.OutCubic
        }
        onFinished: win.edgeAnimating = false
    }

    function slideTo(tx, ty, animate) {
        if (Math.abs(win.x - tx) < 1 && Math.abs(win.y - ty) < 1)
            return
        if (!animate) {
            win.x = Math.round(tx)
            win.y = Math.round(ty)
            return
        }
        slideAnim.stop()
        win.edgeAnimating = true
        slideAnim.toX = tx
        slideAnim.toY = ty
        slideAnim.restart()
    }

    // 拖动停下来之后：判断要不要吸附
    function settlePosition() {
        if (win.edgeAnimating)
            return
        if (!backend.petSnapEnabled)
            return
        if (!win.positionReady)
            return

        if (backend.petEdge) {
            // 已经贴着某条边。**先看它是不是还在原位** ——
            // 在的话什么都不做。这一步是必须的：贴边之后窗口坐标是负的
            // （贴左边 x=-100），要是拿这个坐标再去问「要不要吸附」，
            // 会被判成「离边缘很远」从而把贴边状态清掉，宠物就自己解开了。
            var geo = backend.petEdgeGeometry()
            if (geo && geo.active
                    && Math.abs(win.x - geo.x) <= 2
                    && Math.abs(win.y - geo.y) <= 2)
                return
            // 位置对不上，说明用户把它从边上拖走了
            backend.petDetach()
        }

        var result = backend.petSnap(win.x, win.y)
        if (result && result.edge)
            slideTo(result.x, result.y, true)
    }

    Timer {
        id: settleTimer
        interval: 240
        onTriggered: win.settlePosition()
    }

    // backend 说位置该变了（吸附、滑出、滑回、解除）
    Connections {
        target: backend
        function onPetGeometryChanged() {
            // 姿势和位置一起更新：贴边状态变了，角度也要跟着变。
            // 放在 positionReady 判断之前 —— 姿势不依赖窗口就绪。
            win.syncPose()
            // 不再倒挂了就把晃动收干净，否则下一次贴边会带着一个偏移量
            if (!backend.petHanging)
                win.poseSwing = 0

            if (!win.positionReady)
                return
            var geo = backend.petEdgeGeometry()
            if (!geo || !geo.active) {
                // 贴边被解除了（用户关了开关、或拖离了边缘）：
                // 从屏幕外回到屏幕内，用动画，免得突然跳一下
                win.clampToScreen()
                return
            }
            win.slideTo(geo.x, geo.y, true)
        }
    }

    function restorePosition() {
        // 贴边状态优先：它比坐标可靠 —— 半藏的坐标是负数，而且换分辨率或
        // 换显示器之后就完全对不上了，而「贴着哪条边」到哪都成立。
        var geo = backend.petEdgeGeometry()
        if (geo && geo.active) {
            win.x = Math.round(geo.x)
            win.y = Math.round(geo.y)
            return
        }

        var saved = backend.petPosition()
        var ok = saved && saved.length === 2
                 && saved[0] !== null && saved[0] !== undefined
                 && saved[1] !== null && saved[1] !== undefined
        if (ok) {
            win.x = Number(saved[0])
            win.y = Number(saved[1])
        } else {
            var area = backend.screenAt(0, 0)
            win.x = area.x + area.width - win.width - 40
            win.y = area.y + area.height - win.height - 80
        }
        clampToScreen()
    }

    Component.onCompleted: {
        restorePosition()
        positionReady = true
        // 启动时如果本来就在贴边状态，姿势要一次性对上，
        // 不然会先直挺挺站着再翻过去
        syncPose()
    }

    // 改「始终置顶」会重建原生窗口，位置需要还原一次
    onFlagsChanged: positionTimer.restart()
    onScreenChanged: clampToScreen()

    Timer {
        id: positionTimer
        interval: 80
        onTriggered: {
            var wasReady = win.positionReady
            win.positionReady = false
            win.restorePosition()
            win.positionReady = wasReady
        }
    }

    Timer {
        id: savePositionTimer
        interval: 700
        onTriggered: backend.savePetPosition(win.x, win.y)
    }

    onXChanged: if (positionReady) { savePositionTimer.restart(); settleTimer.restart() }
    onYChanged: if (positionReady) { savePositionTimer.restart(); settleTimer.restart() }

    // ------------------------------------------------------------- 宠物
    //
    // ---- 贴边姿态 ----
    // 贴到哪条边就用哪个姿势（侧躺 / 倒挂），角度由 backend 给。
    //
    // 转的是 **stage 这一层**，不是 Pet 自己：
    //   * Pet 身上挂着 `scale`（而且 transformOrigin 是 TopLeft），再给它加
    //     旋转，两个变换的先后顺序会影响旋转中心落在哪 —— 容易看着对、
    //     实际偏。stage 是干净的 200x220，转它的中心就是转画布中心。
    //   * stage 只包着宠物，所以转它不会把悬停提示和右键菜单一起转歪。
    //
    // 画布留白够（实测四个形象画到的范围约 185x185，居中），所以转 90°
    // 之后仍在窗口内，不会被切掉。
    property real poseAngle: 0          // 目标角度，来自 backend
    property real poseSwing: 0          // 倒挂时的晃动，叠加上去
    readonly property real petAngle: poseAngle + poseSwing

    // 转过去的时候「翻一下」，比线性转好看得多 —— 像真的翻身过去。
    //
    // overshoot 是缓动曲线自己的参数，必须写成 `easing.overshoot`；
    // 直接写 `overshoot:` 会被当成 NumberAnimation 的属性，报
    // 「Cannot assign to non-existent property」。
    Behavior on poseAngle {
        NumberAnimation {
            duration: 420
            easing.type: Easing.OutBack
            easing.overshoot: 1.3
        }
    }

    Item {
        id: stage
        anchors.fill: parent
        // 默认 transformOrigin 就是 Item.Center，绕画布中心转正合适
        rotation: win.petAngle

        Pet {
            id: pet
            width: win.designWidth
            height: win.designHeight
            scale: win.width / win.designWidth
            transformOrigin: Item.TopLeft
            style: backend.pet_style
            hovering: win.hovering
            mode: backend.focus.mode
            running: backend.focus.running
            ringProgress: backend.focus.progress
            badgeText: backend.petBadge
            eyeShiftX: win.eyeShiftX
            eyeShiftY: win.eyeShiftY
            mood: win.petMood
        }
    }

    // 倒挂的时候轻轻晃 —— 挂在那儿一动不动像张贴纸
    SequentialAnimation {
        running: backend.petHanging
        loops: Animation.Infinite
        NumberAnimation {
            target: win; property: "poseSwing"
            to: 2.6; duration: 1700; easing.type: Easing.InOutSine
        }
        NumberAnimation {
            target: win; property: "poseSwing"
            to: -2.6; duration: 1700; easing.type: Easing.InOutSine
        }
    }
    // 不挂了就把晃动收干净，否则下一次贴边会带着一个偏移量。
    // 这个处理并进上面那个 Connections（同一个信号，没必要开两块）。

    // 让姿势跟着贴边状态走。
    //
    // **不能挂 onPetEdgeChanged** —— `win` 本身没有 petEdge 属性，
    // 那个属性在 backend 上。贴边状态一变 backend 就会发
    // petGeometryChanged，下面那个 Connections 里已经有入口。
    function syncPose() {
        win.poseAngle = backend.petPoseAngle
    }

    // 眼睛跟随光标
    property real eyeShiftX: 0
    property real eyeShiftY: 0

    HoverHandler {
        id: hoverHandler
        onPointChanged: {
            var p = point.position
            var nx = (p.x / Math.max(1, win.width)) - 0.5
            var ny = (p.y / Math.max(1, win.height)) - 0.5
            var sx = Math.max(-2.5, Math.min(2.5, nx * 6))
            var sy = Math.max(-2.0, Math.min(2.0, ny * 5))

            // **眼神要按宠物自己的坐标系算。** 宠物转过角度之后，它的
            // 「上」不再是屏幕的上 —— 直接拿屏幕方向的偏移量喂进去，
            // 倒挂时眼睛会朝反方向看，侧躺时上下颠倒。
            // 这里把屏幕方向反过来转同样的角度，换到宠物的坐标系里。
            var a = win.poseAngle * Math.PI / 180
            var cos = Math.cos(a)
            var sin = Math.sin(a)
            win.eyeShiftX = sx * cos + sy * sin
            win.eyeShiftY = -sx * sin + sy * cos
        }
        onHoveredChanged: {
            win.hovering = hovered
            if (!hovered) {
                win.eyeShiftX = 0
                win.eyeShiftY = 0
            }
        }
    }

    DragHandler {
        id: dragHandler
        target: null
        acceptedButtons: Qt.LeftButton
        onActiveChanged: {
            if (active) {
                win.hovering = false
                win.startSystemMove()
            }
        }
    }

    // 单击 / 双击的分流。
    // Qt 的 TapHandler 在双击时会先发一次 tapped 再发 doubleTapped，
    // 所以单击动作要延迟一点点等一等，否则双击会先弹出指令栏再打开工作台，
    // 看起来就是闪一下。180ms 是感觉不到但又足够区分双击的间隔。
    TapHandler {
        id: tapHandler
        acceptedButtons: Qt.LeftButton
        gesturePolicy: TapHandler.WithinBounds

        onTapped: {
            pet.poke()
            win.idleSeconds = 0
            clickTimer.restart()
        }
        onDoubleTapped: {
            clickTimer.stop()
            backend.handlePetDoubleClick()
        }
    }

    Timer {
        id: clickTimer
        interval: 180
        onTriggered: backend.handlePetClick()
    }

    // 菜单是什么时候打开的。
    // requestActivate() 是异步的，Windows 在激活过程中可能抛出一个瞬时的
    // 失活事件。如果失活处理器不设防，菜单刚打开就会被自己关掉 ——
    // 表现为「右键偶尔没反应」。所以加一个宽限期，刚开的那几百毫秒内
    // 忽略失活。
    property double menuOpenedAt: 0
    readonly property int menuGraceMs: 400

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: function (mouse) {
            // 必须先激活窗口再开菜单。
            // 这个窗口是 Qt.Tool + 无边框，显示时不会自动获得焦点，
            // 而 QML 的 Menu 是个独立弹窗，它靠「弹出层抓取 + 失活通知」
            // 来实现「点别处关闭」和 Esc。窗口没焦点的话这两条路都断了，
            // 菜单就会一直挂在那里关不掉。
            win.requestActivate()
            contextMenu.x = mouse.x
            contextMenu.y = mouse.y
            win.menuOpenedAt = Date.now()
            contextMenu.open()
        }
    }

    // 窗口失活就关掉菜单（切到别的程序、按了 Alt+Tab 等等），
    // 但刚打开的那一小段时间不响应，避开上面说的激活竞态。
    onActiveChanged: {
        if (active)
            return
        if (!contextMenu.visible)
            return
        if (Date.now() - win.menuOpenedAt < win.menuGraceMs)
            return
        contextMenu.close()
    }

    // -------------------------------------------------------------- 悬停提示
    // 用自绘气泡而不是 Controls 的 ToolTip：ToolTip 是独立弹窗，
    // 在无边框透明窗口里位置和可见性都不好控制。
    Rectangle {
        id: tip
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: -34
        width: tipText.implicitWidth + 20
        height: 26
        radius: Theme.radiusSm
        color: Theme.surfaceHi
        border.width: 1
        border.color: Theme.border

        opacity: win.hovering && !contextMenu.visible ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: Theme.animNormal } }

        Text {
            id: tipText
            anchors.centerIn: parent
            text: backend.statusLine
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: Theme.fsSmall
        }
    }

    // -------------------------------------------------------------- 右键菜单
    Menu {
        id: contextMenu
        objectName: "petContextMenu"
        font.family: Theme.font
        font.pixelSize: Theme.fsBody
        // 点外部、按 Esc 都关闭。需要窗口可激活才能生效，见上面 MouseArea 的说明。
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            implicitWidth: 230
            color: Theme.surfaceHi
            radius: Theme.radiusMd
            border.width: 1
            border.color: Theme.border
        }

        component Item2: MenuItem {
            id: mi
            implicitHeight: 34
            contentItem: Text {
                leftPadding: 14
                text: mi.text
                font: mi.font
                color: mi.enabled ? Theme.text : Theme.textFaint
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: mi.highlighted ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
                radius: Theme.radiusSm
            }
        }

        Item2 {
            text: "✦  让小爪做事  " + backend.hotkey_ask.toUpperCase()
            onTriggered: backend.showCommandBar()
        }
        MenuSeparator {
            contentItem: Rectangle { implicitHeight: 1; color: Theme.border }
        }
        Item2 {
            text: backend.focus.running ? "⏸  暂停计时" : "▶  开始专注 " + backend.focus_minutes + " 分钟"
            onTriggered: backend.focus.toggle()
        }
        Item2 {
            text: "⏭  跳过当前阶段"
            onTriggered: backend.focus.skip()
        }
        MenuSeparator {
            contentItem: Rectangle { implicitHeight: 1; color: Theme.border }
        }
        Item2 {
            text: "🗒  打开工作台"
            onTriggered: backend.showDashboard("focus")
        }
        Item2 {
            text: "✅  快速添加待办"
            onTriggered: backend.showDashboard("tasks")
        }
        Item2 {
            text: "🔔  提醒设置"
            onTriggered: backend.showDashboard("reminders")
        }
        Item2 {
            text: "📋  复制今日总结"
            onTriggered: backend.exportSummary()
        }
        MenuSeparator {
            contentItem: Rectangle { implicitHeight: 1; color: Theme.border }
        }
        Item2 {
            text: "👁  " + backend.petVisibleLabel + "  " + backend.hotkey_dashboard.toUpperCase()
            onTriggered: backend.togglePet()
        }
        Item2 {
            text: "⚙  设置"
            onTriggered: backend.showDashboard("settings")
        }
        Item2 {
            text: "✖  退出"
            onTriggered: backend.quit()
        }
    }
}
