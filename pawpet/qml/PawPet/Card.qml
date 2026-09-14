import QtQuick
import QtQuick.Layouts
import PawPet 1.0

/* 卡片容器：统一圆角、描边和内边距。 */
Rectangle {
    id: card

    default property alias contentData: body.data

    property string title: ""
    property string subtitle: ""
    property string badge: ""
    property color badgeColor: Theme.accent
    property int bodySpacing: 10

    color: Theme.surface
    radius: Theme.radiusLg
    border.width: 1
    border.color: Theme.borderSoft

    implicitHeight: outer.implicitHeight + Theme.pad * 2

    ColumnLayout {
        id: outer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.pad
        spacing: card.bodySpacing

        RowLayout {
            visible: card.title.length > 0
            Layout.fillWidth: true
            spacing: 8

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Text {
                    text: card.title
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTitle
                    font.bold: true
                }
                Text {
                    visible: card.subtitle.length > 0
                    text: card.subtitle
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                }
            }

            Rectangle {
                visible: card.badge.length > 0
                Layout.alignment: Qt.AlignTop
                implicitWidth: badgeText.implicitWidth + 16
                implicitHeight: 22
                radius: 11
                color: Qt.rgba(card.badgeColor.r, card.badgeColor.g, card.badgeColor.b, 0.16)

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: card.badge
                    color: card.badgeColor
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    font.bold: true
                }
            }
        }

        ColumnLayout {
            id: body
            Layout.fillWidth: true
            spacing: card.bodySpacing
        }
    }
}
