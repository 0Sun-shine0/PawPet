import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import PawPet 1.0

Dialog {
    id: dialog

    property string heading: "确认操作"
    property string message: ""
    property string confirmText: "确认"
    property string cancelText: "取消"
    property string confirmVariant: "danger"

    signal confirmed()

    modal: true
    focus: true
    anchors.centerIn: parent
    padding: 20
    width: Math.max(240, Math.min(420,
                                  parent && parent.width > 0
                                  ? parent.width - 32 : 420))
    closePolicy: Popup.CloseOnEscape

    background: Rectangle {
        color: Theme.surface
        radius: Theme.radiusLg
        border.width: 1
        border.color: Theme.border
    }

    contentItem: ColumnLayout {
        spacing: 12

        Text {
            Layout.fillWidth: true
            text: dialog.heading
            color: Theme.text
            font.family: Theme.font
            font.pixelSize: Theme.fsTitle
            font.bold: true
            wrapMode: Text.Wrap
        }

        Text {
            Layout.fillWidth: true
            text: dialog.message
            color: Theme.textDim
            font.family: Theme.font
            font.pixelSize: Theme.fsBody
            wrapMode: Text.Wrap
            lineHeight: 1.35
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Item { Layout.fillWidth: true }

            PawButton {
                objectName: "confirmCancelButton"
                small: true
                text: dialog.cancelText
                variant: "ghost"
                onClicked: dialog.close()
            }

            PawButton {
                objectName: "confirmAcceptButton"
                small: true
                text: dialog.confirmText
                variant: dialog.confirmVariant
                onClicked: {
                    dialog.confirmed()
                    dialog.close()
                }
            }
        }
    }
}
