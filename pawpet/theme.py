"""用户可调的界面配色 —— 「通过对话定制主题」的落地。

为什么不直接改 Theme.qml
------------------------
最直觉的做法是让用户去覆盖 `Theme.qml`。实测行不通，有两个硬障碍：

1. **单例没法覆盖。** `Theme.qml` 带 `pragma Singleton`，模块定义在
   `pawpet/qml/PawPet/` 里。QML 的模块解析对同名类型是「先找到的赢」，
   而导入路径的顺序不可靠 —— 靠这个做「用户覆盖」是碰运气。
2. **打包后摸不到源码。** PyInstaller 把 QML 打进 exe，运行时解包到
   `_MEIPASS`、退出就删。磁盘上没有可以改的文件。

所以走**数据**这条路：颜色值放在用户数据目录的 `theme.json` 里，
`Theme.qml` 从 Backend 读。好处有三个：
* 不用碰任何一方源码，升级不会覆盖用户的定制；
* 改完**立刻生效**（走 Qt 属性通知），不用重启；
* 值可以被校验 —— 用户（或模型）写进去一个没法读的颜色，
  我们能在写的时候挡掉，而不是让界面变成一片谁也看不懂的颜色。

哪些能改、哪些不能
------------------
不是所有颜色都该开放。`rose`（错误/危险）如果被改成和背景一个色，
用户就再也看不到「这一步失败了」。所以每个角色带一个 `safe` 标记，
危险的不进可改清单，模型也就不会去动它。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 颜色值只接受 #rgb / #rrggbb / #aarrggbb 这三种写法。
# 不接受颜色名（"red"）和 rgba() 表达式 —— 它们在不同背景上的可读性
# 没法预判，而且是模型最容易瞎编的形态。
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class ThemeRole:
    """一个可调的配色角色。"""

    __slots__ = ("key", "label", "hint", "default", "safe")

    def __init__(self, key: str, label: str, default: str,
                 hint: str = "", safe: bool = True) -> None:
        self.key = key
        self.label = label
        self.default = default
        self.hint = hint
        self.safe = safe

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "default": self.default,
            "hint": self.hint,
            "safe": self.safe,
        }


# ==========================================================================
#  角色清单
# ==========================================================================
# 默认值必须和 Theme.qml 里的保持一致 —— 那边读不到覆盖时用的就是这些。
# 不是全部角色都开放：见文件开头的说明。
ROLES: tuple[ThemeRole, ...] = (
    # ---- 背景层
    ThemeRole("bg", "页面底色", "#fdf7f9",
              "整个工作台的背景"),
    ThemeRole("surface", "卡片底色", "#ffffff",
              "卡片、面板的底色，一般比页面底色亮一点"),
    ThemeRole("surfaceAlt", "次级块底色", "#fdf1f5",
              "输入框、说明块这类「陷进去」的区域"),
    ThemeRole("surfaceHi", "高亮/悬停", "#fbe6ee",
              "鼠标悬停、对话气泡的底色"),
    ThemeRole("border", "描边", "#f0d4e0", "卡片和输入框的边线"),
    ThemeRole("borderSoft", "浅描边", "#f7e4ec", "更淡的边线"),

    # ---- 文字
    ThemeRole("text", "正文颜色", "#4a3b45",
              "主要文字。注意要能压在上面的底色上看清"),
    ThemeRole("textDim", "次要文字", "#7d6577", "说明性文字"),
    ThemeRole("textFaint", "最弱文字", "#9a8494",
              "时间戳、附注这类最不重要的文字"),

    # ---- 强调色
    ThemeRole("accent", "主色调", "#f4879f",
              "按钮、选中态、进度条的主色。改这个最出效果"),
    ThemeRole("accentSoft", "主色浅底", "#fde8ee", "主色调的淡背景版"),
    ThemeRole("violet", "次要色（紫）", "#b48ae0", "休息、次要标记"),
    ThemeRole("violetSoft", "次要色浅底", "#f3eafd"),
    ThemeRole("mint", "成功色（薄荷）", "#5fc4ad", "完成、正常状态"),
    ThemeRole("mintSoft", "成功色浅底", "#e6f7f2"),
    ThemeRole("gold", "提醒色（琥珀）", "#e8ab4f", "待确认、提醒"),
    ThemeRole("goldSoft", "提醒色浅底", "#fdf3e2"),

    # ---- 不许改的
    ThemeRole("rose", "错误色（玫红）", "#e8607a",
              "失败和危险操作的颜色。改成和背景相近会让用户看不到报错，"
              "所以固定不可改", safe=False),
    ThemeRole("roseSoft", "错误色浅底", "#fdeaee",
              hint="固定不可改", safe=False),
)

_BY_KEY = {role.key: role for role in ROLES}


# ==========================================================================
#  读写
# ==========================================================================
def editable_roles() -> list[dict]:
    """能给用户（和模型）看的可调角色。"""
    return [role.as_dict() for role in ROLES if role.safe]


def all_roles() -> list[dict]:
    return [role.as_dict() for role in ROLES]


def default_overrides() -> dict[str, str]:
    return {role.key: role.default for role in ROLES}


def is_valid_color(value: str) -> bool:
    return bool(_HEX.match(str(value or "").strip()))


def sanitize(overrides) -> tuple[dict[str, str], list[str]]:
    """把外部给的覆盖值过一遍，返回 (干净的, 被拒绝的原因列表)。

    拒绝而不是「纠错后接受」：模型写错颜色时，静默替换会让用户看到的
    和他要求的不一样，而他还不知道为什么。明确拒绝并说明原因，
    模型下一轮就能改对。
    """
    clean: dict[str, str] = {}
    rejected: list[str] = []
    if not isinstance(overrides, dict):
        return clean, ["覆盖值不是一个键值对"]

    for key, raw in overrides.items():
        role = _BY_KEY.get(str(key))
        if role is None:
            rejected.append(f"没有「{key}」这个配色项")
            continue
        if not role.safe:
            rejected.append(f"「{role.label}」不允许改（{role.hint or '固定'}）")
            continue
        value = str(raw or "").strip()
        if not is_valid_color(value):
            rejected.append(
                f"「{role.label}」的值 {value!r} 不是颜色。"
                "请用 #rrggbb 这种写法，比如 #f4879f"
            )
            continue
        clean[role.key] = value
    return clean, rejected


def load(path: Path) -> dict[str, str]:
    """读用户定制的颜色。读不到就返回空 —— 空等于全用默认值。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    clean, _rejected = sanitize(raw.get("colors") if "colors" in raw else raw)
    return clean


def save(path: Path, overrides: dict[str, str]) -> tuple[bool, str]:
    """写回。失败返回 (False, 原因)。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"colors": overrides}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        return True, "已保存"
    except OSError as exc:
        return False, f"写不进 {path.name}：{exc}"


def reset(path: Path) -> tuple[bool, str]:
    """恢复默认：把文件删掉。"""
    try:
        if path.exists():
            path.unlink()
        return True, "已恢复默认配色"
    except OSError as exc:
        return False, f"删不掉 {path.name}：{exc}"


def resolved(overrides: dict[str, str]) -> dict[str, str]:
    """默认值 + 覆盖值 = 最终生效的完整配色表。"""
    final = default_overrides()
    for key, value in (overrides or {}).items():
        if key in _BY_KEY and is_valid_color(value):
            final[key] = value
    return final


def describe(overrides: dict[str, str]) -> str:
    """给界面/模型看的一行摘要。

    只说**真的和默认不一样**的项。theme.json 里可能留着「值和默认相同」
    的条目（用户先改成默认色、或者手改过文件），把那些也算成「你改过」
    会让用户莫名其妙 —— 他明明什么都没动。
    """
    effective = {
        key: value for key, value in (overrides or {}).items()
        if key in _BY_KEY and is_valid_color(value)
        and value.lower() != _BY_KEY[key].default.lower()
    }
    if not effective:
        return "当前是默认配色（粉白）"
    parts = []
    for key, value in effective.items():
        role = _BY_KEY.get(key)
        parts.append(f"{role.label if role else key} {value}")
    return "你改过：" + "、".join(parts)
