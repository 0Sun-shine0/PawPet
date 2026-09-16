import QtQuick
import QtQuick.Controls
import PawPet 1.0

/* 开关。比系统 Switch 更贴合这套配色，动画也更顺。 */
Switch {
    id: control

    font.family: Theme.font
    font.pixelSize: Theme.fsBody
    hoverEnabled: true
    implicitHeight: 26

    indicator: Rectangle {
        id: track
        implicitWidth: 44
        implicitHeight: 24
        x: control.text.length > 0 ? control.leftPadding : 0
        y: parent.height / 2 - height / 2
        radius: height / 2
        color: control.checked ? control.checkedColor : Theme.surfaceHi
        border.width: 1
        border.color: control.checked ? Qt.rgba(0, 0, 0, 0.06) : Theme.border

        Behavior on color { ColorAnimation { duration: Theme.animFast } }

        Rectangle {
            id: knob
            width: 18
            height: 18
            radius: 9
            y: 2
            x: control.checked ? track.width - width - 3 : 3
            // 关的时候原来用 #8b83a8（深色主题的灰紫），在粉白底上像块脏点。
            // 改成白球 + 淡描边，一眼能看出「这是个可以拨的开关」。
            color: "#ffffff"
            border.width: control.checked ? 0 : 1
            border.color: Theme.border

            Behavior on x {
                NumberAnimation { duration: Theme.animNormal; easing.type: Easing.OutBack; easing.overshoot: 1.4 }
            }
            Behavior on color { ColorAnimation { duration: Theme.animFast } }
        }
    }

    property color checkedColor: Theme.accent
    HoverHandler { cursorShape: Qt.PointingHandCursor }

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? Theme.text : Theme.textFaint
        verticalAlignment: Text.AlignVCenter
        leftPadding: control.indicator.width + 10
    }

    opacity: enabled ? 1.0 : 0.6
}
