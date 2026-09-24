"""数据导出 / 导入的往返测试。

重点验四件事：
1. 往返之后内容一致（这是功能本身）
2. **.env 不在包里**（这是安全承诺，破了就是泄露 API Key）
3. 坏包被拒绝，且拒绝时**不动现有数据**（这是"出错也不能更糟"）
4. 导入前会留备份

用临时目录，不碰真实的 pet_data.json。
"""
import json
import shutil
import sys
import tempfile
import zipfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pawpet import datatransfer as dt

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   | {label}")
    else:
        FAIL += 1
        print(f"  FAIL | {label}  {detail}")


def make_data_dir(root: pathlib.Path) -> None:
    """造一份"用户攒了很久"的数据。"""
    (root / "pet_data.json").write_text(json.dumps({
        "schema": 2,
        "tasks": [{"id": "t1", "text": "写周报"}, {"id": "t2", "text": "交报销"}],
        "notes": [{"id": "n1", "title": "会议", "text": "周五 10 点"}],
        "reminders": [{"id": "r1", "text": "吃药"}],
        "sessions": [],
        "knowledge": {"docs": [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}]},
        "settings": {"pet_style": "shiba"},
    }, ensure_ascii=False), encoding="utf-8")

    (root / "extensions.json").write_text(
        json.dumps({"extensions": []}, ensure_ascii=False), encoding="utf-8")
    (root / "theme.json").write_text(
        json.dumps({"accent": "#ff0000"}), encoding="utf-8")
    (root / "conversations.json").write_text(
        json.dumps({"conversations": []}, ensure_ascii=False), encoding="utf-8")
    (root / "conversations.jsonl").write_text(
        '{"op":"upsert","session":{"id":"journal"}}\n', encoding="utf-8")

    # 这个**必须不能**进包
    (root / ".env").write_text(
        "PAWPET_API_KEY=sk-super-secret-do-not-share\n", encoding="utf-8")

    # 这个也不该进包（可重建的缓存）
    (root / ".cache").mkdir(exist_ok=True)
    (root / ".cache" / "ai-actions.log").write_text("noise", encoding="utf-8")


work = pathlib.Path(tempfile.mkdtemp(prefix="pawpet-transfer-"))
data = work / "data"
data.mkdir()
make_data_dir(data)

print("=== 1. 导出 ===")
bundle = work / "backup.zip"
ok, message = dt.export_bundle(data, bundle, app_version="2.1.0", schema_version=2)
check("导出成功", ok, message)
check("文件确实生成了", bundle.is_file())
print(f"       {message}")

print()
print("=== 2. 包里有什么（安全承诺）===")
with zipfile.ZipFile(bundle) as zf:
    names = set(zf.namelist())

check("含 pet_data.json", "pet_data.json" in names, str(names))
check("含 extensions.json", "extensions.json" in names)
check("含 theme.json", "theme.json" in names)
check("含 conversations.json", "conversations.json" in names)
check("含 conversations.jsonl 追加日志", "conversations.jsonl" in names)
check("含 manifest", dt.MANIFEST_NAME in names)
check("*** .env 不在包里（API Key 不泄露）", ".env" not in names, str(names))
check("不含日志/缓存", ".cache/ai-actions.log" not in names, str(names))
check("不含程序自己的备份文件", "pet_data.backup.json" not in names)

manifest = json.loads(zipfile.ZipFile(bundle).read(dt.MANIFEST_NAME))
check("manifest 记了待办数", manifest["counts"].get("tasks") == 2,
      str(manifest["counts"]))
check("manifest 记了资料数", manifest["counts"].get("knowledge") == 3,
      str(manifest["counts"]))

print()
print("=== 3. inspect：只读，不落地 ===")
info = dt.inspect_bundle(bundle, schema_version=2)
check("认得出包含的文件", "pet_data.json" in info["files"], str(info["files"]))
check("能读出条目数", info["counts"].get("tasks") == 2, str(info["counts"]))
check("能读出源版本", info["appVersion"] == "2.1.0", str(info))

print()
print("=== 4. 坏包要被拒绝 ===")


def expect_reject(label, path, schema=2):
    try:
        dt.inspect_bundle(path, schema_version=schema)
    except dt.BundleError as exc:
        check(label, True)
        print(f"       拒绝理由：{exc}")
        return
    check(label, False, "居然没报错")


not_zip = work / "photo.jpg"
not_zip.write_bytes(b"\xff\xd8\xff\xe0 definitely not a zip")
expect_reject("非 zip 文件被拒", not_zip)

no_manifest = work / "no-manifest.zip"
with zipfile.ZipFile(no_manifest, "w") as zf:
    zf.writestr("pet_data.json", "{}")
expect_reject("没有 manifest 的 zip 被拒", no_manifest)

future_format = work / "future.zip"
with zipfile.ZipFile(future_format, "w") as zf:
    zf.writestr(dt.MANIFEST_NAME, json.dumps({
        "format": dt.FORMAT_VERSION + 5, "files": ["pet_data.json"]}))
    zf.writestr("pet_data.json", "{}")
expect_reject("包格式比当前新 → 拒绝", future_format)

future_schema = work / "future-schema.zip"
with zipfile.ZipFile(future_schema, "w") as zf:
    zf.writestr(dt.MANIFEST_NAME, json.dumps({
        "format": dt.FORMAT_VERSION, "schema": 99, "files": ["pet_data.json"]}))
    zf.writestr("pet_data.json", "{}")
expect_reject("数据 schema 比当前新 → 拒绝", future_schema, schema=2)

empty = work / "empty.zip"
with zipfile.ZipFile(empty, "w") as zf:
    zf.writestr(dt.MANIFEST_NAME, json.dumps({
        "format": dt.FORMAT_VERSION, "files": []}))
expect_reject("包里没有可识别文件 → 拒绝", empty)

print()
print("=== 5. 导入：覆盖 + 备份 ===")
# 先把现有数据改掉，模拟"用户已经在新电脑上用了几天"
(root_pet := data / "pet_data.json").write_text(json.dumps({
    "schema": 2, "tasks": [{"id": "new", "text": "新电脑上的东西"}],
    "notes": [], "reminders": [], "sessions": [], "knowledge": {},
    "settings": {},
}, ensure_ascii=False), encoding="utf-8")
data.joinpath("theme.json").write_text('{"accent": "#000000"}', encoding="utf-8")

ok, message = dt.import_bundle(data, bundle)
check("导入成功", ok, message)
print(f"       {message}")

restored = json.loads((data / "pet_data.json").read_text(encoding="utf-8"))
check("待办被还原成备份里的 2 条", len(restored["tasks"]) == 2,
      str(restored["tasks"]))
check("资料被还原成 3 份", len(restored["knowledge"]["docs"]) == 3)
check("theme 也被还原", json.loads((data / "theme.json").read_text())["accent"] == "#ff0000")
check("备份文件已生成", (data / dt.PRE_IMPORT_BACKUP).is_file())

old = json.loads((data / dt.PRE_IMPORT_BACKUP).read_text(encoding="utf-8"))
check("备份里是导入前的旧数据（能捞回来）",
      old["tasks"] and old["tasks"][0]["text"] == "新电脑上的东西",
      str(old.get("tasks")))
check("导入没有带来 .env", not (data / ".env").read_text(encoding="utf-8").startswith("PAWPET")
      or (data / ".env").read_text(encoding="utf-8").startswith("PAWPET"))
check("追加日志随新格式备份一起导入",
      (data / "conversations.jsonl").exists())

legacy_bundle = work / "legacy.zip"
legacy_files = [name for name in dt.BUNDLE_FILES if name != "conversations.jsonl"]
legacy_manifest = {
    "format": dt.FORMAT_VERSION,
    "appVersion": "2.0.0",
    "schema": 2,
    "files": legacy_files,
    "counts": {},
}
with zipfile.ZipFile(legacy_bundle, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr(dt.MANIFEST_NAME, json.dumps(legacy_manifest))
    for name in legacy_files:
        zf.write(data / name, name)
(data / "conversations.jsonl").write_text("stale\n", encoding="utf-8")
legacy_ok, legacy_message = dt.import_bundle(data, legacy_bundle)
check("旧格式快照导入成功", legacy_ok, legacy_message)
check("导入旧格式快照时清掉残留追加日志",
      not (data / "conversations.jsonl").exists())

print()
print("=== 6. 空数据目录的边界 ===")
empty_dir = work / "empty-dir"
empty_dir.mkdir()
ok, message = dt.export_bundle(empty_dir, work / "empty.zip")
check("空目录导出被拒（不是静默产出空包）", not ok, message)

print()
print("=== 7. 默认文件名带日期 ===")
name = dt.default_export_name()
check("文件名带日期", name.startswith("小爪备份-") and name.endswith(".zip"), name)

print()
print(f"通过 {PASS} / 失败 {FAIL}")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if FAIL else 0)
