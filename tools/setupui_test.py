"""直接读取安装向导里各控件的实际几何，验证排版正确。

比截图可靠：截图会受窗口离屏、主题渲染等干扰，而几何数据是确定的。

检查项：
  * 输入框和「浏览」按钮之间没有大空隙（紧挨着）
  * 所有控件都在窗口范围内（没被裁掉）
  * 进度条有可见的宽度和高度
  * 按钮不重叠
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    import tkinter as tk

    from setup_ui import InstallerWindow

    print("安装向导布局检查")

    root = tk.Tk()
    window = InstallerWindow(root)
    root.update_idletasks()
    root.update()

    # 多跑几次 update，让 pack/grid 把尺寸算稳
    for _ in range(10):
        root.update()
        time.sleep(0.05)

    win_w = root.winfo_width()
    win_h = root.winfo_height()
    print(f"\n窗口：{win_w}x{win_h}")

    # ---------------------------------------------------------- 输入框
    entry = window.dir_entry
    entry_x = entry.winfo_rootx() - root.winfo_rootx()
    entry_y = entry.winfo_rooty() - root.winfo_rooty()
    entry_w = entry.winfo_width()
    entry_h = entry.winfo_height()
    print(f"输入框：x={entry_x} y={entry_y} w={entry_w} h={entry_h}")

    check("输入框有合理宽度（>250px）", entry_w > 250, f"实际 {entry_w}")
    check("输入框有合理高度（>24px）", entry_h > 24, f"实际 {entry_h}")
    check("输入框在窗口内",
          0 <= entry_x and entry_x + entry_w <= win_w,
          f"x={entry_x} w={entry_w} 窗口宽 {win_w}")

    # ---------------------------------------------------------- 浏览按钮
    dir_row = entry.master
    buttons = [child for child in dir_row.winfo_children()
               if isinstance(child, tk.Button)]
    check("找到浏览按钮", len(buttons) == 1, f"实际 {len(buttons)} 个")
    if buttons:
        browse = buttons[0]
        browse.update_idletasks()
        browse_x = browse.winfo_rootx() - root.winfo_rootx()
        browse_w = browse.winfo_width()
        print(f"浏览按钮：x={browse_x} w={browse_w}")

        gap = browse_x - (entry_x + entry_w)
        print(f"输入框与按钮之间的空隙：{gap}px")
        # 之前用 pack(side="right") 时这里会有一大块空白
        check("输入框与按钮紧密相邻（空隙 < 20px）", 0 <= gap < 20, f"实际 {gap}px")
        check("按钮不超出窗口右边界",
              browse_x + browse_w <= win_w + 2,
              f"{browse_x + browse_w} vs {win_w}")

    # ---------------------------------------------------------- 进度条
    bar = window.bar
    bar.update_idletasks()
    bar_w = bar.winfo_width()
    bar_h = bar.winfo_height()
    bar_y = bar.winfo_rooty() - root.winfo_rooty()
    print(f"进度条：w={bar_w} h={bar_h} y={bar_y}")

    check("进度条有宽度", bar_w > 300, f"实际 {bar_w}")
    check("进度条可见高度（>=6px）", bar_h >= 6, f"实际 {bar_h}")
    check("进度条在窗口内", bar_y + bar_h <= win_h, f"{bar_y + bar_h} vs {win_h}")

    # ---------------------------------------------------------- 底部按钮
    footer = window.install_btn.master
    footer.update_idletasks()
    install_x = window.install_btn.winfo_rootx() - root.winfo_rootx()
    install_w = window.install_btn.winfo_width()
    cancel_x = window.cancel_btn.winfo_rootx() - root.winfo_rootx()
    cancel_w = window.cancel_btn.winfo_width()
    install_y = window.install_btn.winfo_rooty() - root.winfo_rooty()
    install_h = window.install_btn.winfo_height()

    print(f"安装按钮：x={install_x} w={install_w}")
    print(f"取消按钮：x={cancel_x} w={cancel_w}")

    check("两个底部按钮不重叠", cancel_x + cancel_w <= install_x + 2,
          f"取消到 {cancel_x + cancel_w}，安装从 {install_x}")
    check("底部按钮在窗口内",
          install_y + install_h <= win_h + 2,
          f"{install_y + install_h} vs {win_h}")
    check("安装按钮有合理宽度", install_w > 70, f"实际 {install_w}")
    check("取消按钮有合理宽度", cancel_w > 50, f"实际 {cancel_w}")
    check("安装按钮比取消按钮显眼（更宽或有颜色）",
          install_w >= cancel_w, f"{install_w} vs {cancel_w}")

    # ---------------------------------------------------------- 复选框
    options = None
    for child in root.winfo_children():
        for sub in child.winfo_children():
            for item in sub.winfo_children():
                if isinstance(item, tk.Frame) and any(
                        isinstance(x, tk.Checkbutton) for x in item.winfo_children()):
                    options = item
    if options is not None:
        boxes = [c for c in options.winfo_children() if isinstance(c, tk.Checkbutton)]
        check("三个选项都在", len(boxes) == 3, f"实际 {len(boxes)}")
        widths = [b.winfo_width() for b in boxes]
        check("选项宽度一致", len(set(widths)) <= 1 or max(widths) - min(widths) < 4,
              f"宽度 {widths}")

    # ---------------------------------------------------------- 默认值
    check("默认装到用户目录（不需要管理员）",
          "LOCALAPPDATA" in str(window.install_dir.get()).upper()
          or "Programs" in window.install_dir.get(),
          window.install_dir.get())
    check("默认创建桌面快捷方式", window.make_desktop.get() is True)
    check("默认不开机自启（尊重用户选择）", window.make_autostart.get() is False)

    root.destroy()

    print(f"\n{'=' * 52}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
