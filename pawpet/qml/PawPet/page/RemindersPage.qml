import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 提醒页：定时提醒 + 久坐提醒。
   旧版只有一个「30/45/60/90 循环切换」的按钮，这里做成真正能用的提醒系统。 */
Flickable {
    id: page
    contentWidth: width
    contentHeight: column.implicitHeight + 24
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    property int repeatIndex: 1

    ColumnLayout {
        id: column
        x: 0
        y: 12
        width: page.width
        spacing: Theme.gap

        // ------------------------------------------------------ 新建提醒
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "新建定时提醒"
            subtitle: "到点会弹气泡 + 托盘通知，需要的话还会响一声"

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                PawField {
                    id: titleInput
                    Layout.fillWidth: true
                    placeholderText: "提醒内容，比如「喝水」「站起来走走」"
                    onAccepted: page.addReminder()
                }

                PawField {
                    id: timeInput
                    Layout.preferredWidth: 96
                    horizontalAlignment: TextInput.AlignHCenter
                    text: "09:00"
                    font.family: Theme.fontMono
                    inputMask: "99:99"
                    onAccepted: page.addReminder()
                }

                ComboBox {
                    id: repeatBox
                    Layout.preferredWidth: 108
                    implicitHeight: 34
                    model: ["仅一次", "每天", "工作日", "每周"]
                    currentIndex: page.repeatIndex
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    onActivated: page.repeatIndex = currentIndex

                    contentItem: Text {
                        leftPadding: 11
                        text: repeatBox.displayText
                        color: Theme.text
                        font: repeatBox.font
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: Theme.surfaceAlt
                        radius: Theme.radiusMd
                        border.width: 1
                        border.color: repeatBox.activeFocus || repeatBox.hovered ? Theme.accent : Theme.border
                    }
                    indicator: Text {
                        x: repeatBox.width - width - 10
                        anchors.verticalCenter: parent.verticalCenter
                        text: "▾"
                        color: Theme.textDim
                        font.pixelSize: 12
                    }
                    popup: Popup {
                        y: repeatBox.height + 4
                        width: repeatBox.width
                        implicitHeight: contentItem.implicitHeight + 8
                        padding: 4
                        background: Rectangle {
                            color: Theme.surfaceHi
                            radius: Theme.radiusMd
                            border.width: 1
                            border.color: Theme.border
                        }
                        contentItem: ListView {
                            clip: true
                            implicitHeight: contentHeight
                            model: repeatBox.popup.visible ? repeatBox.delegateModel : null
                            currentIndex: repeatBox.highlightedIndex
                        }
                    }
                    delegate: ItemDelegate {
                        id: repeatItem
                        width: repeatBox.width - 8
                        implicitHeight: 30
                        contentItem: Text {
                            leftPadding: 8
                            text: repeatItem.text
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: repeatItem.highlighted ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
                            radius: Theme.radiusSm
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                PawButton {
                    text: "添加提醒"
                    glyph: "＋"
                    variant: "primary"
                    onClicked: page.addReminder()
                }
                Text {
                    text: "时间用 24 小时制，例如 09:30 / 21:00"
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                }
                Item { Layout.fillWidth: true }
            }
        }

        // ------------------------------------------------------ 提醒列表
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "我的提醒"
            subtitle: backend.reminders.count > 0 ? "共 " + backend.reminders.count + " 条，其中 "
                                                   + backend.reminders.activeCount + " 条已开启"
                                                 : "还没有提醒"
            badge: backend.reminders.activeCount > 0 ? String(backend.reminders.activeCount) : ""
            badgeColor: Theme.gold

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                visible: backend.reminders.count === 0

                Text {
                    text: "⏰"
                    font.pixelSize: 34
                    Layout.alignment: Qt.AlignHCenter
                }
                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: "上面加一条吧，比如「每天 10:30 起来走走」"
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    wrapMode: Text.Wrap
                }
            }

            Repeater {
                model: backend.reminders

                delegate: Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 56
                    radius: Theme.radiusMd
                    color: reminderMouse.containsMouse ? Theme.surfaceHi : "transparent"
                    border.width: 1
                    border.color: reminderMouse.containsMouse ? Theme.border : "transparent"

                    Behavior on color { ColorAnimation { duration: Theme.animFast } }

                    MouseArea {
                        id: reminderMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 12

                        Text {
                            text: model.time
                            color: model.enabled ? Theme.gold : Theme.textFaint
                            font.family: Theme.fontMono
                            font.pixelSize: 22
                            font.bold: true
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                Layout.fillWidth: true
                                text: model.title
                                color: model.enabled ? Theme.text : Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsBody
                                elide: Text.ElideRight
                            }
                            Text {
                                text: model.repeatLabel + " · " + model.nextText
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                            }
                        }

                        PawSwitch {
                            checked: model.enabled
                            onToggled: backend.reminders.toggle(model.reminderId)
                        }

                        PawButton {
                            small: true
                            variant: "ghost"
                            text: "×"
                            implicitWidth: 30
                            onClicked: backend.reminders.remove(model.reminderId)
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------ 久坐提醒
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "久坐提醒"
            subtitle: backend.sitStatus
            badge: backend.sit_reminder_enabled ? "已开启" : "已关闭"
            badgeColor: backend.sit_reminder_enabled ? Theme.mint : Theme.textFaint

            PawSwitch {
                Layout.fillWidth: true
                text: "连续使用电脑太久时提醒我站起来"
                checked: backend.sit_reminder_enabled
                checkedColor: Theme.mint
                onToggled: backend.sit_reminder_enabled = checked
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                visible: backend.sit_reminder_enabled

                Text {
                    text: "提醒间隔"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    Layout.preferredWidth: 72
                }
                PawSlider {
                    Layout.fillWidth: true
                    from: 15
                    to: 120
                    stepSize: 5
                    value: backend.sit_reminder_minutes
                    onMoved: backend.sit_reminder_minutes = Math.round(value)
                }
                Text {
                    Layout.preferredWidth: 62
                    horizontalAlignment: Text.AlignRight
                    text: backend.sit_reminder_minutes + " 分钟"
                    color: Theme.text
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsBody
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                visible: backend.sit_reminder_enabled

                Text {
                    text: "离开多久算休息"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    Layout.preferredWidth: 108
                }
                PawSlider {
                    Layout.fillWidth: true
                    from: 1
                    to: 30
                    stepSize: 1
                    value: backend.afk_minutes
                    onMoved: backend.afk_minutes = Math.round(value)
                }
                Text {
                    Layout.preferredWidth: 62
                    horizontalAlignment: Text.AlignRight
                    text: backend.afk_minutes + " 分钟"
                    color: Theme.text
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsBody
                }
            }

            PawSwitch {
                Layout.fillWidth: true
                text: "全屏游戏或演示时不要打扰我"
                checked: backend.quiet_when_fullscreen
                checkedColor: Theme.mint
                onToggled: backend.quiet_when_fullscreen = checked
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PawButton {
                    text: "我刚刚休息过了"
                    glyph: "✓"
                    onClicked: backend.resetSitTimer()
                }
                PawButton {
                    text: "试一下提醒效果"
                    glyph: "🔔"
                    variant: "ghost"
                    onClicked: backend.testNotification()
                }
                Item { Layout.fillWidth: true }
            }
        }

        Item { Layout.preferredHeight: 12 }
    }

    function addReminder() {
        var title = titleInput.text.trim()
        if (title.length === 0) {
            titleInput.forceActiveFocus()
            return
        }
        var repeats = ["once", "daily", "weekdays", "weekly"]
        backend.reminders.add(title, timeInput.text, repeats[page.repeatIndex])
        titleInput.text = ""
        titleInput.forceActiveFocus()
    }
}
