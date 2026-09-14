import QtQuick
import QtQuick.Controls
import PawPet 1.0

/* 宠物旁边的对话气泡。
   独立窗口 + WindowTransparentForInput，所以永远不会挡住鼠标点击。 */
Window {
    id: bubble

    property string headline: ""
    property string body: ""
    property string kind: "info"

    readonly property color accentColor: {
        switch (kind) {
        case "sit":         return Theme.mint
        case "focus_done":  return Theme.accent
        case "break_done":  return Theme.violet
        case "reminder":    return Theme.gold
        case "task_done":   return Theme.mint
        default:            return Theme.violet
        }
    }

    // 高度由内容撑开：9*2 外边距 + 14*2 内边距。
    // 注意不要用 frame.implicitHeight，那会和 anchors.fill 形成循环绑定。
    width: 300
    height: Math.max(58, col.height + 46)
    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.Tool
           | (backend.petAlwaysOnTop ? Qt.WindowStaysOnTopHint : 0)
           | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
    title: "小爪提示"

    function show(titleText, bodyText, kindName, petX, petY, petW, petH) {
        headline = titleText
        body = bodyText
        kind = kindName || "info"
        place(petX, petY, petW, petH)
        visible = true
        anim.restart()
        hideTimer.restart()
    }

    function place(petX, petY, petW, petH) {
        // Screen 附加类型没有 availableGeometry，用 backend 里的 QScreen 结果
        var area = backend.screenAt(Math.round(petX), Math.round(petY))
        var x = petX + petW / 2 - width / 2
        var y = petY - height - 6
        if (y < area.y + 8) {
            // 上方放不下就放到宠物下面
            y = petY + petH + 6
        }
        x = Math.max(area.x + 8, Math.min(area.x + area.width - width - 8, x))
        y = Math.max(area.y + 8, Math.min(area.y + area.height - height - 8, y))
        bubble.x = Math.round(x)
        bubble.y = Math.round(y)
    }

    Timer {
        id: hideTimer
        interval: 7000
        onTriggered: fadeOut.start()
    }

    NumberAnimation {
        id: fadeOut
        target: bubble
        property: "opacity"
        to: 0
        duration: Theme.animSlow
        onFinished: bubble.visible = false
    }

    SequentialAnimation {
        id: anim
        PropertyAction { target: bubble; property: "opacity"; value: 0 }
        ParallelAnimation {
            NumberAnimation { target: bubble; property: "opacity"; to: 1; duration: Theme.animNormal }
            NumberAnimation { target: frame; property: "scale"; from: 0.9; to: 1; duration: Theme.animNormal; easing.type: Easing.OutBack }
        }
    }

    Rectangle {
        id: frame
        anchors.fill: parent
        anchors.margins: 9
        color: Theme.surfaceHi
        radius: Theme.radiusLg
        border.width: 1
        border.color: Qt.rgba(bubble.accentColor.r, bubble.accentColor.g, bubble.accentColor.b, 0.55)

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 4
            radius: 2
            color: bubble.accentColor
        }

        Column {
            id: col
            x: 18
            y: 14
            width: frame.width - 32
            spacing: 4

            Text {
                width: parent.width
                text: bubble.headline
                color: bubble.accentColor
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
                font.bold: true
                wrapMode: Text.WordWrap
            }
            Text {
                width: parent.width
                text: bubble.body
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.WordWrap
                visible: bubble.body.length > 0
            }
        }
    }
}
