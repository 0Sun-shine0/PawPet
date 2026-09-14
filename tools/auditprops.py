"""找出所有「PySide6 属性名」和「QML 里访问名」对不上的地方。

PySide6 的 @Property 默认用 Python 函数名当属性名。
函数名是 snake_case 的话，QML 里就得写 snake_case；
写了驼峰就会静默拿到 undefined —— 这类 bug 不会报错，只会显示空白。
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:\pet")
PY_DIRS = [ROOT / "pawpet", ROOT / "tools"]
QML_DIR = ROOT / "pawpet" / "qml"


def python_properties() -> dict[str, set[str]]:
    """收集每个类里 @Property 定义的属性名（= 函数名）。"""
    found: dict[str, set[str]] = defaultdict(set)
    pattern = re.compile(
        r"^\s*@Property\([^)]*\)\s*\n\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        re.M,
    )
    for directory in PY_DIRS:
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            # 粗略地按 class 切分，够用了
            for match in pattern.finditer(text):
                name = match.group(1)
                head = text[: match.start()]
                classes = re.findall(r"^class\s+(\w+)", head, re.M)
                owner = classes[-1] if classes else "_module_"
                found[owner].add(name)
    return found


def qml_accessors() -> list[tuple[Path, int, str]]:
    """收集 QML 里对 backend.* / backend.ai.* 之类的属性访问。"""
    pattern = re.compile(
        r"\b(?:backend|backend\.ai|backend\.tasks|backend\.reminders"
        r"|backend\.notes|backend\.focus|backend\.sessions|backend\.week)"
        r"\.([A-Za-z_][A-Za-z0-9_]*)"
    )
    hits: list[tuple[Path, int, str]] = []
    for path in QML_DIR.rglob("*.qml"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                hits.append((path, number, match.group(1)))
    return hits


def main() -> int:
    props = python_properties()
    all_names: set[str] = set()
    for names in props.values():
        all_names |= names

    print("=== Python 侧用 snake_case 定义的 @Property ===")
    snake = sorted(name for name in all_names if "_" in name)
    if snake:
        for name in snake:
            owners = [owner for owner, names in props.items() if name in names]
            print(f"  {name:28s} 来自 {', '.join(owners)}")
    else:
        print("  （无）")

    print()
    print("=== QML 里访问了驼峰形式、但 Python 只有 snake_case 的 ===")

    def camel_to_snake(name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    problems = 0
    seen: set[str] = set()
    for path, number, accessed in qml_accessors():
        if accessed in all_names:
            continue
        snake_form = camel_to_snake(accessed)
        if snake_form in all_names and snake_form not in seen:
            seen.add(snake_form)
            problems += 1
            print(f"  [XX] QML 用 .{accessed}  ->  Python 里叫 .{snake_form}")
            print(f"       {path.relative_to(ROOT)}:{number}")

    print()
    if problems:
        print(f"发现 {problems} 处不匹配，需要在 Python 里改成驼峰函数名。")
        return 1
    print("没有发现不匹配的属性名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
