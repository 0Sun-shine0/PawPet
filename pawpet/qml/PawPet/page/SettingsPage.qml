import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 设置页：宠物外观、全局热键、启动项、数据与关于。 */
Flickable {
    id: page
    // 自检/截图脚本靠它找到这个滚动区（QML 的 id 不是 objectName）
    objectName: "settingsScroll"
    contentWidth: width
    contentHeight: column.implicitHeight + 24
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    ColumnLayout {
        id: column
        x: 0
        y: 12
        width: page.width
        spacing: Theme.gap

        // 数据迁移提示
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            visible: backend.migrationNote.length > 0
            implicitHeight: migrateText.implicitHeight + 24
            radius: Theme.radiusMd
            color: Qt.rgba(Theme.mint.r, Theme.mint.g, Theme.mint.b, 0.12)
            border.width: 1
            border.color: Qt.rgba(Theme.mint.r, Theme.mint.g, Theme.mint.b, 0.4)

            Text {
                id: migrateText
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: 12
                text: "✨ " + backend.migrationNote
                color: Theme.mint
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }
        }

        // ------------------------------------------------------ 外观
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "宠物外观"
            subtitle: "换形象、调大小，都是立刻生效"

            // ------------------------------------------------ 形象选择
            Text {
                Layout.fillWidth: true
                text: "形象"
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 4
                columnSpacing: 10

                Repeater {
                    model: [
                        { "key": "mochi",   "label": "麻薯猫", "note": "软糯团子" },
                        { "key": "shiba",   "label": "柴犬",   "note": "奶油橘色" },
                        { "key": "penguin", "label": "企鹅",   "note": "黑白圆滚" },
                        { "key": "fox",     "label": "小狐狸", "note": "蓬松大尾" }
                    ]

                    delegate: Rectangle {
                        id: styleCard
                        Layout.fillWidth: true
                        Layout.preferredHeight: 152
                        radius: Theme.radiusLg
                        color: backend.pet_style === modelData.key
                               ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.13)
                               : (styleMouse.containsMouse ? Theme.surfaceHi : Theme.surfaceAlt)
                        border.width: backend.pet_style === modelData.key ? 2 : 1
                        border.color: backend.pet_style === modelData.key
                                      ? Theme.accent : Theme.border

                        Behavior on color { ColorAnimation { duration: Theme.animFast } }

                        MouseArea {
                            id: styleMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: backend.pet_style = modelData.key
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 2

                            Item {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true

                                // 每个卡片里真的画一只，所见即所得
                                Pet {
                                    width: 200
                                    height: 220
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    anchors.verticalCenter: parent.verticalCenter
                                    scale: Math.min(parent.width / 200, parent.height / 220) * 0.96
                                    transformOrigin: Item.Center
                                    style: modelData.key
                                    // 卡片里不跑动画，省得四个一起晃
                                    bob: 0
                                    tailAngle: modelData.key === "shiba" ? -6 : 5
                                    badgeText: ""
                                    ringProgress: 0
                                }
                            }

                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                text: modelData.label
                                color: backend.pet_style === modelData.key ? Theme.accent : Theme.text
                                font.family: Theme.font
                                font.pixelSize: Theme.fsSmall
                                font.bold: backend.pet_style === modelData.key
                            }
                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                text: modelData.note
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    Layout.preferredWidth: 72
                    text: "大小"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawSlider {
                    Layout.fillWidth: true
                    from: 0.6
                    to: 2.0
                    stepSize: 0.05
                    value: backend.pet_scale
                    decimals: 2
                    onMoved: backend.pet_scale = value
                }
                Text {
                    Layout.preferredWidth: 56
                    horizontalAlignment: Text.AlignRight
                    text: Math.round(backend.pet_scale * 100) + "%"
                    color: Theme.text
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsBody
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    Layout.preferredWidth: 72
                    text: "不透明度"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawSlider {
                    Layout.fillWidth: true
                    from: 0.25
                    to: 1.0
                    stepSize: 0.05
                    value: backend.pet_opacity
                    decimals: 2
                    onMoved: backend.pet_opacity = value
                }
                Text {
                    Layout.preferredWidth: 56
                    horizontalAlignment: Text.AlignRight
                    text: Math.round(backend.pet_opacity * 100) + "%"
                    color: Theme.text
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsBody
                }
            }

            PawSwitch {
                Layout.fillWidth: true
                text: "始终显示在最前面"
                checked: backend.always_on_top
                onToggled: backend.always_on_top = checked
            }
            PawSwitch {
                Layout.fillWidth: true
                text: "鼠标移开后自动变淡，不挡视线"
                checked: backend.fade_when_idle
                onToggled: backend.fade_when_idle = checked
            }

            // ------------------------------------------------ 点击行为
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: Theme.borderSoft
            }

            Text {
                Layout.fillWidth: true
                text: "单击小爪时"
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Repeater {
                    model: [
                        { "key": "command",   "label": "弹出指令栏",
                          "hint": "就地输入，回车就走（推荐）" },
                        { "key": "dashboard", "label": "打开工作台",
                          "hint": "直接进完整面板" }
                    ]

                    delegate: Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 54
                        radius: Theme.radiusMd
                        color: backend.pet_click_action === modelData.key
                               ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.14)
                               : (clickMouse.containsMouse ? Theme.surfaceHi : "transparent")
                        border.width: 1
                        border.color: backend.pet_click_action === modelData.key
                                      ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.5)
                                      : Theme.border

                        Behavior on color { ColorAnimation { duration: Theme.animFast } }

                        MouseArea {
                            id: clickMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: backend.pet_click_action = modelData.key
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 11
                            anchors.rightMargin: 11
                            spacing: 9

                            Rectangle {
                                implicitWidth: 16
                                implicitHeight: 16
                                radius: 8
                                color: "transparent"
                                border.width: 2
                                border.color: backend.pet_click_action === modelData.key
                                              ? Theme.accent : Theme.border
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 8
                                    height: 8
                                    radius: 4
                                    color: Theme.accent
                                    visible: backend.pet_click_action === modelData.key
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    text: modelData.label
                                    color: Theme.text
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsSmall
                                    font.bold: backend.pet_click_action === modelData.key
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

            Text {
                Layout.fillWidth: true
                text: "指令栏位置"
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
                visible: backend.pet_click_action === "command"
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                visible: backend.pet_click_action === "command"

                PawButton {
                    text: "跟着小爪"
                    variant: backend.commandBarAnchor === "pet" ? "primary" : "subtle"
                    onClicked: backend.commandBarAnchor = "pet"
                }
                PawButton {
                    text: "屏幕底部居中"
                    variant: backend.commandBarAnchor === "bottom" ? "primary" : "subtle"
                    onClicked: backend.commandBarAnchor = "bottom"
                }
                Item { Layout.fillWidth: true }
            }

            Text {
                Layout.fillWidth: true
                text: "提示：按住小爪左键拖动可以换位置，位置会自动记住；双击打开工作台；右键有快捷菜单。"
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }
        }

        // ------------------------------------------------------ 界面缩放
        // 高分屏上原来的字号明显偏小（1920x1080 的笔记本屏尤其明显，
        // 右边还会空出一大片），所以给一个总开关。
        // 默认按屏幕分辨率自动选，用户觉得小就往右拉。
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "界面大小"
            subtitle: "字太小、或者右边空太多，就调这里"

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    Layout.preferredWidth: 72
                    text: "缩放"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawSlider {
                    Layout.fillWidth: true
                    from: 0.8
                    to: 2.0
                    stepSize: 0.05
                    decimals: 2
                    value: backend.uiScale
                    onMoved: backend.uiScale = value
                }
                Text {
                    Layout.preferredWidth: 56
                    horizontalAlignment: Text.AlignRight
                    text: Math.round(backend.uiScale * 100) + "%"
                    color: Theme.text
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsBody
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: backend.uiScaleHint
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                }
                PawButton {
                    small: true
                    text: "恢复自动"
                    enabled: !backend.uiScaleIsAuto
                    onClicked: backend.resetUiScale(true)
                }
            }
        }

        // ------------------------------------------------------ 界面配色
        //
        // 「通过对话改主题」的可视版。也可以不跟小爪说、直接在这里点。
        //
        // 做成**色块网格**而不是 17 行输入框：泛用户看到 17 个十六进制
        // 输入框会直接放弃。点一个色块展开输入，改完立刻生效。
        // 错误色（rose）不在这里出现 —— 它固定不可改，改了用户就看不到
        // 失败了（见 pawpet/theme.py 的说明）。
        Card {
            id: themeCard
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "界面配色"
            subtitle: "点色块改颜色，改完立刻生效；也能直接跟小爪说「主色调改成蓝的」"

            // 当前展开编辑的那一项（空 = 都没展开）
            property string editing: ""

            GridLayout {
                Layout.fillWidth: true
                columns: 3
                columnSpacing: 8
                rowSpacing: 8

                Repeater {
                    model: backend.themeRoles

                    delegate: Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: swatchRow.implicitHeight + 14
                        radius: Theme.radiusMd
                        color: themeCard.editing === modelData.key
                               ? Theme.surfaceHi : Theme.surfaceAlt
                        border.width: 1
                        border.color: themeCard.editing === modelData.key
                                      ? Theme.accent : Theme.borderSoft

                        Behavior on color { ColorAnimation { duration: Theme.animFast } }

                        RowLayout {
                            id: swatchRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 7

                            // 色块本身：一眼看到当前颜色
                            Rectangle {
                                implicitWidth: 20
                                implicitHeight: 20
                                radius: 5
                                color: backend.themeColors[modelData.key] || modelData.default
                                border.width: 1
                                border.color: Theme.border
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.label
                                    color: Theme.text
                                    font.family: Theme.font
                                    font.pixelSize: Theme.fsSmall
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: backend.themeColors[modelData.key] || modelData.default
                                    color: Theme.textFaint
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.fsTiny
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                themeCard.editing = (themeCard.editing === modelData.key)
                                               ? "" : modelData.key
                                colorField.text = backend.themeColors[modelData.key]
                                                  || modelData.default
                            }
                        }
                    }
                }
            }

            // 展开的编辑行
            RowLayout {
                Layout.fillWidth: true
                visible: themeCard.editing.length > 0
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: {
                        if (themeCard.editing.length === 0)
                            return ""
                        var roles = backend.themeRoles
                        for (var i = 0; i < roles.length; ++i) {
                            if (roles[i].key === themeCard.editing)
                                return "改「" + roles[i].label + "」：" + roles[i].hint
                        }
                        return ""
                    }
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: themeCard.editing.length > 0
                spacing: 8

                PawField {
                    id: colorField
                    Layout.preferredWidth: 140
                    placeholderText: "#4a90d9"
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fsSmall
                }
                PawButton {
                    small: true
                    text: "应用"
                    variant: "primary"
                    onClicked: {
                        var map = {}
                        map[themeCard.editing] = colorField.text.trim()
                        var rejected = backend.applyTheme(map)
                        if (rejected.length > 0)
                            themeHint.text = rejected.join("；")
                        else
                            themeHint.text = "改好了，立刻生效"
                    }
                }
                PawButton {
                    small: true
                    text: "收起"
                    variant: "ghost"
                    onClicked: themeCard.editing = ""
                }
                Item { Layout.fillWidth: true }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    id: themeHint
                    Layout.fillWidth: true
                    text: backend.themeSummary
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    wrapMode: Text.Wrap
                }
                PawButton {
                    small: true
                    text: "恢复默认配色"
                    onClicked: {
                        backend.resetTheme()
                        themeHint.text = "已恢复默认的粉白"
                    }
                }
            }
        }

        // ------------------------------------------------------ 快捷键
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "全局快捷键"
            subtitle: "在任何程序里都能唤出小爪（按 Ctrl+Alt+字母 这种格式写）"

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 12
                rowSpacing: 8

                Text {
                    text: "让小爪做事"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawField {
                    Layout.fillWidth: true
                    text: backend.hotkey_ask
                    font.family: Theme.fontMono
                    onEditingFinished: backend.hotkey_ask = text
                }

                Text {
                    text: "打开工作台"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawField {
                    Layout.fillWidth: true
                    text: backend.hotkey_dashboard
                    font.family: Theme.fontMono
                    onEditingFinished: backend.hotkey_dashboard = text
                }

                Text {
                    text: "开始 / 暂停专注"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawField {
                    Layout.fillWidth: true
                    text: backend.hotkey_focus
                    font.family: Theme.fontMono
                    onEditingFinished: backend.hotkey_focus = text
                }

                Text {
                    text: "快速加待办"
                    color: Theme.textDim
                    font.family: Theme.font
                    font.pixelSize: Theme.fsBody
                }
                PawField {
                    Layout.fillWidth: true
                    text: backend.hotkey_task
                    font.family: Theme.fontMono
                    onEditingFinished: backend.hotkey_task = text
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    Layout.fillWidth: true
                    text: "当前生效：" + backend.hotkeySummary + "（改完需要重启才注册）"
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsSmall
                    wrapMode: Text.Wrap
                }
                PawButton {
                    text: "立即重启"
                    variant: "accent"
                    onClicked: backend.restart()
                }
            }
        }

        // ------------------------------------------------------ 启动
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "启动"
            subtitle: "让小爪跟着电脑一起醒来"

            PawSwitch {
                Layout.fillWidth: true
                text: "开机自动启动（写入注册表 HKCU\\...\\Run）"
                checked: backend.autostart
                onToggled: backend.autostart = checked
            }

            Text {
                Layout.fillWidth: true
                text: "如果这里打开了却看不到效果，可以按 Win+R 输入 shell:startup 检查，或者直接运行项目里的「启动小爪助手.cmd」。"
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }
        }

        // ------------------------------------------------------ 数据
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "数据"
            subtitle: "所有内容只存在你自己的电脑上，不联网、不上传"

            Text {
                Layout.fillWidth: true
                text: "数据文件：" + backend.dataPath
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PawButton {
                    text: "打开数据文件夹"
                    onClicked: backend.openDataFolder()
                }
                PawButton {
                    text: "复制今日总结"
                    glyph: "📋"
                    variant: "ghost"
                    onClicked: backend.exportSummary()
                }
                Item { Layout.fillWidth: true }
            }
        }

        // ------------------------------------------------------ 关于
        Card {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.gap
            Layout.rightMargin: Theme.gap
            title: "关于"
            subtitle: "小爪助手 " + backend.version + " · PySide6 + Qt Quick"

            Text {
                Layout.fillWidth: true
                text: backend.runtimeInfo()
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }

            Text {
                Layout.fillWidth: true
                text: backend.agentStatus()
                color: Theme.textFaint
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PawButton {
                    text: "使用说明"
                    onClicked: backend.openReadme()
                }
                PawButton {
                    text: "重启小爪"
                    variant: "ghost"
                    onClicked: backend.restart()
                }
                PawButton {
                    text: "退出"
                    variant: "danger"
                    onClicked: backend.quit()
                }
                Item { Layout.fillWidth: true }
            }
        }

        Item { Layout.preferredHeight: 12 }
    }
}
