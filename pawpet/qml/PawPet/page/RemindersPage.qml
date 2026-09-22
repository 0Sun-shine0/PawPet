import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 提醒页：定时提醒 + 久坐提醒。
   旧版只有一个「30/45/60/90 循环切换」的按钮，这里做成真正能用的提醒系统。

   2026-09 这一版加了「每 N 分钟」的间隔重复，顺带修了两个问题：
     * 重复方式下拉展开后是个空粉块（delegate 读错了属性，见下面注释）
     * 窄窗口下输入行溢出，控件被挤出卡片外面 */
Flickable {
    id: page
    contentWidth: width
    contentHeight: column.implicitHeight + 24
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    property int repeatIndex: 1
    property int intervalMinutes: 30

    // 当前选中的重复方式（从 backend 的表里取，不在这里另写一份）
    readonly property var currentRepeat: {
        var list = backend.repeatOptions
        var index = Math.max(0, Math.min(list.length - 1, page.repeatIndex))
        return list.length > index ? list[index] : null
    }
    readonly property bool needTime: currentRepeat ? currentRepeat.needsTime : true
    readonly property bool needInterval:
        currentRepeat ? currentRepeat.needsInterval : false

    function addReminder() {
        if (!titleInput.text.trim())
            return
        backend.reminders.add(titleInput.text, timeField.text,
                              currentRepeat ? currentRepeat.key : "daily",
                              page.intervalMinutes)
        titleInput.text = ""
        titleInput.forceActiveFocus()
    }

    // ---------------------------------------------------------- 编辑
    //
    // 原来只能删了重建 —— 想改个时间得重新敲一遍内容。
    // 这里用一个对话框：列表项太窄，内联编辑放不下三个控件。
    property string editingId: ""
    property int editRepeatIndex: 1
    property int editInterval: 30
    readonly property var editRepeat: {
        var list = backend.repeatOptions
        var index = Math.max(0, Math.min(list.length - 1, page.editRepeatIndex))
        return list.length > index ? list[index] : null
    }

    function beginEdit(reminderId, title, when, repeat) {
        page.editingId = reminderId
        editTitle.text = title
        editTime.text = when
        // 把当前的重复方式反查成下拉的 index
        var list = backend.repeatOptions
        for (var i = 0; i < list.length; ++i) {
            if (list[i].key === repeat) {
                page.editRepeatIndex = i
                break
            }
        }
        editDialog.open()
    }

    function commitEdit() {
        if (!page.editingId)
            return
        if (!editTitle.text.trim()) {
            editTitle.forceActiveFocus()
            return
        }
        backend.reminders.update(page.editingId, editTitle.text, editTime.text,
                                 page.editRepeat ? page.editRepeat.key : "",
                                 page.editInterval)
        page.editingId = ""
        editDialog.close()
    }

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

            // ------------------------------------------------ 输入区
            //
            // **拆成两行不是审美选择，是必须的。** 原来标题输入框
            // fillWidth、时间框 96、重复下拉 108 挤在一行 —— 窗口一窄
            // （工作台右栏就 400 多像素）总宽就超过卡片可用宽度，Qt 的
            // RowLayout 会把放不下的控件推到卡片外，而 Card 没有 clip，
            // 于是那个控件就「漏」在外面。用户截图里那个空粉块就是这么来的。
            //
            // 现在：第一行标题（独占，够宽），第二行时间 + 重复方式。
            // 两行各自都能在 300px 宽度下放下。
            PawField {
                id: titleInput
                Layout.fillWidth: true
                placeholderText: "提醒内容，比如「喝水」「站起来走走」"
                onAccepted: page.addReminder()
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                // 时间框：间隔重复不需要它（那个不看几点，只看距上次多久）
                PawField {
                    id: timeField
                    Layout.preferredWidth: 96
                    visible: page.needTime
                    horizontalAlignment: TextInput.AlignHCenter
                    text: "09:00"
                    font.family: Theme.fontMono
                    inputMask: "99:99"
                    onAccepted: page.addReminder()
                }

                ComboBox {
                    id: repeatBox
                    Layout.preferredWidth: 118
                    implicitHeight: 34
                    // **model 从 backend 取，不在这里写死一份。**
                    // 写死的话加了新重复方式就得改两处，漏一处就不一致。
                    textRole: "label"
                    model: backend.repeatOptions
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
                        font.pixelSize: Theme.px(12)
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
                            // **读 modelData.label。**
                            //
                            // 这里原来写的是 `text: repeatItem.text` ——
                            // 而 ItemDelegate.text 是这个控件自己的属性、
                            // 从没被赋值过，所以永远是空串。表现出来就是
                            // 下拉能弹开、高度也对，但每一项都没字，看着
                            // 像一个空的粉色方块（用户截图里那个）。
                            //
                            // 改成 modelData 之后还要注意：model 现在是
                            // **对象数组**（backend.repeatOptions 返回
                            // [{key,label,…}]），所以要取 .label，
                            // 不是直接显示 modelData（那会显示 [object Object]）。
                            text: modelData && modelData.label ? modelData.label : ""
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

            // ------------------------------------------------ 间隔选择
            //
            // 只在「每 N 分钟」时出现。做得像一排快捷按钮 + 一个能自己填的
            // 输入框：常用间隔点一下就选，特殊的自己写。
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                visible: page.needInterval

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Repeater {
                        model: backend.intervalPresets

                        delegate: Rectangle {
                            // **宽度要跟着可用空间收缩。**
                            //
                            // 6 个快捷按钮按内容宽度排下来大约 330px，
                            // 窄窗口（工作台右栏 400px 出头，减掉卡片
                            // 内边距只剩 350 左右）就放不下 —— 而
                            // RowLayout 不会自动换行，超出的部分直接
                            // 跑到卡片外面（Card 没有 clip）。
                            //
                            // 这里按「六个平分可用宽度」并设一个下限，
                            // 保证再窄也能全显示。
                            readonly property real slot:
                                (parent ? parent.width : 300) / 6 - 6
                            implicitWidth: Math.max(52,
                                Math.min(presetText.implicitWidth + 18, slot))
                            implicitHeight: 28
                            radius: 14
                            color: page.intervalMinutes === modelData
                                   ? Theme.accent
                                   : (presetMouse.containsMouse
                                      ? Theme.surfaceHi : Theme.surfaceAlt)
                            border.width: 1
                            border.color: page.intervalMinutes === modelData
                                          ? Theme.accent : Theme.border

                            Behavior on color {
                                ColorAnimation { duration: Theme.animFast }
                            }

                            Text {
                                id: presetText
                                anchors.centerIn: parent
                                // 窄的时候用短写法（「30分」而不是「30 分钟」）。
                                //
                                // **判断依据是容器宽度，不能读 implicitWidth。**
                                // 父项的 implicitWidth 又是 Min(自己 + 18, slot)
                                // 算出来的 —— 读它就形成绑定循环：
                                //   implicitWidth → text → implicitWidth
                                // Qt 会报 "Binding loop detected"，而且文字
                                // 会在长短两种写法之间来回跳。
                                text: {
                                    var long = modelData >= 60
                                               ? (modelData / 60) + " 小时"
                                               : modelData + " 分钟"
                                    // 用父项的可用宽度估算够不够放
                                    var room = (parent ? parent.width : 60) - 14
                                    if (long.length * Theme.fsTiny * 0.62 <= room)
                                        return long
                                    return modelData >= 60
                                           ? (modelData / 60) + "时"
                                           : modelData + "分"
                                }
                                color: page.intervalMinutes === modelData
                                       ? "#FFFFFF" : Theme.text
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                                elide: Text.ElideRight
                            }

                            MouseArea {
                                id: presetMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: page.intervalMinutes = modelData
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: "自定义"
                        color: Theme.textDim
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }
                    PawField {
                        id: intervalField
                        Layout.preferredWidth: 76
                        horizontalAlignment: TextInput.AlignHCenter
                        text: String(page.intervalMinutes)
                        font.family: Theme.fontMono
                        validator: IntValidator {
                            bottom: backend.intervalLimits.min
                            top: backend.intervalLimits.max
                        }
                        onEditingFinished: {
                            var value = parseInt(text)
                            if (!isNaN(value))
                                page.intervalMinutes = Math.max(
                                    backend.intervalLimits.min,
                                    Math.min(backend.intervalLimits.max, value))
                            text = String(page.intervalMinutes)
                        }
                    }
                    Text {
                        text: "分钟（"
                              + backend.intervalLimits.min + "~"
                              + backend.intervalLimits.max + "）"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                    }
                    Item { Layout.fillWidth: true }
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
                    Layout.fillWidth: true
                    // 提示文案跟着重复方式走 —— 选「每 N 分钟」时再说
                    // 「时间用 24 小时制」就没意义了（那个模式不看几点）
                    //
                    // 加 elide 和 maximumLineCount：窄窗口下这句话会换行
                    // 撑高卡片，而它只是个提示，不该占那么大地方。
                    text: page.needInterval
                          ? "每隔这么久提醒一次，从添加时算起"
                          : "时间用 24 小时制，例如 09:30"
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }
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
                    font.pixelSize: Theme.px(34)
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
                        spacing: 8

                        Text {
                            // 间隔提醒没有「几点」这个概念，显示成「每隔」
                            // 而不是硬把 time 字段显示出来 —— 那个值是
                            // 创建时随手填的，对它没有意义。
                            text: model.repeat === "interval" ? "每隔" : model.time
                            color: model.enabled ? Theme.gold : Theme.textFaint
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.px(model.repeat === "interval" ? 15 : 20)
                            font.bold: true
                            // 时间列宽度固定，标题那列才好对齐
                            Layout.preferredWidth: 56
                            horizontalAlignment: Text.AlignLeft
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            // 允许被压到很小：窄窗口下这一列是唯一能让位的
                            Layout.minimumWidth: 40
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
                                text: model.nextText
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                            }
                        }

                        // ---- 稍后提醒 / 编辑 ----
                        //
                        // **用浮层而不是塞进这一行。** 塞进去的话，窄窗口下
                        // 时间 + 标题 + 稍后 + 改 + 开关 + × 六样东西加起来
                        // 必然超过可用宽度，RowLayout 就把最后那个（开关）
                        // 顶到卡片外面 —— 实测在 420px 窗口下开关直接不见了。
                        //
                        // 改成绝对定位浮在右侧：不参与 RowLayout 的宽度
                        // 分配，所以不会把别的控件挤走。悬停时才出现，
                        // 鼠标移开就消失，不挡标题。
                        Row {
                            anchors.right: parent.right
                            anchors.rightMargin: 42      // 让开右边的开关和 ×
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 4
                            visible: reminderMouse.containsMouse

                            PawButton {
                                small: true
                                variant: "ghost"
                                text: "稍后"
                                implicitWidth: 46
                                onClicked: snoozeMenu.popupFor(model.reminderId)
                            }

                            PawButton {
                                small: true
                                variant: "ghost"
                                text: "改"
                                implicitWidth: 34
                                onClicked: page.beginEdit(model.reminderId,
                                                          model.title,
                                                          model.time,
                                                          model.repeat)
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

        // ------------------------------------------------------ 稍后提醒菜单
        //
        // 放在页面级而不是列表项里：列表项是 Repeater 动态创建的，
        // 每个都带一份菜单会有 N 个弹窗对象；而且 item 被删掉时
        // 菜单可能还开着，处理起来更麻烦。
        Menu {
            id: snoozeMenu
            font.family: Theme.font
            font.pixelSize: Theme.fsBody
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

            // 记下这次要延后哪一条。打开菜单时赋一次值。
            property string targetId: ""

            function popupFor(reminderId) {
                targetId = reminderId
                popup()
            }

            MenuItem {
                text: "10 分钟后"
                onTriggered: backend.reminders.snooze(snoozeMenu.targetId, 10)
            }
            MenuItem {
                text: "30 分钟后"
                onTriggered: backend.reminders.snooze(snoozeMenu.targetId, 30)
            }
            MenuItem {
                text: "1 小时后"
                onTriggered: backend.reminders.snooze(snoozeMenu.targetId, 60)
            }

            background: Rectangle {
                implicitWidth: 160
                color: Theme.surfaceHi
                radius: Theme.radiusMd
                border.width: 1
                border.color: Theme.border
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

    // ------------------------------------------------------------ 编辑对话框
    Dialog {
        id: editDialog
        anchors.centerIn: parent
        width: Math.min(420, page.width - 48)
        modal: true
        padding: 0
        closePolicy: Popup.CloseOnEscape

        background: Rectangle {
            color: Theme.surface
            radius: Theme.radiusLg
            border.width: 1
            border.color: Theme.border
        }

        contentItem: ColumnLayout {
            spacing: 12

            Text {
                Layout.fillWidth: true
                text: "修改提醒"
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsTitle
                font.bold: true
            }

            PawField {
                id: editTitle
                Layout.fillWidth: true
                placeholderText: "提醒内容"
                onAccepted: page.commitEdit()
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                PawField {
                    id: editTime
                    Layout.preferredWidth: 96
                    visible: {
                        var r = page.editRepeat
                        return r ? r.needsTime : true
                    }
                    horizontalAlignment: TextInput.AlignHCenter
                    font.family: Theme.fontMono
                    inputMask: "99:99"
                    onAccepted: page.commitEdit()
                }

                ComboBox {
                    id: editRepeatBox
                    Layout.preferredWidth: 118
                    implicitHeight: 34
                    textRole: "label"
                    model: backend.repeatOptions
                    currentIndex: page.editRepeatIndex
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    onActivated: page.editRepeatIndex = currentIndex

                    contentItem: Text {
                        leftPadding: 11
                        text: editRepeatBox.displayText
                        color: Theme.text
                        font: editRepeatBox.font
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: Theme.surfaceAlt
                        radius: Theme.radiusMd
                        border.width: 1
                        border.color: editRepeatBox.activeFocus
                                      || editRepeatBox.hovered
                                      ? Theme.accent : Theme.border
                    }
                    delegate: ItemDelegate {
                        id: editRepeatItem
                        width: editRepeatBox.width - 8
                        implicitHeight: 30
                        contentItem: Text {
                            leftPadding: 8
                            text: modelData && modelData.label ? modelData.label : ""
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: editRepeatItem.highlighted
                                   ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
                            radius: Theme.radiusSm
                        }
                    }
                }

                Item { Layout.fillWidth: true }
            }

            // 间隔输入：只在「每 N 分钟」时出现
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                visible: {
                    var r = page.editRepeat
                    return r ? r.needsInterval : false
                }

                Text {
                    text: "每"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                }
                PawField {
                    id: editIntervalField
                    Layout.preferredWidth: 76
                    horizontalAlignment: TextInput.AlignHCenter
                    text: String(page.editInterval)
                    font.family: Theme.fontMono
                    validator: IntValidator {
                        bottom: backend.intervalLimits.min
                        top: backend.intervalLimits.max
                    }
                    onEditingFinished: {
                        var value = parseInt(text)
                        if (!isNaN(value))
                            page.editInterval = Math.max(
                                backend.intervalLimits.min,
                                Math.min(backend.intervalLimits.max, value))
                        text = String(page.editInterval)
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: "分钟提醒一次"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PawButton {
                    text: "取消"
                    variant: "ghost"
                    onClicked: {
                        page.editingId = ""
                        editDialog.close()
                    }
                }
                PawButton {
                    text: "保存"
                    variant: "primary"
                    onClicked: page.commitEdit()
                }
            }
        }
    }
}
