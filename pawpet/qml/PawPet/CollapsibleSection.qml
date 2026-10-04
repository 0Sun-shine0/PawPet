import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

Item {
    id: section

    default property alias contentData: content.data
    property string title: ""
    property string subtitle: ""
    property bool expanded: true
    property bool forcedExpanded: false
    readonly property bool open: forcedExpanded || expanded

    Layout.fillWidth: true
    implicitHeight: headerShell.implicitHeight
                   + (open ? Theme.gap + content.implicitHeight : 0)

    Rectangle {
        id: headerShell
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        implicitHeight: headerText.implicitHeight + 20
        radius: Theme.radiusMd
        color: Theme.surfaceAlt
        border.width: 1
        border.color: section.open ? Theme.border : Theme.borderSoft

        RowLayout {
            id: headerText
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Theme.space(12)
            anchors.rightMargin: Theme.space(8)
            spacing: 8

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Text {
                    Layout.fillWidth: true
                    text: section.title
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    font.bold: true
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    visible: section.subtitle.length > 0
                    text: section.subtitle
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    elide: Text.ElideRight
                }
            }

            Text {
                text: section.open ? "⌃" : "⌄"
                color: section.forcedExpanded ? Theme.textFaint : Theme.accent
                font.family: Theme.fontLatin
                font.pixelSize: Theme.fsBody
                font.bold: true
            }
        }

        Button {
            id: toggleButton
            objectName: "collapseToggle"
            anchors.fill: parent
            enabled: !section.forcedExpanded
            text: ""
            background: null
            Accessible.name: section.open ? "收起" + section.title
                                          : "展开" + section.title
            onClicked: section.expanded = !section.expanded
        }
    }

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: headerShell.bottom
        anchors.topMargin: Theme.gap
        visible: section.open
        enabled: section.open
        spacing: Theme.gap
        clip: true
    }
}
