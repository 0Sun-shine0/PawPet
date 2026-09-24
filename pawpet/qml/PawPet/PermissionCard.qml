import QtQuick
import QtQuick.Layouts
import PawPet 1.0

Rectangle {
    id: card

    property string requestedLevel: ""
    property string requestedLevelWarning: ""

    function chooseLevel(key) {
        card.requestedLevel = ""
        backend.ai.requestLevelChange(key)
    }

    Connections {
        target: backend.ai

        function onLevelConfirmationRequested(key, warning) {
            card.requestedLevel = key
            card.requestedLevelWarning = warning
        }
    }

    Layout.fillWidth: true
    implicitHeight: levelColumn.implicitHeight + 24
    radius: Theme.radiusLg
    color: Theme.surface
    border.width: 1
    border.color: backend.ai.level === "full"
                  ? Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.5)
                  : Theme.borderSoft

    ColumnLayout {
        id: levelColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.margins: 12
        spacing: 8

        Text {
            text: "操作权限"
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: Theme.fsBody
            font.bold: true
        }

        Repeater {
            model: backend.ai.levelOptions
            delegate: Rectangle {
                Layout.fillWidth: true
                implicitHeight: 44
                radius: Theme.radiusMd
                color: backend.ai.level === modelData.key
                       ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.14)
                       : (levelMouse.containsMouse ? Theme.surfaceHi : "transparent")
                border.width: 1
                border.color: backend.ai.level === modelData.key
                              ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.5)
                              : "transparent"

                Behavior on color { ColorAnimation { duration: Theme.animFast } }

                MouseArea {
                    id: levelMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: card.chooseLevel(modelData.key)
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 8

                    Rectangle {
                        implicitWidth: 16
                        implicitHeight: 16
                        radius: 8
                        color: "transparent"
                        border.width: 2
                        border.color: backend.ai.level === modelData.key
                                      ? Theme.accent : Theme.border

                        Rectangle {
                            anchors.centerIn: parent
                            width: 8
                            height: 8
                            radius: 4
                            color: Theme.accent
                            visible: backend.ai.level === modelData.key
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        Text {
                            text: modelData.label
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                            font.bold: backend.ai.level === modelData.key
                        }

                        Text {
                            Layout.fillWidth: true
                            text: modelData.hint
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: backend.ai.level === "full"
            text: "当前处于完全自动：高危操作不会逐次询问。需要更安全时请选择“逐步确认”或“自动执行”。"
            color: Theme.rose
            font.family: Theme.font
            font.pixelSize: Theme.fsTiny
            wrapMode: Text.Wrap
        }

        Rectangle {
            Layout.fillWidth: true
            visible: card.requestedLevel !== ""
            implicitHeight: levelConfirmColumn.implicitHeight + 20
            radius: Theme.radiusMd
            color: Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.10)
            border.width: 1
            border.color: Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.55)

            ColumnLayout {
                id: levelConfirmColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 10
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: "确认启用完全自动？"
                    color: Theme.rose
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    font.bold: true
                }

                Text {
                    Layout.fillWidth: true
                    text: card.requestedLevelWarning
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    PawButton {
                        text: "确认启用"
                        glyph: "⚠"
                        variant: "danger"
                        onClicked: {
                            backend.ai.confirmLevelChange(card.requestedLevel)
                            card.requestedLevel = ""
                        }
                    }

                    PawButton {
                        text: "先不启用"
                        variant: "ghost"
                        onClicked: card.requestedLevel = ""
                    }
                }
            }
        }
    }
}
