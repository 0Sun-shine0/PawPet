"""数据导出 / 导入：一个 zip 装走全部用户数据。

为什么需要它：待办、便签、专注记录、自定义工具、对话历史全在本机，
换电脑或重装系统就全没了。原来用户唯一的自救手段是设置页里的
「打开数据文件夹」然后自己拷 —— 对泛用户等于没有。

三条设计原则：

1. **`.env` 绝不打包。** 它存着 API Key。config.py 里已经写明「Key 只
   写进 .env，不写进 pet_data.json，因为后者是会被随手备份的」——
   而导出功能正是那种「随手备份」。同一条线这里必须守住，否则一次
   无心的分享就把 key 送出去了。

2. **导入前必须备份。** 覆盖是不可逆的：用户选错文件那一刻，现有的
   待办和便签就没了。所以导入流程是「先备份成 pet_data.before-import
   .json，再覆盖」，出了问题还能人工捞回来。

3. **版本对不上要拒绝，不能硬塞。** 比当前新的备份拒绝了用户还能去
   升级；硬塞进去之后 store 迁移不认识那些字段，可能静默丢数据 ——
   后者更糟。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from pathlib import Path

# 包格式版本。**和 store.SCHEMA_VERSION 是两件事**：
# 前者是"这个 zip 的结构"，后者是"pet_data.json 的结构"。
# 分开的好处是以后往包里加文件（比如知识库单独成文件）不用动数据 schema。
FORMAT_VERSION = 1

MANIFEST_NAME = "pawpet-manifest.json"

# 打进包里的文件。
#
# 都是「数据目录下、用户自己攒出来的东西」。刻意不含：
#   .env          —— API Key，见文件头
#   pawpet.log    —— 日志，没人要
#   .cache/       —— 缩略图、AI 中间产物，可重建
#   *.backup.json —— 程序自己的上一版备份，跟着包走没意义
BUNDLE_FILES = (
    "pet_data.json",
    "extensions.json",
    "theme.json",
    "conversations.json",
    "conversations.jsonl",
)

# 导入前，旧数据备份成这个名字。放数据目录里，用户找得到。
PRE_IMPORT_BACKUP = "pet_data.before-import.json"


class BundleError(Exception):
    """包有问题，且是**用户能看懂**的问题（不是内部异常）。"""


def _counts(state: dict) -> dict:
    """从 pet_data 里数几个能给用户看的数字。

    导入前让用户确认「这里面有 37 件待办」，比只显示文件名有意义得多 ——
    他才知道自己选对了没有。
    """
    def _len(key: str) -> int:
        value = state.get(key)
        return len(value) if isinstance(value, list) else 0

    knowledge = state.get("knowledge")
    docs = knowledge.get("docs") if isinstance(knowledge, dict) else None

    return {
        "tasks": _len("tasks"),
        "notes": _len("notes"),
        "reminders": _len("reminders"),
        "sessions": _len("sessions"),
        "knowledge": len(docs) if isinstance(docs, list) else 0,
    }


def export_bundle(root: Path, target: Path, app_version: str = "",
                  schema_version: int = 0) -> tuple[bool, str]:
    """把数据目录里该带走的文件打成一个 zip。

    返回 `(是否成功, 给用户看的话)`。

    用 zip 而不是「拷一个文件夹」：单个文件好传、好存、好删，
    而且用户不会因为少拷了哪个文件而丢数据 —— 该带的都在里面。
    """
    root = Path(root)
    target = Path(target)

    if not root.is_dir():
        return False, f"找不到数据目录：{root}"

    present = [name for name in BUNDLE_FILES if (root / name).is_file()]
    if not present:
        return False, "数据目录里没有可导出的文件。"

    # 主数据文件的条目数，写进 manifest 供导入前预览
    counts = {}
    try:
        with open(root / "pet_data.json", "r", encoding="utf-8-sig") as handle:
            counts = _counts(json.load(handle))
    except (OSError, json.JSONDecodeError):
        # 数不出来不影响导出 —— counts 只是给人看的参考值
        counts = {}

    manifest = {
        "format": FORMAT_VERSION,
        "app": "小爪助手",
        "appVersion": app_version,
        "schema": schema_version,
        "exportedAt": time.time(),
        "files": present,
        "counts": counts,
    }

    try:
        # ZIP_DEFLATED：pet_data.json 里 knowledge 的正文是重复度很高的
        # 中文文本，压缩率通常在 5~10 倍，值得压。
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            for name in present:
                bundle.write(root / name, name)
    except OSError as exc:
        return False, f"写文件失败：{exc}"
    except zipfile.BadZipFile as exc:  # 目标路径已被占用成坏 zip
        return False, f"目标文件不可用：{exc}"

    size = target.stat().st_size if target.exists() else 0
    detail = "、".join(f"{counts.get(k, 0)} 项{label}"
                      for k, label in (("tasks", "待办"), ("notes", "便签"),
                                       ("knowledge", "资料"))
                      if counts.get(k))
    head = f"已导出 {len(present)} 个文件（{_human_size(size)}）"
    return True, f"{head}。" + (f"里面有 {detail}。" if detail else "")


def inspect_bundle(source: Path, schema_version: int = 0) -> dict:
    """只读地看一眼这个包里有什么，**不落地任何文件**。

    给「导入前确认」用：先告诉用户包里有什么、来自哪个版本，他点了确定
    才真覆盖。顺带把三类硬错误挡在这里 —— 不是 zip、没有 manifest、
    版本比当前新。
    """
    source = Path(source)

    if not source.is_file():
        raise BundleError(f"找不到文件：{source.name}")

    if not zipfile.is_zipfile(source):
        raise BundleError("这不是小爪的备份文件（它应该是一个 .zip）。")

    try:
        with zipfile.ZipFile(source) as bundle:
            names = bundle.namelist()
            if MANIFEST_NAME not in names:
                raise BundleError(
                    "这个 zip 里没有小爪的说明文件，可能不是小爪导出的备份。"
                )
            try:
                raw = bundle.read(MANIFEST_NAME).decode("utf-8")
                manifest = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleError(f"备份说明文件读不出来：{exc}") from exc

            if not isinstance(manifest, dict):
                raise BundleError("备份说明文件格式不对。")

            fmt = manifest.get("format")
            if not isinstance(fmt, int) or fmt > FORMAT_VERSION:
                raise BundleError(
                    "这个备份来自更新版本的小爪，先升级再导入 —— "
                    "直接导会有字段对不上、数据被丢掉的风险。"
                )

            inner = [name for name in BUNDLE_FILES if name in names]

    except zipfile.BadZipFile as exc:
        raise BundleError(f"zip 已损坏：{exc}") from exc

    if not inner:
        raise BundleError("这个备份里没有小爪的数据文件。")

    # bundle 里的 schema 比当前新 → 拒绝。
    # 和上面 format 的判断同理：不认识的结构宁可让用户先升级。
    bundled_schema = manifest.get("schema")
    if isinstance(bundled_schema, int) and schema_version:
        if bundled_schema > schema_version:
            raise BundleError(
                "这个备份来自更新版本的小爪（数据格式 "
                f"v{bundled_schema}，当前 v{schema_version}）。"
                "先升级小爪再导入。"
            )

    counts = manifest.get("counts")
    return {
        "files": inner,
        "counts": counts if isinstance(counts, dict) else {},
        "appVersion": str(manifest.get("appVersion") or ""),
        "schema": int(bundled_schema) if isinstance(bundled_schema, int) else 0,
        "exportedAt": manifest.get("exportedAt"),
    }


def import_bundle(root: Path, source: Path, info: dict | None = None) -> tuple[bool, str]:
    """把包里的文件覆盖到数据目录。

    `info` 是 inspect_bundle 的结果（可选）。传进来是为了少解一次包；
    不传就自己看一眼。
    """
    root = Path(root)
    source = Path(source)

    if info is None:
        info = inspect_bundle(source)

    names = list(info.get("files") or [])
    if not names:
        return False, "这个备份里没有可导入的文件。"

    # ---- 先验内容，再动任何文件。
    #
    # 这一步不能省：inspect_bundle 只看了 manifest，没看**数据本身**。
    # 一个 manifest 完好、但 pet_data.json 被截断的包（网盘传输中断、
    # 手工改过、磁盘坏块）会直接覆盖掉用户的数据，然后 store 读不动它、
    # 回退到 default_state —— 用户看到的是空空如也。而那份救命备份
    # 叫 pet_data.before-import.json，不是 store 自动会读的备份名，
    # 程序不会自己恢复。所以宁可在这里拒绝，也不能写下去。
    try:
        with zipfile.ZipFile(source) as bundle:
            for name in names:
                raw = bundle.read(name)
                # 只验 .json：以后要是往包里加二进制附件（比如缩略图），
                # 那些没法用 JSON 验。
                if not name.endswith(".json"):
                    continue
                parsed = json.loads(raw.decode("utf-8-sig"))
                # 包里这四个文件**顶层都是对象**（pet_data 直接用字段，
                # 其余三个各有一个 "extensions"/"colors"/"sessions" 键）。
                # 顶层不是对象 = 这个文件废了：读它的那几个函数都只认对象，
                # 拿到数组会静默返回空 —— 导进去等于把这一类数据清空。
                if not isinstance(parsed, dict):
                    raise ValueError(f"{name} 的顶层不是对象")
    except (zipfile.BadZipFile, KeyError, OSError, ValueError,
            UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, (f"这个备份里的数据读不出来（{exc}），"
                       "没有覆盖任何东西。建议重新导出一份再试。")

    # ---- 先备份现有数据。覆盖是不可逆的，这一步不能省。
    backed_up = ""
    current = root / "pet_data.json"
    if current.is_file():
        try:
            backup = root / PRE_IMPORT_BACKUP
            # 用 shutil 而不是自己读一遍再写：pet_data.json 可能已经
            # 接近 1MB，直接拷更省事也更稳。
            shutil.copy2(current, backup)
            backed_up = backup.name
        except OSError as exc:
            # 备份失败就别覆盖了 —— 用户可能正是在清理磁盘空间，
            # 继续下去等于在没有安全网的情况下删数据。
            return False, f"导入前的备份没做成（{exc}），为安全起见没有覆盖。请先清理磁盘空间再试。"

    # ---- 解包
    written = []
    try:
        with zipfile.ZipFile(source) as bundle:
            for name in names:
                data = bundle.read(name)
                # 写入沿用 store 的原子写思路：临时文件 + replace。
                # 中途失败不会留下半截文件 —— 那比不导入更糟，
                # 因为主程序下次启动会把它当"损坏"。
                tmp = root / (name + ".importing")
                with open(tmp, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, root / name)
                written.append(name)
        if "conversations.json" in names and "conversations.jsonl" not in names:
            (root / "conversations.jsonl").unlink(missing_ok=True)
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        tail = f"已经写入：{'、'.join(written)}。" if written else ""
        hint = f"原数据备份在 {backed_up}。" if backed_up else ""
        return False, f"导入中途失败：{exc}。{tail}{hint}"

    tail = f"原数据已备份为 {backed_up}。" if backed_up else ""
    return True, f"已导入 {len(written)} 个文件（{'、'.join(written)}）。{tail}"


def _human_size(num: int) -> str:
    if num >= 1024 * 1024:
        return f"{num / 1024 / 1024:.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.0f} KB"
    return f"{num} 字节"


def default_export_name(prefix: str = "小爪备份") -> str:
    """默认文件名：小爪备份-2026-09-18.zip

    带日期是因为用户攒几个备份之后，「小爪备份.zip」「小爪备份(1).zip」
    这种名字根本分不出哪个是新的。
    """
    return f"{prefix}-{time.strftime('%Y-%m-%d')}.zip"
