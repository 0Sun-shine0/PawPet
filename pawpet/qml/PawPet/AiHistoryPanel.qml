import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

Rectangle {
    id: card

    property bool expanded: false
    signal clearHistoryRequested()
    signal openConversationRequested(string sessionId)
    signal deleteConversationRequested(string sessionId)

    objectName: "historyCard"
    Layout.fillWidth: true
    visible: expanded
    implicitHeight: historyColumn.implicitHeight + 26
    radius: Theme.radiusLg
    color: Theme.surface
    border.width: 1
    border.color: Theme.border

    ColumnLayout {
        id: historyColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 14
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: backend.ai.conversationCount > 0
                      ? ("历史对话（" + backend.ai.conversationCount + " 段）")
                      : "历史对话"
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                font.bold: true
            }
            PawButton {
                small: true
                variant: "ghost"
                text: "清空全部"
                enabled: backend.ai.conversationCount > 1
                onClicked: card.clearHistoryRequested()
            }
        }

        Text {
            Layout.fillWidth: true
            visible: backend.ai.conversationCount === 0
            text: "还没有历史。对话会自动存下来，关掉小爪再打开也还在。"
            color: Theme.textFaint
            font.family: Theme.font
            font.pixelSize: Theme.fsTiny
            wrapMode: Text.Wrap
        }

        Repeater {
            model: {
                var all = backend.ai.conversations
                return all.length > 12 ? all.slice(0, 12) : all
            }

            delegate: Rectangle {
                Layout.fillWidth: true
                implicitHeight: convCol.implicitHeight + 14
                radius: Theme.radiusMd
                color: modelData.isCurrent ? Theme.surfaceHi
                                           : (convMouse.containsMouse
                                              ? Theme.surfaceHi
                                              : Theme.surfaceAlt)
                border.width: 1
                border.color: modelData.isCurrent
                              ? Qt.rgba(Theme.accent.r, Theme.accent.g,
                                        Theme.accent.b, 0.5)
                              : Theme.borderSoft

                Behavior on color { ColorAnimation { duration: Theme.animFast } }

                ColumnLayout {
                    id: convCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 10
                    anchors.rightMargin: 8
                    spacing: 2

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Text {
                            Layout.fillWidth: true
                            text: modelData.title || "（没有标题）"
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                            font.bold: modelData.isCurrent
                            elide: Text.ElideRight
                        }
                        Text {
                            visible: modelData.isCurrent
                            text: "当前"
                            color: Theme.accent
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                            font.bold: true
                        }
                        Text {
                            text: modelData.count + " 条"
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                        Rectangle {
                            implicitWidth: 18
                            implicitHeight: 18
                            radius: 9
                            color: delMouse.containsMouse
                                   ? Theme.surfaceHi : "transparent"
                            Text {
                                anchors.centerIn: parent
                                text: "✕"
                                color: delMouse.containsMouse
                                       ? Theme.rose : Theme.textFaint
                                font.family: Theme.fontLatin
                                font.pixelSize: Theme.px(10)
                            }
                            MouseArea {
                                id: delMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: card.deleteConversationRequested(modelData.id)
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData.preview
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        elide: Text.ElideRight
                    }
                }

                MouseArea {
                    id: convMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    enabled: !modelData.isCurrent
                    onClicked: card.openConversationRequested(modelData.id)
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: "存在本地：" + backend.ai.historyFolder()
            color: Theme.textFaint
            font.family: Theme.fontMono
            font.pixelSize: Theme.fsTiny
            elide: Text.ElideMiddle
        }
    }
}
