"""安装程序的界面与逻辑。

这是一个用 tkinter 写的轻量安装向导。选 tkinter 而不是 PySide6，是因为
安装器本身要被 PyInstaller 打成一个独立 exe：tkinter 随 Python 附带，
打进去只有十几 MB；换成 PySide6 光运行库就上百 MB，为了装一个 300MB 的
程序再套一个 150MB 的安装器，太蠢了。

它做这些事：
  1. 让用户选安装目录
  2. 选是否创建桌面快捷方式、是否开机自启
  3. 把内嵌的程序解压出来
  4. 创建开始菜单 / 桌面快捷方式（用 .lnk，走 COM）
  5. 写卸载信息到注册表，并放一个卸载程序
  6. 可选：装完直接启动

卸载程序是同一个 exe 加 `--uninstall` 参数，逻辑在 uninstall() 里。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "小爪助手"
APP_ID = "PawPet"
APP_VERSION = "2.1.0"
APP_EXE = "PawPet.exe"

# 卸载信息写在 HKCU，不需要管理员权限
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 主题色，和主程序保持一致
BG = "#14121c"
CARD = "#1e1b2b"
FIELD = "#262238"
TEXT = "#f2ecff"
DIM = "#a99fc4"
FAINT = "#6f6790"
ACCENT = "#ff9d6c"
VIOLET = "#8b7bf0"
BORDER = "#3a3352"

FONT = "Microsoft YaHei UI"


def resource_path(name: str) -> Path:
    """取打包进 exe 的资源。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def default_install_dir() -> Path:
    """默认装到用户目录下，这样不需要管理员权限。"""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "Programs" / APP_ID


def create_shortcut(link: Path, target: Path, workdir: Path,
                    description: str = "", icon: Path | None = None) -> bool:
    """用 WScript.Shell 创建 .lnk 快捷方式。

    走 COM 而不是写 .url 文件，因为只有 .lnk 才能带图标、才能在
    开始菜单里正常显示、才能被任务栏固定。
    """
    script = f'''
Set shell = CreateObject("WScript.Shell")
Set link = shell.CreateShortcut("{link}")
link.TargetPath = "{target}"
link.WorkingDirectory = "{workdir}"
link.Description = "{description}"
{f'link.IconLocation = "{icon}"' if icon else ""}
link.Save
'''
    temp = Path(os.environ.get("TEMP", ".")) / f"pawpet_link_{os.getpid()}.vbs"
    try:
        # VBS 里中文要用 Unicode，用 utf-16 写才不会被当 ANSI 解析
        temp.write_text(script, encoding="utf-16")
        result = subprocess.run(
            ["cscript", "//nologo", "//B", str(temp)],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0 and link.exists()
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def set_autostart(enabled: bool, exe: Path) -> bool:
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, f'"{exe}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    """取桌面目录。

    不能直接拼 ~/Desktop：中文系统或开了 OneDrive 同步时，
    桌面可能在别的地方。问注册表最准。
    """
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
            return Path(value)
    except OSError:
        return Path.home() / "Desktop"


class InstallerWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.install_dir = tk.StringVar(value=str(default_install_dir()))
        self.make_desktop = tk.BooleanVar(value=True)
        self.make_autostart = tk.BooleanVar(value=False)
        self.launch_after = tk.BooleanVar(value=True)
        self.progress_text = tk.StringVar(value="准备就绪")
        self.progress_value = tk.DoubleVar(value=0.0)
        self.busy = False

        self._build()

    def _draw_paw(self, canvas: tk.Canvas) -> None:
        """在画布上画一个爪印 logo（和 exe 图标同一套形状）。"""
        canvas.create_rectangle(0, 0, 52, 52, fill=CARD, outline=CARD)
        # 掌垫
        canvas.create_oval(14, 23, 38, 42, fill=ACCENT, outline="")
        # 四个脚趾
        for x1, y1, x2, y2 in ((9, 15, 18, 24), (19, 9, 28, 18),
                               (30, 9, 39, 18), (40, 15, 49, 24)):
            canvas.create_oval(x1, y1, x2, y2, fill=ACCENT, outline="")

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        self.root.title(f"{APP_NAME} {APP_VERSION} 安装程序")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        width, height = 560, 430
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - width) // 2
        y = (screen_h - height) // 3
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        # ---------------------------------------------------------- 顶部
        header = tk.Frame(self.root, bg=CARD, height=96)
        header.pack(fill="x")
        header.pack_propagate(False)

        # 用图形画一个爪印当 logo。
        # 不用 emoji：tkinter 的 Label 渲染不了彩色 emoji，
        # 「🐾」在中文 Windows 上会显示成方框。
        logo = tk.Canvas(header, width=52, height=52, bg=CARD,
                         highlightthickness=0, bd=0)
        logo.place(x=26, y=22)
        self._draw_paw(logo)

        tk.Label(header, text=APP_NAME, bg=CARD, fg=TEXT,
                 font=(FONT, 20, "bold")).place(x=92, y=20)
        tk.Label(header, text=f"版本 {APP_VERSION} · 桌面待办 / 番茄钟 / 提醒 / AI 助手",
                 bg=CARD, fg=DIM, font=(FONT, 9)).place(x=94, y=58)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=18)

        # ------------------------------------------------------ 安装目录
        tk.Label(body, text="安装位置", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(anchor="w")

        dir_row = tk.Frame(body, bg=BG)
        dir_row.pack(fill="x", pady=(5, 16))
        # 用 grid 让输入框自动占满剩余宽度，按钮紧贴在右边。
        # 之前用 pack(side="right") 时按钮会被挤到窗口最右边，
        # 和输入框之间空出一大块，看起来像断开了。
        dir_row.columnconfigure(0, weight=1)

        self.dir_entry = tk.Entry(
            dir_row, textvariable=self.install_dir, bg=FIELD, fg=TEXT,
            insertbackground=TEXT, relief="flat", font=(FONT, 9),
        )
        self.dir_entry.grid(row=0, column=0, sticky="ew", ipady=7)

        tk.Button(
            dir_row, text="浏览…", command=self._choose_dir,
            bg=FIELD, fg=DIM, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=(FONT, 9), cursor="hand2", padx=12,
        ).grid(row=0, column=1, padx=(7, 0), ipady=4)

        # ---------------------------------------------------------- 选项
        tk.Label(body, text="选项", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(anchor="w")

        options = tk.Frame(body, bg=BG)
        options.pack(fill="x", pady=(5, 16))

        for text, var in (
            ("创建桌面快捷方式", self.make_desktop),
            ("开机时自动启动小爪", self.make_autostart),
            ("安装完成后立即运行", self.launch_after),
        ):
            cb = tk.Checkbutton(
                options, text=text, variable=var, bg=BG, fg=TEXT,
                activebackground=BG, activeforeground=TEXT, selectcolor=FIELD,
                font=(FONT, 9), anchor="w", cursor="hand2",
                highlightthickness=0, bd=0,
            )
            cb.pack(anchor="w", pady=1)

        # ------------------------------------------------------ 进度
        tk.Label(body, text="进度", bg=BG, fg=DIM,
                 font=(FONT, 9)).pack(anchor="w")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # 轨道色要比背景亮一点，否则进度条整条看不见。
        # 之前把 troughcolor 设成 FIELD(#262238)，在 BG(#14121c) 上几乎融成一片。
        style.configure(
            "Paw.Horizontal.TProgressbar",
            troughcolor=BORDER,        # 比背景明显
            background=ACCENT,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=10,
        )
        self.bar = ttk.Progressbar(
            body, variable=self.progress_value, maximum=100.0,
            style="Paw.Horizontal.TProgressbar",
        )
        self.bar.pack(fill="x", pady=(5, 6))

        # 进度条下面给一个初值，让用户知道它是有进度的（0% 时看不出区别）
        self.progress_value.set(0.0)

        tk.Label(body, textvariable=self.progress_text, bg=BG, fg=FAINT,
                 font=(FONT, 8), anchor="w").pack(fill="x")

        # ------------------------------------------------------ 按钮
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=28, pady=(0, 20))

        self.install_btn = tk.Button(
            footer, text="开始安装", command=self._on_install,
            bg=ACCENT, fg="#2a1c16", activebackground="#ffb98a",
            activeforeground="#2a1c16", relief="flat",
            font=(FONT, 10, "bold"), cursor="hand2", padx=26,
        )
        self.install_btn.pack(side="right", ipady=7)

        self.cancel_btn = tk.Button(
            footer, text="取消", command=self._on_cancel,
            bg=FIELD, fg=DIM, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", font=(FONT, 10), cursor="hand2", padx=20,
        )
        self.cancel_btn.pack(side="right", padx=(0, 8), ipady=7)

    # ---------------------------------------------------------------- 交互
    def _choose_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="选择安装位置",
            initialdir=str(Path(self.install_dir.get()).parent),
        )
        if chosen:
            self.install_dir.set(chosen)

    def _on_cancel(self) -> None:
        if self.busy:
            return
        if messagebox.askyesno(APP_NAME, "确定要取消安装吗？"):
            self.root.destroy()

    def _on_install(self) -> None:
        if self.busy:
            return
        target = Path(self.install_dir.get().strip())
        if not target.name:
            messagebox.showerror(APP_NAME, "请选择一个安装位置。")
            return

        # 装到 C:\Program Files 需要管理员权限，提前说清楚，
        # 免得用户装到一半看到「拒绝访问」一头雾水
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".pawpet-probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except OSError:
            messagebox.showerror(
                APP_NAME,
                f"没有权限写入这个位置：\n{target}\n\n"
                "请换一个目录（比如默认的「用户目录\\Programs\\PawPet」），"
                "或者用管理员身份重新运行安装程序。",
            )
            return

        self.busy = True
        self.install_btn.config(state="disabled", text="安装中…")
        self.cancel_btn.config(state="disabled")
        self.dir_entry.config(state="disabled")
        threading.Thread(target=self._do_install, args=(target,), daemon=True).start()

    def _set_progress(self, percent: float, text: str) -> None:
        self.progress_value.set(percent)
        self.progress_text.set(text)
        self.root.update_idletasks()

    # ---------------------------------------------------------------- 安装
    def _do_install(self, target: Path) -> None:
        try:
            payload = resource_path("payload.zip")
            if not payload.exists():
                raise FileNotFoundError(f"安装包不完整，找不到 {payload}")

            # ---------------------------------------------- 1. 解压
            self._set_progress(2, "正在准备…")
            with zipfile.ZipFile(payload) as archive:
                names = archive.namelist()
                total = len(names) or 1

                # 先清掉旧版本的文件（保留用户数据 —— 数据不在安装目录，
                # 但绿色版可能把数据放在这里，所以只删程序文件）
                if target.exists():
                    for item in target.iterdir():
                        if item.name in ("pet_data.json", "pet_data.backup.json", ".env"):
                            continue
                        try:
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                item.unlink()
                        except OSError:
                            pass

                for index, name in enumerate(names, 1):
                    archive.extract(name, target)
                    if index % 40 == 0 or index == total:
                        percent = 2 + (index / total) * 76
                        self._set_progress(percent, f"正在解压… {index}/{total}")

            exe = target / APP_EXE
            if not exe.exists():
                raise FileNotFoundError("解压后没有找到主程序 PawPet.exe")

            # ---------------------------------------------- 2. 快捷方式
            self._set_progress(80, "正在创建快捷方式…")
            icon = exe

            start_menu = start_menu_dir() / APP_NAME
            start_menu.mkdir(parents=True, exist_ok=True)
            if not create_shortcut(start_menu / f"{APP_NAME}.lnk", exe, target,
                                   f"{APP_NAME} {APP_VERSION}", icon):
                self._set_progress(81, "开始菜单快捷方式创建失败（不影响使用）")

            if self.make_desktop.get():
                create_shortcut(desktop_dir() / f"{APP_NAME}.lnk", exe, target,
                                f"{APP_NAME} {APP_VERSION}", icon)

            # ---------------------------------------------- 3. 自启
            self._set_progress(86, "正在配置启动项…")
            set_autostart(bool(self.make_autostart.get()), exe)

            # ---------------------------------------------- 4. 卸载信息
            self._set_progress(90, "正在写入卸载信息…")
            self._write_uninstall_info(target, exe)

            # ---------------------------------------------- 5. 卸载程序
            self._set_progress(95, "正在准备卸载程序…")
            self._copy_uninstaller(target)

            self._set_progress(100, "安装完成")
            self.root.after(0, lambda: self._finish(target, exe))

        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._fail(error))

    def _write_uninstall_info(self, target: Path, exe: Path) -> None:
        import winreg

        uninstaller = target / "uninstall.exe"
        size_kb = 0
        for item in target.rglob("*"):
            try:
                if item.is_file():
                    size_kb += item.stat().st_size // 1024
            except OSError:
                pass

        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
                winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
                winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "PawPet")
                winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(target))
                winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
                winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ,
                                   f'"{uninstaller}"')
                winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ,
                                   f'"{uninstaller}" --silent')
                winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        except OSError:
            pass

    def _copy_uninstaller(self, target: Path) -> None:
        """把安装器自己复制成 uninstall.exe。

        同一个 exe，带 --uninstall 参数运行时就走卸载流程。
        比单独维护一个卸载程序简单得多。
        """
        if not getattr(sys, "frozen", False):
            return
        source = Path(sys.executable)
        dest = target / "uninstall.exe"
        try:
            shutil.copy2(source, dest)
        except OSError:
            pass

    def _finish(self, target: Path, exe: Path) -> None:
        self.install_btn.config(state="normal", text="完成", command=self.root.destroy)
        self.cancel_btn.pack_forget()

        if self.launch_after.get():
            try:
                subprocess.Popen([str(exe)], cwd=str(target), close_fds=True)
            except OSError:
                pass

        messagebox.showinfo(
            APP_NAME,
            f"安装完成！\n\n"
            f"程序位置：{target}\n"
            f"数据位置：%APPDATA%\\{APP_ID}\n\n"
            "小爪会常驻在系统托盘（右下角）。\n"
            "单击小爪可以输入指令，双击打开工作台。",
        )
        self.root.destroy()

    def _fail(self, error: str) -> None:
        self.busy = False
        self.install_btn.config(state="normal", text="重试")
        self.cancel_btn.config(state="normal")
        self.dir_entry.config(state="normal")
        self._set_progress(0, "安装失败")
        messagebox.showerror(APP_NAME, f"安装失败：\n\n{error}")


# ==========================================================================
#  卸载
# ==========================================================================
def uninstall(silent: bool = False) -> int:
    """从注册表读安装位置，删文件、删快捷方式。

    **刻意不删 %APPDATA%\\PawPet** —— 那是用户的待办和便签，
    比程序本身重要。只有在用户明确选择时才删。
    """
    import winreg

    install_dir: Path | None = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
            install_dir = Path(value)
    except OSError:
        install_dir = None

    if install_dir is None or not install_dir.exists():
        if not silent:
            messagebox.showerror(APP_NAME, "找不到安装信息，可能已经卸载过了。")
        return 1

    if not silent:
        if not messagebox.askyesno(
            APP_NAME,
            f"确定要卸载 {APP_NAME} 吗？\n\n"
            f"程序目录：{install_dir}\n\n"
            "你的数据（待办、便签、设置）会保留。",
        ):
            return 0

    # 先结束正在运行的实例
    subprocess.run(["taskkill", "/F", "/IM", APP_EXE],
                   capture_output=True, text=True)
    subprocess.run(["taskkill", "/F", "/IM", "uninstall.exe"],
                   capture_output=True, text=True)
    import time

    time.sleep(1.0)

    # 删文件。卸载程序自己正被占用，删不掉，留到最后
    for item in install_dir.iterdir():
        if item.name == "uninstall.exe":
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
        except OSError:
            pass

    # 快捷方式
    for link in (
        start_menu_dir() / APP_NAME / f"{APP_NAME}.lnk",
        desktop_dir() / f"{APP_NAME}.lnk",
    ):
        try:
            link.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        (start_menu_dir() / APP_NAME).rmdir()
    except OSError:
        pass

    # 自启与卸载信息
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass
    except OSError:
        pass

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    # 问用户要不要连数据一起删
    data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_ID
    if data_dir.exists():
        if silent:
            keep = True
        else:
            keep = not messagebox.askyesno(
                APP_NAME,
                f"是否同时删除你的数据？\n\n{data_dir}\n\n"
                "选「否」会保留，方便你以后重装继续用。",
            )
        if not keep:
            shutil.rmtree(data_dir, ignore_errors=True)

    # 最后把自己删掉：起一个 cmd 等一会儿再删
    if getattr(sys, "frozen", False):
        subprocess.Popen(
            ["cmd", "/C", "timeout", "/t", "2", "/nobreak", ">nul",
             "&", "del", "/f", "/q", str(Path(sys.executable))],
            shell=False, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    if not silent:
        messagebox.showinfo(APP_NAME, f"{APP_NAME} 已卸载。")
    return 0


def main() -> int:
    if "--uninstall" in sys.argv:
        silent = "--silent" in sys.argv
        return uninstall(silent=silent)

    root = tk.Tk()
    InstallerWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
