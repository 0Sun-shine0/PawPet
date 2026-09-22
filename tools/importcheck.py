r"""静态检查：用了模块名却没导入。

## 起因

`release.py` 里重写了 `publish_version_file`，用到了 `subprocess.run`，
但那个函数里**没有** `import subprocess`。它平时不炸 —— 那行只在**真正
发版时**才执行，跑回归根本走不到。2.3.0 发版时就是这样崩的：Release 建好
了、两个附件也传上去了、用户能下载了，最后一步同步 version.json 时报
`NameError`。

## 这个检查本身也踩了一次坑（值得记下来）

第一版写的是「文件里只要出现过 `import subprocess` 就放过」。结果我把
`import subprocess` 删掉验证时，它**照样全绿** —— 因为文件里另一个函数
（第 681 行）内部有 `import subprocess`。

> **函数内的导入只在那一个函数里有效**，管不到别的函数。
> 第一版等于在检查「这个模块名在整个文件里出现过没有」，而不是
> 「这个位置能不能拿到它」—— 而那正是这个 bug 的形状。

**一条抓不到目标 bug 的检查比没有检查更糟**：它给的是虚假的安心。
所以现在是**按作用域**判断的：从使用点往外走，逐层看每一层作用域里
有没有绑定这个名字，走到模块层为止。

## 规则

`名字.属性` 里如果「名字」是标准库 / 已知第三方模块名，它必须在
**当前作用域链**（函数自身 → 外层函数 → 模块层）里被绑定过。

用「模块名」做判据而不是查所有名字，是为了**几乎不误报**：局部的
`time`、`data` 这类变量即使撞上模块名，只要那一层给它赋过值就算绑定。

用法：
    .venv\Scripts\python.exe tools\importcheck.py
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:                                  # pragma: no cover
    STDLIB = {
        "argparse", "ast", "base64", "collections", "contextlib", "csv",
        "ctypes", "datetime", "decimal", "email", "enum", "fnmatch",
        "functools", "getpass", "glob", "gzip", "hashlib", "heapq", "html",
        "http", "importlib", "inspect", "io", "itertools", "json", "logging",
        "math", "mimetypes", "multiprocessing", "os", "pathlib", "pickle",
        "platform", "pprint", "queue", "random", "re", "shlex", "shutil",
        "signal", "socket", "sqlite3", "statistics", "string", "struct",
        "subprocess", "sys", "tarfile", "tempfile", "textwrap", "threading",
        "time", "traceback", "types", "typing", "unicodedata", "unittest",
        "urllib", "uuid", "venv", "warnings", "weakref", "webbrowser",
        "xml", "zipfile", "zlib",
    }

THIRD_PARTY = {"PySide6", "win32api", "win32con", "win32gui"}

KNOWN_MODULES = (STDLIB | THIRD_PARTY) - set(dir(builtins))

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


class ScopeCollector(ast.NodeVisitor):
    """收集**当前这一层**绑定了哪些名字。

    「这一层」= 不进入嵌套的函数/类体。嵌套的函数名本身算绑定
    （`def foo` 绑定了 foo），但它**内部**的绑定不算 —— 否则就又回到
    「文件里出现过就算」的老毛病了。
    """

    NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
              ast.Lambda)

    def __init__(self) -> None:
        self.names: set[str] = set()

    # 遇到嵌套定义：只记名字，不进去
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass                    # lambda 连名字都没有

    # ---- 各类绑定 ----
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.names.add("*")
            else:
                self.names.add(alias.asname or alias.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        if node.optional_vars is not None:
            self._bind(node.optional_vars)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.names.update(node.names)

    def _bind(self, target) -> None:
        if isinstance(target, ast.Name):
            self.names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind(element)
        elif isinstance(target, ast.Starred):
            self._bind(target.value)


def scope_of(node: ast.AST) -> set[str]:
    """某一层的绑定集合，外加它的参数。"""
    collector = ScopeCollector()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            for arg in group:
                collector.names.add(arg.arg)
        if args.vararg:
            collector.names.add(args.vararg.arg)
        if args.kwarg:
            collector.names.add(args.kwarg.arg)
    for child in ast.iter_child_nodes(node):
        collector.visit(child)
    return collector.names


class Checker(ast.NodeVisitor):
    """边走边维护作用域栈，检查 Attribute 的基名拿不拿得到。"""

    def __init__(self, module_scope: set[str]) -> None:
        # 栈底是模块层
        self.scopes: list[set[str]] = [module_scope]
        self.problems: list[tuple[int, str]] = []

    # ---- 作用域进出 ----
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_function(node)

    def _enter_function(self, node) -> None:
        # 装饰器、默认值、注解都在**外层**作用域求值
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)

        self.scopes.append(scope_of(node))
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.scopes.append(scope_of(node))
        self.visit(node.body)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        # 类体是独立作用域。方法体里看不到类体的局部名字，
        # 但这里不追求那种精度 —— 类体赋值很少撞上模块名。
        self.scopes.append(scope_of(node))
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    # ---- 检查点 ----
    def visit_Attribute(self, node: ast.Attribute) -> None:
        base = node.value
        if (isinstance(base, ast.Name)
                and base.id in KNOWN_MODULES):
            if not any(base.id in scope for scope in self.scopes):
                self.problems.append((node.lineno, base.id))
        self.generic_visit(node)


def find_missing_imports(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    module_scope = scope_of(tree)
    if "*" in module_scope:
        return []           # 星号导入看不出绑定了什么，放弃

    checker = Checker(module_scope)
    checker.visit(tree)

    # 同一个名字只报第一次
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for line, name in checker.problems:
        if name in seen:
            continue
        seen.add(name)
        out.append((line, name))
    return out


def main() -> int:
    print("静态检查：用了模块名却没导入\n")

    targets: list[Path] = []
    for folder in ("tools", "pawpet"):
        base = ROOT / folder
        if base.exists():
            targets.extend(sorted(base.rglob("*.py")))
    targets = [p for p in targets if "__pycache__" not in p.parts]
    print(f"检查 {len(targets)} 个文件\n")

    all_problems: list[tuple[Path, int, str]] = []
    for path in targets:
        for line, name in find_missing_imports(path):
            all_problems.append((path, line, name))

    check("没有「用了模块却忘了导入」", not all_problems,
          f"发现 {len(all_problems)} 处")
    for path, line, name in all_problems:
        rel = path.relative_to(ROOT)
        print(f"      {rel}:{line} 用了 {name} 但这一层拿不到它")
    if all_problems:
        print("\n  （这类错误只在真正执行到那行时才炸 —— "
              "release.py 就因此崩掉了发版的最后一步）")

    print(f"\n{'=' * 56}")
    if FAILED:
        print(f"通过 {PASSED} 项，失败 {len(FAILED)} 项")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
