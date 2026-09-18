"""桌面动作层。

三件事必须做对：

1. **分级安全**。只读动作直接执行；会改变屏幕状态的动作要用户点头；
   影响系统的动作默认关闭。每一级都可配置，但默认值偏保守。

2. **中文输入**。pyautogui.write() 只能打 ASCII，中文会静默丢掉。
   所以非 ASCII 文本走「剪贴板 + Ctrl+V」，并且用完恢复原剪贴板内容。

3. **随时能停**。除了界面上的停止按钮，还打开 pyautogui 的 FAILSAFE：
   把鼠标猛甩到屏幕左上角就能强制中断，这是给用户的物理急停。
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

try:
    import pyautogui

    pyautogui.FAILSAFE = True      # 鼠标甩到左上角即中断
    pyautogui.PAUSE = 0.05         # 每个动作之间留一点时间，别把系统打爆
    PYAUTOGUI_AVAILABLE = True
except Exception:  # noqa: BLE001 - 无显示器环境会抛异常
    pyautogui = None
    PYAUTOGUI_AVAILABLE = False


class Risk:
    READ = "read"        # 只观察，不改变任何东西
    CONFIRM = "confirm"  # 会动你的屏幕/键鼠，需要确认
    DANGER = "danger"    # 会影响系统或文件，默认禁用
    # 永远要问的那一类，**「完全自动」也不例外**。
    #
    # 为什么必须有这一档：`full` 的意义是「日常操作别烦我」，
    # 不是「装一段新的可执行代码也别问我」。给 AI 造出来的代码开一条
    # 静默生效的路，等于把「用户自己决定要不要跑这段代码」这件事
    # 从流程里删掉了 —— 而代码里三处文案都承诺了会弹卡片。
    CRITICAL = "critical"


# 自动放行等级：越高越宽松
LEVEL_READ_ONLY = "read_only"   # 仅只读
LEVEL_CONFIRM = "confirm"       # 只读自动 + 其它需确认（默认）
LEVEL_AUTO = "auto"             # 只读和键鼠自动 + 危险需确认
LEVEL_FULL = "full"             # 全部自动（危险，需要用户明确选）

LEVEL_LABELS = {
    LEVEL_READ_ONLY: "只读",
    LEVEL_CONFIRM: "逐步确认",
    LEVEL_AUTO: "自动执行",
    LEVEL_FULL: "完全自动",
}

# 从紧到松的顺序。需要「比较两个等级哪个更宽松」的地方用它 ——
# 例如导入备份时判断「这份备份想把权限调松吗」。用标签表推导不出来
# 顺序（dict 的键序是插入序，靠它等于靠巧合）。
LEVEL_ORDER = (LEVEL_READ_ONLY, LEVEL_CONFIRM, LEVEL_AUTO, LEVEL_FULL)


# ==========================================================================
#  命令硬拦截
# ==========================================================================
# **这不是安全边界，是「手滑」边界。**
#
# 任何黑名单都能被绕过 —— 把命令拼进变量、用 PowerShell 别名、
# base64 编码后 decode 再执行……所以真正的边界是 `Risk.DANGER`：
# 这条命令得让用户点头（`open_app` / `run_command` 都是 DANGER 级）。
# 黑名单只负责挡掉那些**连问都不该问**的明显毁灭性操作 —— 让用户面对一张
# 「格式化 D 盘」的确认卡片自己去判断，本身就是把风险推给了他。
#
# 但既然写了，就不能写得一眼假。原来那版拿**原始字符串**做子串匹配，于是：
#
#   * 加引号就过：`del /f /s /q "C:\"`
#   * 多打一个空格就过：`vssadmin  delete shadows`
#   * 包一层就过：`cmd /c del /f /s /q "C:\*"`
#   * 反向误伤：`open_app("notepad format_tips.txt")` 被当成格式化
#   * 而且 `open_app` 和 `run_command` **各有一份内容不同的清单**，各自
#     漏了对方有的条目（`reg delete` 只在 open_app 里、`vssadmin delete`
#     只在 run_command 里）—— 换个工具调同一条命令，拦截结果就不一样
#
# 现在统一成一份，流程是：**归一化 → 按 shell 分隔符拆段 → 剥掉外壳 →
# 逐段按词边界匹配**。拆段是因为 `echo hi && del /f /s /q C:\` 这种
# 组合命令，整串匹配会漏；逐段匹配才拦得住。

# 会被硬拒的模式（在归一化后的单个片段上匹配）。
# 左边是正则，右边是给用户看的原因 —— 报错要能指导下一步，
# 只说「被拒绝了」等于没说。
#
# 分成两类：
#   A 类 = 命令词本身就是毁灭性的，出现就拒
#   B 类 = 动词本身没事，配上特定目标才有事（比如 `cipher` 无害，
#          `cipher /w` 是擦盘）
# 「删除动作 + 目标是盘根/系统目录」是第三类，单独写在 command_rejection
# 里 —— 它要看目标，不是看开关。
_BLOCKED_COMMANDS: tuple[tuple[str, str], ...] = (
    # ---- A 类
    # 要求前后是分隔符，避免 `notepad format_tips.txt` 被误伤
    (r"(?:^|[\s|;&/])format(?:\.(?:com|exe))?(?=[\s|;&]|$)", "格式化磁盘"),
    (r"(?:^|[\s|;&/])mkfs(?:\.\w+)?(?=[\s|;&]|$)", "格式化文件系统"),
    (r"(?:^|[\s|;&/])diskpart(?=[\s|;&]|$)", "磁盘分区工具"),
    (r"(?:^|[\s|;&/])bcdedit(?=[\s|;&]|$)", "改启动配置"),
    (r"(?:^|[\s|;&/])shutdown(?=[\s|;&]|$)", "关机或重启"),
    (r"(?:^|[\s|;&/])takeown(?=[\s|;&]|$)", "夺取文件所有权"),
    (r"(?:^|[\s|;&/])(?:clear-disk|initialize-disk|format-volume)(?=[\s|;&]|$)",
     "清盘或格式化"),
    (r"(?:^|[\s|;&/])(?:stop-computer|restart-computer)(?=[\s|;&]|$)", "关机或重启"),
    (r"\bdd\b[^|;&]*\bif=", "裸写磁盘"),
    (r":\(\)\s*\{[^}]*\}\s*;\s*:", "fork 炸弹"),
    # ---- B 类
    (r"\bvssadmin\b[^|;&]*\bdelete\b", "删除卷影副本（勒索软件的标准第一步）"),
    (r"\bcipher\b[^|;&]*\s/w", "擦除磁盘空闲空间"),
    (r"\breg\b[^|;&]*\b(?:delete|add)\b", "改注册表"),
    (r"\bnet\s+user\b[^|;&]*\s/add\b", "新建系统账户"),
    (r"\bnet\s+localgroup\b[^|;&]*\s/add\b", "改用户组"),
    (r"\bicacls\b[^|;&]*\s/grant\b", "改文件访问权限"),
    (r"\battrib\b[^|;&]*[+-][sh]\b", "改文件的系统/隐藏属性"),
    (r"\bschtasks\b[^|;&]*\s/create\b", "新建计划任务"),
)

# shell 的外壳，剥掉之后继续检查里面那条。
#
# 为什么要剥：`cmd /c <危险命令>` 和直接写 <危险命令> 是同一件事，
# 不剥的话前面套一层就绕过去了。剥三层是因为 `cmd /c powershell -c ...`
# 这种套娃真的有人写。
_WRAPPERS: tuple[re.Pattern, ...] = (
    re.compile(r"^cmd(?:\.exe)?\s*/(?:c|k)\s+"),
    # `powershell -Command X` / `powershell -NoProfile -Command X` /
    # `powershell -c X` 都要认。中间那串开关是可选的。
    re.compile(r"^powershell(?:\.exe)?\s+(?:[^|;&]*?\s+)?-(?:c|command)\b\s*"),
    re.compile(r"^(?:bash|sh|zsh)\s+-c\s+"),
    re.compile(r"^wsl(?:\.exe)?(?:\s+--)?\s*(?:bash|sh)?\s*-c\s+"),
)

# shell 里一条命令可以塞多条。按这些分隔符拆开逐段检查。
_SEPARATORS = re.compile(r"&&|\|\||[|;&\n]")

# 会「删东西」的动词。只有它出现时才去检查目标 ——
# `dir c:\` 也要碰盘根，但那不是删除。
_DESTRUCTIVE = re.compile(
    r"\b(?:del|erase|rd|rmdir|rm|remove-item|ri|format|mkfs)\b"
)

# 盘根：`c:` / `c:\` / `c:\\` / `c:\*`
#
# `[\\/]*` 而不是 `[\\/]?` —— Windows 会把重复的分隔符当同一个，
# `del /f /s /q C:\\` 和 `del /f /s /q C:\` 是同一件事。
_DRIVE_ROOT = re.compile(r"(?:^|[\s|;&])([a-z]:[\\/]*\*?)(?=$|[\s|;&])")

# Unix 风格的根。小爪跑在 Windows 上，但用户可能在 Git Bash / WSL 里用，
# 而 `rm -rf /` 一样是灾难。规则同 Windows：**看目标**，不是看到 `-rf` 就拒 ——
# `rm -rf node_modules` 是日常，`rm -rf /` 不是。
_ROOT_UNIX = re.compile(r"(?:^|[\s|;&])(/(?:\*)?|~/?(?:\*)?)(?=$|[\s|;&])")

# 整棵子树都不该被删的地方。
#
# 末尾的 `(?=$|[\\/*\s|;&])` 是个**词边界**：`c:\windows` 要拒，但
# `c:\windows.old` 不拒 —— 后者是很常见的清理对象，一起拒了就是误伤。
_PROTECTED_TREE = re.compile(
    r"[a-z]:[\\/]+(?:windows|program files(?:\s*\(x86\))?|programdata|"
    r"system volume information|\$recycle\.bin|recovery|perflogs|boot)"
    r"(?=$|[\\/*\s|;&])"
)

# `c:\users` 单独处理：**只保护它自己，不保护下面具体用户自己的目录**。
# `rd /s /q c:\users` 是灾难，`rd /s /q c:\users\我\Downloads\tmp` 是日常清理。
_PROTECTED_USERS = re.compile(r"[a-z]:[\\/]+users[\\/]*\*?(?=$|[\s|;&])")


def normalise_command(command: str) -> str:
    """把命令压成「最坏情况的写法」再拿去匹配。

    四件事，每一件都对应一个实测能绕过的写法：

    * 去掉 `^` —— cmd 的转义符，`^f^o^r^m^a^t` 真的能跑
    * 去掉引号 —— `del /f /s /q "C:\\"` 和 `del /f /s /q C:\\` 是同一件事
    * 空白压成一个空格 —— `vssadmin  delete`（两个空格）原来就漏了
    * 转小写
    """
    text = str(command or "").lower()
    text = text.replace("^", "")
    text = re.sub(r"[\"']", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_wrappers(segment: str) -> str:
    """剥掉 `cmd /c` / `powershell -c` 这类外壳，最多三层。"""
    for _ in range(3):
        before = segment
        for pattern in _WRAPPERS:
            segment = normalise_command(pattern.sub("", segment))
        if segment == before or not segment:
            break
    return segment


def command_rejection(command: str) -> str | None:
    """这条命令该不该硬拒。返回原因；None = 不拦。

    **只在归一化后的文本上判**，所以调用方不需要先做什么预处理。
    """
    text = normalise_command(command)
    if not text:
        return None

    for raw in _SEPARATORS.split(text):
        segment = _strip_wrappers(raw.strip())
        if not segment:
            continue

        for pattern, reason in _BLOCKED_COMMANDS:
            if re.search(pattern, segment):
                return reason

        # ---- 第三类：删除动作 + 目标是盘根 / 系统目录
        #
        # **看目标，不看开关。**
        #
        # 一开始我写的是「出现 rd /s 就拒」，测出来立刻误伤了
        # `rd /s /q build` —— 那是常规的构建清理。这种误伤比漏拦更糟：
        # 它会训练用户去关掉整个黑名单，最后连真正危险的也不拦了。
        #
        # 反过来，对盘根或系统目录做递归删除**没有任何正常用途**。
        # 所以规则收敛成「删东西 + 目标是这些地方」，两个方向都有例子：
        #   rd /s /q build            → 放过（常规清理）
        #   rd /s /q "C:\Users"       → 拒
        #   del /f /s /q "C:\*"       → 拒
        #   del /f /s /q D:\项目\旧版  → 放过（DANGER 级本来就会弹卡片问）
        if _DESTRUCTIVE.search(segment):
            match = _DRIVE_ROOT.search(segment) or _ROOT_UNIX.search(segment)
            if match:
                return f"对盘根 {match.group(1)} 做删除操作"
            if _PROTECTED_TREE.search(segment):
                return "删除系统目录"
            if _PROTECTED_USERS.search(segment):
                return "删除用户目录"

    return None


def _rejection_message(reason: str) -> str:
    """把拒绝原因写成人话。**必须告诉用户下一步能干什么。**"""
    return (f"这条命令包含「{reason}」，属于不可逆的系统级操作，已被拒绝执行。"
            "这类操作在黑名单里是硬拦截，模型说什么都不放行。"
            "如果你确实要做，请自己在终端里手动执行。")


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)

    def as_text(self) -> str:
        return self.message or ("完成" if self.ok else "失败")



class AuditLog:
    """动作审计：内存里保留最近若干条，可选同时落盘。"""

    def __init__(self, limit: int = 400) -> None:
        self._items: list[dict] = []
        self._lock = threading.Lock()
        self._limit = limit
        self._path: str | None = None

    def set_file(self, path: str | None) -> None:
        self._path = path

    def add(self, action: str, detail: str, risk: str, approved: str) -> dict:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "stamp": time.time(),
            "action": action,
            "detail": detail,
            "risk": risk,
            "approved": approved,   # auto / user / denied
        }
        with self._lock:
            self._items.append(entry)
            del self._items[:-self._limit]
        if self._path:
            try:
                with open(self._path, "a", encoding="utf-8") as handle:
                    handle.write(
                        f"{datetime.now().isoformat(timespec='seconds')}\t"
                        f"{risk}\t{approved}\t{action}\t{detail}\n"
                    )
            except OSError:
                pass
        return entry

    def recent(self, count: int = 60) -> list[dict]:
        with self._lock:
            return list(self._items[-count:])

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class DesktopActions:
    """受控的键鼠与窗口操作。"""

    def __init__(self, audit: AuditLog | None = None) -> None:
        self.audit = audit or AuditLog()
        self.level = LEVEL_CONFIRM
        self._stop = threading.Event()

    # ------------------------------------------------------------- 运行时状态
    @property
    def available(self) -> bool:
        return PYAUTOGUI_AVAILABLE

    def status(self) -> str:
        if not PYAUTOGUI_AVAILABLE:
            return "桌面控制不可用：缺少 pyautogui"
        return f"桌面控制就绪（急停：鼠标甩到左上角）"

    def request_stop(self) -> None:
        self._stop.set()

    def clear_stop(self) -> None:
        self._stop.clear()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # --------------------------------------------------------------- 安全策略
    def needs_approval(self, risk: str) -> bool:
        # **这一条必须放在最前面。**
        #
        # 下面每一档都会给 FULL 提前 return False，所以写在后面等于没写 ——
        # 而「完全自动」正是这段逻辑唯一会漏掉它的场景。原来 code 档
        # 自我扩权的闭环就是从这儿钻过去的：造草稿是 READ（不问）→
        # 安装是 CONFIRM（full 下不问）→ 调用时合成 CONFIRM（full 下不问）
        # → 子进程跑任意 Python。全程一张卡片都没有。
        if risk == Risk.CRITICAL:
            return True
        if risk == Risk.READ:
            return False
        if risk == Risk.CONFIRM:
            return self.level in (LEVEL_READ_ONLY, LEVEL_CONFIRM)
        # DANGER
        if self.level == LEVEL_FULL:
            return False
        if self.level == LEVEL_READ_ONLY:
            return True     # 只读模式下干脆不放行
        return True

    def blocked(self, risk: str) -> bool:
        """只读模式下，任何会改变状态的动作都不执行。"""
        return self.level == LEVEL_READ_ONLY and risk != Risk.READ

    # ------------------------------------------------------------------ 查询
    def screen_size(self) -> tuple[int, int]:
        if not PYAUTOGUI_AVAILABLE:
            return (0, 0)
        size = pyautogui.size()
        return (int(size.width), int(size.height))

    def mouse_position(self) -> tuple[int, int]:
        if not PYAUTOGUI_AVAILABLE:
            return (0, 0)
        point = pyautogui.position()
        return (int(point.x), int(point.y))

    def active_window(self) -> str:
        import ctypes

        try:
            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            return buffer.value or "(无标题)"
        except Exception:  # noqa: BLE001
            return "(未知)"

    def list_windows(self, limit: int = 40) -> list[str]:
        import ctypes
        from ctypes import wintypes

        titles: list[str] = []
        try:
            user32 = ctypes.windll.user32
            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def callback(handle, _param):
                if user32.IsWindowVisible(handle):
                    length = user32.GetWindowTextLengthW(handle)
                    if length > 0:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(handle, buffer, length + 1)
                        title = buffer.value.strip()
                        if title and title not in titles:
                            titles.append(title)
                return True

            user32.EnumWindows(enum_proc(callback), 0)
        except Exception:  # noqa: BLE001
            pass
        return titles[:limit]

    def activate_window(self, title_part: str) -> ActionResult:
        import ctypes

        title_part = (title_part or "").strip()
        if not title_part:
            return ActionResult(False, "没有给出窗口标题")
        try:
            user32 = ctypes.windll.user32
            found = []

            def callback(handle, _param):
                if user32.IsWindowVisible(handle):
                    length = user32.GetWindowTextLengthW(handle)
                    if length > 0:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(handle, buffer, length + 1)
                        if title_part.lower() in buffer.value.lower():
                            found.append((handle, buffer.value))
                return True

            from ctypes import wintypes

            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(enum_proc(callback), 0)
            if not found:
                return ActionResult(False, f"没找到标题包含「{title_part}」的窗口")

            # 子串匹配很容易撞车：一个 Edge 窗口的**标题**里也可能出现
            # 「记事本」两个字，而 EnumWindows 的顺序是 Z 序，谁在前面谁被选中。
            # 所以按「标题里关键字出现的位置」排序：越靠前说明越像
            # 「这是记事本」而不是「这个页面提到了记事本」。
            # 完全相等的排最前。
            needle = title_part.lower()

            def rank(item):
                text = item[1].lower()
                if text == needle:
                    return (0, 0, len(text))
                return (1, text.find(needle), len(text))

            found.sort(key=rank)
            handle = found[0][0]
            if user32.IsIconic(handle):
                user32.ShowWindow(handle, 9)    # SW_RESTORE

            # SetForegroundWindow 单独调常常**静默失败**：Windows 有个前台锁定，
            # 后台进程不让抢焦点。以前这里照样返回 ok=True（因为报的是
            # active_window()，也就是「现在最前面的是谁」而不是「我们切过去了没」），
            # 于是 AI 以为切好了，其实目标窗口还压在别的窗口后面，
            # 接下来的坐标点击就全点到别人身上了。
            #
            # 标准绕法：把自己的线程输入队列临时挂到目标线程上，再抢前台。
            # 抢完必须解挂，否则两个线程的输入队列会一直绑在一起。
            moved = self._force_foreground(handle)
            time.sleep(0.25)

            if not moved:
                return ActionResult(
                    False,
                    f"切不到「{self._window_title(handle)}」：系统前台锁定挡住了。"
                    f"现在最前面的是「{self.active_window()}」。"
                    "可以先用 ui_click 点一下它的任务栏图标，或者手动点一下窗口再操作。")

            return ActionResult(True, f"已切到窗口：{self.active_window()}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"切换窗口失败：{exc}")

    @staticmethod
    def _window_title(handle) -> str:
        """按句柄读窗口标题。"""
        import ctypes

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value or "（无标题）"

    @staticmethod
    def _force_foreground(handle) -> bool:
        """把窗口提到前台，返回**是否真的提上去了**。

        用 AttachThreadInput 绕开前台锁定。返回前会校验当前前台窗口
        确实是目标窗口 —— 不然「成功」两个字就是假的。
        """
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = wintypes.HWND(handle)

        if user32.GetForegroundWindow() == handle:
            return True

        current_thread = kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        foreground = user32.GetForegroundWindow()
        foreground_thread = (
            user32.GetWindowThreadProcessId(wintypes.HWND(foreground), None)
            if foreground else 0
        )

        attached: list[int] = []
        for thread_id in {target_thread, foreground_thread}:
            if thread_id and thread_id != current_thread:
                if user32.AttachThreadInput(current_thread, thread_id, True):
                    attached.append(thread_id)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            for thread_id in attached:
                user32.AttachThreadInput(current_thread, thread_id, False)

        return user32.GetForegroundWindow() == handle

    # -------------------------------------------------------------- 只读动作
    def clipboard_text(self) -> str:
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                text = root.clipboard_get()
            finally:
                root.destroy()
            return text or ""
        except Exception:  # noqa: BLE001
            try:
                import pyperclip

                return pyperclip.paste() or ""
            except Exception:  # noqa: BLE001
                return ""

    def set_clipboard(self, text: str) -> bool:
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            try:
                root.clipboard_clear()
                root.clipboard_append(text or "")
                root.update()
            finally:
                root.destroy()
            return True
        except Exception:  # noqa: BLE001
            try:
                import pyperclip

                pyperclip.copy(text or "")
                return True
            except Exception:  # noqa: BLE001
                return False

    # ------------------------------------------------------------ 键鼠动作
    def move(self, x: int, y: int, duration: float = 0.2) -> ActionResult:
        try:
            pyautogui.moveTo(int(x), int(y), duration=max(0.0, min(1.5, duration)))
            return ActionResult(True, f"鼠标移到 ({x}, {y})")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"移动鼠标失败：{exc}")

    def click(self, x: int | None = None, y: int | None = None,
              button: str = "left", clicks: int = 1) -> ActionResult:
        try:
            if x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.15)
                time.sleep(0.05)
            button = button if button in ("left", "right", "middle") else "left"
            pyautogui.click(button=button, clicks=max(1, min(3, int(clicks))), interval=0.08)
            where = f"({x}, {y})" if x is not None else "当前位置"
            name = {"left": "左键", "right": "右键", "middle": "中键"}[button]
            times = "双击" if clicks == 2 else ("单击" if clicks == 1 else "三击")
            return ActionResult(True, f"{where} {name}{times}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"点击失败：{exc}")

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> ActionResult:
        try:
            pyautogui.moveTo(int(x1), int(y1), duration=0.15)
            pyautogui.mouseDown()
            time.sleep(0.08)
            pyautogui.moveTo(int(x2), int(y2), duration=max(0.1, min(2.0, duration)))
            time.sleep(0.08)
            pyautogui.mouseUp()
            return ActionResult(True, f"从 ({x1}, {y1}) 拖到 ({x2}, {y2})")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"拖拽失败：{exc}")

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> ActionResult:
        try:
            if x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.15)
            amount = max(-20, min(20, int(amount)))
            pyautogui.scroll(amount * 120)
            return ActionResult(True, f"滚动 {amount} 格")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"滚动失败：{exc}")

    def type_text(self, text: str) -> ActionResult:
        """输入文本。非 ASCII 走剪贴板粘贴，否则中文会丢。"""
        text = "" if text is None else str(text)
        if not text:
            return ActionResult(False, "没有要输入的文本")
        try:
            if text.isascii():
                pyautogui.write(text, interval=0.012)
                return ActionResult(True, f"已输入 {len(text)} 个字符")

            # 中文等非 ASCII：借剪贴板
            previous = self.clipboard_text()
            if not self.set_clipboard(text):
                return ActionResult(False, "无法写入剪贴板，中文输入失败")
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.25)
            if previous:
                # 尽量把用户原来的剪贴板还回去，别把人家复制的东西弄丢
                self.set_clipboard(previous)
            return ActionResult(True, f"已粘贴 {len(text)} 个字符（走剪贴板输入）")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"输入失败：{exc}")

    KEY_ALIASES = {
        "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
        "win": "win", "super": "win", "cmd": "win", "meta": "win",
        "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc",
        "tab": "tab", "space": "space", "backspace": "backspace",
        "delete": "delete", "del": "delete", "home": "home", "end": "end",
        "pageup": "pageup", "pagedown": "pagedown",
        "up": "up", "down": "down", "left": "left", "right": "right",
        "capslock": "capslock", "insert": "insert", "printscreen": "printscreen",
    }

    def press_keys(self, keys: list[str]) -> ActionResult:
        if not keys:
            return ActionResult(False, "没有给出按键")
        normalised: list[str] = []
        for key in keys:
            token = str(key).strip().lower()
            if not token:
                continue
            normalised.append(self.KEY_ALIASES.get(token, token))
        if not normalised:
            return ActionResult(False, "按键解析失败")
        try:
            if len(normalised) == 1:
                pyautogui.press(normalised[0])
                return ActionResult(True, f"按下 {normalised[0]}")
            pyautogui.hotkey(*normalised)
            return ActionResult(True, f"按下组合键 {'+'.join(normalised)}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"按键失败：{exc}")

    # ------------------------------------------------------------ 系统动作
    def open_app(self, command: str) -> ActionResult:
        """启动一个程序或打开一个 URL。"""
        command = (command or "").strip()
        if not command:
            return ActionResult(False, "没有给出要打开的目标")

        # 只检查「当作命令跑」的那条路。
        #
        # URL 直接走 os.startfile，它不会变成 shell 里的命令，所以不该拿
        # 命令黑名单去量它 —— 否则 `https://例.com/?q=del+/s` 这种网址
        # 会被误拦。非 URL 走的是 shell=True，那就必须查。
        if not command.startswith(("http://", "https://")):
            reason = command_rejection(command)
            if reason:
                return ActionResult(False, _rejection_message(reason))

        try:
            if command.startswith(("http://", "https://")):
                os.startfile(command)  # noqa: S606 - 用户明确要求打开 URL
            else:
                subprocess.Popen(command, shell=True, close_fds=True)
            time.sleep(0.6)
            return ActionResult(True, f"已启动：{command}")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"启动失败：{exc}")

    def run_command(self, command: str, timeout: int = 20) -> ActionResult:
        """执行一条命令并取回输出。默认关闭，属于高危动作。"""
        command = (command or "").strip()
        if not command:
            return ActionResult(False, "没有给出命令")
        reason = command_rejection(command)
        if reason:
            return ActionResult(False, _rejection_message(reason))
        try:
            completed = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=max(1, min(120, int(timeout))),
                encoding="utf-8", errors="replace",
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output = output.strip()
            if len(output) > 4000:
                output = output[:4000] + "\n…（输出被截断）"
            return ActionResult(
                completed.returncode == 0,
                output or f"命令结束，退出码 {completed.returncode}",
                {"returncode": completed.returncode},
            )
        except subprocess.TimeoutExpired:
            return ActionResult(False, f"命令超时（>{timeout} 秒）已终止")
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"命令执行失败：{exc}")

    def wait(self, seconds: float) -> ActionResult:
        seconds = max(0.0, min(10.0, float(seconds)))
        # 分片等待，这样停止请求能及时生效
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop.is_set():
                return ActionResult(False, "已被用户中断")
            time.sleep(min(0.1, max(0.0, deadline - time.time())))
        return ActionResult(True, f"等待了 {seconds:.1f} 秒")
