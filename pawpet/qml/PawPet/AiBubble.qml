import QtQuick
import QtQuick.Layouts
import PawPet 1.0

/* 一条聊天记录的气泡。
   角色决定外观，全部通过属性绑定，不做命令式赋值 ——
   这样 ListView 复用 delegate 时不会出现数据错位。

   两个渲染上的要点：
   * 助手回复走 RichText（html 字段是 Python 那边把 Markdown 转好的），
     所以 `**加粗**`、`- 列表`、`# 标题` 会显示成真正的格式，而不是一堆星号。
   * 凡是要显示外部原始文本的地方（工具输出、命令回显）都显式设成
     PlainText。Qt 的 Text 默认是 AutoText，遇到尖括号会当 HTML 解析，
     命令输出里一个 < 就能把整块显示搞乱。 */
Item {
    id: bubble

    property string role: "info"        // user | assistant | tool | error | info
    property string text: ""
    property string html: ""            // 仅助手回复有
    property string plain: ""           // 助手回复的纯文本版（量宽度用）
    property string detail: ""
    property string toolName: ""
    property string risk: ""
    property string recovery: ""       // 失败时给「接下来怎么办」的一行说明
    // 「做完有交代」：干了几件事、文件存哪了、能不能撤回。
    // 由执行层如实统计（模型自己总结容易把「试过但失败」写成「已完成」）。
    property string report: ""
    property bool ok: true
    property string stamp: ""
    property real seconds: 0
    property string imageSource: ""

    readonly property bool isUser: role === "user"
    readonly property bool isAssistant: role === "assistant"
    readonly property bool isTool: role === "tool"
    readonly property bool isError: role === "error"

    readonly property color accent: isUser ? Theme.violet
                                  : isTool ? (ok ? Theme.mint : Theme.rose)
                                  : isError ? Theme.rose
                                  : Theme.mint

    readonly property string richText: html.length > 0 ? html : text

    // 用来量「自然宽度」的纯文本（只给 TextMetrics 用）。
    // 助手消息的 html 里带 <b>/<code> 这类标签，宽度得按标签剥掉之后算，
    // 否则量出来偏宽。text 对助手消息本来就已经是纯文本了
    // （controller 的 to_plain 结果存在 plain 字段里），所以优先用它。
    readonly property string plainText: {
        if (isAssistant && plain.length > 0)
            return plain
        // 兜底：把尖括号标签粗略剥掉。这里量宽度，不需要多精确
        return text.replace(/<[^>]*>/g, "")
    }

    // 高度取「当前这个角色那个壳子」的高度。
    //
    // **不能写成 `Math.max(...)` 把所有壳子都算进来。** 原来就是这么写的，
    // 而没显示的那些壳子照样有高度（它们只是 visible=false），于是
    // 一个不相干的壳子会把气泡撑高 —— 实测 assistantShell 需要 119px，
    // 结果被隐藏的 infoShell（132.75）顶掉了，气泡比内容高出 14px，
    // 而且这 14px 是空的、看着像排版坏了。
    // 每个壳子都是 anchors.fill 之外的独立矩形，同一时刻只会显示一个，
    // 所以按角色取就行。
    implicitHeight: {
        if (bubble.isUser)      return userShell.height
        if (bubble.isAssistant) return assistantShell.height
        if (bubble.isTool)      return toolShell.height
        if (bubble.isError)     return errorShell.height
        return infoShell.height
    }
    // ------------------------------------------------------------ 用户消息
    // 用户自己打的内容原样显示，不做 Markdown 解析 —— 否则他打的星号会被吃掉
    Rectangle {
        id: userShell
        visible: bubble.isUser
        anchors.right: parent.right
        width: Math.min(bubble.width * 0.78, userText.implicitWidth + 34)
        height: userText.implicitHeight + 22
        radius: Theme.radiusLg
        color: Theme.violet

        Text {
            id: userText
            anchors.centerIn: parent
            width: parent.width - 26
            text: bubble.text
            textFormat: Text.PlainText
            color: "#ffffff"
            font.family: Theme.font
            font.pixelSize: Theme.fsBody
            wrapMode: Text.Wrap
        }
    }

    // ------------------------------------------------------------ 助手消息
    //
    // **这个气泡的尺寸有讲究，别随手改。** 四个坑都踩过，记下来免得重犯：
    //
    // 1. 高度手算成 `正文implicitHeight + 交代implicitHeight + 常数`，
    //    而交代 Text 的宽度依赖气泡宽度、高度又依赖宽度 ——
    //    「高度 → 宽度 → 高度」连成环，实测文字**溢出气泡外面**、
    //    被宠物挡住（用户截图里就是那个样子）。
    // 2. 换成 anchors 接力：环没了，但高度**不刷新** —— 连续换内容时
    //    外壳停在旧高度，多行正文被压成一行高。
    // 3. 换成 ColumnLayout + `height: column.implicitHeight`：
    //    column 用 anchors.fill 填父级、父级高度又读 column 的隐式高度，
    //    等于自己量自己 —— 隐式高度恒为 19px。
    // 4. 用隐藏 Text 量自然宽度：**不给 width** 会按第一次的值定住
    //    （实测停在 80px）；**给了 width 又允许换行**时 implicitWidth
    //    变成「换行后最宽那行」（实测整段正文只量出 164px）。
    //
    // 现在这样写的，链路严格单向：
    //   宽度 ← TextMetrics 的自然宽度（纯文本的函数，不含几何）
    //   高度 ← 正文在**最终宽度**下折行的高度 + 交代的高度
    //   三者的宽度都只依赖 bubble.width（外层 Item 的宽度，ListView 给的）
    readonly property real maxShellWidth: bubble.width * 0.94
    readonly property real padH: 36          // 左右各 18
    readonly property real padV: 26          // 上下各 13

    TextMetrics {
        id: bodyNatural
        font.family: Theme.font
        font.pixelSize: Theme.fsBody
        text: bubble.plainText
    }

    Rectangle {
        id: assistantShell
        // 自检脚本靠它量高度（QML 的 id 不是 objectName）
        objectName: "assistantShell"
        visible: bubble.isAssistant
        width: Math.min(bubble.maxShellWidth, bodyNatural.width + bubble.padH)
        // 高度 = 上下内边距 + 正文折行高度
        //        +（有交代时）间距 9 + 分隔线 1 + 间距 7 + 交代折行高度
        //
        // 注意读的是 bodyProbe.**implicitHeight**，不是 .height。
        // 写 .height 的话那个属性的值会被布局写回，和这里的绑定打架 ——
        // 实测外壳停在上一轮的 119px 而实际需要 133px。implicitHeight
        // 是纯测量值，没人会去写它，读它才安全。
        height: bubble.padV
                + Math.max(bodyProbe.implicitHeight,
                           assistantText.font.pixelSize * 1.4)
                + (bubble.report.length > 0 ? 17 + reportMeasure.implicitHeight : 0)
        radius: Theme.radiusLg
        color: Theme.surfaceHi
        border.width: 1
        border.color: Theme.border

        // 正文在**最终宽度**下折行之后有多高。
        // 宽度只依赖 bubble.width，和本矩形的高度无关，所以是单向的。
        Text {
            id: bodyProbe
            visible: false
            width: Math.max(120, assistantShell.width - 36)
            text: bubble.richText
            textFormat: Text.RichText
            font: assistantText.font
            wrapMode: Text.Wrap
            lineHeight: 1.38
        }

        Text {
            id: assistantText
            x: 18
            y: 13
            width: parent.width - 36
            // 正文为空时（理论上很少见）高度会算成 0，交代就会被顶到
            // 内边距外面去。给一个下限 = 一行文字。
            height: Math.max(implicitHeight, font.pixelSize * 1.4)
            text: bubble.richText
            textFormat: Text.RichText
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: Theme.fsBody
            wrapMode: Text.Wrap
            lineHeight: 1.38
            onLinkActivated: function (link) { Qt.openUrlExternally(link) }
        }

        // ---------------------------------------------------- 做完的交代
        // 三个问题一次答完：干了几件事、东西在哪、能不能撤回。
        // 用一条细分隔线和小字，不抢正文的视线。
        //
        // 交代的高度也要在同一个宽度下量（锚点接力会不刷新，见上面第 2 条）。
        Text {
            id: reportMeasure
            visible: false
            width: Math.max(120, assistantShell.width - 36)
            text: bubble.report
            textFormat: Text.PlainText
            font.family: Theme.font
            font.pixelSize: Theme.fsTiny
            wrapMode: Text.Wrap
            lineHeight: 1.4
        }

        Rectangle {
            id: reportLine
            anchors.top: assistantText.bottom
            anchors.topMargin: 9
            x: 18
            width: parent.width - 36
            height: 1
            color: Theme.border
            visible: bubble.report.length > 0
        }

        Text {
            id: reportText
            anchors.top: reportLine.bottom
            anchors.topMargin: 7
            x: 18
            width: parent.width - 36
            text: bubble.report
            textFormat: Text.PlainText
            color: Theme.textFaint
            font.family: Theme.font
            font.pixelSize: Theme.fsTiny
            wrapMode: Text.Wrap
            lineHeight: 1.4
            visible: bubble.report.length > 0
        }
    }

    // ------------------------------------------------------------ 动作卡片
    Rectangle {
        id: toolShell
        visible: bubble.isTool
        width: bubble.width
        height: toolColumn.implicitHeight + 20
        radius: Theme.radiusMd
        color: Theme.surfaceAlt
        border.width: 1
        border.color: bubble.ok ? Theme.border
                                : Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.42)

        ColumnLayout {
            id: toolColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: 12
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Rectangle {
                    implicitWidth: 22
                    implicitHeight: 22
                    radius: 11
                    color: Qt.rgba(bubble.accent.r, bubble.accent.g, bubble.accent.b, 0.2)
                    Text {
                        anchors.centerIn: parent
                        text: bubble.ok ? "✓" : "✕"
                        color: bubble.accent
                        font.pixelSize: Theme.px(11)
                        font.bold: true
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: bubble.text
                    textFormat: Text.PlainText
                    color: Theme.text
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    elide: Text.ElideRight
                }

                Rectangle {
                    visible: bubble.risk === "danger"
                    implicitWidth: 42
                    implicitHeight: 18
                    radius: 9
                    color: Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.22)
                    Text {
                        anchors.centerIn: parent
                        text: "高危"
                        color: Theme.rose
                        font.family: Theme.font
                        font.pixelSize: Theme.px(9)
                        font.bold: true
                    }
                }

                Text {
                    text: bubble.seconds > 0 ? bubble.seconds.toFixed(1) + "s" : ""
                    color: Theme.textFaint
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsTiny
                }
            }

            // 截图缩略图：这是「AI 真的看到了屏幕」最直观的证据
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: bubble.imageSource.length > 0
                                       ? Math.min(230, parent.width * 0.6) : 0
                visible: bubble.imageSource.length > 0
                radius: Theme.radiusSm
                color: Theme.surfaceHi
                border.width: 1
                border.color: Theme.border
                clip: true

                Image {
                    anchors.fill: parent
                    anchors.margins: 3
                    source: bubble.imageSource
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                    sourceSize.width: 640
                }
            }

            // 工具输出可能是命令回显，里面什么都可能有 —— 必须当纯文本
            Text {
                Layout.fillWidth: true
                visible: bubble.detail.length > 0
                text: bubble.detail
                textFormat: Text.PlainText
                color: Theme.textFaint
                font.family: Theme.fontMono
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.Wrap
                maximumLineCount: 5
                elide: Text.ElideRight
            }

            // 失败时的一行「接下来怎么办」。让用户看得懂它是在换路，
            // 而不是在原地卡死。
            Text {
                Layout.fillWidth: true
                visible: !bubble.ok && bubble.recovery.length > 0
                text: "→ " + bubble.recovery
                color: Theme.gold
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.Wrap
            }
        }
    }

    // ------------------------------------------------------------ 错误提示
    Rectangle {
        id: errorShell
        visible: bubble.isError
        width: Math.min(bubble.width * 0.94,
                        Math.max(220, errorMeasure.implicitWidth + 36))
        height: errorColumn.implicitHeight + 22
        radius: Theme.radiusMd
        color: Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.10)
        border.width: 1
        border.color: Qt.rgba(Theme.rose.r, Theme.rose.g, Theme.rose.b, 0.42)

        Text {
            id: errorMeasure
            visible: false
            textFormat: Text.RichText
            text: bubble.richText
            font: errorTitle.font
        }

        ColumnLayout {
            id: errorColumn
            x: 18
            y: 11
            width: parent.width - 36
            spacing: 5

            Text {
                id: errorTitle
                Layout.fillWidth: true
                text: "⚠ " + bubble.richText
                textFormat: Text.RichText
                color: Theme.rose
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                font.bold: true
                wrapMode: Text.Wrap
                lineHeight: 1.35
            }
            Text {
                Layout.fillWidth: true
                visible: bubble.detail.length > 0
                text: bubble.detail
                textFormat: Text.PlainText
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsTiny
                wrapMode: Text.Wrap
            }
        }
    }

    // ------------------------------------------------------------ 普通信息
    Rectangle {
        id: infoShell
        visible: !bubble.isUser && !bubble.isAssistant && !bubble.isTool && !bubble.isError
        width: bubble.width
        height: infoText.implicitHeight + 18
        radius: Theme.radiusMd
        color: Qt.rgba(Theme.mint.r, Theme.mint.g, Theme.mint.b, 0.08)
        border.width: 1
        border.color: Qt.rgba(Theme.mint.r, Theme.mint.g, Theme.mint.b, 0.22)

        Text {
            id: infoText
            anchors.centerIn: parent
            width: parent.width - 26
            text: bubble.text
            textFormat: Text.PlainText
            color: Theme.textDim
            font.family: Theme.font
            font.pixelSize: Theme.fsSmall
            wrapMode: Text.Wrap
            lineHeight: 1.35
        }
    }
}
