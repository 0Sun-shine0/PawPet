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

    // -------------------------------------------------------------- 状态
    function clampToScreen() {
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

    function restorePosition() {
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

    onXChanged: if (positionReady) savePositionTimer.restart()
    onYChanged: if (positionReady) savePositionTimer.restart()

    // ------------------------------------------------------------- 宠物
    Item {
        id: stage
        anchors.fill: parent

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
        }
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
            win.eyeShiftX = Math.max(-2.5, Math.min(2.5, nx * 6))
            win.eyeShiftY = Math.max(-2.0, Math.min(2.0, ny * 5))
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
