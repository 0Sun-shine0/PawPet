import QtQuick
import PawPet 1.0

/* 应用根节点：把三个窗口组织起来，并在 QML 内部完成信号接线。
   Python 侧因此完全不需要 findChild 或反射调用 QML 方法。

   QtObject 没有默认属性，所以三个窗口挂成具名属性；
   preview.py 也是通过 root.property("petWindow") 拿到它们的。 */
QtObject {
    id: root

    property PetWindow petWindow: PetWindow {
        objectName: "petWindow"
    }

    property BubbleWindow bubbleWindow: BubbleWindow {
        objectName: "bubbleWindow"
    }

    property Dashboard dashboardWindow: Dashboard {
        objectName: "dashboardWindow"
    }

    property CommandBar commandBar: CommandBar {
        objectName: "commandBar"
        petWindow: root.petWindow
    }

    property Connections link: Connections {
        target: backend

        function onBubbleRequested(kind, title, body) {
            root.bubbleWindow.show(title, body, kind,
                                   root.petWindow.x, root.petWindow.y,
                                   root.petWindow.width, root.petWindow.height)
        }

        function onShowDashboardRequested(page) {
            root.dashboardWindow.goTo(page)
            root.dashboardWindow.show()
        }

        function onHideDashboardRequested() {
            root.dashboardWindow.hide()
        }
    }
}
