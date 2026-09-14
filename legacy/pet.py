"""小爪助手：一个零依赖、可直接运行的 Windows 桌面宠物。

双击小猫打开工作台；右键打开快捷菜单；按住左键可拖动。
所有待办、便笺和计时器会保存在本文件旁边的 pet_data.json 中。
"""

from __future__ import annotations

import json
import math
import os
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
from agent_console import AgentConsole


APP_NAME = "小爪助手"
PET_BG = "#ff00ff"  # Windows Tk 的透明色
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet_data.json")
DEFAULT_DATA = {
    "tasks": [],
    "note": "",
    "focus_seconds": 25 * 60,
    "timer_mode": "专注",
    "break_minutes": 45,
    "last_break_notice": 0,
}


class PawPet:
    def __init__(self) -> None:
        self.data = self.load_data()
        # 旧版本数据或首次启动没有可用的单调时钟时间；从现在开始计时，
        # 不让用户刚打开小爪就收到一条“该休息了”的提醒。
        if not self.data.get("last_break_notice") or self.data["last_break_notice"] > time.monotonic():
            self.data["last_break_notice"] = time.monotonic()
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("156x176+80+650")
        self.root.configure(bg=PET_BG)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-transparentcolor", PET_BG)
        except tk.TclError:
            # 非 Windows 平台仍可用，只是背景不会透明。
            pass

        self.canvas = tk.Canvas(self.root, width=156, height=176, bg=PET_BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.dashboard: tk.Toplevel | None = None
        self.timer_running = False
        self.timer_last_tick = time.monotonic()
        self.drag_x = 0
        self.drag_y = 0
        self.blink_until = 0.0
        self.next_blink = time.monotonic() + 2.5
        self.bob_phase = 0.0
        self.timer_label: tk.Label | None = None
        self.timer_button: tk.Button | None = None
        self.progress: tk.Canvas | None = None
        self.task_frame: tk.Frame | None = None
        self.task_entry: tk.Entry | None = None
        self.note_box: tk.Text | None = None
        self.status_label: tk.Label | None = None
        self.break_button: tk.Button | None = None

        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.open_dashboard())
        self.canvas.bind("<Button-3>", self.show_menu)
        self.root.bind("<Control-Shift-P>", lambda _event: self.open_dashboard())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.draw_pet()
        self.root.after(40, self.animate)
        self.root.after(1000, self.clock_tick)

    def load_data(self) -> dict:
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            return {**DEFAULT_DATA, **stored}
        except (OSError, json.JSONDecodeError):
            return DEFAULT_DATA.copy()

    def save_data(self) -> None:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ------------------------ the little pet ------------------------
    def draw_pet(self) -> None:
        c = self.canvas
        c.delete("all")
        bob = int(math.sin(self.bob_phase) * 3)
        # A compact, modern fox-robot silhouette with a calm cream/coral/teal palette.
        c.create_oval(40, 153, 116, 164, fill="#c9b9b4", outline="")
        c.create_line(113, 125 + bob, 140, 114 + bob, 143, 96 + bob, smooth=True,
                      width=14, fill="#ef9a75", capstyle="round")
        c.create_line(115, 124 + bob, 137, 114 + bob, 140, 99 + bob, smooth=True,
                      width=5, fill="#ffc2a0", capstyle="round")
        c.create_oval(40, 84 + bob, 116, 156 + bob, fill="#f6eadb", outline="#4b4054", width=2)
        c.create_oval(44, 136 + bob, 73, 158 + bob, fill="#fff8ee", outline="#e5d3c3")
        c.create_oval(83, 136 + bob, 112, 158 + bob, fill="#fff8ee", outline="#e5d3c3")
        c.create_polygon(42, 73 + bob, 47, 27 + bob, 75, 54 + bob, fill="#ef9a75", outline="#4b4054", width=2)
        c.create_polygon(81, 54 + bob, 110, 27 + bob, 115, 74 + bob, fill="#ef9a75", outline="#4b4054", width=2)
        c.create_polygon(51, 60 + bob, 52, 40 + bob, 65, 55 + bob, fill="#ffd0bc", outline="")
        c.create_polygon(92, 55 + bob, 106, 40 + bob, 107, 60 + bob, fill="#ffd0bc", outline="")
        c.create_oval(31, 48 + bob, 125, 132 + bob, fill="#fff3e5", outline="#4b4054", width=2)
        # Face: large readable eyes and a tiny smile.
        blink = time.monotonic() < self.blink_until
        if blink:
            c.create_line(53, 84 + bob, 66, 84 + bob, width=3, fill="#40364d", capstyle="round")
            c.create_line(90, 84 + bob, 103, 84 + bob, width=3, fill="#40364d", capstyle="round")
        else:
            for x in (53, 90):
                c.create_oval(x, 76 + bob, x + 14, 92 + bob, fill="#40364d", outline="")
                c.create_oval(x + 3, 79 + bob, x + 7, 84 + bob, fill="white", outline="")
        c.create_oval(46, 96 + bob, 59, 104 + bob, fill="#f3aaa0", outline="")
        c.create_oval(97, 96 + bob, 110, 104 + bob, fill="#f3aaa0", outline="")
        c.create_polygon(75, 95 + bob, 83, 95 + bob, 79, 101 + bob, fill="#d56f72", outline="")
        c.create_arc(69, 99 + bob, 79, 109 + bob, start=280, extent=85, style="arc", width=2, outline="#80606a")
        c.create_arc(79, 99 + bob, 89, 109 + bob, start=175, extent=85, style="arc", width=2, outline="#80606a")
        # Teal scarf and glowing task badge.
        c.create_polygon(51, 116 + bob, 108, 116 + bob, 102, 130 + bob, 57, 130 + bob,
                         fill="#3f9e9b", outline="#2d6d73", width=1)
        c.create_polygon(91, 126 + bob, 108, 124 + bob, 103, 146 + bob, 88, 140 + bob,
                         fill="#4bb8b0", outline="#2d6d73", width=1)
        c.create_oval(67, 120 + bob, 91, 144 + bob, fill="#ffd76a", outline="#a77450", width=1)
        c.create_text(79, 132 + bob, text="✓" if self.done_count() else "•", fill="#6f4b50", font=("Segoe UI", 11, "bold"))
        c.create_text(78, 170, text="双击打开工作台", fill="#6d6075", font=("Microsoft YaHei UI", 8))

    def animate(self) -> None:
        now = time.monotonic()
        self.bob_phase += 0.08
        if now > self.next_blink:
            self.blink_until = now + 0.16
            self.next_blink = now + 2.8 + (now % 1.8)
        self.draw_pet()
        self.root.after(40, self.animate)

    def start_drag(self, event: tk.Event) -> None:
        self.drag_x, self.drag_y = event.x, event.y

    def drag(self, event: tk.Event) -> None:
        self.root.geometry(f"+{event.x_root - self.drag_x}+{event.y_root - self.drag_y}")

    def end_drag(self, _event: tk.Event) -> None:
        pass

    def show_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(self.root, tearoff=False, font=("Microsoft YaHei UI", 9))
        menu.add_command(label="打开小爪工作台", command=self.open_dashboard)
        menu.add_command(label="打开 AI 控制台", command=self.open_agent_console)
        menu.add_command(label="开始 25 分钟专注", command=self.start_focus)
        menu.add_command(label="快速添加待办", command=self.quick_task)
        menu.add_separator()
        menu.add_command(label="退出小爪助手", command=self.quit)
        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------- useful dashboard ----------------------
    def open_dashboard(self) -> None:
        if self.dashboard and self.dashboard.winfo_exists():
            self.dashboard.deiconify()
            self.dashboard.lift()
            self.dashboard.focus_force()
            self.refresh_dashboard()
            return

        d = self.dashboard = tk.Toplevel(self.root)
        d.title("小爪工作台")
        d.geometry("470x650")
        d.minsize(430, 570)
        d.configure(bg="#201c2b")
        d.attributes("-topmost", True)
        d.protocol("WM_DELETE_WINDOW", self.hide_dashboard)
        d.bind("<Control-Return>", lambda _event: self.add_task())
        d.bind("<Control-s>", lambda _event: self.save_note())

        header = tk.Frame(d, bg="#312a44", height=106)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="小爪工作台", bg="#312a44", fg="#ffe7c4",
                 font=("Microsoft YaHei UI", 21, "bold")).place(x=24, y=18)
        self.status_label = tk.Label(header, bg="#312a44", fg="#bdb1d6",
                                     font=("Microsoft YaHei UI", 10))
        self.status_label.place(x=25, y=58)
        tk.Button(header, text="×", command=self.hide_dashboard, bg="#312a44", fg="#ffe7c4",
                  activebackground="#554869", activeforeground="white", relief="flat",
                  font=("Segoe UI", 18), cursor="hand2").place(relx=1, x=-43, y=12, width=35, height=35)

        outer = tk.Frame(d, bg="#201c2b")
        outer.pack(fill="both", expand=True, padx=20, pady=16)
        self.make_focus_card(outer)
        self.make_task_card(outer)
        self.make_note_card(outer)
        footer = tk.Frame(outer, bg="#201c2b")
        footer.pack(fill="x", pady=(9, 0))
        tk.Button(footer, text="AI 控制台", command=self.open_agent_console, bg="#201c2b", fg="#d5c5ff",
                  relief="flat", activebackground="#201c2b", activeforeground="white",
                  font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2").pack(side="left")
        tk.Label(footer, text="双击宠物可随时唤出这里  ·  右键宠物有快捷操作",
                 bg="#201c2b", fg="#847b97", font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.break_button = tk.Button(footer, text=f"休息提醒：{self.data['break_minutes']} 分钟", command=self.cycle_break_time,
                                      bg="#201c2b", fg="#bca9ed", relief="flat", activebackground="#201c2b",
                                      activeforeground="#e1d6ff", font=("Microsoft YaHei UI", 8), cursor="hand2")
        self.break_button.pack(side="right")
        self.refresh_dashboard()

    def open_agent_console(self) -> None:
        AgentConsole(self.root)

    def card(self, parent: tk.Widget, title: str, subtitle: str) -> tk.Frame:
        box = tk.Frame(parent, bg="#312a44", highlightbackground="#493e61", highlightthickness=1)
        box.pack(fill="x", pady=(0, 12))
        tk.Label(box, text=title, bg="#312a44", fg="#ffe7c4", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(box, text=subtitle, bg="#312a44", fg="#a79abf", font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16)
        return box

    def make_focus_card(self, parent: tk.Widget) -> None:
        box = self.card(parent, "专注计时", "把这一小段时间留给最重要的事")
        row = tk.Frame(box, bg="#312a44")
        row.pack(fill="x", padx=16, pady=(7, 14))
        self.timer_label = tk.Label(row, bg="#312a44", fg="#ffffff", font=("Consolas", 25, "bold"))
        self.timer_label.pack(side="left")
        controls = tk.Frame(row, bg="#312a44")
        controls.pack(side="right")
        self.timer_button = tk.Button(controls, command=self.toggle_timer, text="开始",
                                      bg="#f29e6d", fg="#302132", relief="flat", padx=14,
                                      font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2")
        self.timer_button.pack(side="left", padx=(0, 5))
        tk.Button(controls, command=self.reset_timer, text="重置", bg="#554869", fg="#f1e8ff",
                  relief="flat", padx=10, font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left")
        self.progress = tk.Canvas(box, height=6, bg="#312a44", highlightthickness=0)
        self.progress.pack(fill="x", padx=16, pady=(0, 14))

    def make_task_card(self, parent: tk.Widget) -> None:
        box = self.card(parent, "今日小事", "完成一个，就把负担放下一个")
        add_row = tk.Frame(box, bg="#312a44")
        add_row.pack(fill="x", padx=16, pady=(8, 5))
        self.task_entry = tk.Entry(add_row, bg="#211c2e", fg="#f7efff", insertbackground="#f7efff",
                                   relief="flat", font=("Microsoft YaHei UI", 10))
        self.task_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.task_entry.bind("<Return>", lambda _event: self.add_task())
        tk.Button(add_row, text="添加", command=self.add_task, bg="#9377ce", fg="white", relief="flat",
                  padx=12, font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2").pack(side="left", padx=(7, 0), ipady=3)
        self.task_frame = tk.Frame(box, bg="#312a44")
        self.task_frame.pack(fill="x", padx=16, pady=(0, 11))

    def make_note_card(self, parent: tk.Widget) -> None:
        box = self.card(parent, "随手记", "灵感、临时信息，先放在这里（Ctrl + S 保存）")
        self.note_box = tk.Text(box, height=6, wrap="word", bg="#211c2e", fg="#ede5f6", insertbackground="white",
                                relief="flat", padx=9, pady=8, font=("Microsoft YaHei UI", 9))
        self.note_box.pack(fill="x", padx=16, pady=(8, 7))
        self.note_box.insert("1.0", self.data.get("note", ""))
        tk.Button(box, text="保存便笺", command=self.save_note, bg="#554869", fg="#f1e8ff", relief="flat",
                  font=("Microsoft YaHei UI", 8), cursor="hand2").pack(anchor="e", padx=16, pady=(0, 12))

    # --------------------------- productivity -----------------------
    def done_count(self) -> int:
        return sum(1 for task in self.data["tasks"] if task.get("done"))

    def refresh_dashboard(self) -> None:
        if not self.dashboard or not self.dashboard.winfo_exists():
            return
        pending = sum(1 for t in self.data["tasks"] if not t.get("done"))
        date = datetime.now().strftime("%m 月 %d 日 · %A")
        if self.status_label:
            self.status_label.config(text=f"{date}   |   还有 {pending} 件事值得慢慢完成")
        if self.break_button:
            self.break_button.config(text=f"休息提醒：{self.data['break_minutes']} 分钟")
        mins, secs = divmod(max(0, int(self.data["focus_seconds"])), 60)
        if self.timer_label:
            self.timer_label.config(text=f"{mins:02d}:{secs:02d}")
        if self.timer_button:
            self.timer_button.config(text="暂停" if self.timer_running else "开始")
        if self.progress:
            self.progress.delete("all")
            width = max(self.progress.winfo_width(), 1)
            total = 25 * 60 if self.data["timer_mode"] == "专注" else 5 * 60
            ratio = min(1, max(0, self.data["focus_seconds"] / total))
            self.progress.create_rectangle(0, 0, width, 6, fill="#211c2e", outline="")
            self.progress.create_rectangle(0, 0, width * ratio, 6, fill="#f29e6d", outline="")
        if self.task_frame:
            for child in self.task_frame.winfo_children():
                child.destroy()
            visible = self.data["tasks"][-5:]
            if not visible:
                tk.Label(self.task_frame, text="还没有待办。给今天一个小小的开始吧。", bg="#312a44", fg="#887e9d",
                         font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=4)
            for task in visible:
                row = tk.Frame(self.task_frame, bg="#312a44")
                row.pack(fill="x", pady=2)
                completed = task.get("done", False)
                var = tk.BooleanVar(value=completed)
                check = tk.Checkbutton(row, variable=var, command=lambda item=task, value=var: self.toggle_task(item, value),
                                       bg="#312a44", activebackground="#312a44", selectcolor="#211c2e", relief="flat")
                check.pack(side="left")
                fg = "#837a94" if completed else "#f1e9fb"
                text = ("✓  " if completed else "") + task["text"]
                tk.Label(row, text=text, bg="#312a44", fg=fg, anchor="w", font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True)
                tk.Button(row, text="×", command=lambda item=task: self.delete_task(item), bg="#312a44", fg="#a99dbd",
                          activebackground="#554869", activeforeground="white", relief="flat", cursor="hand2").pack(side="right")

    def add_task(self) -> None:
        if not self.task_entry:
            return
        text = self.task_entry.get().strip()
        if text:
            self.data["tasks"].append({"text": text[:100], "done": False})
            self.task_entry.delete(0, "end")
            self.save_data()
            self.refresh_dashboard()
            self.draw_pet()

    def quick_task(self) -> None:
        self.open_dashboard()
        if self.task_entry:
            self.task_entry.focus_set()

    def toggle_task(self, task: dict, variable: tk.BooleanVar) -> None:
        task["done"] = variable.get()
        self.save_data()
        self.refresh_dashboard()
        self.draw_pet()

    def delete_task(self, task: dict) -> None:
        if task in self.data["tasks"]:
            self.data["tasks"].remove(task)
            self.save_data()
            self.refresh_dashboard()
            self.draw_pet()

    def save_note(self) -> None:
        if self.note_box:
            self.data["note"] = self.note_box.get("1.0", "end-1c")
            self.save_data()
            self.toast("便笺已经收好啦", "随手记会保存在本地。")

    def start_focus(self) -> None:
        self.data["timer_mode"] = "专注"
        if self.data["focus_seconds"] <= 0 or self.data["focus_seconds"] > 25 * 60:
            self.data["focus_seconds"] = 25 * 60
        self.timer_running = True
        self.timer_last_tick = time.monotonic()
        self.open_dashboard()
        self.refresh_dashboard()

    def toggle_timer(self) -> None:
        self.timer_running = not self.timer_running
        self.timer_last_tick = time.monotonic()
        self.refresh_dashboard()

    def reset_timer(self) -> None:
        self.timer_running = False
        self.data["focus_seconds"] = 25 * 60 if self.data["timer_mode"] == "专注" else 5 * 60
        self.save_data()
        self.refresh_dashboard()

    def clock_tick(self) -> None:
        now = time.monotonic()
        if self.timer_running:
            elapsed = int(now - self.timer_last_tick)
            if elapsed:
                self.data["focus_seconds"] = max(0, self.data["focus_seconds"] - elapsed)
                self.timer_last_tick = now
                if self.data["focus_seconds"] == 0:
                    self.timer_finished()
        # 休息提示只在程序正在运行时触发，避免后台常驻带来的打扰。
        interval = int(self.data["break_minutes"]) * 60
        if now - self.data.get("last_break_notice", now) >= interval:
            self.data["last_break_notice"] = now
            self.toast("伸个懒腰吧", f"你已经专注了 {self.data['break_minutes']} 分钟，看看远处、喝口水。")
        self.refresh_dashboard()
        self.root.after(1000, self.clock_tick)

    def timer_finished(self) -> None:
        self.timer_running = False
        if self.data["timer_mode"] == "专注":
            self.data["timer_mode"] = "休息"
            self.data["focus_seconds"] = 5 * 60
            self.toast("专注完成！", "做得好。接下来休息 5 分钟，眼睛和肩膀都需要喘口气。")
        else:
            self.data["timer_mode"] = "专注"
            self.data["focus_seconds"] = 25 * 60
            self.toast("休息结束", "准备好了就开启下一轮专注吧。")
        self.save_data()

    def cycle_break_time(self) -> None:
        choices = [30, 45, 60, 90]
        current = self.data["break_minutes"]
        self.data["break_minutes"] = choices[(choices.index(current) + 1) % len(choices)] if current in choices else 45
        self.data["last_break_notice"] = time.monotonic()
        self.save_data()
        self.refresh_dashboard()
        self.toast("提醒间隔已更新", f"小爪会每 {self.data['break_minutes']} 分钟提醒你活动一下。")

    def toast(self, title: str, body: str) -> None:
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#3a304e")
        x = self.root.winfo_screenwidth() - 360
        y = self.root.winfo_screenheight() - 195
        toast.geometry(f"330x105+{x}+{y}")
        tk.Label(toast, text=title, bg="#3a304e", fg="#ffe7c4", anchor="w", font=("Microsoft YaHei UI", 12, "bold")).pack(fill="x", padx=16, pady=(13, 2))
        tk.Label(toast, text=body, bg="#3a304e", fg="#e1d8eb", justify="left", anchor="w", wraplength=290,
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=16)
        toast.after(7000, toast.destroy)

    def hide_dashboard(self) -> None:
        if self.dashboard:
            self.save_note()
            self.dashboard.withdraw()

    def quit(self) -> None:
        if self.dashboard and self.dashboard.winfo_exists():
            self.save_note()
        self.save_data()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    PawPet().run()
