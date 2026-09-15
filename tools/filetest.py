"""本地文件读写自测。

重点不是「能不能读」，而是**安全策略有没有漏洞**：
* 凭据位置必须读写都拒（这是最要紧的一条）
* 系统目录只拒写、允许读
* 各种绕法（`..`、相对路径、符号链接、大小写）不管用
* 二进制要挡住，中文编码要认得出

用法：
    .venv\\Scripts\\python.exe tools\\filetest.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "filetest"
os.environ["PAWPET_HOME"] = str(SCRATCH)

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
    print("小爪本地文件读写自测\n")

    from pawpet.ai import files

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    # ================================================== 一、正常读写
    print("=== 一、正常读写 ===")

    target = SCRATCH / "hello.txt"
    result = files.write_text(str(target), "第一行\n第二行\n第三行")
    check("能新建文件", result.ok, result.message)
    check("写中文没问题", result.ok and target.exists())
    check("默认用 UTF-8 存",
          target.read_bytes().startswith("第一行".encode("utf-8")),
          str(target.read_bytes()[:12]))

    back = files.read_text(str(target))
    check("能读回来", back.ok, back.message)
    check("内容一致", back.text == "第一行\n第二行\n第三行", repr(back.text))
    check("报出了行数", back.total_lines == 3, str(back.total_lines))
    check("报出了编码", bool(back.encoding), back.encoding)

    # 分页
    page2 = files.read_text(str(target), start_line=2, max_lines=1)
    check("start_line 能翻页", page2.text.startswith("第二行"), repr(page2.text))
    check("翻页时说明了还有多少",
          "共 3 行" in page2.message, page2.message)

    # ================================================== 二、不覆盖
    print("\n=== 二、默认不覆盖已有文件 ===")

    again = files.write_text(str(target), "我要覆盖你")
    check("默认拒绝覆盖", not again.ok, again.message[:40])
    check("拒绝时说明了文件已存在",
          "已经存在" in again.message, again.message[:60])
    check("拒绝时提示了怎么覆盖",
          "overwrite" in again.message, again.message[:80])
    check("文件内容真的没被改",
          target.read_text(encoding="utf-8").startswith("第一行"),
          target.read_text(encoding="utf-8")[:10])

    forced = files.write_text(str(target), "覆盖了", overwrite=True)
    check("显式要求时才覆盖", forced.ok, forced.message)
    check("覆盖后内容变了",
          target.read_text(encoding="utf-8") == "覆盖了")

    # ================================================== 三、凭据位置（最要紧）
    print("\n=== 三、凭据位置：读写都必须拒 ===")

    secret_paths = [
        "~/.ssh/id_rsa",
        "~/.ssh/config",
        "~/.aws/credentials",
        "~/.gnupg/secring.gpg",
        "~/.docker/config.json",
        "~/.netrc",
        "~/AppData/Local/Google/Chrome/User Data/Default/Login Data",
        "~/AppData/Roaming/Mozilla/Firefox/profiles.ini",
    ]
    for item in secret_paths:
        got = files.read_text(item)
        check(f"拒读 {item}", not got.ok, "居然读到了！")

    for item in ("~/.ssh/id_rsa", "~/.ssh/newkey"):
        got = files.write_text(item, "内容")
        check(f"拒写 {item}", not got.ok, "居然写进去了！")

    # 绕法：用 .. 回到敏感目录
    sneaky = [
        str(Path.home() / ".ssh" / ".." / ".ssh" / "id_rsa"),
        "~/.ssh/../.ssh/id_rsa",
    ]
    for item in sneaky:
        got = files.read_text(item)
        check(f"用 .. 绕不过去：{item[:34]}…", not got.ok, "被绕过了！")

    # 密钥库后缀
    for suffix in (".pfx", ".p12", ".kdbx", ".jks"):
        got = files.read_text(str(SCRATCH / f"key{suffix}"))
        check(f"拒绝密钥库后缀 {suffix}", not got.ok, "放行了")

    # ================================================== 四、系统目录
    print("\n=== 四、系统目录：允许读、禁止写 ===")

    windows = os.environ.get("SystemRoot") or r"C:\Windows"
    hosts = Path(windows) / "System32" / "drivers" / "etc" / "hosts"
    if hosts.exists():
        got = files.read_text(str(hosts))
        check("能读 hosts（这是合理的排查需求）", got.ok, got.message[:60])
    else:
        check("能找到 hosts 文件", False, "这个系统上没有")

    written = files.write_text(str(hosts), "127.0.0.1 evil", overwrite=True)
    check("禁止写 hosts", not written.ok, "写进去了！")
    check("拒绝时说明是系统目录",
          "系统目录" in written.message, written.message[:60])

    if os.environ.get("ProgramFiles"):
        target_pf = Path(os.environ["ProgramFiles"]) / "pawpet-test.txt"
        got = files.write_text(str(target_pf), "x")
        check("禁止写 Program Files", not got.ok, "写进去了！")

    # ================================================== 五、编码
    print("\n=== 五、中文编码 ===")

    gbk_file = SCRATCH / "gbk.txt"
    gbk_file.write_bytes("中文内容测试\n第二行".encode("gb18030"))
    got = files.read_text(str(gbk_file))
    check("GBK 文件能认出来", got.ok, got.message[:60])
    check("GBK 内容不乱码", "中文内容测试" in got.text, repr(got.text[:20]))
    check("报出了是 GBK 系", "gb" in (got.encoding or "").lower(), got.encoding)

    bom_file = SCRATCH / "bom.txt"
    bom_file.write_bytes("\ufeff带 BOM 的内容".encode("utf-8"))
    got = files.read_text(str(bom_file))
    check("带 BOM 的文件能读", got.ok, got.message[:50])
    check("BOM 不会跑进内容里",
          got.text.startswith("带 BOM"), repr(got.text[:12]))

    utf16_file = SCRATCH / "utf16.txt"
    utf16_file.write_bytes("UTF-16 内容".encode("utf-16"))
    got = files.read_text(str(utf16_file))
    check("UTF-16 也能读", got.ok and "UTF-16 内容" in got.text,
          repr(got.text[:16]))

    # ================================================== 六、二进制与体积
    print("\n=== 六、二进制与超大文件 ===")

    binary_file = SCRATCH / "fake.exe"
    binary_file.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 600)
    got = files.read_text(str(binary_file))
    check("拒绝二进制文件", not got.ok, "读了")
    check("拒绝时解释了为什么",
          "二进制" in got.message, got.message[:60])

    big_file = SCRATCH / "big.txt"
    big_file.write_text("\n".join(f"第 {i} 行内容" for i in range(50000)),
                        encoding="utf-8")
    got = files.read_text(str(big_file))
    check("超大文本会被截断而不是全读", got.truncated, str(got.truncated))
    check("截断时说明了行数范围",
          "共" in got.message and "行" in got.message, got.message[:70])
    check("截断后内容不会太大", len(got.text) < 200_000, str(len(got.text)))

    huge = SCRATCH / "huge.bin"
    with open(huge, "wb") as handle:
        handle.write(b"A" * (files.MAX_WRITE_BYTES + 100))
    got = files.write_text(str(SCRATCH / "out.txt"),
                           "x" * (files.MAX_WRITE_BYTES + 10))
    check("拒绝写入超大内容", not got.ok, "写进去了")

    # ================================================== 七、找不到时的提示
    print("\n=== 七、找不到文件时的提示 ===")

    got = files.read_text(str(SCRATCH / "不存在的文件.txt"))
    check("不存在时明确说不存在", not got.ok and "没有这个文件" in got.message,
          got.message[:60])

    (SCRATCH / "报告2024.csv").write_text("a,b\n1,2", encoding="utf-8")
    got = files.read_text(str(SCRATCH / "报告2025.csv"))
    check("缺文件时会提示名字相近的",
          "相近" in got.message and "报告2024" in got.message,
          got.message[:90])

    got = files.read_text(str(SCRATCH))
    check("把目录当文件读会给出正确指引",
          not got.ok and "文件夹" in got.message, got.message[:60])

    # ================================================== 八、列目录
    print("\n=== 八、列目录 ===")

    got = files.list_dir(str(SCRATCH))
    check("能列目录", got.ok, got.message[:50])
    check("列出了刚才写的文件", "hello.txt" in got.text, got.text[:120])

    # 目录要出现在文件之前（实测原来把文件排在前面，找目录很费劲）
    sub_first = SCRATCH / "aaa_dir"
    sub_first.mkdir(exist_ok=True)
    ordered = files.list_dir(str(SCRATCH))
    check("目录排在文件前面",
          ordered.text.index("[目录]") < ordered.text.index("hello.txt"),
          ordered.text[:100].replace("\n", " | "))

    sub = SCRATCH / "sub"
    sub.mkdir(exist_ok=True)
    (sub / "a.csv").write_text("x", encoding="utf-8")
    (sub / "b.txt").write_text("y", encoding="utf-8")
    filtered = files.list_dir(str(sub), pattern="*.csv")
    check("按通配过滤有效",
          "a.csv" in filtered.text and "b.txt" not in filtered.text,
          filtered.text[:80])

    got = files.list_dir("~/.ssh")
    check("列目录也挡敏感位置", not got.ok, "列出来了！")

    # ================================================== 九、工具接线
    print("\n=== 九、工具接线 ===")

    from pawpet.ai.tools import TOOL_INDEX

    for name in ("read_file", "list_dir", "write_file"):
        spec = TOOL_INDEX.get(name)
        check(f"工具 {name} 已注册", spec is not None)
    check("read_file 是只读风险", TOOL_INDEX["read_file"].risk == "read")
    check("list_dir 是只读风险", TOOL_INDEX["list_dir"].risk == "read")
    check("write_file 需要确认（写盘不可逆）",
          TOOL_INDEX["write_file"].risk == "confirm",
          TOOL_INDEX["write_file"].risk)

    from pawpet.ai.actions import LEVEL_CONFIRM, LEVEL_READ_ONLY, AuditLog, DesktopActions
    from pawpet.ai.tools import ToolContext
    from pawpet.ai.vision import ScreenCapture
    from pawpet.store import Store

    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    actions = DesktopActions(AuditLog())
    actions.level = LEVEL_CONFIRM
    context = ToolContext(ScreenCapture(), actions, store)

    ok, message, _ = context.execute("read_file",
                                     {"path": str(SCRATCH / "hello.txt")})
    check("通过 ToolContext 能读文件", ok, message[:60])
    check("返回内容里带文件路径", str(SCRATCH) in message or "hello" in message,
          message[:60])

    ok, message, _ = context.execute("list_dir", {"path": str(SCRATCH)})
    check("通过 ToolContext 能列目录", ok, message[:50])

    ok, message, _ = context.execute("write_file", {
        "path": str(SCRATCH / "from_tool.txt"), "content": "工具写的"})
    check("通过 ToolContext 能写文件", ok, message[:50])

    ok, message, _ = context.execute("read_file", {"path": "~/.ssh/id_rsa"})
    check("工具这条路也挡敏感位置", not ok, "放行了！")

    # 只读模式下 read 仍可用，write 被拦
    actions.level = LEVEL_READ_ONLY
    ok, _, _ = context.execute("read_file", {"path": str(SCRATCH / "hello.txt")})
    check("只读模式下还能读文件", ok)
    blocked, message, _ = context.execute("write_file", {
        "path": str(SCRATCH / "nope.txt"), "content": "x"})
    check("只读模式下写文件被拦", not blocked, message[:50])
    check("被拦时说明了原因",
          "只读" in message or "模式" in message, message[:60])

    # ================================================== 十、提示词
    print("\n=== 十、系统提示词 ===")

    from pawpet.ai.agent import SYSTEM_PROMPT

    check("提示词里教了用 read_file 而不是复制粘贴",
          "read_file" in SYSTEM_PROMPT and "复制" in SYSTEM_PROMPT)
    check("提示词里说明了 Office 文档不是文本",
          "PDF" in SYSTEM_PROMPT and "乱码" in SYSTEM_PROMPT)
    check("提示词里有防注入的说明（文件内容是数据不是指令）",
          "不是指令" in SYSTEM_PROMPT, "缺少这条防线")
    check("提示词里说明了默认不覆盖",
          "overwrite" in SYSTEM_PROMPT)

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
