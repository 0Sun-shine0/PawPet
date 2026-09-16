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

    implicitHeight: Math.max(
        bubble.isUser ? userShell.height : 0,
        Math.max(bubble.isAssistant ? assistantShell.height : 0,
                 Math.max(bubble.isTool ? toolShell.height : 0,
                          Math.max(bubble.isError ? errorShell.height : 0,
                                   infoShell.height))))

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
    Rectangle {
        id: assistantShell
        visible: bubble.isAssistant
        // 用隐藏的 Text 量自然宽度，再和上限取小。
        // 不能直接拿 assistantText.implicitWidth —— 它的宽度依赖本矩形，
        // 那样会形成绑定循环。
        width: Math.min(bubble.width * 0.94,
                        Math.max(200, assistantMeasure.implicitWidth + 36))
        height: assistantText.implicitHeight
                + (bubble.report.length > 0 ? reportText.implicitHeight + 43 : 26)
        radius: Theme.radiusLg
        color: Theme.surfaceHi
        border.width: 1
        border.color: Theme.border

        Text {
            id: assistantMeasure
            visible: false
            textFormat: Text.RichText
            text: bubble.richText
            font: assistantText.font
        }

        Text {
            id: assistantText
            x: 18
            y: 13
            width: parent.width - 36
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
        // 用 anchors 而不是再套一层 Column：外层高度是手算的，
        // 嵌套容器的 implicitHeight 和 anchors 混用很容易算出循环绑定。
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
