r"""本地文件读写，带一层路径安全策略。

为什么需要它
------------
没有它的时候，用户想让 AI 看一个文件，只能：打开文件 → 全选 → 复制 →
切到小爪 → 粘贴。绕一大圈，长文件还会把剪贴板搞爆。

为什么必须带安全层
------------------
「能读任意文件」和「只读屏幕」不是一个量级的风险：

* 屏幕截图只暴露**此刻显示的东西**；
* 任意文件读取暴露的是**整块硬盘**。

而且这些内容会**发到模型服务商那里去**。所以真正的威胁不是
「AI 把文件删了」，而是「AI 被一段恶意文字骗着把 ~/.ssh/id_rsa
或浏览器密码库读出来发走」。

所以这里的策略是**先按路径分档，再看权限等级**：

1. **凭据位置读写都拒**（`_SECRET_DIRS`）：`.ssh`、`.aws`、
   浏览器 User Data、注册表配置单元。这些是密钥和密码库，
   模型永远不该有机会把它们塞进对话里。
2. **系统位置只拒写**（`_PROTECTED_DIRS`）：`C:\Windows`、
   `Program Files`。这里面的东西不是秘密（`hosts`、日志、证书都在这），
   用户看它们是合理需求；但让 AI 改这里的东西没必要，直接拒。
3. **普通位置按权限等级走**（和键鼠操作同一套 Risk 机制）。
4. **写入永远比读取更严**，且默认不覆盖已有文件。

另外所有动作都进审计日志，用户能回看 AI 碰过哪些文件。
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 体积上限
MAX_READ_BYTES = 1_000_000        # 单次最多读 1 MB
MAX_LINES = 2_000                 # 单次最多返回 2000 行
MAX_WRITE_BYTES = 2_000_000       # 单次最多写 2 MB
MAX_LIST_ENTRIES = 300            # 列目录最多 300 项


# ==========================================================================
#  敏感路径：这些地方**永远不读也不写**
# ==========================================================================
def _env_path(name: str, *parts: str) -> Path | None:
    """取一个系统目录。环境变量拿不到时**按标准位置兜底**，返回 None 是最后手段。

    为什么不能只用环境变量：`_secret_dirs()` 是「凭据位置一律拒读写」那条
    红线，而它是**在导入时按环境变量算出来的**。APPDATA / LOCALAPPDATA
    只要缺失（精简版环境、某些启动器/服务方式拉起、被沙箱剥掉的环境），
    浏览器那几条就**静默从拒读清单里消失** —— 界面一切正常，只是 AI 从此
    读得到 Firefox 的 profiles.ini 和 Chrome 的 Login Data。

    这类「守卫自己悄悄失效」比没有守卫更危险，因为没人会去复核它。
    所以这里按 Windows 的标准布局兜底一次，环境变量只是第一优先。

    只兜 Windows：其它平台这两条路径本来就不存在，`_existing()` 会过滤掉。
    """
    base = os.environ.get(name)
    if base:
        return Path(base).joinpath(*parts)

    home = Path.home()
    fallback = {
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
    }.get(name)
    if fallback is None:
        return None
    return fallback.joinpath(*parts)


def _secret_dirs() -> list[Path]:
    """凭据/密钥所在地。**读写都拒。**

    判断方式是**目录前缀匹配**，不是文件名匹配 —— 名字匹配太容易绕过
    （改个名就行），而目录边界是稳定的。
    """
    home = Path.home()

    candidates: list[Path | None] = [
        # 凭据 / 密钥
        home / ".ssh",
        home / ".aws",
        home / ".gnupg",
        home / ".kube",
        home / ".docker",
        home / ".netrc",
        home / ".npmrc",
        home / ".pypirc",
        home / ".git-credentials",
        home / ".config" / "gh",
        # 浏览器与应用的私密数据
        _env_path("APPDATA", "Mozilla"),
        _env_path("LOCALAPPDATA", "Google", "Chrome", "User Data"),
        _env_path("LOCALAPPDATA", "Microsoft", "Edge", "User Data"),
        _env_path("APPDATA", "Microsoft", "Crypto"),
        _env_path("APPDATA", "Microsoft", "Protect"),
        _env_path("APPDATA", "Microsoft", "Windows", "PowerShell"),
        _env_path("APPDATA", "Microsoft", "Credentials"),
        _env_path("LOCALAPPDATA", "Microsoft", "Credentials"),
        # 注册表配置单元（SAM/SYSTEM/SECURITY 里是口令散列）
        Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "config",
    ]
    return _existing(candidates)


def _protected_dirs() -> list[Path]:
    """系统位置。**只拒写，允许读。**

    这里面的东西不是秘密 —— hosts、日志、证书、各种配置都在 Windows 目录下，
    用户让 AI 看一眼是合理需求。但让 AI 改系统目录里的文件没有任何好理由，
    所以写操作一律拒。
    """
    windows = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    candidates: list[Path | None] = [
        windows,
        Path("C:/ProgramData/Microsoft"),
    ]
    for name in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value))
    return _existing(candidates)


def _existing(candidates: list[Path | None]) -> list[Path]:
    result: list[Path] = []
    for item in candidates:
        if item is None:
            continue
        try:
            result.append(item.resolve(strict=False))
        except (OSError, RuntimeError):
            continue
    return result


_SECRET_DIRS = _secret_dirs()
_PROTECTED_DIRS = _protected_dirs()

# 文件名精确匹配的额外黑名单（在允许目录里也要挡住这些）
_SENSITIVE_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "shadow", "passwd", "sudoers",
    "login.keychain", "keychain-db",
    "sam", "system", "security",   # 注册表配置单元
}
_SENSITIVE_SUFFIXES = {".pfx", ".p12", ".jks", ".keystore", ".kdbx", ".ppk"}


class FileDenied(Exception):
    """路径被策略拒绝。消息是给用户/模型看的。"""


@dataclass
class ReadResult:
    ok: bool
    text: str = ""
    message: str = ""
    path: str = ""
    encoding: str = ""
    total_lines: int = 0
    truncated: bool = False
    size: int = 0
    extra: dict = field(default_factory=dict)


# ==========================================================================
#  路径检查
# ==========================================================================
def describe_sensitive() -> list[str]:
    """给界面/模型看的受限目录清单。"""
    return ([f"[读写都禁止] {item}" for item in _SECRET_DIRS]
            + [f"[只允许读]   {item}" for item in _PROTECTED_DIRS])


def expand(path: str) -> Path:
    """把用户/模型给的路径展开成绝对路径。

    支持 `~`、环境变量（%USERPROFILE%、$HOME），以及相对路径
    （相对当前工作目录）。
    """
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        raise FileDenied("没有给路径")
    # Windows 上 %VAR%，POSIX 上 $VAR 和 ${VAR}
    text = os.path.expandvars(text)
    text = os.path.expanduser(text)
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise FileDenied(f"路径无法解析：{exc}") from exc


def check_path(path: Path, writing: bool = False) -> None:
    """检查路径是否允许访问。

    reading：只要不落在凭据目录里就行。
    writing：凭据目录和系统目录都不许。
    """
    for blocked in _SECRET_DIRS:
        try:
            path.relative_to(blocked)
        except ValueError:
            continue
        raise FileDenied(
            f"「{blocked}」是凭据/密钥位置，出于安全考虑不读也不写。\n"
            "如果确实需要里面的信息，请你手动打开后复制内容给我。"
        )

    if writing:
        for blocked in _PROTECTED_DIRS:
            try:
                path.relative_to(blocked)
            except ValueError:
                continue
            raise FileDenied(
                f"「{blocked}」是系统目录，只允许读、不允许写。\n"
                "要保存内容请换一个位置，比如桌面或文档目录。"
            )

    name = path.name.lower()
    if name in _SENSITIVE_NAMES:
        raise FileDenied(f"「{path.name}」看起来是密钥或系统文件，不读也不写。")
    if path.suffix.lower() in _SENSITIVE_SUFFIXES:
        raise FileDenied(f"「{path.suffix}」是密钥库格式，不读也不写。")


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} 字节"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"


# ==========================================================================
#  解码
# ==========================================================================
_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


def bom_encoding(raw: bytes) -> str:
    """按 BOM 判断编码。没有 BOM 返回空串。"""
    for marker, encoding in _BOMS:
        if raw.startswith(marker):
            return encoding
    return ""


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """把文件内容解成文本，返回 (文本, 用了什么编码)。

    中文 Windows 上大量文本文件是 GBK，直接用 utf-8 读会乱码或报错，
    所以逐个试。utf-8-sig 放最前面是为了吃掉 BOM
    （这个坑在数据文件那边踩过一次）。

    带 BOM 的文件**直接按 BOM 指定的编码解**，不要再去逐个试：
    UTF-16 的字节流里全是 NUL，按 utf-8/gb18030 试都会失败，
    最后会落到 errors="replace" 那条路，读出来是一堆替换字符。
    """
    by_bom = bom_encoding(raw)
    if by_bom:
        try:
            return raw.decode(by_bom), by_bom
        except (UnicodeDecodeError, LookupError):
            pass   # BOM 撒了谎，继续按下面的顺序试

    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue

    # 最后兜底：替换非法字节，保证一定能返回点东西
    return raw.decode("utf-8", errors="replace"), "utf-8(有损坏字节)"


def looks_binary(raw: bytes) -> bool:
    """靠 NUL 字节判断是不是二进制。

    文本文件（哪怕 GBK）不会有 NUL；exe/zip/png 开头几十字节里就有。

    但**带 BOM 的 UTF-16/32 例外** —— 它们的 BOM 里就有 NUL
    （`\\xff\\xfe` 之后每两个字节一个 0），会被误判成二进制。
    这是实测踩出来的：UTF-16 存的文本文件原本一律读不了。
    """
    if bom_encoding(raw):
        return False
    return b"\x00" in raw[:8192]


# ==========================================================================
#  读写实现
# ==========================================================================
def read_text(path_text: str, start_line: int = 1, max_lines: int = MAX_LINES,
              max_bytes: int = MAX_READ_BYTES) -> ReadResult:
    """读一个文本文件。"""
    try:
        path = expand(path_text)
        check_path(path)
    except FileDenied as exc:
        return ReadResult(False, message=str(exc))

    if not path.exists():
        return _not_found(path)
    if path.is_dir():
        return ReadResult(
            False,
            message=(f"「{path.name}」是一个文件夹，不是文件。"
                     "用 list_dir 看里面有什么，或者指定具体的文件名。"),
            path=str(path),
        )

    try:
        size = path.stat().st_size
    except OSError as exc:
        return ReadResult(False, message=f"读不到文件信息：{exc}", path=str(path))

    if size == 0:
        return ReadResult(True, text="", message="这个文件是空的。",
                          path=str(path), size=0, total_lines=0)

    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except PermissionError:
        return ReadResult(False, path=str(path),
                          message=f"没有权限读「{path.name}」（被占用或需要管理员）。")
    except OSError as exc:
        return ReadResult(False, path=str(path), message=f"读文件失败：{exc}")

    truncated_bytes = len(raw) > max_bytes
    raw = raw[:max_bytes]

    if looks_binary(raw):
        return ReadResult(
            False, path=str(path), size=size,
            message=(f"「{path.name}」像是二进制文件（{human_size(size)}），"
                     "读出来会是乱码，所以没读。\n"
                     "如果是文档（Word/Excel/PDF），让我用别的方式打开它看内容；"
                     "如果是程序或压缩包，请说明你想从中得到什么。"),
        )

    text, encoding = decode_bytes(raw)
    lines = text.splitlines()
    total_lines = len(lines)

    start = max(1, int(start_line or 1))
    limit = max(1, min(int(max_lines or MAX_LINES), MAX_LINES))
    chosen = lines[start - 1: start - 1 + limit]
    body = "\n".join(chosen)

    truncated = truncated_bytes or (start - 1 + limit) < total_lines
    note = ""
    if truncated:
        shown_to = start - 1 + len(chosen)
        note = (f"\n\n（只显示了第 {start}-{shown_to} 行，共 {total_lines} 行。"
                f"要继续看就再调一次，把 start_line 设成 {shown_to + 1}）")

    header = (f"文件：{path}\n"
              f"共 {total_lines} 行 · {human_size(size)} · 编码 {encoding}")
    if truncated_bytes:
        header += f" · 只读了前 {human_size(max_bytes)}"

    return ReadResult(
        True, text=body + note, message=header, path=str(path),
        encoding=encoding, total_lines=total_lines,
        truncated=truncated, size=size,
    )


def _not_found(path: Path) -> ReadResult:
    """文件不存在时，顺手找几个名字相近的 —— 十有八九是路径打错了。"""
    parent = path.parent
    if not parent.is_dir():
        return ReadResult(False, path=str(path),
                          message=f"没有这个文件，而且上级目录也不存在：{path}")

    stem = path.stem.lower()
    try:
        nearby = [
            item.name for item in parent.iterdir()
            if item.is_file() and stem and stem[:3] in item.name.lower()
        ][:6]
    except OSError:
        nearby = []

    message = f"没有这个文件：{path}"
    if nearby:
        message += f"\n同目录下名字相近的有：{'、'.join(nearby)}"
    return ReadResult(False, message=message, path=str(path))


def list_dir(path_text: str, pattern: str = "", max_entries: int = MAX_LIST_ENTRIES) -> ReadResult:
    """列目录。pattern 可选，按文件名通配过滤。"""
    try:
        path = expand(path_text or ".")
        check_path(path)
    except FileDenied as exc:
        return ReadResult(False, message=str(exc))

    if not path.exists():
        return _not_found(path)
    if not path.is_dir():
        return ReadResult(False, path=str(path),
                          message=f"「{path.name}」是文件不是目录。用 read_file 读它。")

    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return ReadResult(False, path=str(path), message=f"没有权限列出「{path}」。")
    except OSError as exc:
        return ReadResult(False, path=str(path), message=f"列目录失败：{exc}")

    if pattern:
        needle = pattern.lower()
        entries = [item for item in entries if fnmatch.fnmatch(item.name.lower(), needle)]

    lines = []
    for item in entries[:max(1, min(int(max_entries or MAX_LIST_ENTRIES), MAX_LIST_ENTRIES))]:
        try:
            if item.is_dir():
                lines.append(f"  [目录] {item.name}/")
            else:
                size = item.stat().st_size
                lines.append(f"  {human_size(size):>10s}  {item.name}")
        except OSError:
            lines.append(f"  ?          {item.name}")

    total = len(entries)
    header = f"目录：{path}\n共 {total} 项" + (f"（按「{pattern}」过滤）" if pattern else "")
    if total > len(lines):
        header += f"，只列了前 {len(lines)} 项"

    return ReadResult(True, text="\n".join(lines) or "  （空目录）",
                      message=header, path=str(path),
                      extra={"total": total, "shown": len(lines)})


def write_text(path_text: str, content: str, overwrite: bool = False,
               create_dirs: bool = True) -> ReadResult:
    """写文件。默认**不覆盖**已有文件 —— 覆盖是不可逆的。"""
    try:
        path = expand(path_text)
        check_path(path, writing=True)
    except FileDenied as exc:
        return ReadResult(False, message=str(exc))

    data = content if isinstance(content, str) else str(content or "")
    encoded = data.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        return ReadResult(
            False, path=str(path),
            message=(f"内容太大了（{human_size(len(encoded))}），"
                     f"单次最多写 {human_size(MAX_WRITE_BYTES)}。"))

    if path.exists() and path.is_dir():
        return ReadResult(False, path=str(path),
                          message=f"「{path.name}」是目录，不能当文件写。")

    if path.exists() and not overwrite:
        try:
            old_size = path.stat().st_size
        except OSError:
            old_size = 0
        return ReadResult(
            False, path=str(path),
            message=(f"「{path.name}」已经存在（{human_size(old_size)}），"
                     "没有覆盖它。\n"
                     "如果确实要覆盖，把 overwrite 设成 true 再调一次；"
                     "或者换一个文件名。"),
            extra={"exists": True},
        )

    parent = path.parent
    if not parent.is_dir():
        if not create_dirs:
            return ReadResult(False, path=str(path),
                              message=f"目录不存在：{parent}")
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ReadResult(False, path=str(path), message=f"建目录失败：{exc}")

    existed = path.exists()
    try:
        # 先写临时文件再原子替换：中途断电也不会留下半个文件
        temp = path.with_suffix(path.suffix + ".pawpet-tmp")
        with open(temp, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except PermissionError:
        return ReadResult(False, path=str(path),
                          message=f"没有权限写「{path}」（文件可能被别的程序占用）。")
    except OSError as exc:
        return ReadResult(False, path=str(path), message=f"写文件失败：{exc}")

    action = "覆盖" if existed else "写入"
    return ReadResult(
        True, path=str(path), encoding="utf-8", size=len(encoded),
        message=f"已{action}：{path}（{human_size(len(encoded))}）",
    )
