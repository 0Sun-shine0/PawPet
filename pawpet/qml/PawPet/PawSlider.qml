import QtQuick
import QtQuick.Controls
import PawPet 1.0

/* 滑块。Basic 样式下自己画轨道和把手，保证和整体配色一致。 */
Slider {
    id: control

    property string suffix: ""
    property int decimals: 0

    implicitHeight: 30
    implicitWidth: 180
    hoverEnabled: true

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 6
        radius: 3
        color: Theme.surfaceHi

        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: 3
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Theme.violet }
                GradientStop { position: 1.0; color: Theme.accent }
            }
        }
    }

    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 20
        implicitHeight: 20
        radius: 10
        color: control.pressed ? Theme.accent : "#ffffff"
        border.width: 2
        border.color: Theme.violet

        Behavior on color { ColorAnimation { duration: Theme.animFast } }

        scale: control.pressed ? 1.15 : 1.0
        Behavior on scale { NumberAnimation { duration: Theme.animFast } }
    }

    // 自绘数值气泡：Controls 的 ToolTip 是独立弹窗，在 Flickable 里位置会飘
    Rectangle {
        id: valueBubble
        visible: control.pressed
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: -28
        width: bubbleText.implicitWidth + 16
        height: 22
        radius: 11
        color: Theme.surfaceHi
        border.width: 1
        border.color: Theme.accent

        Text {
            id: bubbleText
            anchors.centerIn: parent
            text: control.displayValue + control.suffix
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: Theme.fsSmall
        }
    }

    readonly property string displayValue: decimals > 0 ? control.value.toFixed(decimals) : String(Math.round(control.value))
}
