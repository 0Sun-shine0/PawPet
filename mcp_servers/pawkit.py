#!/usr/bin/env python3
"""小爪百宝袋 —— 给 PawPet 用的本地 MCP 工具集（纯标准库，零依赖）。

通信方式是 PawPet 自己那套「stdio + 每行一个 JSON-RPC 2.0」：
  · 一行 JSON 请求进来，一行 JSON 响应出去；
  · stdout 只能吐协议消息，任何日志都走 stderr；
  · 没有 id 的消息是通知（notifications/*），不回复。

提供 8 个小爪本身没有的实用小工具：
  now          当前时间详情
  time_until   距离某个日期/时间还有多久
  ts_convert   时间戳 <-> 日期时间互转
  sys_status   内存占用 / 开机时长
  calc         安全数学计算器
  decide       抛硬币 / 掷骰子 / 帮我选一个
  gen_password 生成密码或数字 PIN
  file_hash    计算文件的 md5 / sha1 / sha256
"""

from __future__ import annotations

import ast
import ctypes
import datetime as dt
import hashlib
import json
import math
import operator
import os
import random
import secrets
import sys
import time

SERVER_INFO = {"name": "pawkit", "version": "1.0"}
PROTOCOL_VERSION = "2024-11-05"

WEEKDAYS = "一二三四五六日"


# ================================================================== 协议层
def send(payload: dict) -> None:
    """把一条 JSON-RPC 消息作为单行写到 stdout。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request_id, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def text_result(text: str, is_error: bool = False) -> dict:
    """tools/call 的标准返回：一段文本内容。"""
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# ================================================================== 工具实现
def tool_now(_args: dict) -> str:
    now = dt.datetime.now()
    stamp = int(time.time())
    return "\n".join([
        f"现在是 {now.strftime('%Y-%m-%d %H:%M:%S')}（星期{WEEKDAYS[now.weekday()]}）",
        f"ISO 周：{now.isocalendar().year} 年第 {now.isocalendar().week} 周",
        f"Unix 时间戳：{stamp}（毫秒 {int(time.time() * 1000)}）",
        f"时区：UTC{_tz_offset_hours():+d}",
    ])


def _tz_offset_hours() -> float:
    offset = time.timezone if (time.daylight == 0) else time.altzone
    return -round(offset / 3600)


_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                 "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
                 "%m-%d", "%m/%d")

# 只给「时:分」的形式，按**今天**算。
#
# 为什么必须有这一组：工具的说明里写着「到 9:30 还有多久」是可以问的，
# 但原来只认完整日期，模型照着说明传 "9:30" 就被拒 —— 它会以为工具坏了，
# 然后在对话里跟用户说「这个工具不支持」。说明和实现不一致比少个功能更糟。
_TIME_ONLY = ("%H:%M:%S", "%H:%M")


def tool_time_until(args: dict) -> str:
    raw = str(args.get("target") or "").strip()
    if not raw:
        raise ValueError("需要给一个目标时间，比如 2026-10-01 或 09:30")

    target = None
    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            if fmt in ("%m-%d", "%m/%d"):        # 只给月日时补当前年份
                parsed = parsed.replace(year=dt.date.today().year)
            target = parsed
            break
        except ValueError:
            continue

    # 只有时间没日期 → 按今天算；如果那个点已经过了，就是**明天**的。
    # 这样「离 9:30 还有多久」在 15:00 问会答「明天 9:30」，
    # 符合人的直觉 —— 答「已经过了 5 小时」虽然也对，但不是用户想问的。
    if target is None:
        for fmt in _TIME_ONLY:
            try:
                parsed = dt.datetime.strptime(raw, fmt)
            except ValueError:
                continue
            now = dt.datetime.now()
            target = now.replace(hour=parsed.hour, minute=parsed.minute,
                                 second=parsed.second, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            break

    if target is None:
        raise ValueError(f"看不懂这个时间：{raw}，试试 2026-10-01、"
                         f"2026-10-01 09:30 或者 09:30")

    delta = target - dt.datetime.now()
    seconds = int(delta.total_seconds())
    if seconds == 0:
        return f"就是现在！（{target.strftime('%Y-%m-%d %H:%M')}）"

    parts = []
    days, rem = divmod(abs(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    if minutes:
        parts.append(f"{minutes} 分钟")
    if not days and not hours and secs:
        parts.append(f"{secs} 秒")
    human = " ".join(parts) or "不到 1 分钟"

    if seconds > 0:
        return f"距离 {raw} 还有 {human}。"
    return f"{raw} 已经过去 {human} 了。"


def tool_ts_convert(args: dict) -> str:
    raw = str(args.get("value") or "").strip()
    if not raw:
        return f"当前 Unix 时间戳：{int(time.time())}"

    # 纯数字：时间戳 -> 时间。秒/毫秒自动分辨。
    if raw.lstrip("-").isdigit():
        number = int(raw)
        if abs(number) > 10_000_000_000:        # 毫秒
            number = number // 1000
        moment = dt.datetime.fromtimestamp(number)
        utc = dt.datetime.fromtimestamp(number, dt.timezone.utc)
        return f"{raw} -> {moment.strftime('%Y-%m-%d %H:%M:%S')}（本地时间），" \
               f"UTC {utc.strftime('%Y-%m-%d %H:%M:%S')}"

    # 否则按日期时间解析：时间 -> 时间戳
    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            if fmt in ("%m-%d", "%m/%d"):
                parsed = parsed.replace(year=dt.date.today().year)
            return f"{raw} -> Unix 时间戳 {int(parsed.timestamp())}"
        except ValueError:
            continue
    raise ValueError(f"看不懂：{raw}。给时间戳数字，或 2026-10-01 09:30 这种日期时间")


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def tool_sys_status(_args: dict) -> str:
    if sys.platform != "win32":
        return f"当前系统：{sys.platform}（内存/开机时长只在 Windows 上读取）"

    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total_gb = status.ullTotalPhys / 1024 ** 3
    avail_gb = status.ullAvailPhys / 1024 ** 3
    used_pct = status.dwMemoryLoad

    uptime_ms = ctypes.windll.kernel32.GetTickCount64()
    uptime_sec = uptime_ms // 1000
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    uptime = f"{days} 天 {hours} 小时 {minutes} 分钟" if days else f"{hours} 小时 {minutes} 分钟"

    return "\n".join([
        f"内存：已用 {used_pct}%（{total_gb - avail_gb:.1f} / {total_gb:.1f} GB，"
        f"剩余 {avail_gb:.1f} GB）",
        f"开机时长：{uptime}",
    ])


# ---------------------------------------------------------------- 安全计算器
_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_ALLOWED_FUNCTIONS = {
    "sqrt": math.sqrt, "cbrt": lambda x: x ** (1 / 3),
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "abs": abs, "round": round,
    "floor": math.floor, "ceil": math.ceil, "degrees": math.degrees,
    "radians": math.radians, "factorial": math.factorial,
}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _ALLOWED_CONSTANTS:
        return _ALLOWED_CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 10 ** 9):
            raise ValueError("指数太大了，换个小点的数")
        return _ALLOWED_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _ALLOWED_FUNCTIONS
            and not node.keywords):
        args = [_safe_eval(arg) for arg in node.args]
        return _ALLOWED_FUNCTIONS[node.func.id](*args)
    raise ValueError("表达式里有不允许的写法（只支持四则运算、幂、括号和常用数学函数）")


def tool_calc(args: dict) -> str:
    expression = str(args.get("expression") or "").strip()
    if not expression:
        raise ValueError("要算什么？比如 (1 + 2) * 3 或 sqrt(2)")
    tree = ast.parse(expression, mode="eval")
    value = _safe_eval(tree)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expression} = {value}"


# ---------------------------------------------------------------- 随机决策
def tool_decide(args: dict) -> str:
    mode = str(args.get("mode") or "pick")
    if mode == "coin":
        return "抛硬币结果：" + random.choice(["正面", "反面"])
    if mode == "dice":
        sides = int(args.get("sides") or 6)
        if not 2 <= sides <= 100:
            raise ValueError("骰子面数要在 2 到 100 之间")
        return f"掷了一枚 {sides} 面骰：{random.randint(1, sides)}"

    options = args.get("options") or []
    options = [str(item).strip() for item in options if str(item).strip()]
    if not options:
        raise ValueError("给几个选项吧，比如 [\"火锅\", \"烧烤\", \"面条\"]")
    if len(options) > 50:
        raise ValueError("选项太多了，最多 50 个")
    chosen = random.choice(options)
    return f"小爪帮你选：**{chosen}**（从 {len(options)} 个选项里挑的）"


# ---------------------------------------------------------------- 密码生成
def tool_gen_password(args: dict) -> str:
    pin = bool(args.get("pin"))
    length = int(args.get("length") or (6 if pin else 16))
    count = int(args.get("count") or 1)
    if not 1 <= count <= 10:
        raise ValueError("一次最多生成 10 个")

    import string
    if pin:
        if not 4 <= length <= 12:
            raise ValueError("PIN 长度在 4 到 12 位之间")
        generated = ["".join(secrets.choice(string.digits) for _ in range(length))
                     for _ in range(count)]
        return f"{count} 个 {length} 位数字 PIN：\n" + "\n".join(generated)

    if not 8 <= length <= 64:
        raise ValueError("密码长度在 8 到 64 位之间")
    alphabet = string.ascii_letters + string.digits
    if args.get("symbols", True):
        alphabet += "!@#$%^&*-_=+?"
    passwords = []
    for _ in range(count):
        passwords.append("".join(secrets.choice(alphabet) for _ in range(length)))
    return f"{count} 个 {length} 位随机密码：\n" + "\n".join(passwords)


# ---------------------------------------------------------------- 文件哈希
def tool_file_hash(args: dict) -> str:
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        raise ValueError("需要文件路径")
    path = os.path.expandvars(os.path.expanduser(raw_path))
    algorithm = str(args.get("algorithm") or "sha256").lower()
    if algorithm not in ("md5", "sha1", "sha256", "sha512"):
        raise ValueError("只支持 md5 / sha1 / sha256 / sha512")
    if not os.path.isfile(path):
        raise ValueError(f"找不到文件：{path}")

    digest = hashlib.new(algorithm)
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return "\n".join([
        f"文件：{path}",
        f"大小：{size:,} 字节",
        f"{algorithm}：{digest.hexdigest()}",
    ])


# ================================================================== 工具清单
TOOLS = [
    {
        "name": "now",
        "description": "查看当前的日期和时间（星期、ISO 周、Unix 时间戳、时区）。"
                       "用户问「几点了」「今天周几」时用。",
        "inputSchema": {"type": "object", "properties": {}},
        "run": tool_now,
    },
    {
        "name": "time_until",
        "description": "计算距离某个日期/时间还有多久，或者已经过去多久。"
                       "用户问「离国庆还有几天」「到 9:30 还有多久」时用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "description": "目标时间。可以给完整日期（2026-10-01）、"
                                          "日期加时间（2026-10-01 09:30）、"
                                          "只给月日（10-01），或者只给时间"
                                          "（09:30 —— 按今天算，已经过了就是明天）"},
            },
            "required": ["target"],
        },
        "run": tool_time_until,
    },
    {
        "name": "ts_convert",
        "description": "Unix 时间戳和日期时间互相转换。传数字按时间戳解析（自动识别秒/毫秒），"
                       "传日期时间则返回时间戳；不传则返回当前时间戳。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "string",
                          "description": "时间戳数字，或 2026-10-01 09:30 这样的日期时间"},
            },
        },
        "run": tool_ts_convert,
    },
    {
        "name": "sys_status",
        "description": "查看这台电脑的内存使用情况和开机时长。"
                       "用户问「电脑卡不卡」「内存还剩多少」「开了多久机」时用。",
        "inputSchema": {"type": "object", "properties": {}},
        "run": tool_sys_status,
    },
    {
        "name": "calc",
        "description": "精确的数学计算器，支持 + - * / 幂、括号、pi/e 和 sqrt/sin/log/factorial "
                       "等常用函数。涉及具体数字计算时不要心算，用它。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式，如 (1+2)*3、sqrt(2)"},
            },
            "required": ["expression"],
        },
        "run": tool_calc,
    },
    {
        "name": "decide",
        "description": "选择困难助手：mode=pick 时从 options 里随机选一个；"
                       "mode=coin 抛硬币；mode=dice 掷骰子（默认 6 面）。"
                       "用户说「帮我选一个」「抛硬币决定」「今天吃什么」时用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["pick", "coin", "dice"],
                         "description": "pick=帮我选 / coin=抛硬币 / dice=掷骰子，默认 pick"},
                "options": {"type": "array", "items": {"type": "string"},
                            "description": "mode=pick 时的候选选项"},
                "sides": {"type": "integer", "description": "mode=dice 时骰子面数，默认 6"},
            },
        },
        "run": tool_decide,
    },
    {
        "name": "gen_password",
        "description": "生成随机密码或纯数字 PIN（用密码学安全的随机源）。"
                       "用户说「帮我想个密码」「来个 6 位验证码」时用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "length": {"type": "integer", "description": "长度，密码默认 16（8-64），PIN 默认 6（4-12）"},
                "count": {"type": "integer", "description": "生成几个，默认 1，最多 10"},
                "pin": {"type": "boolean", "description": "true=只要数字 PIN，默认 false"},
                "symbols": {"type": "boolean", "description": "密码是否包含符号，默认 true"},
            },
        },
        "run": tool_gen_password,
    },
    {
        "name": "file_hash",
        "description": "计算本地文件的校验值（md5 / sha1 / sha256 / sha512，默认 sha256）。"
                       "用户要核对下载文件、问文件指纹时用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径，支持 ~ 和环境变量"},
                "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256", "sha512"],
                              "description": "哈希算法，默认 sha256"},
            },
            "required": ["path"],
        },
        "run": tool_file_hash,
    },
]

TOOL_INDEX = {tool["name"]: tool for tool in TOOLS}


# ================================================================== 请求分发
def handle(message: dict) -> None:
    method = message.get("method")
    request_id = message.get("id")

    # 通知（没有 id）一律不回复
    if request_id is None:
        return

    if method == "initialize":
        reply(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    elif method == "ping":
        reply(request_id, {})
    elif method == "tools/list":
        public = [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in TOOLS
        ]
        reply(request_id, {"tools": public})
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") or ""
        tool = TOOL_INDEX.get(name)
        if tool is None:
            fail(request_id, -32601, f"没有名为 {name} 的工具")
            return
        try:
            text = tool["run"](params.get("arguments") or {})
            reply(request_id, text_result(text))
        except Exception as exc:  # 业务错误用 isError 返回，模型能读到原因
            print(f"[pawkit] 工具 {name} 出错：{exc}", file=sys.stderr, flush=True)
            reply(request_id, text_result(f"{name} 执行失败：{exc}", is_error=True))
    else:
        fail(request_id, -32601, f"不支持的方法：{method}")


def main() -> None:
    # Windows 上 stdin/stdout 默认可能是 GBK，强制 UTF-8 对齐客户端
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[pawkit] 丢弃无法解析的行：{exc}", file=sys.stderr, flush=True)
            continue
        try:
            handle(message)
        except Exception as exc:  # 绝不能因为一条消息崩掉整个进程
            print(f"[pawkit] 处理消息失败：{exc}", file=sys.stderr, flush=True)
            if isinstance(message, dict) and message.get("id") is not None:
                fail(message["id"], -32603, f"服务器内部错误：{exc}")


if __name__ == "__main__":
    main()
