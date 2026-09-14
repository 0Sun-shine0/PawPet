import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 便签页：左边列表、右边编辑，输入即自动保存。 */
Item {
    id: page

    property bool loading: false
    property string hintText: "输入即自动保存"
    property color hintColor: Theme.textFaint

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.gap
        spacing: Theme.gap

        // ------------------------------------------------------ 左侧列表
        Rectangle {
            Layout.preferredWidth: 210
            Layout.fillHeight: true
            radius: Theme.radiusLg
            color: Theme.surface
            border.width: 1
            border.color: Theme.borderSoft

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8

                PawButton {
                    Layout.fillWidth: true
                    text: "新建便签"
                    glyph: "＋"
                    variant: "accent"
                    onClicked: {
                        backend.newNote()
                        page.loading = true
                        titleField.text = backend.notes.currentTitle
                        bodyArea.text = backend.notes.currentText
                        page.loading = false
                    }
                }

                ListView {
                    id: noteList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: backend.notes
                    boundsBehavior: Flickable.StopAtBounds
                    currentIndex: backend.notes.currentIndex
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        id: noteRow
                        width: noteList.width
                        implicitHeight: noteColumn.implicitHeight + 14
                        radius: Theme.radiusMd
                        color: noteList.currentIndex === index ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
                                                               : (noteMouse.containsMouse ? Theme.surfaceHi : "transparent")
                        border.width: 1
                        border.color: noteList.currentIndex === index ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.5)
                                                                      : "transparent"

                        Behavior on color { ColorAnimation { duration: Theme.animFast } }

                        MouseArea {
                            id: noteMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (backend.notes.currentIndex !== index) {
                                    backend.notes.currentIndex = index
                                    page.loadCurrent()
                                }
                            }
                        }

                        ColumnLayout {
                            id: noteColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 9
                            spacing: 2

                            Text {
                                Layout.fillWidth: true
                                text: model.title
                                color: Theme.text
                                font.family: Theme.font
                                font.pixelSize: Theme.fsBody
                                font.bold: noteList.currentIndex === index
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: model.preview
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                                elide: Text.ElideRight
                            }
                            Text {
                                text: model.updatedText
                                color: Theme.textFaint
                                font.family: Theme.font
                                font.pixelSize: Theme.fsTiny
                                opacity: 0.7
                            }
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------ 右侧编辑
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusLg
            color: Theme.surface
            border.width: 1
            border.color: Theme.borderSoft

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    PawField {
                        id: titleField
                        Layout.fillWidth: true
                        placeholderText: "便签标题"
                        onTextChanged: page.touchSave()
                    }

                    PawButton {
                        text: "复制"
                        variant: "ghost"
                        onClicked: backend.copyText(bodyArea.text)
                    }
                    PawButton {
                        text: "删除"
                        variant: "ghost"
                        onClicked: {
                            backend.deleteNote(backend.notes.currentId)
                            page.loadCurrent()
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: Theme.radiusMd
                    color: Theme.surfaceAlt
                    border.width: 1
                    border.color: bodyArea.activeFocus ? Theme.accent : Theme.border

                    Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 4
                        clip: true

                        TextArea {
                            id: bodyArea
                            placeholderText: "随手写点什么…内容会自动保存。"
                            color: Theme.text
                            placeholderTextColor: Theme.textFaint
                            font.family: Theme.font
                            font.pixelSize: Theme.fsBody
                            wrapMode: TextArea.Wrap
                            selectByMouse: true
                            background: null
                            padding: 10
                            onTextChanged: page.touchSave()
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: page.hintText
                        color: page.hintColor
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: bodyArea.length + " 字"
                        color: Theme.textFaint
                        font.family: Theme.font
                        font.pixelSize: Theme.fsSmall
                    }
                }
            }
        }
    }

    // 自动保存：停止输入 700ms 后写入
    Timer {
        id: saveTimer
        interval: 700
        onTriggered: {
            backend.saveNote(backend.notes.currentId, titleField.text, bodyArea.text)
            page.hintText = "已自动保存 · " + Qt.formatTime(new Date(), "hh:mm:ss")
            page.hintColor = Theme.mint
        }
    }

    function touchSave() {
        if (page.loading)
            return
        page.hintText = "正在输入…"
        page.hintColor = Theme.textFaint
        saveTimer.restart()
    }

    function loadCurrent() {
        page.loading = true
        titleField.text = backend.notes.currentTitle
        bodyArea.text = backend.notes.currentText
        page.loading = false
        page.hintText = "输入即自动保存"
        page.hintColor = Theme.textFaint
    }

    Component.onCompleted: loadCurrent()

    Connections {
        target: backend.notes
        function onCurrentChanged() {
            if (backend.notes.currentId !== "")
                page.loadCurrent()
        }
    }
}
