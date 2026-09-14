import QtQuick
import QtQuick.Controls
import PawPet 1.0

/* 通用按钮。variant 决定配色，glyph 是可选的图标字符。 */
Button {
    id: control

    property string variant: "subtle"     // primary | accent | subtle | ghost | danger
    property string glyph: ""
    property bool small: false

    implicitHeight: small ? 28 : 34
    implicitWidth: Math.max(small ? 60 : 76, row.implicitWidth + (small ? 20 : 28))
    padding: 0
    font.family: Theme.font
    font.pixelSize: small ? Theme.fsSmall : Theme.fsBody
    hoverEnabled: true

    readonly property color _bg: {
        switch (variant) {
        case "primary": return Theme.accent
        case "accent":  return Theme.violet
        case "danger":  return Theme.rose
        case "ghost":   return "transparent"
        default:        return Theme.surfaceHi
        }
    }
    readonly property color _fg: {
        switch (variant) {
        case "primary": return "#2a1c16"
        case "accent":  return "#ffffff"
        case "danger":  return "#2a1218"
        case "ghost":   return Theme.textDim
        default:        return Theme.text
        }
    }

    contentItem: Row {
        id: row
        spacing: 6
        anchors.centerIn: parent

        Text {
            visible: control.glyph.length > 0
            anchors.verticalCenter: parent.verticalCenter
            text: control.glyph
            color: control._fg
            font.family: Theme.fontLatin
            font.pixelSize: control.small ? 11 : 13
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: control._fg
            font: control.font
            opacity: control.enabled ? 1.0 : 0.45
        }
    }

    background: Rectangle {
        radius: control.small ? Theme.radiusSm : Theme.radiusMd
        color: {
            if (!control.enabled)
                return control.variant === "ghost" ? "transparent" : Qt.rgba(1, 1, 1, 0.05)
            if (control.pressed)
                return Qt.darker(control._bg, control.variant === "ghost" ? 1.0 : 1.25)
            if (control.hovered)
                return control.variant === "ghost"
                       ? Qt.rgba(1, 1, 1, 0.07)
                       : Qt.lighter(control._bg, 1.12)
            return control._bg
        }
        border.width: control.variant === "ghost" ? 0 : 1
        border.color: Qt.rgba(1, 1, 1, 0.10)

        Behavior on color { ColorAnimation { duration: Theme.animFast } }
    }

    HoverHandler { cursorShape: Qt.PointingHandCursor }
}
