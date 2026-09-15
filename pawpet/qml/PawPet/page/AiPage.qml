import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* AI 操作台。
   左边是屏幕预览和权限设置，右边是对话与动作卡片。
   每一步动作都会以卡片形式留在对话里，做过什么一目了然。 */
Item {
    id: page
    objectName: "aiPage"

    property bool showSettings: false
    // 记忆全文默认收起：它可能很长，展开会把左边栏撑得没法看
    property bool showMemory: false

    // 给自测用的只读探针：两栏的真实宽度
    readonly property real leftColumnWidth: leftColumn.width
    readonly property real rightColumnWidth: rightColumn.width

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.gap
        spacing: Theme.gap

        // ============================================================ 左栏
        ColumnLayout {
            id: leftColumn
            // preferredWidth 只是个偏好：一旦右栏的隐式宽度很大（聊天气泡里的
            // 长文本），RowLayout 会把空间让给「更想要宽」的那一侧，右栏就会被
            // 压成 0 宽。所以这里必须同时钉死上下限，让左栏宽度不可协商。
            Layout.preferredWidth: 320
            Layout.minimumWidth: 280
            Layout.maximumWidth: 360
            Layout.fillHeight: true
            spacing: Theme.gap

            // -------------------------------------------------- 屏幕预览
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.borderSoft
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "屏幕预览"
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            implicitWidth: 8
                            implicitHeight: 8
                            radius: 4
                            color: backend.ai.running ? Theme.mint : Theme.textFaint
                            SequentialAnimation on opacity {
                                running: backend.ai.running
                                loops: Animation.Infinite
                                NumberAnimation { to: 0.25; duration: 550 }
                                NumberAnimation { to: 1.0; duration: 550 }
                            }
                        }
                        Text {
                            text: backend.ai.running ? "运行中" : "待命"
                            color: backend.ai.running ? Theme.mint : Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: Theme.radiusMd
                        color: "#0d0b14"
                        border.width: 1
                        border.color: Theme.border
                        clip: true

                        Image {
                            id: preview
                            anchors.fill: parent
                            anchors.margins: 4
                            source: backend.ai.previewSource
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                            cache: false
                            visible: backend.ai.hasPreview
                        }

                        ColumnLayout {
                            anchors.centerIn: parent
                            width: parent.width - 40
                            spacing: 8
                            visible: !backend.ai.hasPreview

                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                text: "🖥"
                                font.pixelSize: 32
                            }
                            Text {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: "还没有截图。点下面的按钮看一眼屏幕，\n或者直接在右边让 AI 去做事。"
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsSmall
                                wrapMode: Text.Wrap
                                lineHeight: 1.4
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        PawButton {
                            Layout.fillWidth: true
                            text: "截一张"
                            glyph: "◉"
                            onClicked: backend.ai.capturePreview()
                        }
                        PawButton {
                            text: "设置"
                            glyph: "⚙"
                            variant: page.showSettings ? "accent" : "subtle"
                            onClicked: page.showSettings = !page.showSettings
                        }
                    }
                }
            }

            // -------------------------------------------------- 权限等级
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: levelColumn.implicitHeight + 24
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: backend.ai.level === "full" ? Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.5)
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
                                onClicked: backend.ai.level = modelData.key
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
                                    border.color: backend.ai.level === modelData.key ? Theme.accent : Theme.border
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
                }
            }
        }

        // ============================================================ 右栏
        ColumnLayout {
            id: rightColumn
            Layout.fillWidth: true
            Layout.fillHeight: true
            // 给一个下限，配合左栏的 maximumWidth，保证这一侧永远不会被挤没
            Layout.minimumWidth: 380
            spacing: Theme.gap

            // -------------------------------------------------- 顶部状态条
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 52
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.borderSoft

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    spacing: 10

                    Text {
                        text: backend.ai.configured ? "🤖" : "⚠️"
                        font.pixelSize: 16
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            Layout.fillWidth: true
                            text: backend.ai.configured ? backend.ai.clientLabel : "还没有配置模型"
                            color: backend.ai.configured ? Theme.text : Theme.rose
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: backend.ai.status
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                            elide: Text.ElideRight
                        }
                    }

                    PawButton {
                        small: true
                        text: "测试连接"
                        variant: "ghost"
                        enabled: !backend.ai.running
                        onClicked: backend.ai.testConnection()
                    }
                    PawButton {
                        small: true
                        text: "清空"
                        variant: "ghost"
                        onClicked: backend.ai.clear()
                    }
                }
            }

            // -------------------------------------------------- 模型设置
            Rectangle {
                Layout.fillWidth: true
                visible: page.showSettings
                implicitHeight: settingsColumn.implicitHeight + 28
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                ColumnLayout {
                    id: settingsColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 10

                    Text {
                        text: "模型设置"
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        font.bold: true
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "支持任何 OpenAI 兼容接口。Key 只写进 .env，不会进 pet_data.json。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.preferredWidth: 72
                            text: "接口地址"
                            color: Theme.textDim
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                        }
                        PawField {
                            id: baseField
                            Layout.fillWidth: true
                            text: backend.ai.baseUrl
                            placeholderText: "https://api.openai.com/v1"
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.fsSmall
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.preferredWidth: 72
                            text: "模型名"
                            color: Theme.textDim
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                        }
                        PawField {
                            id: modelField
                            Layout.fillWidth: true
                            text: backend.ai.model
                            placeholderText: "gpt-4.1-mini / deepseek-chat / qwen-vl-max"
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.fsSmall
                        }
                        PawButton {
                            small: true
                            text: "拉取列表"
                            variant: "ghost"
                            onClicked: backend.ai.fetchModels()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.preferredWidth: 72
                            text: "API Key"
                            color: Theme.textDim
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                        }
                        PawField {
                            id: keyField
                            Layout.fillWidth: true
                            echoMode: TextInput.Password
                            placeholderText: backend.ai.apiKeyHint
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.fsSmall
                        }
                        PawButton {
                            small: true
                            text: "保存"
                            variant: "primary"
                            onClicked: {
                                backend.ai.saveApiKey(keyField.text)
                                keyField.text = ""
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        PawButton {
                            text: "保存接口设置"
                            onClicked: backend.ai.saveEndpoint(baseField.text, modelField.text)
                        }
                        Item { Layout.fillWidth: true }
                    }

                    // 模型下拉（拉取到列表时才显示）
                    RowLayout {
                        Layout.fillWidth: true
                        visible: backend.ai.availableModels.length > 0
                        spacing: 8
                        Text {
                            Layout.preferredWidth: 72
                            text: "选择模型"
                            color: Theme.textDim
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                        }
                        ComboBox {
                            Layout.fillWidth: true
                            implicitHeight: 32
                            model: backend.ai.availableModels
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.fsSmall
                            onActivated: modelField.text = currentText
                            contentItem: Text {
                                leftPadding: 10
                                text: parent.displayText
                                color: Theme.text
                                font: parent.font
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }
                            background: Rectangle {
                                color: Theme.surfaceAlt
                                radius: Theme.radiusMd
                                border.width: 1
                                border.color: Theme.border
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: capText.implicitHeight + 18
                        radius: Theme.radiusMd
                        color: Theme.surfaceAlt
                        Text {
                            id: capText
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 9
                            text: backend.ai.capabilityReport
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                            wrapMode: Text.Wrap
                            lineHeight: 1.35
                        }
                    }

                    // 每轮最多执行步数。这是「一轮任务能干多少活」的总闸门，
                    // 所以和权限、自动截图放在一起。
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.preferredWidth: 72
                            text: "执行步数"
                            color: Theme.textDim
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                        }
                        ComboBox {
                            id: stepBox
                            // QML 的 id 不是 objectName，联调脚本靠这个名字找控件
                            objectName: "stepBox"
                            Layout.fillWidth: true
                            implicitHeight: 32
                            textRole: "label"
                            model: backend.aiStepOptions
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall

                            Component.onCompleted: {
                                var options = backend.aiStepOptions
                                for (var i = 0; i < options.length; ++i) {
                                    if (options[i].value === backend.aiMaxSteps) {
                                        currentIndex = i
                                        break
                                    }
                                }
                            }
                            onActivated: function (index) {
                                var options = backend.aiStepOptions
                                if (index >= 0 && index < options.length)
                                    backend.aiMaxSteps = options[index].value
                            }

                            contentItem: Text {
                                leftPadding: 10
                                text: parent.displayText
                                color: Theme.text
                                font: parent.font
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }
                            background: Rectangle {
                                color: Theme.surfaceAlt
                                radius: Theme.radiusMd
                                border.width: 1
                                border.color: Theme.border
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: backend.aiMaxStepsHint
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                        lineHeight: 1.3
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        PawSwitch {
                            Layout.fillWidth: true
                            text: "每轮开始自动看一眼屏幕"
                            checked: backend.ai.autoScreenshot
                            onToggled: backend.ai.autoScreenshot = checked
                        }
                    }

                    // -------------------------------------------------- 跨会话记忆
                    // 记忆会进系统提示词，属于「AI 知道什么」的一部分，
                    // 所以必须让用户看得见、删得掉 —— 偷偷记住东西很招人烦。
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: memColumn.implicitHeight + 20
                        radius: Theme.radiusMd
                        color: Theme.surfaceAlt
                        border.width: 1
                        border.color: Theme.borderSoft

                        ColumnLayout {
                            id: memColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 10
                            spacing: 8

                            PawSwitch {
                                Layout.fillWidth: true
                                text: "记住我的习惯和进度"
                                checked: backend.aiMemoryEnabled
                                onToggled: backend.aiMemoryEnabled = checked
                            }

                            Text {
                                Layout.fillWidth: true
                                text: backend.aiMemoryCount > 0
                                      ? "已记住 " + backend.aiMemoryCount + " 条"
                                      : "还没有记忆，聊几次就有了"
                                color: Theme.textDim
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                            }

                            // 只在展开时才渲染全文，免得挤占左栏空间
                            Text {
                                Layout.fillWidth: true
                                visible: page.showMemory
                                text: backend.aiMemorySummary
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                                wrapMode: Text.Wrap
                                lineHeight: 1.35
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                PawButton {
                                    small: true
                                    text: page.showMemory ? "收起" : "看看记住了什么"
                                    onClicked: {
                                        page.showMemory = !page.showMemory
                                        if (page.showMemory)
                                            backend.refreshMemory()
                                    }
                                }
                                Item { Layout.fillWidth: true }
                                PawButton {
                                    small: true
                                    text: "清空"
                                    enabled: backend.aiMemoryCount > 0
                                    onClicked: backend.aiClearMemory()
                                }
                            }
                        }
                    }
                }
            }

            // -------------------------------------------------- 对话区
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.borderSoft
                clip: true

                ListView {
                    id: chat
                    anchors.fill: parent
                    anchors.margins: 10
                    clip: true
                    spacing: 8
                    model: backend.ai.messages
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    onCountChanged: Qt.callLater(function () { chat.positionViewAtEnd() })

                    // messages 是 QVariantList，delegate 里用 modelData 取元素
                    delegate: AiBubble {
                        width: chat.width
                        role: modelData.role
                        text: modelData.text
                        html: modelData.html || ""
                        detail: modelData.detail
                        toolName: modelData.tool
                        risk: modelData.risk
                        ok: modelData.ok
                        stamp: modelData.time
                        seconds: modelData.seconds
                        imageSource: modelData.image
                    }
                }

                // 空状态
                ColumnLayout {
                    anchors.centerIn: parent
                    width: parent.width - 60
                    spacing: 10
                    visible: backend.ai.messageCount === 0

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "🐾"
                        font.pixelSize: 44
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: "我可以看你的屏幕，也能帮你操作电脑"
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTitle
                        font.bold: true
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "试试这样说：\n" +
                              "「屏幕上这个报错是什么意思」\n" +
                              "「打开记事本，写下明天要做的三件事」\n" +
                              "「帮我把这段话记成待办」"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        lineHeight: 1.5
                    }
                }
            }

            // -------------------------------------------------- 审批卡片
            Rectangle {
                Layout.fillWidth: true
                visible: backend.ai.hasPendingApproval
                implicitHeight: approveColumn.implicitHeight + 26
                radius: Theme.radiusLg
                color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.10)
                border.width: 2
                border.color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.65)

                ColumnLayout {
                    id: approveColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: "⚠️"
                            font.pixelSize: 16
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "小爪想执行一个操作，需要你确认"
                            color: Theme.gold
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            font.bold: true
                        }
                        Rectangle {
                            implicitWidth: riskLabel.implicitWidth + 16
                            implicitHeight: 20
                            radius: 10
                            color: Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.2)
                            Text {
                                id: riskLabel
                                anchors.centerIn: parent
                                text: backend.ai.pendingApproval.risk === "danger" ? "高危" : "会动你的电脑"
                                color: Theme.rose
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                                font.bold: true
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: backend.ai.pendingApproval.summary || ""
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        wrapMode: Text.Wrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        PawButton {
                            text: "允许这一次"
                            glyph: "✓"
                            variant: "primary"
                            onClicked: backend.ai.resolvePending(true)
                        }
                        PawButton {
                            text: "拒绝"
                            variant: "ghost"
                            onClicked: backend.ai.resolvePending(false)
                        }

                        Item { Layout.fillWidth: true }

                        PawButton {
                            small: true
                            text: "改为自动执行"
                            variant: "ghost"
                            onClicked: {
                                backend.ai.level = "auto"
                                backend.ai.resolvePending(true)
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "提示：随时可以把鼠标快速甩到屏幕左上角强制中断。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                    }
                }
            }

            // -------------------------------------------------- 输入区
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: inputColumn.implicitHeight + 24
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: inputArea.activeFocus ? Theme.accent : Theme.borderSoft

                Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                ColumnLayout {
                    id: inputColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 12
                    spacing: 8

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Math.min(120, Math.max(40, inputArea.contentHeight + 18))
                        radius: Theme.radiusMd
                        color: Theme.surfaceAlt

                        TextArea {
                            id: inputArea
                            anchors.fill: parent
                            anchors.margins: 9
                            placeholderText: backend.ai.configured
                                             ? "让小爪做什么？回车发送，Shift+回车换行"
                                             : "先在上面配置模型 API Key"
                            color: Theme.text
                            placeholderTextColor: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            wrapMode: TextArea.Wrap
                            selectByMouse: true
                            background: null
                            enabled: backend.ai.configured && !backend.ai.running
                            Keys.onPressed: function (event) {
                                if (event.key === Qt.Key_Return && !(event.modifiers & Qt.ShiftModifier)) {
                                    event.accepted = true
                                    page.submit()
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        PawButton {
                            text: backend.ai.running ? "停止" : "发送"
                            glyph: backend.ai.running ? "■" : "▶"
                            variant: backend.ai.running ? "danger" : "primary"
                            implicitWidth: 96
                            onClicked: backend.ai.running ? backend.ai.stop() : page.submit()
                        }
                        PawButton {
                            text: "看屏幕"
                            glyph: "◉"
                            enabled: !backend.ai.running
                            onClicked: {
                                backend.ai.capturePreview()
                            }
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            text: backend.ai.running ? "AI 正在工作…（可随时停止）" : "回车发送"
                            color: backend.ai.running ? Theme.mint : Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                    }
                }
            }
        }
    }

    function submit() {
        var text = inputArea.text.trim()
        if (text.length === 0)
            return
        inputArea.text = ""
        backend.ai.send(text)
    }

    Component.onCompleted: {
        if (!backend.ai.hasPreview)
            backend.ai.capturePreview()
    }
}
