import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 首次启动引导：三屏，随时可跳过。

   为什么要这个东西：用户装完打开，屏幕上只有一只宠物飘着。他不知道
   能拖、能双击、能右键，更不知道有七个页面 —— README 里全写了，
   但装桌面宠物的人不看 README。

   三条设计约束：

   1. **必须能跳过，而且跳过之后产品依然完整。** 待办、专注、便签、
       提醒都不需要配模型，第三屏讲 AI 的时候要明确说这一点。把引导
       做成「不配 Key 不让用」是劝退。

   2. **每屏只讲一件事。** 三屏各一个小图形 + 一句标题 + 两行说明。
       字多了没人读 —— 这是引导，不是帮助文档。

   3. **只该出现一次。** 状态存在 settings.onboarding_done 里，不靠
       「数据文件是否为空」判断（老用户升级上来数据是满的，但引导
       从没看过）。设置页里可以手动再调出来。

   窗口是无边框卡片。拖动用 startSystemMove（和 Dashboard 标题栏一致），
   这样在不同 DPI 下不会因为手动算位移而漂移。 */
Window {
    id: onboarding

    readonly property int cardWidth: Theme.px(500)
    readonly property int cardHeight: Theme.px(432)

    width: cardWidth
    height: cardHeight
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    title: "小爪助手 · 上手指引"
    visible: false

    // 当前是第几屏。0/1/2 共三屏。
    property int step: 0

    readonly property var steps: [
        {
            "icon": "pet",
            "title": "这是小爪，它会一直待在你桌面上",
            "body": "按住左键拖动能换位置，位置会自动记住。\n"
                    + "双击打开工作台，右键有快捷菜单。",
            "hint": "任何时候按 " + backend.hotkey_ask + " 都能直接跟它说话"
        },
        {
            "icon": "local",
            "title": "你的东西都存在这台电脑上",
            "body": "待办、专注、便签、提醒 —— 都不联网，不上传。\n"
                    + "AI 关掉也完全不影响这四个功能。",
            "hint": "设置 → 数据里可以导出备份，换电脑能带走"
        },
        {
            "icon": "spark",
            "title": "想让它更聪明，就给它一个模型",
            "body": "配上模型之后，它能看屏幕、替你操作软件。\n"
                    + "这需要你自己去服务商拿一个 Key —— 现在不配也没关系。",
            "hint": "随时能在「AI 操作」页里配，不急"
        }
    ]

    function start() {
        step = 0
        place()
        visible = true
        raise()
        requestActivate()
        intro.restart()
    }

    function place() {
        var area = backend.primaryScreenArea()
        x = Math.round(area.x + (area.width - width) / 2)
        y = Math.round(area.y + (area.height - height) / 2)
    }

    Connections {
        target: backend
        function onScreenGeometryChanged() {
            if (onboarding.visible)
                onboarding.place()
        }
    }

    // 关掉引导。done=true 表示用户走完了流程（两种都算走完）。
    // 具体落到哪个设置项由 Python 侧决定 —— QML 不直接碰 store。
    function finish(done) {
        visible = false
        backend.finishOnboarding(done)
    }

    function next() {
        if (step < steps.length - 1) {
            step += 1
        } else {
            finish(true)
        }
    }

    function back() {
        if (step > 0)
            step -= 1
    }

    SequentialAnimation {
        id: intro
        PropertyAction { target: card; property: "opacity"; value: 0 }
        PropertyAction { target: card; property: "scale"; value: 0.97 }
        ParallelAnimation {
            NumberAnimation {
                target: card; property: "opacity"
                to: 1; duration: Theme.animNormal
            }
            NumberAnimation {
                target: card; property: "scale"
                to: 1; duration: Theme.animNormal
                easing.type: Easing.OutCubic
            }
        }
    }

    // 整张卡片都能拖 —— 无边框窗口没有标题栏可抓
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        onPressed: onboarding.startSystemMove()
    }

    Rectangle {
        id: card
        anchors.fill: parent
        anchors.margins: Theme.px(10)
        radius: Theme.radiusXl
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderSoft

        // 外层再来一圈极淡的描边当柔光，和气泡同一套做法 ——
        // 浅色界面不靠阴影分层，看起来会「贴」在壁纸上。
        Rectangle {
            anchors.fill: parent
            anchors.margins: -1
            z: -1
            radius: parent.radius + 1
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.px(26)
            spacing: 0

            // ------------------------------------------------ 顶部：跳过
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Row {
                    spacing: 5
                    Repeater {
                        model: onboarding.steps.length
                        delegate: Rectangle {
                            width: Theme.px(6)
                            height: Theme.px(6)
                            radius: Theme.px(3)
                            anchors.verticalCenter: parent.verticalCenter
                            color: index === onboarding.step
                                   ? Theme.accent
                                   : Theme.surfaceHi
                            Behavior on color { ColorAnimation { duration: Theme.animFast } }
                        }
                    }
                }

                Item { Layout.fillWidth: true }

                PawButton {
                    small: true
                    variant: "ghost"
                    text: "跳过"
                    onClicked: onboarding.finish(false)
                }
            }

            Item { Layout.fillHeight: true; Layout.preferredHeight: Theme.px(6) }

            // ------------------------------------------------ 图形
            Item {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: Theme.px(120)
                Layout.preferredHeight: Theme.px(120)

                // 第 1 屏：一张简化的猫脸。用矩形/圆角矩形拼出来，
                // 不用 Canvas —— 打包时少一个潜在依赖，而且静态图形
                // 用 Canvas 属于杀鸡用牛刀。
                Item {
                    id: petGlyph
                    anchors.fill: parent
                    visible: onboarding.steps[onboarding.step].icon === "pet"

                    Circle {
                        anchors.centerIn: parent
                        size: Theme.px(92)
                        tint: Theme.accentSoft
                    }
                    // 耳朵
                    Rectangle {
                        width: Theme.px(22); height: Theme.px(22)
                        radius: Theme.px(6)
                        x: Theme.px(20); y: Theme.px(16)
                        rotation: -18
                        color: Theme.accent
                        opacity: 0.75
                    }
                    Rectangle {
                        width: Theme.px(22); height: Theme.px(22)
                        radius: Theme.px(6)
                        x: Theme.px(78); y: Theme.px(16)
                        rotation: 18
                        color: Theme.accent
                        opacity: 0.75
                    }
                    // 眼睛
                    Circle {
                        x: Theme.px(40); y: Theme.px(48)
                        size: Theme.px(9)
                        tint: Theme.petEye
                    }
                    Circle {
                        x: Theme.px(71); y: Theme.px(48)
                        size: Theme.px(9)
                        tint: Theme.petEye
                    }
                    // 腮红
                    Circle {
                        x: Theme.px(30); y: Theme.px(64)
                        size: Theme.px(11)
                        tint: Theme.petBlush
                        opacity: 0.55
                    }
                    Circle {
                        x: Theme.px(79); y: Theme.px(64)
                        size: Theme.px(11)
                        tint: Theme.petBlush
                        opacity: 0.55
                    }
                    // 一点点拖动的暗示：右侧两个小箭头
                    Text {
                        x: Theme.px(96); y: Theme.px(50)
                        text: "⇔"
                        color: Theme.textFaint
                        font.family: Theme.fontLatin
                        font.pixelSize: Theme.px(14)
                    }
                }

                // 第 2 屏：本地存储 —— 一个文档 + 一把锁
                Item {
                    anchors.fill: parent
                    visible: onboarding.steps[onboarding.step].icon === "local"

                    Circle {
                        anchors.centerIn: parent
                        size: Theme.px(92)
                        tint: Theme.mintSoft
                    }
                    Rectangle {
                        x: Theme.px(44); y: Theme.px(30)
                        width: Theme.px(34); height: Theme.px(44)
                        radius: Theme.px(6)
                        color: Theme.surface
                        border.width: 1
                        border.color: Theme.mint
                        // 几行「文字」
                        Column {
                            anchors.centerIn: parent
                            spacing: Theme.px(4)
                            Repeater {
                                model: 3
                                delegate: Rectangle {
                                    width: Theme.px(18)
                                    height: Theme.px(3)
                                    radius: Theme.px(1.5)
                                    color: Theme.mint
                                    opacity: 0.5
                                }
                            }
                        }
                    }
                    // 锁扣
                    Rectangle {
                        x: Theme.px(52); y: Theme.px(66)
                        width: Theme.px(18); height: Theme.px(15)
                        radius: Theme.px(4)
                        color: Theme.mint
                        Rectangle {
                            x: Theme.px(4); y: -Theme.px(7)
                            width: Theme.px(10); height: Theme.px(10)
                            radius: Theme.px(5)
                            color: "transparent"
                            border.width: Theme.px(2)
                            border.color: Theme.mint
                        }
                    }
                }

                // 第 3 屏：模型 —— 一颗星
                Item {
                    anchors.fill: parent
                    visible: onboarding.steps[onboarding.step].icon === "spark"

                    Circle {
                        anchors.centerIn: parent
                        size: Theme.px(92)
                        tint: Theme.violetSoft
                    }
                    Text {
                        anchors.centerIn: parent
                        text: "✦"
                        color: Theme.violet
                        font.family: Theme.fontLatin
                        font.pixelSize: Theme.px(38)
                    }
                }
            }

            Item { Layout.fillHeight: true; Layout.preferredHeight: Theme.px(10) }

            // ------------------------------------------------ 文案
            Text {
                Layout.fillWidth: true
                text: onboarding.steps[onboarding.step].title
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsTitle
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }

            Item { Layout.preferredHeight: Theme.px(8) }

            Text {
                Layout.fillWidth: true
                text: onboarding.steps[onboarding.step].body
                color: Theme.textDim
                font.family: Theme.font
                font.pixelSize: Theme.fsBody
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                lineHeight: 1.45
            }

            Item { Layout.fillHeight: true }

            // 小提示条：第三屏尤其重要 —— 不配 Key 也不影响使用
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: hintText.implicitHeight + Theme.px(14)
                radius: Theme.radiusSm
                color: Theme.surfaceAlt

                Text {
                    id: hintText
                    anchors.centerIn: parent
                    width: parent.width - Theme.px(20)
                    text: onboarding.steps[onboarding.step].hint
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }

            Item { Layout.preferredHeight: Theme.px(14) }

            // ------------------------------------------------ 底部按钮
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                PawButton {
                    visible: onboarding.step > 0
                    text: "上一步"
                    variant: "ghost"
                    onClicked: onboarding.back()
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: (onboarding.step + 1) + " / " + onboarding.steps.length
                    color: Theme.textFaint
                    font.family: Theme.font
                    font.pixelSize: Theme.fsTiny
                    // 在布局里必须用 Layout.alignment。
                    // 这里原来写的是 anchors.verticalCenter，Qt 会警告
                    // 「anchors on an item managed by a layout」——
                    // 属于未定义行为，换个数量的屏就可能错位。
                    Layout.alignment: Qt.AlignVCenter
                }

                Item { Layout.preferredWidth: Theme.px(4) }

                PawButton {
                    variant: "primary"
                    text: onboarding.step === onboarding.steps.length - 1
                          ? "开始用" : "下一步"
                    onClicked: onboarding.next()
                }
            }
        }
    }

    // Escape 也能关 —— 用户按 Esc 的意图就是「别烦我」
    Shortcut {
        sequence: "Escape"
        onActivated: onboarding.finish(false)
    }

    // 内部小工具：一个正圆。
    // Rectangle 只能做圆角矩形，「圆」要靠 radius = size/2 —— 写三遍容易
    // 漏掉一处，抽出来统一。
    component Circle: Rectangle {
        property int size: Theme.px(10)
        property color tint: Theme.accent
        width: size
        height: size
        radius: size / 2
        color: tint
    }
}
