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
    // 「外部工具」的详情（server 清单、工具名、配置路径）。
    // **默认收起**：那部分有 200px 高，常驻会把右栏撑到 2000px 以上 ——
    // 「太占视野」是用户明确抱怨过的。标题行（状态点 + 开关）常驻就够用了。
    property bool showMcpDetail: false

    // 给自测用的只读探针：两栏的真实宽度
    readonly property real leftColumnWidth: leftColumn.width
    readonly property real rightColumnWidth: rightColumn.width

    RowLayout {
        id: aiRow
        objectName: "aiRow"          // 联调脚本靠它量布局
        anchors.fill: parent
        anchors.margins: Theme.gap
        spacing: Theme.gap

        // ============================================================ 左栏
        ColumnLayout {
            id: leftColumn
            objectName: "aiLeftColumn"
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
        //
        // 右栏包一层 Flickable + Item，**不能**直接把 ColumnLayout 放进
        // RowLayout。原因：列里有「对话区」这张带 Layout.fillHeight 的卡片，
        // 它想「填满剩余空间」，而列又想「按内容撑高」—— 循环依赖。
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
        // 对话区仍然留在滚动区里、保持固定高度 —— 它一旦 fillHeight 就会
        // 和「按内容撑高」的父级形成环（第 1 条那个 bug）。
        //
        // 两个孩子的 Layout 属性都是必需的：
        //   * 滚动区 fillHeight 才吃得到剩余空间；
        //   * 输入卡片 preferredHeight 才能**先**拿到自己的高度 ——
        //     只写 implicitHeight 的话实测被挤到 y=716（视口才 696）。
        ColumnLayout {
            id: rightPane
            objectName: "aiRightPane"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 380
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
                height: rightColumn.implicitHeight
        ColumnLayout {
            id: rightColumn
            objectName: "aiRightColumn"
            width: rightBody.width
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

            // -------------------------------------------------- 历史对话
            // 对话持久化之后，这是「翻回去看上次怎么处理的」的入口。
            // 用户的原话是「反复处理同类工单」，所以列表要能一眼看出
            // 每个会话是干什么的（title 取第一条用户消息）。
            Rectangle {
                id: historyCard
                Layout.fillWidth: true
                visible: page.showHistory
                implicitHeight: historyColumn.implicitHeight + 26
                radius: Theme.radiusLg
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                ColumnLayout {
                    id: historyColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 14
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text {
                            Layout.fillWidth: true
                            text: backend.ai.conversationCount > 0
                                  ? ("历史对话（" + backend.ai.conversationCount + " 段）")
                                  : "历史对话"
                            color: Theme.text
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                            font.bold: true
                        }
                        PawButton {
                            small: true
                            variant: "ghost"
                            text: "清空全部"
                            enabled: backend.ai.conversationCount > 1
                            onClicked: backend.ai.clearHistory()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: backend.ai.conversationCount === 0
                        text: "还没有历史。对话会自动存下来，关掉小爪再打开也还在。"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsTiny
                        wrapMode: Text.Wrap
                    }

                    // 最多显示最近 12 段：列表太长会把对话区顶没，
                    // 而且再往前的记录本来也很少翻。
                    Repeater {
                        model: {
                            var all = backend.ai.conversations
                            return all.length > 12 ? all.slice(0, 12) : all
                        }

                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: convCol.implicitHeight + 14
                            radius: Theme.radiusMd
                            color: modelData.isCurrent ? Theme.surfaceHi
                                                       : (convMouse.containsMouse
                                                          ? Theme.surfaceHi
                                                          : Theme.surfaceAlt)
                            border.width: 1
                            border.color: modelData.isCurrent
                                          ? Qt.rgba(Theme.accent.r, Theme.accent.g,
                                                    Theme.accent.b, 0.5)
                                          : Theme.borderSoft

                            Behavior on color { ColorAnimation { duration: Theme.animFast } }

                            ColumnLayout {
                                id: convCol
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 10
                                anchors.rightMargin: 8
                                spacing: 2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.title || "（没有标题）"
                                        color: Theme.text
                                        font.family: Theme.font
                                        font.pixelSize: Theme.fsSmall
                                        font.bold: modelData.isCurrent
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        visible: modelData.isCurrent
                                        text: "当前"
                                        color: Theme.accent
                                        font.family: Theme.font
                                        font.pixelSize: Theme.fsTiny
                                        font.bold: true
                                    }
                                    Text {
                                        text: modelData.count + " 条"
                                        color: Theme.textFaint
                                        font.family: Theme.font
                                        font.pixelSize: Theme.fsTiny
                                    }
                                    // 删单条。放右边不抢注意力，但要用的时候找得到。
                                    Rectangle {
                                        implicitWidth: 18
                                        implicitHeight: 18
                                        radius: 9
                                        color: delMouse.containsMouse
                                               ? Theme.surfaceHi : "transparent"
                                        Text {
                                            anchors.centerIn: parent
                                            text: "✕"
                                            color: delMouse.containsMouse
                                                   ? Theme.rose : Theme.textFaint
                                            font.family: Theme.fontLatin
                                            font.pixelSize: Theme.px(10)
                                        }
                                        MouseArea {
                                            id: delMouse
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: backend.ai.deleteConversation(modelData.id)
                                        }
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.preview
                                    color: Theme.textFaint
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsTiny
                                    elide: Text.ElideRight
                                }
                            }

                            MouseArea {
                                id: convMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                // 已经打开的那个不用再切一次（会白刷一遍界面）
                                enabled: !modelData.isCurrent
                                onClicked: backend.ai.openConversation(modelData.id)
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "存在本地：" + backend.ai.historyFolder()
                        color: Theme.textFaint
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fsTiny
                        elide: Text.ElideMiddle
                    }
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
            Rectangle {
                Layout.fillWidth: true
                // **没配好模型时必须自动展开。** 以前只在点了「设置」按钮
                // 之后才显示，而泛用户根本不知道要点那个按钮 —— 他会对着
                // 「还没有配置模型」的提示发呆。配好之后才允许收起。
                visible: page.showSettings || !backend.ai.configured
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

                        Text {
                            Layout.fillWidth: true
                            text: "第一次用？两步：拿 Key → 粘到下面"
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
                                text: page.showProviders ? "收起其他服务商" : "想用别的服务商？"
                                onClicked: page.showProviders = !page.showProviders
                            }
                            PawButton {
                                small: true
                                variant: "ghost"
                                visible: page.showProviders
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
                            visible: page.showProviders
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
                                visible: page.showMemory
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

                    // -------------------------------------------------- 知识库
                    // 导入自己的资料，AI 回答时能引用。
                    // 正文**不会**全塞进对话 —— 按需检索，所以可以导很多。
                    Rectangle {
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
                                visible: page.showKnowledge
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
                                    text: page.showKnowledge ? "收起" : "看看导入了什么"
                                    onClicked: page.showKnowledge = !page.showKnowledge
                                }
                                Item { Layout.fillWidth: true }
                                PawButton {
                                    small: true
                                    text: "清空"
                                    enabled: backend.aiKnowledgeCount > 0
                                    onClicked: {
                                        backend.aiClearKnowledge()
                                        kbResult.text = ""
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
            Rectangle {
                id: mcpCard
                objectName: "mcpCard"
                Layout.fillWidth: true
                implicitHeight: mcpColumn.implicitHeight + (page.showMcpDetail ? 26 : 18)
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
                            text: page.showMcpDetail ? "收起" : "详情"
                            onClicked: page.showMcpDetail = !page.showMcpDetail
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
                            visible: page.showMcpDetail && backend.ai.mcpEnabled
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
                        visible: page.showMcpDetail && backend.ai.mcpEnabled

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
                        visible: page.showMcpDetail && !backend.ai.mcpEnabled
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
                        visible: page.showMcpDetail
                        text: "想加自己的 server？改这个文件：" + backend.ai.mcpConfigPath
                        color: Theme.textFaint
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fsTiny
                        elide: Text.ElideMiddle
                    }
                }
            }

            // -------------------------------------------------- 对话区
            Rectangle {
                Layout.fillWidth: true
                // 对话区**不要**用 Layout.fillHeight。
                //
                // 放在可滚动的列里时它是个陷阱：fillHeight 想「填满剩余空间」，
                // 列又想「按内容撑高」，形成循环依赖 —— 实测这张卡片要么被撑到
                // 995px 把别的卡片顶出去，要么被压成 0 高（对话完全看不见）。
                //
                // 改成「给一个合适的高度 + 下限」：高度确定，列就能正确算出
                // 自己的隐式高度，内容太高时交给外层 Flickable 滚动。
                // 对话消息本来就在自己的 ListView 里，会自动滚到最新一条。
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
                        role: modelData.role
                        text: modelData.text
                        html: modelData.html || ""
                        plain: modelData.plain || ""
                        detail: modelData.detail
                        toolName: modelData.tool
                        risk: modelData.risk
                        ok: modelData.ok
                        stamp: modelData.time
                        seconds: modelData.seconds
                        imageSource: modelData.image
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
                Layout.fillWidth: true
                visible: backend.ai.hasPendingQuestion
                implicitHeight: askColumn.implicitHeight + 26
                radius: Theme.radiusLg
                color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.09)
                border.width: 2
                border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.45)

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
                Layout.fillWidth: true
                // 提问卡和审批卡不能同时出现，样式也完全不同
                visible: backend.ai.hasPendingApproval && !backend.ai.hasPendingQuestion
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
                Layout.fillWidth: true
                visible: backend.ai.hasPendingBatch
                implicitHeight: batchColumn.implicitHeight + 26
                radius: Theme.radiusLg
                color: Qt.rgba(Theme.violet.r, Theme.violet.g, Theme.violet.b, 0.10)
                border.width: 2
                border.color: Qt.rgba(Theme.violet.r, Theme.violet.g, Theme.violet.b, 0.65)

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
