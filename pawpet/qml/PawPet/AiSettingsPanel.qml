import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

Rectangle {
    id: settingsPanel
    objectName: "settingsCard"

    property bool showProviders: false
    property bool showMemory: false
    property bool showKnowledge: false
    property string pendingClearKind: ""

    signal providersVisibilityRequested(bool value)
    signal memoryVisibilityRequested(bool value)
    signal knowledgeVisibilityRequested(bool value)
    Layout.fillWidth: true
    // **没配好模型时必须自动展开。** 以前只在点了「设置」按钮
    // 之后才显示，而泛用户根本不知道要点那个按钮 —— 他会对着
    // 「还没有配置模型」的提示发呆。配好之后才允许收起。
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
                placeholderText: "https://api.deepseek.com/v1"
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
                // 默认那家（DeepSeek）当前的模型名放在最前面。
                // deepseek-flash 是官方文档给的现役名字，
                // 旧的 deepseek-chat 已经下线（仍能调用，但按新模型计费）。
                placeholderText: "deepseek-flash / deepseek-v4-pro / gpt-4o-mini"
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

        // -------------------------------------------- 还没配好时的向导
        //
        // 泛用户卡在第一步：不知道去哪拿 API Key。
        // 以前这里只有两个空输入框（接口地址 / 模型名），等于让他
        // 自己查文档 —— 他连「OpenAI 兼容接口」是什么都不知道。
        //
        // 现在**默认就是 DeepSeek**，用户只需要做一件事：
        // 点「去 DeepSeek 拿 Key」→ 浏览器打开官方创建页 →
        // 复制 → 粘回下面的输入框。接口地址和模型名已经填好了。
        //
        // 其他服务商收在「想用别的」后面。给七家做选择题是负担，
        // 不是帮助 —— 默认那家能跑通，用户才有耐心看别的。
        ColumnLayout {
            Layout.fillWidth: true
            visible: !backend.ai.configured
            spacing: 8

            // 先安抚，再给步骤。
            //
            // 原来这里第一句就是「第一次用？两步：拿 Key → 粘到
            // 下面」—— 对泛用户来说那是「不配就没法用」的意思，
            // 而这是整个产品唯一需要花钱花时间配置的地方。
            // 实际上待办/专注/便签/提醒根本不依赖它，先把这句话
            // 说清楚，用户才愿意往下看。
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: notNeeded.implicitHeight + 16
                radius: Theme.radiusSm
                color: Theme.mintSoft

                Text {
                    id: notNeeded
                    anchors.centerIn: parent
                    width: parent.width - 16
                    text: "不配也能用：待办、专注、便签、提醒都是本机的，"
                          + "跟这里没关系。配了之后小爪才多出「看屏幕、替你操作」的能力。"
                    color: Theme.mint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.35
                }
            }

            Text {
                Layout.fillWidth: true
                text: "想解锁 AI 能力？两步：拿 Key → 粘到下面"
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                font.bold: true
                wrapMode: Text.Wrap
            }

            // 第 1 步：一个显眼的外链按钮
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                PawButton {
                    text: "① " + backend.ai.keyPageLabel
                    glyph: "↗"
                    variant: "primary"
                    implicitWidth: 200
                    onClicked: backend.ai.openKeyPage()
                }

                Text {
                    Layout.fillWidth: true
                    text: backend.ai.primaryProvider.freeHint || ""
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                }
            }

            Text {
                Layout.fillWidth: true
                text: "会打开浏览器跳到官方页面。没账号就先注册（手机号即可）。"
                      + "页面上点「创建 API Key」，把 sk- 开头那一整串复制下来。"
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.Wrap
                lineHeight: 1.35
            }

            Text {
                Layout.fillWidth: true
                text: "② 粘到下面的「API Key」框里，点「保存并测试」。"
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }

            // 当前会填什么地址和模型 —— 让用户知道「不用管这两个」
            Text {
                Layout.fillWidth: true
                text: "接口地址和模型名已经按 "
                      + (backend.ai.primaryProvider.name || "DeepSeek")
                      + " 填好了，不用改。"
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.Wrap
                lineHeight: 1.35
            }

            // 其他服务商：默认收起
            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                PawButton {
                    small: true
                    variant: "ghost"
                    text: showProviders ? "收起其他服务商" : "想用别的服务商？"
                    onClicked: providersVisibilityRequested(!showProviders)
                }
                PawButton {
                    small: true
                    variant: "ghost"
                    visible: showProviders
                    text: "看价格"
                    glyph: "↗"
                    enabled: backend.ai.primaryProvider.links
                             && backend.ai.primaryProvider.links.length > 0
                    onClicked: {
                        var links = backend.ai.primaryProvider.links
                        if (links && links.length > 0)
                            backend.ai.openUrl(links[0].url)
                    }
                }
                Item { Layout.fillWidth: true }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: showProviders
                spacing: 6

                Repeater {
                    model: backend.ai.otherProviders

                    delegate: Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: altCol.implicitHeight + 16
                        radius: Theme.radiusMd
                        color: altMouse.containsMouse ? Theme.surfaceHi
                                                      : Theme.surfaceAlt
                        border.width: 1
                        border.color: backend.ai.baseUrl === modelData.baseUrl
                                      ? Qt.rgba(Theme.accent.r, Theme.accent.g,
                                                Theme.accent.b, 0.55)
                                      : Theme.borderSoft

                        Behavior on color { ColorAnimation { duration: Theme.animFast } }

                        ColumnLayout {
                            id: altCol
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
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsSmall
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    visible: backend.ai.baseUrl === modelData.baseUrl
                                    text: "已选"
                                    color: Theme.accent
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                    font.bold: true
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: modelData.freeHint
                                color: Theme.textDim
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                                wrapMode: Text.Wrap
                                lineHeight: 1.3
                            }
                        }

                        MouseArea {
                            id: altMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            // 选它只改地址和模型；key 还得用户自己弄，
                            // 所以选完要提示他点「去拿 Key」
                            onClicked: backend.ai.applyProvider(modelData.key)
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: "Key 只存在你自己电脑上（.env 文件），不会上传到任何地方。"
                      + "按用量计费，用多少花多少 —— 具体单价点上面「看价格」。"
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.Wrap
                lineHeight: 1.35
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
                // 粘贴之后直接回车保存，省一次点击
                onAccepted: {
                    if (text.length > 0) {
                        backend.ai.saveApiKey(text)
                        text = ""
                    }
                }
            }
            PawButton {
                small: true
                text: "保存并测试"
                variant: "primary"
                enabled: keyField.text.length > 0
                onClicked: {
                    backend.ai.saveApiKey(keyField.text)
                    keyField.text = ""
                    backend.ai.testConnection()
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
                // 把「当前跑的是什么版本、步数设置是多少」也摆出来。
                // 改了代码却没重启时，这里一眼就能看出来。
                text: backend.buildInfo + "\n" + backend.ai.capabilityReport
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
                    // 说清楚是「自己判断」而不是等用户喊记一下 ——
                    // 这正是这个功能的价值所在
                    text: "自动记住我的习惯"
                    checked: backend.aiMemoryEnabled
                    onToggled: backend.aiMemoryEnabled = checked
                }

                Text {
                    Layout.fillWidth: true
                    text: {
                        if (backend.aiMemoryCount <= 0)
                            return "还没有记忆。正常聊几句，它自己会判断该记什么。"
                        var base = "已记住 " + backend.aiMemoryCount + " 条"
                        if (backend.aiAutoMemoryCount > 0)
                            base += "（其中 " + backend.aiAutoMemoryCount + " 条是它自己学的）"
                        return base
                    }
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                }

                // 只在展开时才渲染全文，免得挤占左栏空间
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: showMemory
                    spacing: 6

                    // 最近一次自动学习的收获，放在最上面 ——
                    // 用户最想知道的是「它刚刚自己记了什么」
                    Text {
                        Layout.fillWidth: true
                        text: backend.aiAutoLearnStatus
                        color: Theme.accent
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                        lineHeight: 1.3
                    }

                    Text {
                        Layout.fillWidth: true
                        text: backend.aiMemorySummary
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                        lineHeight: 1.35
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    PawButton {
                        small: true
                        text: showMemory ? "收起" : "看看记住了什么"
                        onClicked: {
                            var next = !showMemory
                            memoryVisibilityRequested(next)
                            if (next)
                                backend.refreshMemory()
                        }
                    }
                    Item { Layout.fillWidth: true }
                    PawButton {
                        small: true
                        text: "清空"
                        enabled: backend.aiMemoryCount > 0
                        onClicked: {
                            settingsPanel.pendingClearKind = "memory"
                            clearConfirmDialog.open()
                        }
                    }
                }
            }
        }

        // -------------------------------------------------- 知识库
        // 导入自己的资料，AI 回答时能引用。
        // 正文**不会**全塞进对话 —— 按需检索，所以可以导很多。
        //
        // **只在高级模式下显示。** 这个功能要先自己把资料转成
        // md 再导进来，泛用户看到只会问「这是什么」。开关在
        // 设置页的「高级模式」。
        Rectangle {
            id: kbCard
            objectName: "kbCard"      // 回归靠它验高级模式的显隐
            visible: backend.advanced_mode
            Layout.fillWidth: true
            implicitHeight: kbColumn.implicitHeight + 20
            radius: Theme.radiusMd
            color: Theme.surfaceAlt
            border.width: 1
            border.color: Theme.borderSoft

            ColumnLayout {
                id: kbColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 10
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: "📚 知识库"
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    font.bold: true
                }

                Text {
                    Layout.fillWidth: true
                    text: backend.aiKnowledgeCount > 0
                          ? ("已导入 " + backend.aiKnowledgeCount
                             + " 份资料、" + backend.aiKnowledgeChunks + " 块")
                          : "导入资料后，我回答时能引用里面的内容"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    PawField {
                        id: kbPathField
                        Layout.fillWidth: true
                        placeholderText: "文件或文件夹路径"
                    }
                    PawButton {
                        small: true
                        text: "导入"
                        variant: "primary"
                        onClicked: {
                            backend.aiImportKnowledge(kbPathField.text, false)
                            kbPathField.text = ""
                        }
                    }
                }

                // 让用户自己验证检索效果 —— 比只显示「导入了 N 块」有用得多
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    PawField {
                        id: kbQueryField
                        Layout.fillWidth: true
                        placeholderText: "试查一句，看能不能找到"
                        onAccepted: kbResult.text =
                            backend.aiSearchKnowledge(kbQueryField.text)
                    }
                    PawButton {
                        small: true
                        text: "查"
                        onClicked: kbResult.text =
                            backend.aiSearchKnowledge(kbQueryField.text)
                    }
                }

                Text {
                    id: kbResult
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: ""
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                    maximumLineCount: 8
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    visible: showKnowledge
                    text: backend.aiKnowledgeSummary
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    PawButton {
                        small: true
                        text: showKnowledge ? "收起" : "看看导入了什么"
                        onClicked: knowledgeVisibilityRequested(!showKnowledge)
                    }
                    Item { Layout.fillWidth: true }
                    PawButton {
                        small: true
                        text: "清空"
                        enabled: backend.aiKnowledgeCount > 0
                        onClicked: {
                            settingsPanel.pendingClearKind = "knowledge"
                            clearConfirmDialog.open()
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "支持文本类资料（md / txt / 代码 / csv / 日志…）。"
                          + "PDF、Word、Excel 请先另存为 txt 或 md。"
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                }
            }
        }
    }

    ConfirmDialog {
        id: clearConfirmDialog
        objectName: "aiClearConfirmDialog"
        heading: pendingClearKind === "memory" ? "清空记忆？" : "清空知识库？"
        message: pendingClearKind === "memory"
                 ? ("将删除当前保存的 " + backend.aiMemoryCount
                    + " 条记忆，删除后无法恢复。")
                 : ("将删除已导入的 " + backend.aiKnowledgeCount
                    + " 份资料和 " + backend.aiKnowledgeChunks
                    + " 个文本块，删除后无法恢复。")
        confirmText: "确认清空"
        onConfirmed: {
            if (settingsPanel.pendingClearKind === "memory")
                backend.aiClearMemory()
            else if (settingsPanel.pendingClearKind === "knowledge") {
                backend.aiClearKnowledge()
                kbResult.text = ""
            }
            settingsPanel.pendingClearKind = ""
        }
        onClosed: settingsPanel.pendingClearKind = ""
    }
}
