import QtQuick
import QtQuick.Controls
import PawPet 1.0

/* 统一样式的输入框。 */
TextField {
    id: control

    property string label: ""

    implicitHeight: 34
    color: Theme.text
    placeholderTextColor: Theme.textFaint
    font.family: Theme.font
    font.pixelSize: Theme.fsBody
    selectByMouse: true
    leftPadding: 11
    rightPadding: 11

    background: Rectangle {
        radius: Theme.radiusMd
        color: Theme.surfaceAlt
        border.width: 1
        border.color: control.activeFocus ? Theme.accent : Theme.border

        Behavior on border.color { ColorAnimation { duration: Theme.animFast } }
    }

    HoverHandler { cursorShape: Qt.IBeamCursor }
}
