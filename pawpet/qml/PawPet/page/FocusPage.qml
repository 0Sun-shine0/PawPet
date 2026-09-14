import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 专注页：大圆环计时器 + 控制 + 快捷时长 + 轮次设置。 */
Flickable {
    id: page
    contentWidth: width
    contentHeight: column.implicitHeight + 24
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    readonly property color modeColor: Theme.modeColor(backend.focus.mode)

    ColumnLayout {
        id: column
        x: 0
        y: 12
        width: page.width
        spacing: Theme.gap

        // ------------------------------------------------------ 计时圆环
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            implicitHeight: ringBox.implicitHeight + 48
            radius: Theme.radiusXl
            color: Theme.surface
            border.width: 1
            border.color: Qt.rgba(page.modeColor.r, page.modeColor.g, page.modeColor.b, 0.35)

            ColumnLayout {
                id: ringBox
                anchors.centerIn: parent
                spacing: 14

                Item {
                    id: ringItem
                    objectName: "focusRing"
                    Layout.alignment: Qt.AlignHCenter
                    implicitWidth: 260
                    implicitHeight: 260

                    Canvas {
                        id: ring
                        objectName: "focusRingCanvas"
                        anchors.fill: parent
                        antialiasing: true
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            var cx = width / 2
                            var cy = height / 2
                            var r = width / 2 - 16

                            // 轨道
                            ctx.beginPath()
                            ctx.arc(cx, cy, r, 0, Math.PI * 2, false)
                            ctx.strokeStyle = Theme.surfaceHi
                            ctx.lineWidth = 16
                            ctx.stroke()

                            // 进度
                            var p = Math.max(0, Math.min(1, backend.focus.progress))
                            if (p > 0.0005) {
                                ctx.beginPath()
                                ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p, false)
                                ctx.strokeStyle = page.modeColor
                                ctx.lineWidth = 16
                                ctx.lineCap = "round"
                                ctx.stroke()

                                // 进度头部的小圆点
                                var angle = -Math.PI / 2 + Math.PI * 2 * p
                                ctx.beginPath()
                                ctx.arc(cx + r * Math.cos(angle), cy + r * Math.sin(angle), 9, 0, Math.PI * 2, false)
                                ctx.fillStyle = "#ffffff"
                                ctx.fill()
                                ctx.strokeStyle = page.modeColor
                                ctx.lineWidth = 3
                                ctx.stroke()
                            }

                            // 内圈淡淡的背景
                            ctx.beginPath()
                            ctx.arc(cx, cy, r - 15, 0, Math.PI * 2, false)
                            ctx.fillStyle = Theme.surfaceAlt
                            ctx.fill()
                        }
                    }

                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 4

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: backend.focus.modeLabel
                            color: page.modeColor
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            font.bold: true
                        }
                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: backend.focus.clock
                            color: Theme.text
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.fsClock
                            font.bold: true
                        }
                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: "第 " + (backend.focus.round + 1) + " 轮 · "
                                  + backend.focus.stateLabel
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                        }
                    }

                    Connections {
                        target: backend.focus
                        function onTick() { ring.requestPaint() }
                    }
                }

                // -------------------------------------------------- 主控制
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 10

                    PawButton {
                        text: backend.focus.running ? "暂停" : (backend.focus.progress > 0 ? "继续" : "开始")
                        glyph: backend.focus.running ? "⏸" : "▶"
                        variant: "primary"
                        implicitWidth: 108
                        implicitHeight: 40
                        onClicked: backend.focus.toggle()
                    }
                    PawButton {
                        text: "重置"
                        glyph: "↺"
                        implicitHeight: 40
                        onClicked: backend.focus.reset()
                    }
                    PawButton {
                        text: "跳过"
                        glyph: "⏭"
                        implicitHeight: 40
                        variant: "ghost"
                        onClicked: backend.focus.skip()
                    }
                }
            }
        }

        // ------------------------------------------------------ 快捷时长
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "这一轮要多久"
            subtitle: "点一下立刻生效，正在计时也可以改"

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Repeater {
                    model: [15, 25, 45, 60, 90]

                    delegate: PawButton {
                        Layout.fillWidth: true
                        text: modelData + " 分"
                        variant: backend.focus_minutes === modelData && backend.focus.mode === "focus"
                                 ? "primary" : "subtle"
                        onClicked: backend.focus.setMinutes(modelData)
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: "休息时长"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                }
                Item { Layout.fillWidth: true }
                Repeater {
                    model: [3, 5, 10, 15]
                    delegate: PawButton {
                        small: true
                        text: modelData + " 分"
                        variant: backend.short_break_minutes === modelData ? "accent" : "subtle"
                        onClicked: backend.short_break_minutes = modelData
                    }
                }
            }
        }

        // ------------------------------------------------------ 自动化
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "自动化"
            subtitle: "少点几下，多专注一会儿"

            PawSwitch {
                Layout.fillWidth: true
                text: "一轮结束后自动开始下一阶段"
                checked: backend.auto_start_next
                onToggled: backend.auto_start_next = checked
            }
            PawSwitch {
                Layout.fillWidth: true
                text: "提示音（结束和提醒时响一下）"
                checked: backend.sound_enabled
                enabled: backend.soundAvailable
                onToggled: backend.sound_enabled = checked
            }
            PawSwitch {
                Layout.fillWidth: true
                text: "系统通知与气泡"
                checked: backend.notify_enabled
                onToggled: backend.notify_enabled = checked
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Text {
                    text: "每几轮进入一次长休息"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                Item { Layout.fillWidth: true }
                PawButton {
                    small: true
                    text: "−"
                    implicitWidth: 34
                    onClicked: backend.rounds_before_long_break =
                               Math.max(2, backend.rounds_before_long_break - 1)
                }
                Text {
                    text: backend.rounds_before_long_break
                    color: Theme.text
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsTitle
                    font.bold: true
                }
                PawButton {
                    small: true
                    text: "+"
                    implicitWidth: 34
                    onClicked: backend.rounds_before_long_break =
                               Math.min(12, backend.rounds_before_long_break + 1)
                }
                Text {
                    text: "长休息 " + backend.long_break_minutes + " 分"
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                }
            }

            PawButton {
                text: "试一下提醒效果"
                glyph: "🔔"
                variant: "ghost"
                onClicked: backend.testNotification()
            }
        }

        Item { Layout.preferredHeight: 12 }
    }
}
