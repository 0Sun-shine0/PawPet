import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 今日概览：问候、大时钟、当前状态、近 7 天柱状图。
   放在最前面，打开面板第一眼就能看到「今天做了多少」。 */
Flickable {
    id: page
    contentWidth: width
    contentHeight: column.implicitHeight + 24
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    ColumnLayout {
        id: column
        x: 0
        y: 12
        width: page.width
        spacing: Theme.gap

        // ------------------------------------------------------ 问候卡片
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            implicitHeight: head.implicitHeight + 40
            radius: Theme.radiusXl
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "#2b2440" }
                GradientStop { position: 1.0; color: "#1f2b3a" }
            }
            border.width: 1
            border.color: Theme.border

            RowLayout {
                id: head
                anchors.fill: parent
                anchors.margins: 20
                spacing: 16

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        text: backend.greeting + "，" + backend.dateText
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsH1
                        font.bold: true
                    }
                    Text {
                        text: "现在是 " + backend.clockText + " · " + backend.statusLine
                        color: Theme.textDim
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                    }
                    Text {
                        text: backend.todayLine
                        color: Theme.accent
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }
                }

                // 大时钟
                ColumnLayout {
                    spacing: 0
                    Text {
                        Layout.alignment: Qt.AlignRight
                        text: backend.clockText
                        color: Theme.text
                        font.family: Theme.fontMono
                        font.pixelSize: 46
                        font.bold: true
                    }
                    Text {
                        Layout.alignment: Qt.AlignRight
                        text: backend.focus.stateLabel
                        color: backend.focus.running ? Theme.mint : Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }
                }
            }
        }

        // ------------------------------------------------------ 今日目标
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "今日专注目标"
            subtitle: backend.focus.todayMinutes + " / " + backend.focus.dailyGoal + " 分钟"
            badge: Math.round(backend.focus.goalProgress * 100) + "%"
            badgeColor: Theme.accent

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 12
                radius: 6
                color: Theme.surfaceHi

                Rectangle {
                    width: Math.max(0, Math.min(parent.width, parent.width * backend.focus.goalProgress))
                    height: parent.height
                    radius: 6
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: Theme.accent }
                        GradientStop { position: 1.0; color: Theme.gold }
                    }
                    Behavior on width {
                        NumberAnimation { duration: Theme.animSlow; easing.type: Theme.easing }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 18
                StatChip { value: String(backend.focus.todayRounds); label: "专注轮数"; color: Theme.violet }
                StatChip { value: String(backend.focus.todayTasksDone); label: "完成待办"; color: Theme.mint }
                StatChip { value: String(backend.tasks.pendingCount); label: "待办剩余"; color: Theme.accent }
                Item { Layout.fillWidth: true }
            }
        }

        // ------------------------------------------------------ 近 7 天
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "近 7 天专注"
            subtitle: "柱高 = 当天的专注分钟数"

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Repeater {
                    model: backend.week
                    delegate: ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Item {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 96
                            Layout.alignment: Qt.AlignBottom

                            Rectangle {
                                anchors.bottom: parent.bottom
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: Math.min(parent.width - 4, 26)
                                height: Math.max(model.minutes > 0 ? 4 : 2,
                                                 parent.height * Math.max(model.minutes > 0 ? 0.06 : 0.02, model.ratio))
                                radius: 7
                                color: model.isToday ? Theme.accent
                                                     : Qt.rgba(Theme.violet.r, Theme.violet.g, Theme.violet.b, 0.75)

                                Behavior on height {
                                    NumberAnimation { duration: Theme.animSlow; easing.type: Theme.easing }
                                }

                                Text {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    anchors.bottom: parent.top
                                    anchors.bottomMargin: 3
                                    text: model.minutes > 0 ? model.minutes : ""
                                    color: Theme.textDim
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                }
                            }
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: model.dayLabel
                            color: model.isToday ? Theme.accent : Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                            font.bold: model.isToday
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------ 当前状态
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "健康提醒"
            subtitle: backend.sitStatus
            badge: backend.sit_reminder_enabled ? "已开启" : "已关闭"
            badgeColor: backend.sit_reminder_enabled ? Theme.mint : Theme.textFaint

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 8
                radius: 4
                color: Theme.surfaceHi

                Rectangle {
                    width: Math.max(0, Math.min(parent.width, parent.width * backend.sitProgress))
                    height: parent.height
                    radius: 4
                    color: backend.sitProgress > 0.85 ? Theme.rose : Theme.mint
                    Behavior on width {
                        NumberAnimation { duration: Theme.animSlow; easing.type: Theme.easing }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                PawButton {
                    text: "我刚刚休息过了"
                    glyph: "✓"
                    onClicked: backend.resetSitTimer()
                }
                PawButton {
                    text: "去设置里调整"
                    variant: "ghost"
                    onClicked: backend.showDashboard("settings")
                }
                Item { Layout.fillWidth: true }
            }
        }

        // ------------------------------------------------------ 接下来
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            visible: backend.upcoming.length > 0
            title: "接下来"
            subtitle: "最近的几条定时提醒"

            Repeater {
                model: backend.upcoming
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: "⏰"
                        font.pixelSize: Theme.fsBody
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData
                        color: Theme.textDim
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        elide: Text.ElideRight
                    }
                }
            }
        }

        // ------------------------------------------------------ 最近专注
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "最近的专注"
            subtitle: backend.sessions.recent.length > 0 ? "离线期间跑完的轮次会标记为「离线补记」"
                                                        : "还没有记录，开始第一轮吧"
            visible: backend.sessions.recent.length > 0

            Repeater {
                model: backend.sessions.recent
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Text {
                        text: modelData.when
                        color: Theme.textFaint
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fsSmall
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData.label
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        elide: Text.ElideRight
                    }
                    Text {
                        text: modelData.minutes + " 分钟"
                        color: Theme.accent
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                        font.bold: true
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 12 }
    }

    // 统计小方块
    component StatChip: ColumnLayout {
        property string value: "0"
        property string label: ""
        property color color: Theme.accent
        spacing: 2

        Text {
            text: parent.value
            color: parent.color
            font.family: Theme.fontMono
            font.pixelSize: Theme.fsH1
            font.bold: true
        }
        Text {
            text: parent.label
            color: Theme.textFaint
            font.family: Theme.font
            font.pixelSize: Theme.fsSmall
        }
    }
}
