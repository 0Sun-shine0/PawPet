import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 待办页。
   旧版最大的毛病是「只显示最后 5 条」+「每秒把整个列表重建一遍」，
   这里用 ListView + 真模型：多少条都能滚动看到，并且只有数据变了才刷新。 */
Item {
    id: page

    property string editingId: ""
    property string filter: "pending"      // pending | all
    property bool cancellingEdit: false

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.gap
        spacing: Theme.gap

        // ------------------------------------------------------ 新增
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: addColumn.implicitHeight + 28
            radius: Theme.radiusLg
            color: Theme.surface
            border.width: 1
            border.color: Theme.borderSoft

            ColumnLayout {
                id: addColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 38
                        radius: Theme.radiusMd
                        color: Theme.surfaceAlt
                        border.width: 1
                        border.color: input.activeFocus ? Theme.accent : Theme.border

                        Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                        TextField {
                            id: input
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            placeholderText: "今天要做的下一件小事…  （回车添加）"
                            color: Theme.text
                            placeholderTextColor: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            background: null
                            selectByMouse: true
                            onAccepted: page.submit()
                        }
                    }

                    PawButton {
                        text: "添加"
                        glyph: "＋"
                        variant: "primary"
                        implicitHeight: 38
                        onClicked: page.submit()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: "优先级"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }

                    Repeater {
                        model: [
                            { "value": 0, "label": "普通", "color": Theme.textDim },
                            { "value": 1, "label": "重要", "color": Theme.gold },
                            { "value": 2, "label": "紧急", "color": Theme.rose }
                        ]
                        delegate: PawButton {
                            small: true
                            text: modelData.label
                            variant: page.priority === modelData.value ? "accent" : "subtle"
                            onClicked: page.priority = modelData.value
                        }
                    }

                    Item { Layout.fillWidth: true }

                    Text {
                        text: "共 " + backend.tasks.totalCount + " 条 · 待办 "
                              + backend.tasks.pendingCount + " 条"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }
                }
            }
        }

        // ------------------------------------------------------ 筛选
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            PawButton {
                small: true
                text: "待办 " + backend.tasks.pendingCount
                variant: page.filter === "pending" ? "primary" : "subtle"
                onClicked: page.setFilter("pending")
            }
            PawButton {
                small: true
                text: "全部 " + backend.tasks.totalCount
                variant: page.filter === "all" ? "primary" : "subtle"
                onClicked: page.setFilter("all")
            }

            Rectangle {
                Layout.preferredWidth: 190
                Layout.minimumWidth: 120
                implicitHeight: 34
                radius: Theme.radiusMd
                color: Theme.surfaceAlt
                border.width: 1
                border.color: taskSearch.activeFocus ? Theme.accent : Theme.border

                TextField {
                    id: taskSearch
                    objectName: "taskSearchField"
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 38
                    placeholderText: "\u641c\u7d22\u5f85\u529e\u2026"
                    color: Theme.text
                    placeholderTextColor: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    background: null
                    selectByMouse: true
                    onTextChanged: backend.tasks.searchText = text
                    Keys.onPressed: function(event) {
                        if (event.key === Qt.Key_Escape && text.length > 0) {
                            clear()
                            event.accepted = true
                        }
                    }
                }

                PawButton {
                    id: clearTaskSearch
                    objectName: "clearTaskSearchButton"
                    anchors.right: parent.right
                    anchors.rightMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    small: true
                    text: "×"
                    variant: "ghost"
                    tooltipText: "清空搜索"
                    implicitWidth: 28
                    visible: taskSearch.text.length > 0
                    onClicked: {
                        taskSearch.clear()
                        taskSearch.forceActiveFocus()
                    }
                }
            }

            Item { Layout.fillWidth: true }

            PawButton {
                small: true
                text: "排序"
                glyph: "⇅"
                variant: "ghost"
                onClicked: sortMenu.open()
            }
            PawButton {
                small: true
                text: "清除已完成"
                variant: "ghost"
                enabled: backend.tasks.doneCount > 0
                onClicked: clearDoneDialog.open()
            }

            Menu {
                id: sortMenu
                font.family: Theme.font
                background: Rectangle {
                    implicitWidth: 150
                    color: Theme.surfaceHi
                    radius: Theme.radiusMd
                    border.width: 1
                    border.color: Theme.border
                }
                component SortItem: MenuItem {
                    id: si
                    implicitHeight: 32
                    contentItem: Text {
                        leftPadding: 14
                        text: si.text
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: si.highlighted ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
                        radius: Theme.radiusSm
                    }
                }
                SortItem {
                    text: "智能排序"
                    onTriggered: backend.tasks.sortMode = "smart"
                }
                SortItem {
                    text: "按创建时间"
                    onTriggered: backend.tasks.sortMode = "created"
                }
                SortItem {
                    text: "按优先级"
                    onTriggered: backend.tasks.sortMode = "priority"
                }
            }
        }

        // ------------------------------------------------------ 列表
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusLg
            color: Theme.surface
            border.width: 1
            border.color: Theme.borderSoft
            clip: true

            ListView {
                id: list
                objectName: "taskList"
                anchors.fill: parent
                anchors.margins: 8
                clip: true
                spacing: 4
                model: backend.tasks
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                add: Transition {
                    NumberAnimation { properties: "opacity"; from: 0; to: 1; duration: Theme.animNormal }
                    NumberAnimation { properties: "x"; from: -16; to: 0; duration: Theme.animNormal; easing.type: Theme.easing }
                }
                remove: Transition {
                    NumberAnimation { properties: "opacity"; to: 0; duration: Theme.animFast }
                }
                displaced: Transition {
                    NumberAnimation { properties: "y"; duration: Theme.animNormal; easing.type: Theme.easing }
                }

                delegate: Rectangle {
                    id: row
                    width: list.width
                    implicitHeight: rowColumn.implicitHeight + 16
                    radius: Theme.radiusMd
                    color: rowMouse.containsMouse ? Theme.surfaceHi : "transparent"
                    border.width: 1
                    border.color: rowMouse.containsMouse ? Theme.border : "transparent"

                    Behavior on color { ColorAnimation { duration: Theme.animFast } }

                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton
                    }

                    RowLayout {
                        id: rowColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        spacing: 10

                        // 勾选框
                        Rectangle {
                            Layout.alignment: Qt.AlignVCenter
                            implicitWidth: 22
                            implicitHeight: 22
                            radius: 7
                            color: model.done ? Theme.mint : "transparent"
                            border.width: 2
                            border.color: model.done ? Theme.mint
                                                     : (model.priority === 2 ? Theme.rose
                                                        : (model.priority === 1 ? Theme.gold : Theme.border))

                            Behavior on color { ColorAnimation { duration: Theme.animFast } }

                            Text {
                                anchors.centerIn: parent
                                text: "✓"
                                color: "#12261f"
                                font.pixelSize: Theme.px(13)
                                font.bold: true
                                opacity: model.done ? 1 : 0
                                Behavior on opacity { NumberAnimation { duration: Theme.animFast } }
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: backend.tasks.toggle(model.taskId)
                            }
                        }

                        // 内容
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            TextField {
                                id: editField
                                objectName: "taskEditField_" + model.taskId
                                Layout.fillWidth: true
                                visible: page.editingId === model.taskId
                                text: model.text
                                color: Theme.text
                                font.family: Theme.font
                                font.pixelSize: Theme.fsBody
                                background: Rectangle {
                                    color: Theme.surfaceAlt
                                    radius: Theme.radiusSm
                                    border.width: 1
                                    border.color: Theme.accent
                                }
                                onAccepted: {
                                    page.cancellingEdit = false
                                    page.saveEdit(model.taskId, text)
                                }
                                onEditingFinished: {
                                    if (page.cancellingEdit) {
                                        page.cancellingEdit = false
                                        return
                                    }
                                    if (text.trim().length === 0) {
                                        forceActiveFocus()
                                    } else {
                                        page.saveEdit(model.taskId, text)
                                    }
                                }
                                Keys.onEscapePressed: function(event) {
                                    page.cancellingEdit = true
                                    page.editingId = ""
                                    event.accepted = true
                                }

                                onVisibleChanged: {
                                    if (visible) {
                                        page.cancellingEdit = false
                                        forceActiveFocus()
                                        selectAll()
                                    }
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                visible: page.editingId !== model.taskId
                                text: model.text
                                color: model.done ? Theme.textFaint : Theme.text
                                font.family: Theme.font
                                font.pixelSize: Theme.fsBody
                                font.strikeout: model.done
                                wrapMode: Text.Wrap
                                elide: Text.ElideRight
                                maximumLineCount: 2

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.IBeamCursor
                                    onDoubleClicked: page.editingId = model.taskId
                                }
                            }

                            RowLayout {
                                spacing: 8
                                visible: page.editingId !== model.taskId

                                Text {
                                    visible: model.priority > 0
                                    text: model.priorityLabel
                                    color: model.priority === 2 ? Theme.rose : Theme.gold
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                    font.bold: true
                                }
                                Text {
                                    visible: model.hasDue
                                    text: model.dueText
                                    color: model.overdue ? Theme.rose : Theme.textFaint
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                }
                                Text {
                                    visible: model.pomodoros > 0
                                    text: "🍅 " + model.pomodoros
                                    font.pixelSize: Theme.fsTiny
                                    color: Theme.textFaint
                                }
                                Text {
                                    text: model.createdText
                                    color: Theme.textFaint
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                    opacity: 0.7
                                }
                            }
                        }

                        // 操作
                        RowLayout {
                            Layout.alignment: Qt.AlignVCenter
                            spacing: 2
                            opacity: rowMouse.containsMouse || model.priority > 0 ? 1.0 : 0.0
                            Behavior on opacity { NumberAnimation { duration: Theme.animFast } }

                            PawButton {
                                objectName: "taskPriorityButton_" + model.taskId
                                small: true
                                variant: "ghost"
                                text: "✦"
                                tooltipText: "设置优先级"
                                implicitWidth: 30
                                onClicked: {
                                    priorityMenu.taskId = model.taskId
                                    priorityMenu.currentPriority = model.priority
                                    priorityMenu.open()
                                }
                            }

                            PawButton {
                                objectName: "taskDueButton_" + model.taskId
                                small: true
                                variant: "ghost"
                                text: "⚑"
                                tooltipText: "设置到期日"
                                implicitWidth: 30
                                onClicked: {
                                    dueMenu.taskId = model.taskId
                                    dueMenu.open()
                                }
                            }
                            PawButton {
                                objectName: "taskDeleteButton_" + model.taskId
                                small: true
                                variant: "ghost"
                                text: "×"
                                tooltipText: "删除待办"
                                implicitWidth: 30
                                onClicked: backend.tasks.remove(model.taskId)
                            }
                        }
                    }
                }

                // 空状态
                ColumnLayout {
                    anchors.centerIn: parent
                    width: parent.width - 40
                    spacing: 8
                    visible: list.count === 0

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: page.filter === "pending" ? "🎉" : "🗒"
                        font.pixelSize: Theme.px(40)
                    }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: backend.tasks.searchText.trim().length > 0
                              ? "\u6ca1\u6709\u5339\u914d\u7684\u5f85\u529e"
                              : page.filter === "pending"
                              ? "待办清空了，享受这份轻松吧"
                              : "还没有任何待办。在上面输入框里写一件小事开始。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }

    Timer {
        id: undoTimer
        interval: 6000
        onTriggered: backend.tasks.clearUndo()
    }

    Connections {
        target: backend.tasks
        function onUndoChanged() {
            if (backend.tasks.canUndo)
                undoTimer.restart()
            else
                undoTimer.stop()
        }
    }

    Rectangle {
        objectName: "taskUndoBar"
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.gap
        z: 10
        width: Math.min(360, Math.max(260, parent.width - Theme.gap * 2))
        height: 52
        radius: Theme.radiusMd
        visible: backend.tasks.canUndo
        color: Theme.surfaceHi
        border.width: 1
        border.color: Theme.accent

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 8
            spacing: 8

            Text {
                Layout.fillWidth: true
                text: "已删除：" + backend.tasks.lastRemovedText
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                elide: Text.ElideRight
            }
            PawButton {
                small: true
                text: "撤销"
                variant: "accent"
                onClicked: backend.tasks.undoRemove()
            }
        }
    }

    ConfirmDialog {
        id: clearDoneDialog
        objectName: "clearDoneConfirmDialog"
        heading: "清除已完成待办？"
        message: "将删除已完成的 " + backend.tasks.doneCount
                 + " 条待办，删除后无法恢复。"
        confirmText: "清除已完成"
        onConfirmed: backend.tasks.clearDone()
    }

    // 到期日菜单
    Menu {
        id: dueMenu
        property string taskId: ""
        font.family: Theme.font
        background: Rectangle {
            implicitWidth: 150
            color: Theme.surfaceHi
            radius: Theme.radiusMd
            border.width: 1
            border.color: Theme.border
        }
        component DueItem: MenuItem {
            id: di
            implicitHeight: 32
            contentItem: Text {
                leftPadding: 14
                text: di.text
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: di.highlighted ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
                radius: Theme.radiusSm
            }
        }
        DueItem { text: "今天到期"; onTriggered: backend.tasks.setDue(dueMenu.taskId, page.today(0)) }
        DueItem { text: "明天到期"; onTriggered: backend.tasks.setDue(dueMenu.taskId, page.today(1)) }
        DueItem { text: "三天后";   onTriggered: backend.tasks.setDue(dueMenu.taskId, page.today(3)) }
        DueItem { text: "清除到期日"; onTriggered: backend.tasks.setDue(dueMenu.taskId, "") }
    }

    // 任务创建后也应该能调整优先级，不必删掉重建。
    Menu {
        id: priorityMenu
        objectName: "taskPriorityMenu"
        property string taskId: ""
        property int currentPriority: 0
        font.family: Theme.font
        background: Rectangle {
            implicitWidth: 150
            color: Theme.surfaceHi
            radius: Theme.radiusMd
            border.width: 1
            border.color: Theme.border
        }
        component PriorityItem: MenuItem {
            id: pi
            checkable: true
            implicitHeight: 32
            contentItem: Text {
                leftPadding: 14
                text: pi.text
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
                verticalAlignment: Text.AlignVCenter
            }
            background: Rectangle {
                color: pi.highlighted ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
                radius: Theme.radiusSm
            }
        }
        PriorityItem {
            text: "普通"
            checked: priorityMenu.currentPriority === 0
            onTriggered: backend.tasks.setPriority(priorityMenu.taskId, 0)
        }
        PriorityItem {
            text: "重要"
            checked: priorityMenu.currentPriority === 1
            onTriggered: backend.tasks.setPriority(priorityMenu.taskId, 1)
        }
        PriorityItem {
            text: "紧急"
            checked: priorityMenu.currentPriority === 2
            onTriggered: backend.tasks.setPriority(priorityMenu.taskId, 2)
        }
    }

    property int priority: 0

    Shortcut {
        sequence: "Ctrl+F"
        context: Qt.WindowShortcut
        enabled: page.visible
        onActivated: taskSearch.forceActiveFocus()
    }

    function today(offset) {
        var d = new Date()
        d.setDate(d.getDate() + offset)
        var m = d.getMonth() + 1
        var day = d.getDate()
        return d.getFullYear() + "-" + (m < 10 ? "0" + m : m) + "-" + (day < 10 ? "0" + day : day)
    }

    function submit() {
        var text = input.text.trim()
        if (text.length === 0)
            return
        backend.tasks.add(text, page.priority)
        input.text = ""
        input.forceActiveFocus()
    }

    function saveEdit(taskId, value) {
        if (page.editingId !== taskId)
            return
        var text = (value || "").trim()
        if (text.length === 0)
            return
        backend.tasks.rename(taskId, text)
        page.editingId = ""
    }

    function setFilter(value) {
        page.filter = value
        backend.tasks.showDone = (value === "all")
    }

    Component.onCompleted: {
        backend.tasks.showDone = (page.filter === "all")
    }

    onVisibleChanged: {
        if (visible)
            input.forceActiveFocus()
    }
}
