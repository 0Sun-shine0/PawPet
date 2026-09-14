import QtQuick
import PawPet 1.0

/* 一只眼睛。
   单独拆成文件是因为 QML 的内联组件必须声明在文档根作用域里，
   而这里需要在多个地方按不同尺寸复用。

   结构很简单：深色眼珠 + 一个主高光 + 一个小高光。
   眨眼时眼珠压扁成一条线，用 Behavior 做出顺滑的收放。 */
Item {
    id: eye

    property real eyeW: 22
    property real eyeH: 24
    property bool shut: false
    property real shiftX: 0
    property real shiftY: 0
    property color eyeColor: "#3E3548"
    property color glint: "#FFFFFF"

    implicitWidth: eyeW
    implicitHeight: eyeH

    Rectangle {
        anchors.centerIn: parent
        width: eye.eyeW
        height: eye.shut ? Math.max(2.5, eye.eyeH * 0.13) : eye.eyeH
        radius: height / 2
        color: eye.eyeColor

        Behavior on height {
            NumberAnimation { duration: 80; easing.type: Easing.InOutQuad }
        }
    }

    // 主高光
    Rectangle {
        x: eye.eyeW * 0.16
        y: eye.eyeH * 0.13
        width: eye.eyeW * 0.34
        height: width
        radius: width / 2
        color: eye.glint
        opacity: eye.shut ? 0.0 : 0.95
        Behavior on opacity { NumberAnimation { duration: 80 } }
    }

    // 次高光，让眼睛看起来是湿润的
    Rectangle {
        x: eye.eyeW * 0.58
        y: eye.eyeH * 0.56
        width: eye.eyeW * 0.17
        height: width
        radius: width / 2
        color: eye.glint
        opacity: eye.shut ? 0.0 : 0.55
        Behavior on opacity { NumberAnimation { duration: 80 } }
    }

    transform: Translate {
        x: eye.shut ? 0 : eye.shiftX
        y: eye.shut ? 0 : eye.shiftY
    }
}
