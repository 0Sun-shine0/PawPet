import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 工作台主面板：自绘标题栏 + 左侧导航 + 页面堆栈。
   全部用 Qt Quick 渲染，所以圆角、阴影、抗锯齿和动画都是免费拿到的。 */
Window {
    id: dash

    width: 960
    height: 680
    minimumWidth: 860
    minimumHeight: 560
    visible: backend.dashboardVisible
    color: Theme.bg
    title: "小爪工作台"

    property string currentPage: "today"
    property var pages: [
        { "key": "today",     "label": "今日", "icon": "◉" },
        { "key": "focus",     "label": "专注", "icon": "◷" },
        { "key": "tasks",     "label": "待办", "icon": "☑" },
        { "key": "notes",     "label": "便签", "icon": "✎" },
        { "key": "reminders", "label": "提醒", "icon": "◔" },
        { "key": "ai",        "label": "AI 操作", "icon": "✦" },
        { "key": "settings",  "label": "设置", "icon": "⚙" }
    ]

    flags: Qt.Window | Qt.FramelessWindowHint

    function goTo(pageKey) {
        currentPage = pageKey
    }

    onCurrentPageChanged: {
        for (var i = 0; i < pages.length; ++i) {
            if (pages[i].key === currentPage) {
                stack.currentIndex = i
                return
            }
        }
        stack.currentIndex = 0
    }

    // 第一次打开时居中，之后保持用户调好的位置
    property bool placed: false

    onVisibleChanged: {
        if (visible) {
            if (!placed) {
                placed = true
                centerOnScreen()
            }
            raise()
            requestActivate()
        }
    }

    onClosing: function (close) {
        close.accepted = false
        backend.dashboardVisible = false
    }

    function centerOnScreen() {
        var area = backend.screenAt(x + Math.round(width / 2), y + Math.round(height / 2))
        x = Math.round(area.x + (area.width - width) / 2)
        y = Math.round(area.y + (area.height - height) / 2)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ================================================== 标题栏
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 46
            color: Theme.surface

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                onPressed: dash.startSystemMove()
                onDoubleClicked: dash.visibility === Window.Maximized
                                 ? dash.showNormal() : dash.showMaximized()
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 8
                spacing: 10

                Text {
                    text: "🐾"
                    font.pixelSize: Theme.px(15)
                }
                Text {
                    text: "小爪工作台"
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                    font.bold: true
                }
                Text {
                    Layout.fillWidth: true
                    text: backend.statusLine
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    elide: Text.ElideRight
                    leftPadding: 8
                }

                PawButton {
                    small: true
                    variant: "ghost"
                    text: "—"
                    implicitWidth: 34
                    onClicked: dash.showMinimized()
                }
                PawButton {
                    small: true
                    variant: "ghost"
                    text: dash.visibility === Window.Maximized ? "❐" : "▢"
                    implicitWidth: 34
                    onClicked: dash.visibility === Window.Maximized
                               ? dash.showNormal() : dash.showMaximized()
                }
                PawButton {
                    small: true
                    variant: "ghost"
                    text: "✕"
                    implicitWidth: 34
                    onClicked: backend.dashboardVisible = false
                }
            }

            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.border
            }
        }

        // ================================================== 主体
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // ---------------------------------------------- 侧边导航
            Rectangle {
                Layout.preferredWidth: 168
                Layout.fillHeight: true
                color: Theme.surface

                Rectangle {
                    anchors.right: parent.right
                    width: 1
                    height: parent.height
                    color: Theme.borderSoft
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 4

                    Repeater {
                        model: dash.pages

                        delegate: Rectangle {
                            id: navItem
                            Layout.fillWidth: true
                            implicitHeight: 40
                            radius: Theme.radiusMd
                            color: stack.currentIndex === index
                                   ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.15)
                                   : (navMouse.containsMouse ? Theme.surfaceHi : "transparent")

                            Behavior on color { ColorAnimation { duration: Theme.animFast } }

                            Rectangle {
                                visible: stack.currentIndex === index
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                width: 3
                                height: 18
                                radius: 1.5
                                color: Theme.accent
                            }

                            MouseArea {
                                id: navMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: dash.goTo(modelData.key)
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 14
                                anchors.rightMargin: 12
                                spacing: 10

                                Text {
                                    text: modelData.icon
                                    color: stack.currentIndex === index ? Theme.accent : Theme.textDim
                                    font.pixelSize: Theme.px(14)
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.label
                                    color: stack.currentIndex === index ? Theme.text : Theme.textDim
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsBody
                                    font.bold: stack.currentIndex === index
                                }

                                // 待办角标
                                Rectangle {
                                    visible: modelData.key === "tasks" && backend.tasks.pendingCount > 0
                                    implicitWidth: Math.max(18, badgeTxt.implicitWidth + 10)
                                    implicitHeight: 18
                                    radius: 9
                                    color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.25)
                                    Text {
                                        id: badgeTxt
                                        anchors.centerIn: parent
                                        text: backend.tasks.pendingCount
                                        color: Theme.accent
                                        font.family: Theme.font
                                        font.pixelSize: Theme.fsTiny
                                        font.bold: true
                                    }
                                }

                                // 提醒角标
                                Rectangle {
                                    visible: modelData.key === "reminders" && backend.reminders.activeCount > 0
                                    implicitWidth: Math.max(18, badgeTxt2.implicitWidth + 10)
                                    implicitHeight: 18
                                    radius: 9
                                    color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.22)
                                    Text {
                                        id: badgeTxt2
                                        anchors.centerIn: parent
                                        text: backend.reminders.activeCount
                                        color: Theme.gold
                                        font.family: Theme.font
                                        font.pixelSize: Theme.fsTiny
                                        font.bold: true
                                    }
                                }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }

                    // 底部小状态
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: sitBox.implicitHeight + 18
                        radius: Theme.radiusMd
                        color: Theme.surfaceAlt

                        ColumnLayout {
                            id: sitBox
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 10
                            spacing: 6

                            Text {
                                text: "久坐计时"
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 5
                                radius: 2.5
                                color: Theme.surfaceHi
                                Rectangle {
                                    width: parent.width * backend.sitProgress
                                    height: parent.height
                                    radius: 2.5
                                    color: backend.sitProgress > 0.85 ? Theme.rose : Theme.mint
                                    Behavior on width { NumberAnimation { duration: Theme.animSlow } }
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: backend.sit_reminder_enabled ? (backend.sit_reminder_minutes + " 分钟一次") : "已关闭"
                                color: Theme.textDim
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                            }
                        }
                    }
                }
            }

            // ---------------------------------------------- 内容区
            StackLayout {
                id: stack
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: 0

                TodayPage {}
                FocusPage {}
                TasksPage {}
                NotesPage {}
                RemindersPage {}
                AiPage {}
                SettingsPage {}
            }
        }

        // ================================================== 状态栏
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 30
            color: Theme.surface

            Rectangle {
                anchors.top: parent.top
                width: parent.width
                height: 1
                color: Theme.borderSoft
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 8

                Text {
                    text: backend.statusLine
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                }
                Item { Layout.fillWidth: true }
                Text {
                    text: "数据保存在本地 · " + backend.dataPath
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    elide: Text.ElideLeft
                    Layout.maximumWidth: 460
                }
            }
        }
    }

    // ================================================== 无边框缩放
    component ResizeHandle: MouseArea {
        property int edges: 0
        acceptedButtons: Qt.LeftButton
        onPressed: dash.startSystemResize(edges)
    }

    ResizeHandle {
        edges: Qt.LeftEdge
        width: 5
        height: parent.height - 12
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        cursorShape: Qt.SizeHorCursor
    }
    ResizeHandle {
        edges: Qt.RightEdge
        width: 5
        height: parent.height - 12
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        cursorShape: Qt.SizeHorCursor
    }
    ResizeHandle {
        edges: Qt.TopEdge
        height: 5
        width: parent.width - 12
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        cursorShape: Qt.SizeVerCursor
    }
    ResizeHandle {
        edges: Qt.BottomEdge
        height: 5
        width: parent.width - 12
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        cursorShape: Qt.SizeVerCursor
    }
    ResizeHandle {
        edges: Qt.LeftEdge | Qt.TopEdge
        width: 10; height: 10
        anchors.left: parent.left
        anchors.top: parent.top
        cursorShape: Qt.SizeFDiagCursor
    }
    ResizeHandle {
        edges: Qt.RightEdge | Qt.TopEdge
        width: 10; height: 10
        anchors.right: parent.right
        anchors.top: parent.top
        cursorShape: Qt.SizeBDiagCursor
    }
    ResizeHandle {
        edges: Qt.LeftEdge | Qt.BottomEdge
        width: 10; height: 10
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        cursorShape: Qt.SizeBDiagCursor
    }
    ResizeHandle {
        edges: Qt.RightEdge | Qt.BottomEdge
        width: 10; height: 10
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        cursorShape: Qt.SizeFDiagCursor
    }
}
