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
        color: Theme.surface
        radius: Theme.radiusXl
        border.width: 1
        border.color: Theme.borderSoft

        // 轻柔投影。浅色界面靠它和背景分层，比描边自然。
        // 原来这里是「左边一条 4px 的竖色条」—— 用户明确说不要，
        // 而且竖条会让气泡看起来像「告警框」而不是「说话」。
        Rectangle {
            anchors.fill: parent
            anchors.margins: -1
            z: -1
            radius: parent.radius + 1
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(bubble.accentColor.r, bubble.accentColor.g,
                                  bubble.accentColor.b, 0.18)
        }

        Column {
            id: col
            x: 16
            y: 13
            width: frame.width - 32
            spacing: 3

            // 小圆点代替原来的竖条：既标出类型，又不抢视线
            Row {
                spacing: 6
                Rectangle {
                    width: 7
                    height: 7
                    radius: 3.5
                    anchors.verticalCenter: parent.verticalCenter
                    color: bubble.accentColor
                }
                Text {
                    width: col.width - 13
                    text: bubble.headline
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    font.bold: true
                    wrapMode: Text.WordWrap
                }
            }

            Text {
                width: parent.width
                // **纯文本，绝不显示 Markdown。**
                // 气泡是「小爪在说话」，不是渲染文档 ——
                // 用户看到 **加粗** 这种记号只会觉得没做好。
                text: bubble.body
                textFormat: Text.PlainText
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.WordWrap
                lineHeight: 1.25
                visible: bubble.body.length > 0
                maximumLineCount: 4
                elide: Text.ElideRight
            }
        }
    }
}
