# -*- mode: python ; coding: utf-8 -*-
"""安装程序的 PyInstaller 配置。

把 build/setup_ui.py 和程序本体（压缩成 payload.zip）一起打成一个 exe。
用户拿到的就是这一个文件，双击即可安装。

为什么用 tkinter 做安装界面：它随 Python 附带，打进去只有十几 MB。
用 PySide6 的话安装器自己就要 150MB —— 为了装一个程序再套一个同样大的
安装器，没必要。

用法（跑 tools/build.py --installer 就行）：
    .venv\\Scripts\\pyinstaller.exe --noconfirm --clean build/setup.spec
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent          # noqa: F821 - PyInstaller 注入

# payload.zip 是 tools/make_setup.py 事先压好的程序本体
payload = ROOT / "build" / "payload.zip"

a = Analysis(                         # noqa: F821
    [str(ROOT / "build" / "setup_ui.py")],
    pathex=[str(ROOT / "build")],
    binaries=[],
    datas=[(str(payload), ".")] if payload.exists() else [],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 安装器不需要的东西，排掉让 exe 尽量小
        "PySide6", "shiboken6", "numpy", "cv2", "mss", "pyautogui",
        "unittest", "pydoc", "doctest", "lib2to3", "distutils", "pip",
        "setuptools", "email", "xmlrpc", "pdb", "curses",
        "PyInstaller",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                     # noqa: F821

# 单文件模式：把 scripts / binaries / datas 全部塞进 EXE，不要 COLLECT。
# 这才是 spec 里写 onefile 的正确方式 —— EXE 没有 onefile 这个参数。
exe = EXE(                            # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="小爪助手-安装程序",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                    # 双击不弹控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "build" / "pawpet.ico")
         if (ROOT / "build" / "pawpet.ico").exists() else None,
)
