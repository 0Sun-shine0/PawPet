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
        // 主色按钮（暖粉/玫红）是中间调，深色字会糊、白字才立得住
        case "primary": return "#ffffff"
        case "accent":  return "#ffffff"
        case "danger":  return "#ffffff"
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
            font.pixelSize: control.small ? Theme.px(11) : Theme.px(13)
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
            // 注意：底色是浅色，所以「禁用/悬停」的覆盖层必须是深色半透明。
            // 原来写的是 Qt.rgba(1,1,1,...)（白色覆盖层），那是深色主题的写法，
            // 在粉白底上等于什么都没画。
            if (!control.enabled)
                return control.variant === "ghost" ? "transparent"
                                                   : Qt.rgba(control._bg.r, control._bg.g, control._bg.b, 0.38)
            if (control.pressed)
                return control.variant === "ghost"
                       ? Qt.rgba(0, 0, 0, 0.06)
                       : Qt.darker(control._bg, 1.14)
            if (control.hovered)
                return control.variant === "ghost"
                       ? Theme.surfaceHi
                       : Qt.darker(control._bg, 1.06)
            return control._bg
        }
        border.width: control.variant === "ghost" ? 0 : 1
        border.color: Qt.rgba(0, 0, 0, 0.05)

        Behavior on color { ColorAnimation { duration: Theme.animFast } }
    }

    HoverHandler { cursorShape: Qt.PointingHandCursor }
}
