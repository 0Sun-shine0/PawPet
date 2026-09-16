"""让用户通过对话给自己造工具 —— 「二次开发」的落地层。

为什么需要
----------
用户（或同事）会反复遇到同一类活儿：处理某种工单、按固定格式整理数据、
把某个网站的内容抓下来归档。这类东西现在只能靠**提示词**表达 ——
用户写在便签里，每次让小爪读了再重新理解、重新决定怎么做。

同一条流程跑两次，结果不会完全一样，步骤可能漏、顺序可能变。
把它变成工具就是：写死一次，以后每次都一样。

安全模型：三档，默认最保守
--------------------------
造工具这件事的风险不在「工具有没有用」，在**它会动用户什么**。
所以每一档都明确限定能力，而不是给一个「任意执行」的出口：

* `note`   —— 纯提示词。不读写任何文件、不联网、不动键鼠。
                本质是「给模型的一段固定指令」，最安全。
* `recipe` —— 一串**只读**步骤：读文件、列目录、正则提取、生成回复。
                能覆盖大多数「整理 / 汇总 / 起草」类需求。
* `code`   —— 真生成 Python。**默认关闭**，要用户显式打开。
                理由：这是「用户让 AI 造的东西在用户电脑上执行任意代码」。
                对泛用户我认为不可接受；给愿意承担的人留个开关，
                但每一次运行都要单独确认。

**越权是硬拦截，不是提示词劝阻。** 每个级别有一个能力白名单，
recipe 步骤里出现 `write_file` 这种直接拒掉 —— 和 advisor 的止损一个思路：
光靠提示词劝不住，得在代码里挡。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# 名字要能当工具名用：小写字母数字下划线，字母开头
_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

# 级别
LEVEL_NOTE = "note"
LEVEL_RECIPE = "recipe"
LEVEL_CODE = "code"

LEVEL_LABELS = {
    LEVEL_NOTE: "固定说法",
    LEVEL_RECIPE: "固定流程",
    LEVEL_CODE: "自定义代码",
}

LEVEL_HINTS = {
    LEVEL_NOTE: "只是给模型一段固定指令，不碰文件、不联网",
    LEVEL_RECIPE: "按固定的几步做（读文件、提取、生成结果），只读，不写不改",
    LEVEL_CODE: "运行你自己写的代码。能力最强，但请只在你信任的代码上用",
}

# 允许的最高级别。code 默认关掉 —— 见文件开头的说明。
MAX_LEVEL_DEFAULT = LEVEL_RECIPE
_LEVEL_RANK = {LEVEL_NOTE: 0, LEVEL_RECIPE: 1, LEVEL_CODE: 2}


# ==========================================================================
#  recipe 的步骤 —— 这是「能力边界」的具体清单
# ==========================================================================
# 每个步骤是一个 dict：{"op": "...", ...参数}。
# 只有列在这里的 op 能被执行，别的直接拒。
#
# **故意没有 write_file / run_command / click 这些。** 要写文件就必须
# 走 code 档，而 code 档每次运行都要用户点头 —— 这样「静默改用户东西」
# 在结构上就不可能发生。
READ_OPS = {
    "read_file": "读一个文本文件的内容",
    "list_dir": "列出一个目录里的文件名",
    "regex_find": "用正则从文本里抽取片段",
    "pick_lines": "按关键词挑出包含它的行",
    "template": "把变量填进一个模板，生成结果",
    "join": "把几段文本拼起来",
    "truncate": "截断到指定长度",
    "count": "数一数有多少条 / 多少行",
}


@dataclass
class Step:
    op: str = ""
    args: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"op": self.op, **self.args}


@dataclass
class Extension:
    """一个用户定义的扩展工具。"""

    name: str = ""
    title: str = ""                 # 中文名，界面上显示这个
    description: str = ""           # 给模型看的：什么时候该用它
    level: str = LEVEL_NOTE
    # note 档用这个：一段固定指令
    prompt: str = ""
    # recipe 档用这个：一串只读步骤
    steps: list[dict] = field(default_factory=list)
    # note / recipe 都要声明参数，模型按这个传
    parameters: dict = field(default_factory=dict)
    # code 档用这个（默认关）
    code: str = ""
    created: float = 0.0
    updated: float = 0.0
    version: int = 1
    # 用户是不是明确同意过（造的时候要点一次）
    approved: bool = False
    run_count: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "level": self.level,
            "levelLabel": LEVEL_LABELS.get(self.level, self.level),
            "prompt": self.prompt,
            "steps": list(self.steps),
            "parameters": dict(self.parameters),
            "code": self.code,
            "created": self.created,
            "updated": self.updated,
            "version": self.version,
            "approved": self.approved,
            "runCount": self.run_count,
        }

    @classmethod
    def from_dict(cls, raw) -> "Extension":
        if not isinstance(raw, dict):
            return cls()
        steps = raw.get("steps")
        return cls(
            name=str(raw.get("name") or ""),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            level=str(raw.get("level") or LEVEL_NOTE),
            prompt=str(raw.get("prompt") or ""),
            steps=[s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else [],
            parameters=raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {},
            code=str(raw.get("code") or ""),
            created=float(raw.get("created") or 0.0),
            updated=float(raw.get("updated") or 0.0),
            version=int(raw.get("version") or 1),
            approved=bool(raw.get("approved")),
            run_count=int(raw.get("runCount") or 0),
        )

    # ---------------------------------------------------------------- 校验
    def problems(self, max_level: str = MAX_LEVEL_DEFAULT) -> list[str]:
        """这个定义有哪些问题。空列表 = 可以装。"""
        issues: list[str] = []

        if not _NAME.match(self.name or ""):
            issues.append(
                f"名字 {self.name!r} 不合法：只能用**小写字母开头**的"
                "小写字母、数字、下划线，长度 3~41"
            )
        if not (self.title or "").strip():
            issues.append("缺中文名（title）—— 界面上要显示这个")
        if not (self.description or "").strip():
            issues.append(
                "缺说明（description）—— 模型靠它判断什么时候该用这个工具，"
                "写清楚「用户说什么的时候用它」"
            )

        if self.level not in LEVEL_LABELS:
            issues.append(f"级别 {self.level!r} 不认识，只能是 note / recipe / code")
            return issues

        if _LEVEL_RANK[self.level] > _LEVEL_RANK.get(max_level, 0):
            issues.append(
                f"「{LEVEL_LABELS[self.level]}」这一档现在是关着的"
                f"（当前最高允许到「{LEVEL_LABELS.get(max_level, max_level)}」）。"
                "要开它得让用户在设置里明确打开 —— 那意味着允许运行自定义代码。"
            )

        if self.level == LEVEL_NOTE and not (self.prompt or "").strip():
            issues.append("固定说法这一档必须给 prompt（那段固定指令）")

        if self.level == LEVEL_RECIPE:
            if not self.steps:
                issues.append("固定流程这一档必须给 steps（要做的几步）")
            else:
                issues.extend(_check_steps(self.steps))

        if self.level == LEVEL_CODE and not (self.code or "").strip():
            issues.append("自定义代码这一档必须给 code")

        # 参数声明要合法
        if self.parameters:
            if self.parameters.get("type") != "object":
                issues.append("parameters 必须是 JSON Schema 对象（顶层 type 为 object）")
            props = self.parameters.get("properties")
            if props is not None and not isinstance(props, dict):
                issues.append("parameters.properties 必须是对象")
            required = self.parameters.get("required")
            if required is not None:
                if not isinstance(required, list):
                    issues.append("parameters.required 必须是数组")
                elif props and any(key not in props for key in required):
                    missing = [k for k in required if k not in props]
                    issues.append(f"required 里的 {missing} 没在 properties 里声明")

        return issues

    def summary(self) -> str:
        """给用户看的人话说明。**审批卡片上显示的就是这个。**"""
        lines = [f"{self.title}（{self.name}）",
                 f"类型：{LEVEL_LABELS[self.level]} —— {LEVEL_HINTS[self.level]}"]
        if self.description:
            lines.append(f"什么时候用：{self.description}")

        params = ((self.parameters or {}).get("properties") or {})
        if params:
            items = []
            for key, spec in params.items():
                desc = (spec or {}).get("description") or ""
                items.append(f"{key}（{desc}）" if desc else key)
            lines.append("要问用户拿：" + "、".join(items))
        else:
            lines.append("不需要额外信息")

        if self.level == LEVEL_RECIPE:
            lines.append("会做这几步：")
            for index, step in enumerate(self.steps, 1):
                op = step.get("op", "?")
                lines.append(f"  {index}. {READ_OPS.get(op, op)}"
                             f"  {_step_detail(step)}")
            lines.append("**只读。不会改、不会删任何文件。**")
        elif self.level == LEVEL_NOTE:
            lines.append(f"固定指令：{self.prompt[:200]}")
        elif self.level == LEVEL_CODE:
            lines.append("会运行这段代码（每次运行都要你确认）：")
            lines.append("  " + (self.code or "").replace("\n", "\n  ")[:400])
        return "\n".join(lines)


def _step_detail(step: dict) -> str:
    """把一步的参数压成一小段，让用户看得出它在干嘛。"""
    op = step.get("op")
    if op == "read_file":
        return f"（{step.get('path', '?')}）"
    if op == "list_dir":
        return f"（{step.get('path', '?')}）"
    if op == "regex_find":
        return f"（规则 {step.get('pattern', '?')}）"
    if op == "pick_lines":
        return f"（含 {step.get('keyword', '?')} 的行）"
    if op == "truncate":
        return f"（留 {step.get('limit', '?')} 字）"
    if op == "template":
        text = str(step.get("text", ""))
        return f"（{text[:40]}…）" if len(text) > 40 else f"（{text}）"
    return ""


def _check_steps(steps: list) -> list[str]:
    """逐步检查：op 认不认识、必填参数在不在。

    **这里是越权的硬拦截点。** 出现写文件、执行命令这类 op 直接拒 ——
    和 advisor 的止损一个思路：光靠提示词劝不住，得在代码里挡。
    """
    issues: list[str] = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            issues.append(f"第 {index} 步不是对象")
            continue
        op = step.get("op")
        if not op:
            issues.append(f"第 {index} 步缺 op")
            continue
        if op not in READ_OPS:
            hint = ""
            if op in ("write_file", "delete_file", "move", "run_command",
                      "click", "type_text", "open_app"):
                hint = ("—— 这一档**只允许只读操作**。要写文件得用"
                        "「自定义代码」档，而且每次运行都要用户确认。")
            issues.append(f"第 {index} 步的 op「{op}」不在允许清单里{hint}")
            continue
        # 各 op 的必填参数
        need = {
            "read_file": ("path",),
            "list_dir": ("path",),
            "regex_find": ("pattern",),
            "pick_lines": ("keyword",),
            "template": ("text",),
        }.get(op, ())
        for key in need:
            if not str(step.get(key) or "").strip():
                issues.append(f"第 {index} 步（{op}）缺 {key}")
    return issues


# ==========================================================================
#  执行
# ==========================================================================
def _as_text(value) -> str:
    """把上一步的输出统一成文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def _resolve_vars(text: str, variables: dict) -> str:
    """把 {名字} 换成参数值。

    只做最简单的替换，不支持表达式 —— 这里不是模板引擎，
    用户（和模型）能预期的就是「把 {x} 换成 x 的值」。
    """
    def swap(match: re.Match) -> str:
        key = match.group(1)
        return str(variables.get(key, match.group(0)))

    return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", swap, text)


def run_recipe(ext: Extension, arguments: dict, allowed_roots=None
               ) -> tuple[bool, str]:
    """执行 recipe 档。返回 (成功, 结果文本)。

    每一步只能做 READ_OPS 里的事，且**每一步的产物都往上下文里塞** ——
    后面的步骤可以引用 {上一步的输出}，也可以用参数。
    """
    from . import files

    context: dict = dict(arguments or {})
    if allowed_roots is None:
        allowed_roots = [Path.home()]

    for index, step in enumerate(ext.steps, 1):
        op = step.get("op")
        if op not in READ_OPS:
            # 双保险：install 时已经拦过一次，这里再拦一次 ——
            # 万一有别的路径（手改配置）绕过校验写进来了。
            return False, (f"第 {index} 步的 op「{op}」不允许执行。"
                           "这一档只能做只读操作。")
        try:
            if op == "read_file":
                path = _resolve_vars(str(step.get("path") or ""), context)
                result = files.read_text(path, max_lines=int(step.get("max_lines") or 500))
                if not result.ok:
                    return False, f"第 {index} 步读不了 {path}：{result.message}"
                context["text"] = result.text
                context["content"] = result.text

            elif op == "list_dir":
                path = _resolve_vars(str(step.get("path") or ""), context)
                expanded = files.expand(path)
                if not expanded.is_dir():
                    return False, f"第 {index} 步：{path} 不是目录"
                names = sorted(item.name for item in expanded.iterdir())
                context["text"] = "\n".join(names)
                context["names"] = names

            elif op == "regex_find":
                pattern = _resolve_vars(str(step.get("pattern") or ""), context)
                source = _as_text(context.get("text") or context.get("content") or "")
                flags = re.M if step.get("multiline", True) else 0
                found = re.findall(pattern, source, flags)
                # findall 带分组时返回元组，展平成字符串方便用
                flat = []
                for item in found:
                    flat.append(" ".join(item) if isinstance(item, tuple) else str(item))
                context["text"] = "\n".join(flat)
                context["matches"] = flat

            elif op == "pick_lines":
                keyword = _resolve_vars(str(step.get("keyword") or ""), context)
                source = _as_text(context.get("text") or context.get("content") or "")
                kept = [line for line in source.splitlines() if keyword in line]
                context["text"] = "\n".join(kept)
                context["lines"] = kept

            elif op == "template":
                text = _resolve_vars(str(step.get("text") or ""), context)
                context["text"] = text
                context["result"] = text

            elif op == "join":
                parts = [_as_text(context.get(key)) for key in (step.get("fields") or [])]
                sep = str(step.get("separator") or "\n")
                context["text"] = sep.join(p for p in parts if p)

            elif op == "truncate":
                limit = int(step.get("limit") or 2000)
                text = _as_text(context.get("text") or "")
                context["text"] = text[:limit]

            elif op == "count":
                text = _as_text(context.get("text") or "")
                unit = str(step.get("unit") or "lines")
                n = len(text.splitlines()) if unit == "lines" else len(text)
                context["count"] = n
                context["text"] = f"{n}"

        except files.FileDenied as exc:
            return False, f"第 {index} 步（{op}）被安全策略拦下：{exc}"
        except (OSError, re.error, ValueError) as exc:
            return False, f"第 {index} 步（{op}）出错：{exc}"

    output = _as_text(context.get("result") or context.get("text") or "")
    if not output.strip():
        return False, "流程跑完了，但没有产出任何内容。检查一下步骤顺序。"
    return True, output


# ==========================================================================
#  注册表
# ==========================================================================
def load_all(path: Path) -> list[Extension]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("extensions")
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        ext = Extension.from_dict(item)
        if ext.name:
            out.append(ext)
    return out


def save_all(path: Path, items: list[Extension]) -> tuple[bool, str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"extensions": [item.as_dict() for item in items]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        return True, "已保存"
    except OSError as exc:
        return False, f"写不进 {path.name}：{exc}"


def as_openai_tools(items: list[Extension]) -> list[dict]:
    """把装好的扩展转成给模型的工具定义。

    只导出 approved 的 —— 没经过用户点头的定义不该出现在模型的可选清单里。
    """
    out = []
    for ext in items:
        if not ext.approved:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": f"ext_{ext.name}"[:64],
                "description": (ext.description or ext.title)[:1024],
                "parameters": (ext.parameters
                               or {"type": "object", "properties": {}}),
            },
        })
    return out


def find_by_tool_name(items: list[Extension], tool_name: str) -> Extension | None:
    """ext_xxx → Extension。"""
    if not tool_name.startswith("ext_"):
        return None
    want = tool_name[4:]
    for ext in items:
        if ext.name == want:
            return ext
    return None
