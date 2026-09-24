import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

Rectangle {
    id: mcpPanel
    objectName: "mcpCard"

    property bool showDetail: false
    signal detailVisibilityRequested(bool value)
    Layout.fillWidth: true
    implicitHeight: mcpColumn.implicitHeight + (showDetail ? 26 : 18)
    radius: Theme.radiusLg
    color: Theme.surface
    border.width: 1
    border.color: Theme.borderSoft

    ColumnLayout {
        id: mcpColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 14
        spacing: 8

        // 标题行。**永远显示** —— 状态点、工具数、开关都在这里，
        // 不点开也能一眼看到「连上没有」。
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "🧰"
                font.pixelSize: Theme.px(15)
            }
            Text {
                Layout.fillWidth: true
                text: "外部工具"
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
                font.bold: true
            }
            // 状态点 + 计数，一眼看出连上没有
            Rectangle {
                implicitWidth: 7
                implicitHeight: 7
                radius: 3.5
                color: {
                    if (!backend.ai.mcpEnabled)
                        return Theme.textFaint
                    var list = backend.ai.mcpServers
                    for (var i = 0; i < list.length; ++i)
                        if (list[i].running) return Theme.mint
                    return Theme.gold
                }
            }
            Text {
                text: backend.ai.mcpHint()
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
            }
            // 展开/收起。**默认收起** —— 详情（工具清单、路径）
            // 有 200px 高，常驻会把右栏撑到 2000px 以上，
            // 而「太占视野」正是用户明确抱怨过的。
            PawButton {
                small: true
                variant: "ghost"
                text: showDetail ? "收起" : "详情"
                onClicked: detailVisibilityRequested(!showDetail)
            }
            PawSwitch {
                checked: backend.ai.mcpEnabled
                onToggled: backend.ai.mcpEnabled = checked
            }
        }

        // server 列表
        Repeater {
            model: backend.ai.mcpServers

            delegate: Rectangle {
                Layout.fillWidth: true
                visible: showDetail && backend.ai.mcpEnabled
                implicitHeight: srvCol.implicitHeight + 14
                radius: Theme.radiusMd
                color: Theme.surfaceAlt
                border.width: 1
                border.color: modelData.running
                              ? Qt.rgba(Theme.mint.r, Theme.mint.g,
                                        Theme.mint.b, 0.45)
                              : Theme.borderSoft

                ColumnLayout {
                    id: srvCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 3

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Text {
                            Layout.fillWidth: true
                            text: modelData.name
                            color: Theme.text
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.fsSmall
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: modelData.running
                                  ? ("已连接 · " + modelData.toolCount + " 个工具")
                                  : "未连接"
                            color: modelData.running
                                   ? Theme.mint : Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: modelData.toolCount > 0
                        text: modelData.tools.join("、")
                        color: Theme.textDim
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                        lineHeight: 1.3
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: showDetail && backend.ai.mcpEnabled

            PawButton {
                small: true
                text: "连接"
                variant: "subtle"
                enabled: !backend.ai.running
                onClicked: backend.ai.connectMcp()
            }
            PawButton {
                small: true
                text: "断开"
                variant: "ghost"
                onClicked: backend.ai.disconnectMcp()
            }
            Item { Layout.fillWidth: true }
        }

        Text {
            Layout.fillWidth: true
            visible: showDetail && !backend.ai.mcpEnabled
            text: "关着。打开之后，下面这些外部工具就能被小爪调用 —— "
                  + "它们是独立的进程，能做的事由各自的配置决定。"
            color: Theme.textFaint
            font.family: Theme.font
            font.pixelSize: Theme.fsTiny
            wrapMode: Text.Wrap
            lineHeight: 1.35
        }

        Text {
            Layout.fillWidth: true
            visible: showDetail
            text: "想加自己的 server？改这个文件：" + backend.ai.mcpConfigPath
            color: Theme.textFaint
            font.family: Theme.fontMono
            font.pixelSize: Theme.fsTiny
            elide: Text.ElideMiddle
        }
    }
}
