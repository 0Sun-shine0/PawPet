import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

/* 便签页：左边列表、右边编辑，输入即自动保存。 */
Item {
    id: page
    objectName: "notesPage"

    property bool loading: false
    property bool dirty: false
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
                    onClicked: page.createNote()
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
                                    page.flushSave()
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
                        objectName: "noteTitleField"
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
                            page.flushSave()
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
                            objectName: "noteBodyArea"
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
                        text: page.dirty ? "未保存修改 · Ctrl+S 立即保存" : page.hintText
                        color: page.dirty ? Theme.gold : page.hintColor
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
        onTriggered: page.saveNow()
    }

    Timer {
        id: undoTimer
        interval: 6000
        onTriggered: backend.notes.clearUndo()
    }

    Connections {
        target: backend.notes
        function onUndoChanged() {
            if (backend.notes.canUndo)
                undoTimer.restart()
            else
                undoTimer.stop()
        }
    }

    Rectangle {
        objectName: "noteUndoBar"
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.gap
        z: 10
        width: Math.min(360, Math.max(260, parent.width - Theme.gap * 2))
        height: 52
        radius: Theme.radiusMd
        visible: backend.notes.canUndo
        color: Theme.surfaceHi
        border.width: 1
        border.color: Theme.accent

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 8
            spacing: 8

            Text {
                Layout.fillWidth: true
                text: "已删除便签：" + backend.notes.lastRemovedTitle
                color: Theme.text
                font.family: Theme.font
                font.pixelSize: Theme.fsSmall
                elide: Text.ElideRight
            }
            PawButton {
                small: true
                text: "撤销"
                variant: "accent"
                onClicked: backend.notes.undoRemove()
            }
        }
    }

    function touchSave() {
        if (page.loading)
            return
        page.dirty = true
        page.hintText = "正在输入…"
        page.hintColor = Theme.textFaint
        saveTimer.restart()
    }

    function saveNow() {
        if (page.loading || !page.dirty || backend.notes.currentId === "")
            return
        backend.saveNote(backend.notes.currentId, titleField.text, bodyArea.text)
        page.dirty = false
        page.hintText = "已自动保存 · " + Qt.formatTime(new Date(), "hh:mm:ss")
        page.hintColor = Theme.mint
    }

    function flushSave() {
        saveTimer.stop()
        page.saveNow()
    }

    function createNote() {
        page.flushSave()
        backend.newNote()
        page.loadCurrent()
        titleField.forceActiveFocus()
        titleField.selectAll()
    }

    function loadCurrent() {
        saveTimer.stop()
        page.loading = true
        titleField.text = backend.notes.currentTitle
        bodyArea.text = backend.notes.currentText
        page.loading = false
        page.dirty = false
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

    Shortcut {
        sequence: "Ctrl+S"
        context: Qt.WindowShortcut
        enabled: page.visible
        onActivated: page.flushSave()
    }

    Shortcut {
        sequence: "Ctrl+N"
        context: Qt.WindowShortcut
        enabled: page.visible
        onActivated: page.createNote()
    }
}
