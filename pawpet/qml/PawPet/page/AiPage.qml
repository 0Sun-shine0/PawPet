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
    property bool showKnowledge: false
    // 「想用别的服务商？」默认收起。给七家做选择题是负担不是帮助 ——
    // 默认那家（DeepSeek）能跑通，用户才有耐心看别的。
    property bool showProviders: false
    // 「历史对话」面板。对话持久化之后，这是翻回去看的入口。
    property bool showHistory: false
    property string pendingHistoryAction: ""
    property string pendingHistorySessionId: ""
    // 「外部工具」的详情（server 清单、工具名、配置路径）。
    // **默认收起**：那部分有 200px 高，常驻会把右栏撑到 2000px 以上 ——
    // 「太占视野」是用户明确抱怨过的。标题行（状态点 + 开关）常驻就够用了。
    property bool showMcpDetail: false
    // 给自测用的只读探针：两栏的真实宽度
    readonly property real leftColumnWidth: leftColumn.width
    readonly property real rightColumnWidth: rightColumn.width

    function revealPendingCard() {
        var target = null
        if (backend.ai.hasPendingQuestion) {
            target = askCard
        } else if (backend.ai.hasPendingApproval) {
            target = approvalCard
        } else if (backend.ai.hasPendingBatch) {
            target = batchCard
        }
        if (!target || !target.visible || rightScroll.height <= 0)
            return

        var maxY = Math.max(0, rightScroll.contentHeight - rightScroll.height)
        rightScroll.contentY = maxY
    }

    Timer {
        id: pendingRevealTimer
        interval: 0
        repeat: false
        onTriggered: page.revealPendingCard()
    }

    Connections {
        target: backend.ai
        function onApprovalChanged() {
            if (backend.ai.hasPendingApproval || backend.ai.hasPendingBatch)
                pendingRevealTimer.restart()
        }
    }
    // 对话区填满右栏滚动视口剩余的空间。放在页面级计算，避免把
    // `y` 直接写进卡片的 Layout.preferredHeight 后出现绑定不刷新的情况。

    Item {
        id: aiRow
        objectName: "aiRow"          // 联调脚本靠它量布局
        anchors.fill: parent
        anchors.margins: Theme.gap

        // ============================================================ 左栏
        ColumnLayout {
            id: leftColumn
            objectName: "aiLeftColumn"
            // preferredWidth 只是个偏好：一旦右栏的隐式宽度很大（聊天气泡里的
            // 长文本），RowLayout 会把空间让给「更想要宽」的那一侧，右栏就会被
            // 压成 0 宽。所以这里必须同时钉死上下限，让左栏宽度不可协商。
            x: 0
            y: 0
            width: Math.min(360, Math.max(280,
                          aiRow.width - Theme.gap - 380))
            height: aiRow.height
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
                        // 截图底板。原来是近黑色（#0d0b14），深色主题下像「显示器」；
                        // 浅色主题里它变成一大块黑疤。浅灰粉底 + 淡描边同样能衬托截图。
                        color: Theme.surfaceAlt
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
                                font.pixelSize: Theme.px(32)
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

            PermissionCard { objectName: "permissionCard" }
        }

        // ============================================================ 右栏
        //
        // 右栏包一层 Flickable + Item，避免把可滚动内容直接塞进
        // RowLayout。列里的对话卡片在内容较少时会填满视口，内容变长时
        // 则按实际内容滚动，输入区仍然固定在底部。
        // 实测 908x590 的窗口下，列里第一张卡片被撑到 995px 而列只有 562px，
        // 把其余卡片全顶出去；另一头「对话区」又被压成 0 高。
        // 这就是用户报的「前端小屏 UI bug」。
        //
        // 用 Item 包住列、让 Item 的高度取自列的隐式高度，高度链路就变成
        // 单向的；内容超出时 Flickable 负责滚动，别的卡片不会被挤没。
        //
        // 缩进保持原样（QML 不看缩进），改动集中在首尾两处，减少改坏的机会。
        // ============================================================ 右栏
        //
        // 右栏分两块：**上面滚动、下面钉住**。
        //
        // 历史教训（三次）：
        //
        // 1. 最初直接把 ColumnLayout 放进 RowLayout，列里的「对话区」带
        //    Layout.fillHeight、列又想按内容撑高 —— 循环依赖。实测 908x590
        //    下列里第一张卡片被撑到 995px 而列只有 562px，其余卡片全被顶出去，
        //    另一头对话区被压成 0 高。这是用户报过的「前端小屏 UI bug」。
        // 2. 整列塞进滚动区：列内容固定 828px、视口只有 612px，输入框被推到
        //    折叠线以下，得先滚过「点一下就跑」那张 300px 的卡片才能打字。
        //    用户的原话是「还是这样的」。
        // 3. 前两版重构把卡片插错了层（QML 语法照样合法、qmlcheck 抓不到，
        //    跑起来才发现输入框宽度是 0 / 掉到屏幕外）。所以现在改完必须
        //    用几何再验一次，不能只看语法。
        //
        // 结构：输入卡片是右栏 ColumnLayout 的孩子，和滚动区**同级**。
        //   * 滚动区（Layout.fillHeight）：状态条 / 进度 / 对话 / 卡片 /
        //     设置 / 现成任务
        //   * 钉住区（Layout.preferredHeight）：输入卡片，永远贴着底
        // 对话区留在滚动区里：内容少时填满视口，内容多时保持自己的
        // 最小高度并交给外层 Flickable 滚动。
        //
        // 两个孩子的 Layout 属性都是必需的：
        //   * 滚动区 fillHeight 才吃得到剩余空间；
        //   * 输入卡片 preferredHeight 才能**先**拿到自己的高度 ——
        //     只写 implicitHeight 的话实测被挤到 y=716（视口才 696）。
        ColumnLayout {
            id: rightPane
            objectName: "aiRightPane"
            x: leftColumn.x + leftColumn.width + Theme.gap
            y: 0
            width: Math.max(380, aiRow.width - leftColumn.width - Theme.gap)
            height: aiRow.height
            spacing: Theme.gap
        Flickable {
            id: rightScroll
            objectName: "rightScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 380
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            contentWidth: width
            contentHeight: rightBody.height
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            Item {
                id: rightBody
                width: rightScroll.width
                height: Math.max(rightColumn.implicitHeight, rightScroll.height)
        ColumnLayout {
            id: rightColumn
            objectName: "aiRightColumn"
            width: rightBody.width
            height: rightBody.height
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
                    id: topRow
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    spacing: 10

                    Text {
                        text: backend.ai.configured ? "🤖" : "⚠️"
                        font.pixelSize: Theme.px(16)
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
                        // 窄窗口下先让位给「历史对话」和「新对话」——
                        // 这两个是日常入口，测试连接是配好之后就不用的。
                        // 不设这个的话，右侧按钮会把左边挤出可见区
                        // （实测 1000px 宽时「历史对话」整个不见了）。
                        visible: topRow.width > 520
                        enabled: !backend.ai.running
                        onClicked: backend.ai.testConnection()
                    }
                    // 「历史对话」：对话现在是持久化的，得有个地方翻回去
                    PawButton {
                        small: true
                        text: "历史"
                        glyph: "🕘"
                        variant: page.showHistory ? "accent" : "ghost"
                        enabled: !backend.ai.running
                        onClicked: page.showHistory = !page.showHistory
                    }
                    // 「新对话」和「清空」是两件事：
                    //   新对话 = 当前这段归档起来（之后还能翻到），开一段空的
                    //   清空   = 同上（用户眼里「清空」就是从头开始，
                    //            所以也归档，真正删干净在历史面板里）
                    PawButton {
                        small: true
                        text: "新对话"
                        variant: "ghost"
                        enabled: !backend.ai.running && backend.ai.messageCount > 0
                        onClicked: backend.ai.newConversation()
                    }
                    PawButton {
                        small: true
                        text: "清空"
                        variant: "ghost"
                        onClicked: backend.ai.clear()
                    }
                }
            }

            AiHistoryPanel {
                id: historyCard
                expanded: page.showHistory
                onClearHistoryRequested: page.confirmClearHistory()
                onOpenConversationRequested: function (sessionId) {
                    backend.ai.openConversation(sessionId)
                }
                onDeleteConversationRequested: function (sessionId) {
                    page.confirmDeleteHistory(sessionId)
                }
            }

            // -------------------------------------------------- 边做边说
            // 「每步显示：正在打开浏览器 → 找到导出按钮」。
            // 只在跑的时候出现：跑完了这些步骤会留在下面的动作卡片里，
            // 状态条再挂着就是噪音。
            //
            // 和对话区里的卡片**不重复**：那里是完整记录（带输出、截图、
            // 耗时），这里是最近几步的即时进度 —— 用户眼睛不用离开顶部
            // 就知道它现在在干什么，还能就地按停。
            Rectangle {
                Layout.fillWidth: true
                visible: backend.ai.running
                         && backend.ai.progressLine.length > 0
                implicitHeight: progressRow.implicitHeight + 18
                radius: Theme.radiusMd
                color: Theme.surfaceAlt
                border.width: 1
                border.color: Theme.borderSoft

                RowLayout {
                    id: progressRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 10
                    spacing: 7

                    Rectangle {
                        implicitWidth: 7
                        implicitHeight: 7
                        radius: 3.5
                        color: Theme.mint
                        SequentialAnimation on opacity {
                            running: backend.ai.running
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.25; duration: 520 }
                            NumberAnimation { to: 1.0; duration: 520 }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: backend.ai.progressLine
                        textFormat: Text.PlainText
                        color: Theme.textDim
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        // 从左边截断：用户最关心的是「刚刚做了什么」，
                        // 那是链条的末尾，不能被截掉。
                        elide: Text.ElideLeft
                    }

                    PawButton {
                        small: true
                        variant: "ghost"
                        text: "停"
                        implicitWidth: 38
                        onClicked: backend.ai.stop()
                    }
                }
            }

            // -------------------------------------------------- 模型设置
            AiSettingsPanel {
                id: settingsPanel
                Layout.fillWidth: true
                visible: page.showSettings || !backend.ai.configured
                showProviders: page.showProviders
                showMemory: page.showMemory
                showKnowledge: page.showKnowledge
                onProvidersVisibilityRequested: page.showProviders = value
                onMemoryVisibilityRequested: page.showMemory = value
                onKnowledgeVisibilityRequested: page.showKnowledge = value
            }

            // -------------------------------------------------- 外部工具（MCP）
            //
            // **这一块以前完全不存在。** `connectMcp()` 有实现但全项目
            // 零调用，QML 里连「MCP」这个词都没有 —— 于是随包带的 pawkit
            // （8 个工具：几点、算数、生成密码、算文件校验值…）一次都没在
            // 真实对话里出现过。功能是好的，只是用户碰不到。
            //
            // 做成独立小卡片而不是塞进设置里：连接状态是动态的，
            // 用户要能一眼看到「连上没有、有几个工具」。
            //
            // **但现在整张卡片归到高级模式下。** 上面那个理由是「用户要能
            // 一眼看到」—— 那是**会用 MCP 的用户**的需求。对泛用户，
            // 一屏出现「MCP」「server」「工具名」只会增加理解负担，
            // 而 MCP 开关本身默认就是开的，包里的 pawkit 照常生效，
            // 不看这个卡片一点也不影响使用。
            AiMcpPanel {
                id: mcpCard
                Layout.fillWidth: true
                visible: backend.advanced_mode
                showDetail: page.showMcpDetail
                onDetailVisibilityRequested: page.showMcpDetail = value
            }

            // -------------------------------------------------- 对话区
            Rectangle {
                id: chatCard
                objectName: "chatCard"
                Layout.fillWidth: true
                // rightColumn 的高度已经由 rightBody 明确给出：至少填满滚动
                // 视口，内容变长时则按实际内容增长。因此这里可以安全地填充
                // 剩余空间：现成任务收起时补上卡片下方的空白，内容变长时
                // 仍保留 320px 的偏好和 220px 的下限，并交给 ListView 滚动。
                Layout.fillHeight: true
                Layout.preferredHeight: 320
                Layout.minimumHeight: 220
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.borderSoft
                clip: true

                ListView {
                    id: chat
                    objectName: "chat"      // 布局回归测试靠它量对话区高度
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
                        role: modelData.role || "info"
                        text: modelData.text || ""
                        html: modelData.html || ""
                        plain: modelData.plain || ""
                        detail: modelData.detail || ""
                        toolName: modelData.tool || ""
                        risk: modelData.risk || ""
                        ok: modelData.ok === undefined ? true : modelData.ok
                        stamp: modelData.time || ""
                        seconds: Number(modelData.seconds || 0)
                        imageSource: modelData.image || ""
                        recovery: modelData.recovery || ""
                        report: modelData.report || ""
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
                        font.pixelSize: Theme.px(44)
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
                        text: "屏幕上的事、电脑里的事，说一句就行。\n下面有现成的，点一下我就开始干。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        lineHeight: 1.5
                    }
                }
            }

            // -------------------------------------------------- 现成任务
            // 「首页直接列几个能点就跑的任务」。
            //
            // 放这里而不是塞进空状态里：空状态只在一条消息都没有时出现，
            // 而用户干完一件事之后往往还想再干一件 —— 那时候模板照样该在。
            // 这里只在**空闲**（没在跑、也没挂着待确认）时显示，
            // 免得任务执行中界面还在劝他再点一个。
            Rectangle {
                id: templateCard
                // QML 的 id 不是 objectName，界面自检脚本靠这个名字找它
                objectName: "templateCard"
                Layout.fillWidth: true
                visible: !backend.aiTemplatesHidden
                         && !backend.ai.running
                         && !backend.ai.hasPendingApproval
                         && !backend.ai.hasPendingBatch
                implicitHeight: tmplColumn.implicitHeight + 26
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.borderSoft

                property int activeGroup: 0

                ColumnLayout {
                    id: tmplColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: "✨"
                            font.pixelSize: Theme.px(16)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "点一下就跑"
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            font.bold: true
                        }
                        Text {
                            text: "不知道说什么好？挑一个"
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                        // 关闭按钮。用户的原话：「这个点一下就跑要加一个 x
                        // 来关闭，要不然很占视野」—— 这张卡片有两行标签加
                        // 四到六个任务块，收起之后对话区能多出 300px。
                        // 关掉的状态会记住，不用每次都点一遍。
                        Rectangle {
                            implicitWidth: 22
                            implicitHeight: 22
                            radius: 11
                            color: closeMouse.containsMouse ? Theme.surfaceHi
                                                            : "transparent"
                            Behavior on color { ColorAnimation { duration: Theme.animFast } }

                            MouseArea {
                                id: closeMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: backend.aiTemplatesHidden = true
                            }
                            Text {
                                anchors.centerIn: parent
                                text: "✕"
                                color: closeMouse.containsMouse ? Theme.text
                                                                : Theme.textFaint
                                font.family: Theme.fontLatin
                                font.pixelSize: Theme.px(12)
                            }
                        }
                    }

                    // 分人群切换：学生 / 上班党 / 日常
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Repeater {
                            model: backend.ai.taskGroups

                            delegate: Rectangle {
                                implicitWidth: groupText.implicitWidth + 26
                                implicitHeight: 28
                                radius: 14
                                color: templateCard.activeGroup === index
                                       ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
                                       : (groupMouse.containsMouse ? Theme.surfaceHi : Theme.surfaceAlt)
                                border.width: 1
                                border.color: templateCard.activeGroup === index
                                              ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.45)
                                              : Theme.borderSoft

                                Behavior on color { ColorAnimation { duration: Theme.animFast } }

                                MouseArea {
                                    id: groupMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: templateCard.activeGroup = index
                                }

                                Text {
                                    id: groupText
                                    anchors.centerIn: parent
                                    text: modelData.icon + " " + modelData.label
                                    color: templateCard.activeGroup === index
                                           ? Theme.accent : Theme.textDim
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsSmall
                                    font.bold: templateCard.activeGroup === index
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }

                    // 当前人群下的任务。两列排，一屏能看全。
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 8
                        rowSpacing: 8

                        Repeater {
                            model: {
                                var groups = backend.ai.taskGroups
                                if (groups.length === 0)
                                    return []
                                var idx = Math.max(0, Math.min(groups.length - 1,
                                                               templateCard.activeGroup))
                                return groups[idx].items
                            }

                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 54
                                radius: Theme.radiusMd
                                color: tmplMouse.containsMouse
                                       ? Theme.surfaceHi : Theme.surfaceAlt
                                border.width: 1
                                border.color: tmplMouse.containsMouse
                                              ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.4)
                                              : Theme.borderSoft

                                Behavior on color { ColorAnimation { duration: Theme.animFast } }

                                MouseArea {
                                    id: tmplMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: backend.ai.runTemplate(modelData.key)
                                }

                                ColumnLayout {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 12
                                    anchors.rightMargin: 10
                                    spacing: 2

                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.label
                                        color: Theme.text
                                        font.family: Theme.font
                                        font.pixelSize: Theme.fsSmall
                                        font.bold: true
                                        elide: Text.ElideRight
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

                    Text {
                        Layout.fillWidth: true
                        visible: !backend.ai.configured
                        text: "还没有配置模型 —— 去左边「设置」里填一个 API Key 才能跑。"
                        color: Theme.rose
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                    }
                }
            }

            // -------------------------------------------------- 提问卡片
            // 「拿不准就停一下问」的落地：模型信息不够时不猜，停在这里问。
            // 和下面的审批卡片是两回事 —— 审批是「要不要做」，这里是
            // 「你想怎么做」，所以给的是选项按钮或输入框，不是允许/拒绝。
            Rectangle {
                id: askCard
                objectName: "askCard"
                Layout.fillWidth: true
                visible: backend.ai.hasPendingQuestion
                implicitHeight: Math.max(askColumn.implicitHeight + 26, 228)
                radius: Theme.radiusLg
                color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.09)
                border.width: 2
                border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.45)
                onVisibleChanged: if (visible) pendingRevealTimer.restart()
                onHeightChanged: if (visible && height > 0) pendingRevealTimer.restart()

                property var answerOptions: backend.ai.pendingApproval.options
                                               ? backend.ai.pendingApproval.options : []

                ColumnLayout {
                    id: askColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: "💬"
                            font.pixelSize: Theme.px(16)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "小爪想先问你一句"
                            color: Theme.accent
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            font.bold: true
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: backend.ai.pendingApproval.summary || ""
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        wrapMode: Text.Wrap
                        lineHeight: 1.4
                    }

                    // 有选项就用按钮（点一下就走），没有就给输入框。
                    // 两种都留「跳过」的出口 —— 用户可能懒得答，
                    // 卡着他比答错更烦。
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6
                        visible: askCard.answerOptions.length > 0

                        Repeater {
                            model: askCard.answerOptions
                            delegate: PawButton {
                                small: true
                                variant: "subtle"
                                text: modelData
                                onClicked: backend.ai.answerPending(modelData)
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        PawField {
                            id: askField
                            Layout.fillWidth: true
                            placeholderText: "回答小爪，或者直接说你想怎么做"
                            onAccepted: {
                                if (text.trim().length > 0) {
                                    backend.ai.answerPending(text)
                                    text = ""
                                }
                            }
                        }
                        PawButton {
                            text: "回答"
                            variant: "primary"
                            enabled: askField.text.trim().length > 0
                            onClicked: {
                                backend.ai.answerPending(askField.text)
                                askField.text = ""
                            }
                        }
                        PawButton {
                            text: "你自己定"
                            variant: "ghost"
                            onClicked: {
                                backend.ai.answerPending("你自己看着办，按最合理的来")
                                askField.text = ""
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "不回答也行 —— 直接说「你自己定」，我会挑最稳妥的做法继续。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                    }
                }
            }

            // -------------------------------------------------- 审批卡片
            Rectangle {
                id: approvalCard
                objectName: "approvalCard"
                Layout.fillWidth: true
                // 提问卡和审批卡不能同时出现，样式也完全不同
                visible: backend.ai.hasPendingApproval && !backend.ai.hasPendingQuestion
                implicitHeight: Math.max(approveColumn.implicitHeight + 26, 210)
                radius: Theme.radiusLg
                color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.10)
                border.width: 2
                border.color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.65)
                onVisibleChanged: if (visible) pendingRevealTimer.restart()
                onHeightChanged: if (visible && height > 0) pendingRevealTimer.restart()

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
                            font.pixelSize: Theme.px(16)
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

            // -------------------------------------------------- 合并确认卡片
            // 同一轮里的多个操作合成**一次**询问。
            // 关键：减少的是「问几次」，不是「给多少信息」——清单仍然是完整的。
            Rectangle {
                id: batchCard
                objectName: "batchCard"
                Layout.fillWidth: true
                visible: backend.ai.hasPendingBatch
                implicitHeight: Math.max(
                    batchColumn.implicitHeight + 26,
                    146 + backend.ai.pendingBatch.length * 38)
                Layout.minimumHeight: 184
                radius: Theme.radiusLg
                color: Qt.rgba(Theme.violet.r, Theme.violet.g, Theme.violet.b, 0.10)
                border.width: 2
                border.color: Qt.rgba(Theme.violet.r, Theme.violet.g, Theme.violet.b, 0.65)
                onVisibleChanged: if (visible) pendingRevealTimer.restart()
                onHeightChanged: if (visible && height > 0) pendingRevealTimer.restart()

                // 用户逐条勾选的状态。默认全选 —— 嫌烦就一键全允许，
                // 想细看就点掉不想做的。
                property var decisions: ({})
                property int revision: 0

                function resetDecisions() {
                    var map = {}
                    var items = backend.ai.pendingBatch
                    for (var i = 0; i < items.length; ++i)
                        map[items[i].index] = true
                    decisions = map
                    revision += 1
                }

                function isChecked(key) {
                    // revision 只是为了让绑定重新求值
                    var _ = revision
                    return decisions[key] !== false
                }

                function toggle(key) {
                    var map = {}
                    for (var k in decisions) map[k] = decisions[k]
                    map[key] = !isChecked(key)
                    decisions = map
                    revision += 1
                    backend.ai.resolveBatchItem(key, map[key])
                }

                function checkedCount() {
                    var n = 0
                    var items = backend.ai.pendingBatch
                    for (var i = 0; i < items.length; ++i)
                        if (isChecked(items[i].index)) n += 1
                    return n
                }

                // 批次一出现就把勾选状态复位
                Connections {
                    target: backend.ai
                    function onApprovalChanged() {
                        if (backend.ai.hasPendingBatch)
                            batchCard.resetDecisions()
                    }
                }

                ColumnLayout {
                    id: batchColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            text: "📋"
                            font.pixelSize: Theme.px(16)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "小爪想连着做 " + backend.ai.pendingBatch.length + " 个操作"
                            color: Theme.violet
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            font.bold: true
                        }
                    }

                    Repeater {
                        model: backend.ai.pendingBatch
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 30
                            radius: Theme.radiusMd
                            color: batchCard.isChecked(modelData.index)
                                   ? Qt.rgba(Theme.violet.r, Theme.violet.g, Theme.violet.b, 0.12)
                                   : Theme.surfaceAlt
                            border.width: 1
                            border.color: batchCard.isChecked(modelData.index)
                                          ? Theme.violet : Theme.borderSoft

                            RowLayout {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 8
                                spacing: 8

                                Text {
                                    Layout.preferredWidth: 18
                                    text: batchCard.isChecked(modelData.index) ? "☑" : "☐"
                                    color: batchCard.isChecked(modelData.index)
                                           ? Theme.violet : Theme.textFaint
                                    font.pixelSize: Theme.fsBody
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: (index + 1) + ". " + (modelData.summary || modelData.tool)
                                    color: batchCard.isChecked(modelData.index)
                                           ? Theme.text : Theme.textFaint
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsSmall
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: modelData.risk === "danger" ? "高危" : ""
                                    visible: modelData.risk === "danger"
                                    color: Theme.rose
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                    font.bold: true
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: batchCard.toggle(modelData.index)
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        PawButton {
                            text: "全部允许"
                            glyph: "✓"
                            variant: "primary"
                            onClicked: {
                                backend.ai.resolveBatchAll(true)
                                backend.ai.confirmBatch()
                            }
                        }
                        PawButton {
                            text: "全部拒绝"
                            variant: "ghost"
                            onClicked: {
                                backend.ai.resolveBatchAll(false)
                                backend.ai.confirmBatch()
                            }
                        }
                        Item { Layout.fillWidth: true }
                        PawButton {
                            small: true
                            text: "只做勾选的 " + batchCard.checkedCount() + " 个"
                            variant: "subtle"
                            enabled: batchCard.checkedCount() > 0
                            onClicked: backend.ai.confirmBatch()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "点条目可以取消勾选，没勾的按「不做」处理。"
                              + "高危操作不会出现在这里 —— 那种会单独问你。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
            }
        }

            // -------------------------------------------------- 输入区
            Rectangle {
                id: inputCard
                objectName: "inputCard"
                Layout.fillWidth: true
                // 用 Layout.preferredHeight 而不是 implicitHeight：
                // 滚动区带 fillHeight，只有 preferredHeight 才能保证
                // 这个卡片**先**拿到自己的高度。实测只写 implicitHeight
                // 时它被挤到 y=716（视口才 696），整块掉到屏幕外面。
                Layout.preferredHeight: inputColumn.implicitHeight + 24
                Layout.minimumHeight: inputColumn.implicitHeight + 24
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
                        // 高度必须同时满足两件事：
                        //   1. 放下 TextArea 的内容 + 它上下各 9 的 margin；
                        //   2. **至少**放下第一行字。
                        //
                        // 原来是 `Math.max(40, contentHeight + 18)`，
                        // 最小值 40 减掉 18 的 margin 只剩 22px —— 而
                        // 一行 16px 的字要占 19.2px，加上光标行的额外高度
                        // 就放不下，placeholder「让小爪做什么？」上半截被切掉。
                        // 这是用户截图里报的问题。
                        //
                        // 46 是实测能稳住的值（比一行文字的 1.2 倍再多留一点）。
                        // 值变小了就会重新切字，所以 bubshot.py 里钉了一条断言。
                        implicitHeight: Math.min(120, Math.max(46, inputArea.contentHeight + 22))
                        radius: Theme.radiusMd
                        color: Theme.surfaceAlt

                        TextArea {
                            id: inputArea
                            objectName: "aiInput"
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
                        // 收起「点一下就跑」之后还得能找回来，否则用户
                        // 手滑点掉 x 就永远见不到那些现成任务了。
                        PawButton {
                            visible: backend.aiTemplatesHidden && !backend.ai.running
                            text: "现成任务"
                            glyph: "✨"
                            variant: "ghost"
                            onClicked: backend.aiTemplatesHidden = false
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

    ConfirmDialog {
        id: historyConfirmDialog
        objectName: "historyConfirmDialog"
        heading: page.pendingHistoryAction === "clear"
                 ? "清空历史对话？" : "删除这段历史对话？"
        message: page.pendingHistoryAction === "clear"
                 ? "将删除除当前对话外的所有历史记录，删除后无法恢复。"
                 : "这段对话会从历史记录中永久删除，删除后无法恢复。"
        confirmText: page.pendingHistoryAction === "clear"
                     ? "清空历史" : "删除对话"
        onConfirmed: {
            if (page.pendingHistoryAction === "clear")
                backend.ai.clearHistory()
            else if (page.pendingHistoryAction === "delete"
                     && page.pendingHistorySessionId.length > 0)
                backend.ai.deleteConversation(page.pendingHistorySessionId)
            page.pendingHistoryAction = ""
            page.pendingHistorySessionId = ""
        }
        onClosed: {
            page.pendingHistoryAction = ""
            page.pendingHistorySessionId = ""
        }
    }

    function confirmClearHistory() {
        if (backend.ai.conversationCount <= 1)
            return
        page.pendingHistoryAction = "clear"
        page.pendingHistorySessionId = ""
        historyConfirmDialog.open()
    }

    function confirmDeleteHistory(sessionId) {
        if (!sessionId)
            return
        page.pendingHistoryAction = "delete"
        page.pendingHistorySessionId = sessionId
        historyConfirmDialog.open()
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
