r"""把模型输出的 Markdown 渲染出来**看**一眼。

为什么单独做这个：mdtest 检查的是「转出来的 HTML 字符串对不对」，
但用户看到的是**像素**。字号、颜色对比度、行距、`<pre>` 的底色，
这些只有渲染成图才发现得了。

用法：
    .venv\\Scripts\\python.exe tools\\mdshot.py
输出：
    .cache/shots/md-*.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / ".cache" / "shots"
# 必须用真实的 windows 平台插件：offscreen 平台拿不到系统字体，
# 中文会全部渲染成豆腐块（□），那样的截图看不出任何字体问题。
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

SAMPLES = {
    "md-1-加粗列表": """**整理完了。**

- 文档 18 个 → `下载/文档`
- 图片 12 个 → `下载/图片`
- 压缩包 7 个 → `下载/压缩包`

有 1 张图片被占用，留在原地。""",

    "md-2-代码块": """用这段试试：

```python
for i in range(3):
    print(i)
```

行内代码是 `Theme.px(14)` 这样的写法。""",

    "md-3-标题引用": """# 第一步
## 第二步

> 这一页需要登录，你自己输密码，输完我接着干。

分隔线下面：

---

普通段落，可以点这个链接 https://github.com/0Sun-shine0/PawPet 。""",

    "md-4-表格": """| 类型 | 数量 | 去向 |
| --- | --- | --- |
| 文档 | 18 | 下载/文档 |
| 图片 | 12 | 下载/图片 |
| 安装包 | 6 | 下载/安装包 |""",

    "md-5-引用块": """> 这段报错的字面意思是「无法访问该文件」。
> 通常是文件被别的程序占用了。

处理办法：右键 → 属性 → 安全，看是不是只读。""",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.ai.markdown import to_qt_html
    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    scratch = ROOT / ".cache" / "mdshot"
    os.environ["PAWPET_HOME"] = str(scratch)
    os.environ["PAWPET_INSTANCE_SUFFIX"] = "mdshot"
    import shutil
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])

    store = Store(scratch / "pet_data.json", scratch / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)

    # 用真实的 AiBubble 渲染 —— 不然量的是别的控件，白测。
    #
    # 注意必须包在 Window 里：grabToImage 要求 item 已经挂到一个窗口上，
    # 光有 Item 会报 "item is not attached to a window"。
    host = QQmlComponent(engine)
    host.setData(b"""
        import QtQuick
        import QtQuick.Layouts
        import PawPet 1.0
        Window {
            id: win
            property real pw: 620
            property string body: ""
            property string bodyHtml: ""
            property string report: ""
            width: pw
            height: col.implicitHeight + 24
            visible: true
            color: "transparent"
            flags: Qt.FramelessWindowHint
            ColumnLayout {
                id: col
                x: 12
                y: 12
                width: win.width - 24
                spacing: 10
                AiBubble {
                    objectName: "probe"
                    Layout.fillWidth: true
                    role: "assistant"
                    text: win.body
                    html: win.bodyHtml
                    report: win.report
                }
            }
        }
    """, QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "mdhost.qml")))

    root = host.create(engine.rootContext())
    if root is None:
        for error in host.errors():
            print("   ", error.toString())
        return 1

    for _ in range(10):
        app.processEvents()

    from PySide6.QtCore import QObject, Qt
    probe = root.findChild(QObject, "probe", Qt.FindChildrenRecursively)

    def shoot(name: str) -> None:
        for _ in range(12):
            app.processEvents()
        img = root.grabWindow()
        if img.isNull():
            print(f"    {name}: 抓图失败")
            return
        img.save(str(OUT / f"{name}.png"))
        print(f"    {name}.png  {img.width()}x{img.height()}")

    for name, raw in SAMPLES.items():
        root.setProperty("body", raw)
        root.setProperty("bodyHtml", to_qt_html(raw))
        root.setProperty("report", "")
        shoot(name)

    # 带「收尾交代」的样子
    root.setProperty("body", "整理好了：43 个文件分成了 4 类。")
    root.setProperty("bodyHtml", to_qt_html("整理好了：43 个文件分成了 4 类。"))
    root.setProperty("report",
                     "这一轮做了 5 个操作，另有 1 个没成功。"
                     "文件位置：D:\\下载\\文档、D:\\下载\\图片。"
                     "只是移动位置，没有删除任何东西。")
    shoot("md-6-带交代")

    print(f"\n输出目录：{OUT}")
    backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
