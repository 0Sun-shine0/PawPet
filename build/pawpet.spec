# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（一般不用直接调它，跑 tools/build.py 就行）：
    .venv\\Scripts\\pyinstaller.exe --noconfirm --clean build/pawpet.spec

几个关键点：

* **onedir 而不是 onefile**。onefile 每次启动都要把上百 MB 解压到临时目录，
  启动要等好几秒；桌宠是要开机自启常驻的，那个体验不能接受。
* **QML 必须作为 data 带上**，而且目录结构要保持 `pawpet/qml/...`，
  因为 config.py 是按这个相对路径找的。
* **排除掉用不到的大件**。PySide6-Essentials 里 QtWebEngine、Qt3D、
  QtCharts 之类我们一个都没用，排掉能省几十 MB。
* **console=False**，双击不弹黑框；出错信息通过 MessageBox 弹出来。
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent          # noqa: F821 - SPECPATH 是 PyInstaller 注入的

hiddenimports = [
    # Qt 的模块**不要**写在这里。
    # PySide6 的模块是编译进 Qt 的 C++ 库，PyInstaller 靠 hook-PySide6.*
    # 来收集它们。把 "PySide6.QtQuick" 当普通模块塞进 hiddenimports 反而
    # 会让引导阶段去 import 一个并不存在的 Python 模块，
    # 结果就是启动时弹「could not import module 'PySide6.QtQuick'」。
    # 我们只依赖 PySide6.QtQml / QtQuickControls2 等被 hook 自动覆盖的模块。
]

# ---------------------------------------------------------------- 资源清单
datas = [
    # QML 界面（必须保留 pawpet/qml 这层目录结构，config.py 是按这个相对路径找的）
    (str(ROOT / "pawpet" / "qml"), "pawpet/qml"),
    # 给用户看的说明
    (str(ROOT / "build" / "使用说明.md"), "."),
    # 随包发布的 MCP server（pawkit 那 8 个工具）。
    #
    # **必须打进去**：它是 .py 脚本、不在 import 图里，PyInstaller 的静态
    # 分析看不到它 —— 不写这行的话，装出来的小爪在界面上会显示
    # 「没有内置的 server：pawkit」，而那正是「MCP 做了但从没生效过」
    # 的原因之一。
    #
    # 只取 .py，**不带 __pycache__**：开发机上跑过测试之后那里会留一堆
    # 字节码，整目录拷进去会白塞几十 KB 陈旧 .pyc 给用户。
    *[(str(p), "mcp_servers") for p in sorted((ROOT / "mcp_servers").glob("*.py"))],
]

# AI 的可选依赖不写进 datas，交给 PyInstaller 自己分析 import 关系带上。
# 手动塞整个 site-packages 目录会把 cv2 的 112MB 重复打进去（它还会被
# 自动分析再收一次），而且会把测试数据和文档一起拖进来。
#
# 这些包如果装了，hiddenimports 负责确保它们被收进来；
# 没装也不影响 —— ai/ 里的导入都包在 try/except 里。
for _optional in ("mss", "numpy", "cv2", "pyautogui"):
    try:
        __import__(_optional)
        hiddenimports.append(_optional)
    except ImportError:
        pass

# ---------------------------------------------------------------- 不打的包
excludes = [
    # 标准库里确实用不到的
    "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
    "distutils", "pip", "xmlrpc", "pdb", "curses",
    # 注意：不要排 sqlite3 / email / http。pyautogui、numpy 之类会间接引用，
    # 排掉之后报的是运行时的 ModuleNotFoundError，只有真跑才知道。
    # PySide6 里我们没用到的模块，排掉能省很多体积。
    # 注意：QtQml / QtQuick / QtQuickControls2 / QtQuickTemplates2 / QtNetwork
    # 这些是必需的，绝对不能排 —— 排掉之后 QML 引擎起不来，
    # 表现就是启动时弹「could not import module」。
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSpatialAudio",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql",
    "PySide6.QtNetworkAuth", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtUiTools", "PySide6.QtStateMachine", "PySide6.QtSvgWidgets",
    # 别的 GUI 框架和数据分析库
    "PyQt5", "PyQt6", "wx", "matplotlib", "pandas", "scipy",
    # 打包工具自身
    "PyInstaller",
]


a = Analysis(                       # noqa: F821
    [str(ROOT / "run_pawpet.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                   # noqa: F821

exe = EXE(                          # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PawPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 压缩过的 exe 经常被杀软误报，关掉
    console=False,                  # 双击不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "build" / "pawpet.ico") if (ROOT / "build" / "pawpet.ico").exists() else None,
)

coll = COLLECT(                     # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PawPet",
)
