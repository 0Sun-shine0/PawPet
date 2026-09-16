import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 指令栏 —— 日常用 AI 的主入口。

   单击小爪就地弹出，打完回车就走：不用打开工作台，不占视野。
   设计上的几个取舍：

   * 位置跟着小爪走（可改到屏幕底部居中）。点哪儿弹哪儿，视线不用移动。
   * 窗口不抢任务栏、始终置顶、点别处自动收起。
   * AI 在跑的时候不自动收起 —— 否则你切去干别的事就看不到进度了。
   * 审批卡片直接内联在这里。这是关键：如果用了快捷栏却还要开工作台
     才能点「允许」，那这个快捷栏就白做了。
   * Esc 只收起，不打断 —— AI 继续跑，跑完用气泡通知你。 */
Window {
    id: bar
    objectName: "commandBar"

    property var petWindow: null

    readonly property bool busy: backend.ai.running
    readonly property bool hasResult: !busy && backend.ai.lastSummary.length > 0
    readonly property bool showChips: !busy && input.text.length === 0
                              && !backend.ai.hasPendingApproval

    width: 660
    // 高度必须由内容撑开：panel 用了 anchors.fill，它的 implicitHeight 恒为 0。
    // content 上下各有 14 的 margin，panel 上下各有 9 的 margin。
    height: content.implicitHeight + 28 + 18
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
    visible: backend.commandBarVisible
    title: "小爪指令"

    // ------------------------------------------------------------ 生命周期
    onVisibleChanged: {
        if (visible) {
            place()
            raise()
            requestActivate()
            Qt.callLater(function () { input.forceActiveFocus() })
            popAnim.restart()
            hideTimer.stop()
        }
    }

    // 失焦就收起来；但 AI 还在跑时不收，免得看不到进度
    onActiveChanged: {
        if (active)
            hideTimer.stop()
        else if (visible && !busy)
            hideTimer.restart()
    }

    Timer {
        id: hideTimer
        interval: 260
        onTriggered: {
            if (!bar.active && !bar.busy)
                backend.commandBarVisible = false
        }
    }

    onClosing: function (close) {
        close.accepted = false
        backend.commandBarVisible = false
    }

    function place() {
        var px = 0, py = 0, pw = 200, ph = 220
        if (petWindow) {
            px = petWindow.x
            py = petWindow.y
            pw = petWindow.width
            ph = petWindow.height
        }
        var area = backend.screenAt(px + Math.round(pw / 2), py + Math.round(ph / 2))
        var x, y

        if (backend.commandBarAnchor === "bottom") {
            x = area.x + (area.width - width) / 2
            y = area.y + area.height - height - 60
        } else {
            x = px + pw / 2 - width / 2
            y = py - height - 10                 // 优先贴在小爪正上方
            if (y < area.y + 8)
                y = py + ph + 10                 // 上面放不下就放下方
            if (y + height > area.y + area.height - 8)
                y = area.y + area.height - height - 60
            if (y < area.y + 8)
                y = area.y + 8
        }

        bar.x = Math.round(Math.max(area.x + 8,
                                    Math.min(area.x + area.width - width - 8, x)))
        bar.y = Math.round(Math.max(area.y + 8,
                                    Math.min(area.y + area.height - height - 8, y)))
    }

    SequentialAnimation {
        id: popAnim
        ParallelAnimation {
            NumberAnimation {
                target: panel; property: "opacity"; from: 0; to: 1
                duration: Theme.animNormal
            }
            NumberAnimation {
                target: panel; property: "scale"; from: 0.96; to: 1
                duration: Theme.animNormal; easing.type: Easing.OutBack
            }
        }
    }

    function submit() {
        var text = input.text.trim()
        if (text.length === 0 || busy)
            return
        input.text = ""
        backend.ai.send(text)
        input.forceActiveFocus()
    }

    // ================================================================ 面板
    Rectangle {
        id: panel
        anchors.fill: parent
        anchors.margins: 9
        radius: Theme.radiusLg
        color: Theme.surfaceHi
        border.width: backend.ai.hasPendingApproval ? 2 : 1
        border.color: backend.ai.hasPendingApproval
                      ? Theme.gold
                      : Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.45)

        // 状态用**描边颜色**表达，不再画左边那条竖色条。
        // 用户明确说过不要「左边一根大竖线」—— 那是告警框的语汇，
        // 而这个窗口是「小爪在跟你说话」。描边 + 右上角的圆点已经够了。

        ColumnLayout {
            id: content
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            spacing: 9

            // ---------------------------------------------------- 输入行
            RowLayout {
                Layout.fillWidth: true
                spacing: 10

                Text {
                    text: "🐾"
                    font.pixelSize: Theme.px(16)
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 40
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
                        placeholderText: !backend.ai.configured
                                         ? "还没有配置模型，去「设置 → AI 操作」填一个 API Key"
                                         : (bar.busy ? "小爪正在干活…" : "想让小爪做什么？回车发送")
                        color: Theme.text
                        placeholderTextColor: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsBody
                        background: null
                        selectByMouse: true
                        enabled: backend.ai.configured && !bar.busy
                        onAccepted: bar.submit()

                        Keys.onEscapePressed: backend.commandBarVisible = false
                    }
                }

                PawButton {
                    text: bar.busy ? "停止" : "发送"
                    glyph: bar.busy ? "■" : "▶"
                    variant: bar.busy ? "danger" : "primary"
                    implicitWidth: 84
                    implicitHeight: 40
                    enabled: backend.ai.configured
                    onClicked: bar.busy ? backend.ai.stop() : bar.submit()
                }

                PawButton {
                    variant: "ghost"
                    text: "✕"
                    implicitWidth: 34
                    implicitHeight: 40
                    onClicked: backend.commandBarVisible = false
                }
            }

            // ---------------------------------------------------- 状态行
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Rectangle {
                    implicitWidth: 8
                    implicitHeight: 8
                    radius: 4
                    color: bar.busy ? Theme.mint : Theme.textFaint
                    // 浅色底上单靠薄荷色不够显眼，加一圈淡描边把它托起来
                    border.width: 1
                    border.color: bar.busy ? Qt.rgba(Theme.mint.r, Theme.mint.g,
                                                     Theme.mint.b, 0.45)
                                           : Theme.border
                    SequentialAnimation on opacity {
                        running: bar.busy
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.25; duration: 520 }
                        NumberAnimation { to: 1.0; duration: 520 }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: backend.ai.status
                    // 薄荷色当正文太浅了（原来深色底上没问题），
                    // 浅底上要用深一点的字，只让点点担颜色
                    color: bar.busy ? Theme.text : Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    elide: Text.ElideRight
                }

                Text {
                    visible: backend.ai.configured
                    text: "权限：" + (backend.ai.level === "read_only" ? "只读"
                                    : backend.ai.level === "confirm" ? "逐步确认"
                                    : backend.ai.level === "auto" ? "自动执行" : "完全自动")
                    color: backend.ai.level === "full" ? Theme.rose : Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                }
            }

            // ---------------------------------------------------- 审批行
            Rectangle {
                Layout.fillWidth: true
                visible: backend.ai.hasPendingApproval
                implicitHeight: approveRow.implicitHeight + 20
                radius: Theme.radiusMd
                color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.13)
                border.width: 1
                border.color: Qt.rgba(Theme.gold.r, Theme.gold.g, Theme.gold.b, 0.55)

                RowLayout {
                    id: approveRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 11
                    spacing: 10

                    Text {
                        text: "⚠️"
                        font.pixelSize: Theme.px(15)
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            Layout.fillWidth: true
                            // 提问时不加「小爪想」前缀 —— 那句已经是一个完整的问句，
                            // 拼起来会变成「小爪想「下载」里有两个文件夹…」这种病句
                            text: backend.ai.hasPendingQuestion
                                  ? (backend.ai.pendingApproval.summary || "")
                                  : ("小爪想" + (backend.ai.pendingApproval.summary || ""))
                            color: Theme.gold
                            font.family: Theme.font
                            font.pixelSize: Theme.fsSmall
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: backend.ai.hasPendingQuestion
                                  ? "要你拿个主意，答完我接着干"
                                  : "需要你点头才会执行"
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                    }

                    // 提问：给输入框 + 快捷选项（和 AI 页里那张卡片一致）
                    PawField {
                        Layout.fillWidth: true
                        visible: backend.ai.hasPendingQuestion
                        placeholderText: "回答小爪，或者直接说「你自己定」"
                        onAccepted: {
                            if (text.trim().length > 0) {
                                backend.ai.answerPending(text)
                                text = ""
                            }
                        }
                    }

                    PawButton {
                        visible: backend.ai.hasPendingQuestion
                        text: "你自己定"
                        variant: "ghost"
                        small: true
                        onClicked: backend.ai.answerPending("你自己看着办，按最合理的来")
                    }

                    PawButton {
                        text: "允许"
                        glyph: "✓"
                        variant: "primary"
                        small: true
                        visible: !backend.ai.hasPendingQuestion
                        onClicked: backend.ai.resolvePending(true)
                    }
                    PawButton {
                        text: "拒绝"
                        variant: "ghost"
                        small: true
                        visible: !backend.ai.hasPendingQuestion
                        onClicked: backend.ai.resolvePending(false)
                    }
                }
            }

            // ---------------------------------------------------- 结果行
            Rectangle {
                Layout.fillWidth: true
                visible: bar.hasResult
                implicitHeight: resultColumn.implicitHeight + 18
                radius: Theme.radiusMd
                color: Theme.surfaceAlt

                ColumnLayout {
                    id: resultColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 11
                    spacing: 5

                    // 助手回复是 Markdown，用 Python 转好的富文本渲染，
                    // 不然一堆 ** 和 - 会直接糊在脸上
                    Text {
                        Layout.fillWidth: true
                        text: backend.ai.lastSummaryHtml
                        textFormat: Text.RichText
                        color: Theme.text
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                        wrapMode: Text.Wrap
                        maximumLineCount: 6
                        elide: Text.ElideRight
                        lineHeight: 1.35
                        onLinkActivated: function (link) { Qt.openUrlExternally(link) }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        PawButton {
                            small: true
                            variant: "ghost"
                            text: "打开工作台看详情"
                            glyph: "↗"
                            onClicked: {
                                backend.commandBarVisible = false
                                backend.showDashboard("ai")
                            }
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: "Esc 收起"
                            color: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsTiny
                        }
                    }
                }
            }

            // ---------------------------------------------------- 快捷指令
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                visible: bar.showChips

                Repeater {
                    model: [
                        { "label": "看看屏幕", "prompt": "看看我的屏幕上现在有什么，简要说明" },
                        { "label": "解释报错", "prompt": "屏幕上如果有报错或异常，解释它是什么意思、怎么解决" },
                        { "label": "翻译屏幕", "prompt": "把屏幕上主要的外文内容翻译成中文" },
                        { "label": "整理成待办", "prompt": "看看屏幕，把里面提到的待办事项整理出来，逐条加到我的待办里" }
                    ]

                    delegate: PawButton {
                        small: true
                        variant: "subtle"
                        text: modelData.label
                        onClicked: backend.ai.send(modelData.prompt)
                    }
                }

                Item { Layout.fillWidth: true }
            }
        }
    }
}
