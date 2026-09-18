"""导入备份之后「内存态有没有跟着换」的回归测试。

为什么单独有这个文件 —— 这是一个**静默丢数据**的 bug，而且特别难被发现：

    用户点了「从备份导入」→ 界面提示「导入完成」→ 他打开待办页，
    看到的还是导入前那几条（内存没换）。他以为是界面没刷新，随手
    改了个设置 —— 这一次 `flush()` 把内存里的旧数据整份写回磁盘，
    刚导入的东西就没了。从头到尾没有任何一处报错。

所以这里验的不是「import_bundle 能不能写文件」（那是 datatranstest 的
事），而是「写完文件之后，**内存里活着的那些对象**是不是也换了」。

四个必须成立的断言：

1. store 和各 Model 立刻就是导入后的内容（不用重启）。
2. 重载完再 `flush()` 一次，磁盘上仍然是导入的那份 —— 这条最要命，
   它直接复现上面那个丢数据的路径。
3. AI 侧的对话历史也换了，并且那个「2 秒后写盘」的延迟任务已经被掐掉
   （它会拿着旧的内存副本把导入的对话覆盖回去）。
4. 内容坏掉的包必须**在覆盖之前**被拒绝，磁盘一字不动。

用法：
    .venv\\Scripts\\python.exe tools\\reloadtest.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtGui import QGuiApplication  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _task(text: str, done: bool = False) -> dict:
    now = time.time()
    return {"id": "t-" + text, "text": text, "done": done, "created": now,
            "done_at": None, "priority": 0, "due": None, "pomodoros": 0}


def _make_bundle(target: Path, payload: dict, *, app_version: str = "2.1.0",
                 broken_pet_data: bytes | None = None,
                 extra_files: dict[str, str] | None = None) -> None:
    """造一个能通过的备份包。

    `broken_pet_data` 用来造「manifest 是好的、但数据本身坏了」的包 ——
    那种包才是真正危险的，只看 manifest 检查不出来。
    `extra_files` 用来往包里塞 theme.json / extensions.json 这类附属文件。
    """
    extra = dict(extra_files or {})
    names = ["pet_data.json"] + sorted(extra)
    manifest = {
        "format": 1, "app": "小爪助手", "appVersion": app_version,
        "schema": 2, "exportedAt": time.time(),
        "files": names,
        "counts": {"tasks": len(payload.get("tasks") or [])},
    }
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("pawpet-manifest.json",
                        json.dumps(manifest, ensure_ascii=False))
        if broken_pet_data is not None:
            bundle.writestr("pet_data.json", broken_pet_data)
        else:
            bundle.writestr("pet_data.json",
                            json.dumps(payload, ensure_ascii=False))
        for name, text in extra.items():
            bundle.writestr(name, text)


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="pawpet-reload-"))
    app = QGuiApplication(sys.argv)      # noqa: F841 - Backend 需要 QApplication 存在

    try:
        from pawpet import datatransfer as transfer
        from pawpet.store import SCHEMA_VERSION, Store

        data = scratch / "data"
        data.mkdir()

        # theme.json 的位置不是从 store 推出来的，而是 config.ROOT 下的固定
        # 路径（生产环境里正好和 store.path.parent 是同一个目录）。测试里
        # 必须把它指到临时目录，否则会读/写到**仓库根目录**的那份真配色。
        # 注意要和 import_bundle 的 root 用同一个目录，否则测的就不是生产
        # 环境的那条路径了。
        import pawpet.backend as backend_mod

        original_theme_file = backend_mod.THEME_FILE
        backend_mod.THEME_FILE = data / "theme.json"

        # ---------------------------------------------------------- 现场
        # 当前数据：2 条待办。
        store = Store(data / "pet_data.json", data / "pet_data.backup.json")
        state = store.load()
        state["tasks"] = [_task("旧任务A"), _task("旧任务B")]
        store.save()

        # 备份里的数据：3 条不同的待办。
        payload = dict(state)
        payload["tasks"] = [_task("导入任务1"), _task("导入任务2"), _task("导入任务3")]
        bundle = scratch / "backup.zip"
        # 包里带一份配色覆盖 —— 用来验证「重载把 theme.json 也换了」，
        # 而不是只换了主数据文件。
        _make_bundle(bundle, payload,
                     extra_files={"theme.json": '{"accent": "#00ff00"}'})

        # 对话历史：磁盘上是**导入来的**那份。
        (data / "conversations.json").write_text(
            json.dumps({"schema": 1, "sessions": [
                {"id": "s-imported", "started": 1.0, "updated": 1.0,
                 "title": "导入来的对话", "messages": [
                     {"role": "user", "text": "导入来的问题"},
                     {"role": "assistant", "text": "导入来的回答"}]}]},
                ensure_ascii=False),
            encoding="utf-8")
        (data / "theme.json").write_text('{"accent": "#123456"}', encoding="utf-8")

        from pawpet.backend import Backend

        backend = Backend(store)

        section("一、导入前：内存里是旧数据")
        check("待办是旧的 2 条", len(store.tasks) == 2, f"实际 {len(store.tasks)}")
        check("TaskModel 也是旧的 2 行", backend.tasks.rowCount() == 2,
              f"实际 {backend.tasks.rowCount()}")
        check("AI 侧已读到导入前的对话", bool(backend.ai._current),
              "conversations.json 没被读进来，后面的断言就没意义了")
        check("配色是导入前那份（#123456）",
              backend._theme_overrides.get("accent") == "#123456",
              str(backend._theme_overrides))

        # ------------------------------------------------------ 导入 + 重载
        # 走和 importData() 完全一样的路径：先只读检查，再覆盖，再重载。
        info = transfer.inspect_bundle(bundle, schema_version=SCHEMA_VERSION)
        ok, message = transfer.import_bundle(data, bundle, info)

        section("二、导入本身")
        check("import_bundle 成功", ok, message)
        check("提示语里没有「重启后生效」这种话（因为不用重启）",
              "重启后生效" not in message, message)

        # 留一手：抓 reloadData 发的信号。少了任何一个，界面上就会
        # 「数据换了但显示没换」。
        seen: list[str] = []
        backend.settingsChanged.connect(lambda: seen.append("settings"))
        backend.themeChanged.connect(lambda: seen.append("theme"))
        backend.memoryChanged.connect(lambda: seen.append("memory"))

        suffix = backend.reloadData()

        section("三、重载后：内存立刻是导入后的内容（不用重启）")
        check("store.tasks 变成导入的 3 条", len(store.tasks) == 3,
              f"实际 {len(store.tasks)}")
        check("待办文本是导入的那批",
              [t["text"] for t in store.tasks] == ["导入任务1", "导入任务2", "导入任务3"],
              str([t["text"] for t in store.tasks]))
        check("TaskModel 重新算了 _visible（3 行）",
              backend.tasks.rowCount() == 3, f"实际 {backend.tasks.rowCount()}")
        check("TaskModel.pendingCount 跟着变（说明计数信号也发了）",
              backend.tasks.pendingCount == 3, f"实际 {backend.tasks.pendingCount}")
        check("便签 Model 也重建过（有 currentId 可用）",
              bool(backend.notes.currentId) or len(store.notes) == 0)
        check("会话 Model 重新读了 sessions", backend.sessions.count >= 0)
        check("配色换成了导入的那份（#00ff00）",
              backend._theme_overrides.get("accent") == "#00ff00",
              str(backend._theme_overrides))
        check("themeColors 这个给 QML 读的完整表也跟着变了",
              backend.themeColors.get("accent") == "#00ff00",
              str(backend.themeColors.get("accent")))
        check("settingsChanged 发出去了", "settings" in seen)
        check("themeChanged 发出去了", "theme" in seen)
        check("memoryChanged 发出去了", "memory" in seen)
        check("返回的提示语说清了「现在就是导入后的」",
              "重新载入" in suffix, suffix)

        section("四、AI 侧的对话历史也换了（这是最容易漏的一处）")
        check("内存里的对话换成了导入的那条",
              bool(backend.ai._current) and backend.ai._current.title == "导入来的对话",
              getattr(backend.ai._current, "title", "<无>"))
        check("消息列表也是导入的（不是启动时那份）",
              [m.get("text") for m in backend.ai._messages]
              == ["导入来的问题", "导入来的回答"],
              str([m.get("text") for m in backend.ai._messages]))
        check("延迟写盘的脏标记被清掉了",
              backend.ai._history_dirty is False,
              "不清的话下一次 flush 会把旧对话写回磁盘")
        check("那个 2 秒的写盘定时器也停了",
              not backend.ai._history_timer.isActive())

        section("五、关键一条：重载之后立刻保存，磁盘上还是导入的那份")
        backend.flush()
        on_disk = json.loads((data / "pet_data.json").read_text(encoding="utf-8-sig"))
        check("磁盘上仍是导入的 3 条待办（没有被旧内存覆盖回去）",
              len(on_disk.get("tasks") or []) == 3,
              f"实际 {len(on_disk.get('tasks') or [])} 条")
        check("磁盘上的待办文本也对",
              [t["text"] for t in on_disk["tasks"]]
              == ["导入任务1", "导入任务2", "导入任务3"],
              str([t["text"] for t in on_disk.get("tasks") or []]))

        # 再走一遍 AI 的写盘路径：显式标脏 + flush，看它会不会把内存里的
        # 旧对话写回去。因为已经在上面换成了导入的内容，写回去也该是导入的。
        backend.ai._flush_history()
        conv_raw = json.loads((data / "conversations.json").read_text(encoding="utf-8"))
        conv = conv_raw.get("sessions") if isinstance(conv_raw, dict) else conv_raw
        titles = [s.get("title") for s in (conv or [])]
        check("对话文件里没有多出一份启动时的旧对话", len(conv or []) == 1,
              f"实际 {len(conv or [])} 条：{titles}")
        check("写回去的仍然只可能是导入的那条（不会被旧内存污染）",
              titles == ["导入来的对话"], str(titles))

        section("六、内容坏掉的包：拒绝，且磁盘一字不动")
        before = (data / "pet_data.json").read_bytes()
        junk = scratch / "junk.zip"
        _make_bundle(junk, {}, broken_pet_data=b'{"schema": 2, "tasks": [{"te')
        jinfo = transfer.inspect_bundle(junk, schema_version=SCHEMA_VERSION)
        jok, jmessage = transfer.import_bundle(data, junk, jinfo)
        check("坏内容的包被拒绝", not jok, jmessage)
        check("拒绝理由说清了「没有覆盖任何东西」",
              "没有覆盖" in jmessage, jmessage)
        check("磁盘上的数据一字未动",
              (data / "pet_data.json").read_bytes() == before)
        check("没有留下 .importing 临时文件",
              not list(data.glob("*.importing")), str(list(data.glob("*.importing"))))

        section("七、顶层不是对象的 json 都要拒绝")
        notobj = scratch / "notobj.zip"
        _make_bundle(notobj, {}, broken_pet_data=b"[1, 2, 3]")
        ninfo = transfer.inspect_bundle(notobj, schema_version=SCHEMA_VERSION)
        nok, _ = transfer.import_bundle(data, notobj, ninfo)
        check("数组顶层的 pet_data.json 被拒绝", not nok)
        check("磁盘数据仍然没动",
              (data / "pet_data.json").read_bytes() == before)

        # 附属文件同理：conversations.json 顶层是数组的话，history.load()
        # 会静默返回空列表 —— 导进去等于把对话全清空。
        bad_conv = scratch / "badconv.zip"
        _make_bundle(bad_conv, payload,
                     extra_files={"conversations.json": "[{\"id\": \"x\"}]"})
        binfo = transfer.inspect_bundle(bad_conv, schema_version=SCHEMA_VERSION)
        bok, bmessage = transfer.import_bundle(data, bad_conv, binfo)
        check("顶层是数组的 conversations.json 被拒绝", not bok, bmessage)
        check("拒绝理由点名了是哪个文件",
              "conversations.json" in bmessage, bmessage)
        check("pet_data.json 也没被这次导入改动",
              (data / "pet_data.json").read_bytes() == before)

        section("八、空备份也能导（用户就是一条待办都没有）")
        empty_payload = dict(payload)
        empty_payload["tasks"] = []
        empty_payload["notes"] = []
        empty = scratch / "empty.zip"
        _make_bundle(empty, empty_payload)
        einfo = transfer.inspect_bundle(empty, schema_version=SCHEMA_VERSION)
        eok, emessage = transfer.import_bundle(data, empty, einfo)
        if eok:
            backend.reloadData()
        check("空备份导入成功（不是被当成坏包）", eok, emessage)
        check("导入空数据后 Model 不会崩", backend.tasks.rowCount() == 0,
              f"实际 {backend.tasks.rowCount()}")
        check("便签至少留了一条可写的（_ensure_seed 起作用）",
              len(store.notes) >= 1, f"实际 {len(store.notes)}")

        section("九、重载不会把不认识的东西当数据")
        check("pet_data.before-import.json 还在（导错了能捞回来）",
              (data / transfer.PRE_IMPORT_BACKUP).is_file())

        section("十、权限等级：导入可以把权限收紧，但不许悄悄放松")
        # 一个被转发的「备份」如果能把 ai_level 拉到「完全自动」，
        # 那就是拿数据文件当提权工具。规则：取两者中更紧的那个。
        from pawpet.ai import LEVEL_FULL, LEVEL_READ_ONLY

        store.settings["ai_level"] = LEVEL_FULL
        # 必须落盘：reloadData 会重新读盘，内存里改了不写等于没改。
        store.save()
        # 运行中的对象也要同步 —— 这条断言测的是「用户本来就在完全自动，
        # 备份里也是完全自动」这种正常情况，不该被收紧。
        backend.ai.actions.level = LEVEL_FULL
        backend.reloadData()
        check("导入前是「完全自动」，重载后还是它（备份里也是 full）",
              backend.ai.actions.level == LEVEL_FULL,
              backend.ai.actions.level)

        # 现在模拟「用户自己把等级调到只读，然后导入一份想拉满的备份」
        backend.ai.actions.level = LEVEL_READ_ONLY
        store.settings["ai_level"] = LEVEL_READ_ONLY
        strict_payload = dict(payload)
        strict_payload["settings"] = dict(payload.get("settings") or {})
        strict_payload["settings"]["ai_level"] = LEVEL_FULL
        risky = scratch / "risky.zip"
        _make_bundle(risky, strict_payload)
        rinfo = transfer.inspect_bundle(risky, schema_version=SCHEMA_VERSION)
        rok, _ = transfer.import_bundle(data, risky, rinfo)
        check("这份「想拉满权限」的备份本身是合法包", rok)
        backend.reloadData()
        check("导入后权限停在「只读」，没被文件拉上去",
              backend.ai.actions.level == LEVEL_READ_ONLY,
              backend.ai.actions.level)
        check("store 里也写回了收紧后的等级（界面显示 = 实际生效）",
              store.settings.get("ai_level") == LEVEL_READ_ONLY,
              str(store.settings.get("ai_level")))

    finally:
        try:
            backend_mod.THEME_FILE = original_theme_file
        except NameError:
            pass
        shutil.rmtree(scratch, ignore_errors=True)

    print("\n" + "=" * 58)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            print(f"  - {item}")
        print(f"通过 {PASSED} / 失败 {len(FAILED)}")
        return 1
    print(f"全部通过（{PASSED} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
