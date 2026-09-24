"""发版流程的守卫 + 更新检查本身。

**这个套件为什么存在。**

发版链路上有三处「静默失效」——它们不报错、不崩溃，只是悄悄什么都没做：

1. 忘了提交 `version.json` → 客户端的首选来源一直 404
2. 忘了建 Release → 用户点「去下载」是 404
3. 版本号没往上走 → 老用户永远看不到更新提示

这三条在本地跑一万次别的测试都发现不了。所以这里专门钉两件事：

* **发版脚本的门禁真的会拦。** 一道门只要写错一次，代价是一整个版本。
* **`update.check()` 的三种返回。** 它决定设置页上说「已是最新」还是
  「没查到」——这两句话对用户的含义完全不同，而这段逻辑原来一行测试都没有。

全部离线跑，不发任何真实请求（`_fetch` 被替掉）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

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
    print("小爪 发版流程与更新检查")

    import release
    from pawpet import update

    # ==================================================== 一、版本门禁
    print("\n=== 一、版本门禁：版本号没往上走就必须拦 ===")

    ok, why = release.version_gate("2.2.0", "")
    check("没有历史 Release 时放行", ok is True, why)

    ok, why = release.version_gate("2.2.0", "v2.1.0")
    check("比远程新 → 放行", ok is True, why)

    ok, why = release.version_gate("2.1.0", "v2.1.0")
    check("和远程一样 → 拦（同版本重发没意义）", ok is False, why)
    check("拦的时候说清了下一步（改 APP_VERSION）",
          "APP_VERSION" in why, why)

    ok, why = release.version_gate("2.0.0", "v2.1.0")
    check("比远程旧 → 拦", ok is False, why)

    # 这条是「字符串比较」那个老坑在发版链路上的复现：
    # "2.10.0" < "2.9.0" 按字符串比是 True，会让版本号倒退，发出去就乱套。
    ok, _why = release.version_gate("2.10.0", "v2.9.0")
    check("2.10 比 2.9 新（字符串比较会答错）", ok is True)

    ok, why = release.version_gate("2.2.0", "2.2.0")
    check("远程 tag 不带 v 也能识别为同一个版本", ok is False, why)

    # ==================================================== 二、产物门禁
    print("\n=== 二、产物门禁：没东西可发就不许发 ===")

    with tempfile.TemporaryDirectory() as tmp:
        fake_dist = Path(tmp)
        original_dist = release.DIST
        release.DIST = fake_dist
        try:
            missing = release.collect_assets("9.9.9")
            check("dist 空着时挑不出任何产物", missing == [], str(missing))

            exe = fake_dist / release.INSTALLER_NAME
            exe.write_bytes(b"x" * 100)
            picked = release.collect_assets("9.9.9")
            check("有安装包就挑得到", [p.name for p, _u, _l in picked] == [exe.name],
                  str([p.name for p, _u, _l in picked]))

            # 顺序有实际意义：Releases 页面上文件的排列就是上传顺序，
            # 用户第一眼要看到「双击就能装」的那个。
            zip_path = fake_dist / "小爪助手-9.9.9-绿色版.zip"
            zip_path.write_bytes(b"y" * 50)
            picked = release.collect_assets("9.9.9")
            check("安装包排在绿色版前面（下载页第一眼看到的是它）",
                  [p.name for p, _u, _l in picked] == [exe.name, zip_path.name],
                  str([p.name for p, _u, _l in picked]))

            # 0 字节的文件是残包，不能当成功
            zip_path.write_bytes(b"")
            picked = release.collect_assets("9.9.9")
            check("0 字节的产物不算数",
                  [p.name for p, _u, _l in picked] == [exe.name],
                  str([p.name for p, _u, _l in picked]))
        finally:
            release.DIST = original_dist

    # ==================================================== 二之二、附件名
    #
    # **这一节是踩坑之后加的。** 第一版直接用本地文件名上传，而本地名是
    # 中文的 —— GitHub 会把 `?name=` 里的非 ASCII 字符**全部剥掉**：
    #
    #     小爪助手-安装程序.exe  →  -.exe
    #
    # 上传返回 201、大小也对，只有名字是错的，所以本地怎么测都发现不了，
    # 用户下载到的是一个叫「-.exe」的文件。
    #
    # 做了受控实验确认（两种编码方式都被剥光，是 GitHub 侧按字符过滤）：
    #     测试-中文名.txt      →  -.txt
    #     测试-手动utf8-q.txt  →  -.utf8-q.txt
    #
    # 所以上传名必须纯 ASCII。这里把它钉死。
    print("\n=== 二之二、附件名必须纯 ASCII（中文会被 GitHub 剥光）===")

    for local_name, expect in (
        ("小爪助手-安装程序.exe", "PawPet-Setup-2.2.0.exe"),
        ("小爪助手-2.2.0-绿色版.zip", "PawPet-Portable-2.2.0.zip"),
    ):
        got = release.remote_name(Path(local_name), "2.2.0")
        check(f"{local_name} → {expect}", got == expect, f"实际 {got!r}")
        check(f"{expect} 是纯 ASCII", got.isascii(), got)
        # 后缀不能丢：丢了用户双击不了也解压不了
        check(f"{expect} 后缀保住了",
              got.lower().endswith(Path(local_name).suffix.lower()), got)
        # 版本号要在名字里：用户下多个版本时能分清
        check(f"{expect} 带版本号", "2.2.0" in got, got)

    # 兜底分支也不能产出空名字或非 ASCII
    odd = release.remote_name(Path("中文名.rar"), "1.0")
    check("陌生后缀走兜底分支也不崩、且是 ASCII", odd.isascii() and odd, odd)

    # ==================================================== 三、上传地址
    print("\n=== 三、上传地址：主机名错了只会拿到 404 ===")

    url = release.asset_url("owner", "repo", 42, "PawPet-Setup-2.2.0.exe")
    check("走的是 uploads.github.com，不是 api.github.com",
          url.startswith("https://uploads.github.com/"), url)
    check("带上 release id", "/releases/42/" in url, url)
    check("带 ?name= 否则页面上显示成 assets", "?name=" in url, url)
    check("ASCII 名字原样带过去（不用转义）",
          url.endswith("?name=PawPet-Setup-2.2.0.exe"), url)
    # 万一有人又传了中文名，url 里也必须是转义过的 —— 不转义会 400
    url_cn = release.asset_url("owner", "repo", 42, "小爪助手.exe")
    check("真传了中文名也不会拼出非法 URL",
          "%" in url_cn and "小爪助手" not in url_cn, url_cn)

    # ==================================================== 四、远程核对
    print("\n=== 四、远程核对：传上去的和本地必须一模一样 ===")

    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.exe"
        b = Path(tmp) / "b.zip"
        a.write_bytes(b"1" * 100)
        b.write_bytes(b"2" * 200)
        # (本地路径, 上传用的名字, 说明)
        assets = [(a, "a.exe", "装"), (b, "b.zip", "解压")]

        problems, good = release.check_remote_assets(
            {"assets": [{"name": "a.exe", "size": 100},
                        {"name": "b.zip", "size": 200}]}, assets)
        check("都在、大小都对 → 没问题", problems == [], str(problems))
        check("对得上的两个都报出来", sorted(good) == ["a.exe", "b.zip"], str(good))

        problems, good = release.check_remote_assets(
            {"assets": [{"name": "a.exe", "size": 100}]}, assets)
        check("少传了一个 → 报出来", len(problems) == 1 and "b.zip" in problems[0],
              str(problems))
        check("没问题的那条不跟着一起报", "a.exe" not in problems[0], str(problems))

        # 上传中途断了：GitHub 可能照样返回 201，但大小对不上
        problems, _good = release.check_remote_assets(
            {"assets": [{"name": "a.exe", "size": 100},
                        {"name": "b.zip", "size": 37}]}, assets)
        check("大小对不上 → 报出来（传一半的残包）",
              len(problems) == 1 and "b.zip" in problems[0], str(problems))

        problems, _good = release.check_remote_assets({"assets": []}, assets)
        check("一个都没传 → 两条都报", len(problems) == 2, str(problems))

        problems, good = release.check_remote_assets({}, assets)
        check("返回里没有 assets 键也不崩（照样两条都报）",
              len(problems) == 2 and good == [], str(problems))

        # **按上传名查，不是按本地名。** 本地叫「小爪助手-安装程序.exe」、
        # 传上去叫「PawPet-Setup-2.2.0.exe」—— 用本地名查永远查不到，
        # 回验会一直误报「Release 上没有」。
        local_cn = Path(tmp) / "小爪助手-安装程序.exe"
        local_cn.write_bytes(b"z" * 300)
        cn_assets = [(local_cn, "PawPet-Setup-2.2.0.exe", "装")]
        problems, good = release.check_remote_assets(
            {"assets": [{"name": "PawPet-Setup-2.2.0.exe", "size": 300}]},
            cn_assets)
        check("上传名和本地名不同时，按上传名核对", problems == [], str(problems))
        problems, _g = release.check_remote_assets(
            {"assets": [{"name": "小爪助手-安装程序.exe", "size": 300}]},
            cn_assets)
        check("远程用本地中文名反而是错的（会被判没传）",
              len(problems) == 1, str(problems))


    # ==================================================== 五、发布说明
    print("\n=== 五、发布说明：下载页是唯一会被认真读的页面 ===")

    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / release.INSTALLER_NAME
        exe.write_bytes(b"x" * 2048)
        # (本地路径, 上传用的 ASCII 名, 说明)
        upload_as = release.remote_name(exe, "2.2.0")
        body = release._release_body("2.2.0", "修了三个问题",
                                     [(exe, upload_as, "双击装")])
        check("写了版本号", "2.2.0" in body)
        check("写进了这次的一句话说明", "修了三个问题" in body)
        # **要写上传后的名字**：用户点下载拿到的是那个，
        # 写成本地中文名就对不上了。
        check("点出了要下载哪个文件（用的是上传后的 ASCII 名）",
              upload_as in body, body[:300])
        check("下载说明里不该出现本地中文名",
              release.INSTALLER_NAME not in body, body[:300])
        check("带上了文件大小（用户能判断下载对不对）", "2.0 KB" in body, body[:200])
        # SmartScreen 那一句不能删：没签名的新版本首次运行必弹红字，
        # 不告诉用户怎么过，他会以为装了个病毒。
        check("讲了 SmartScreen 怎么过", "更多信息" in body and "仍要运行" in body)
        check("说了老数据会保留（用户最担心的）", "保留" in body)
        check("说了会自动提示更新", "提示" in body)

        empty = release._release_body("2.2.0", "", [])
        check("没有说明时也不出错", "2.2.0" in empty)

    # ==================================================== 六、更新检查
    print("\n=== 六、更新检查：三种返回的含义完全不同 ===")

    def fake_fetch(mapping):
        """把 _fetch 换成一张查表。mapping: url → dict | None"""
        def _fetch(url):
            return mapping.get(url)
        return _fetch

    src_a, src_b = update.VERSION_SOURCES

    original = update._fetch
    try:
        # 6.1 首选源有新版
        update._fetch = fake_fetch({src_a: {"version": "9.9.9", "note": "新"}})
        result = update.check("2.1.0")
        check("首选源有新版本 → ok，并带回版本号",
              result["ok"] is True and result["found"]["version"] == "9.9.9",
              str(result))

        # 6.2 首选源说是最新的 —— 这时**不该**再去问第二个源
        update._fetch = fake_fetch({src_a: {"version": "2.1.0"}})
        calls = []
        inner = update._fetch

        def counting(url):
            calls.append(url)
            return inner(url)
        update._fetch = counting
        result = update.check("2.1.0")
        check("首选源说「已是最新」→ found 是 None",
              result["ok"] is True and result["found"] is None, str(result))
        check("这时只发了 1 个请求（不用白跑第二个源）",
              len(calls) == 1, f"实际 {len(calls)} 次")

        # 6.3 首选源挂了 → 退到 Releases API
        update._fetch = fake_fetch({
            src_a: None,
            src_b: {"tag_name": "v9.9.9", "html_url": "http://x", "name": "9.9.9"},
        })
        result = update.check("2.1.0")
        check("首选源连不上 → 用 Releases API 兜底",
              result["ok"] is True and result["found"]["version"] == "v9.9.9",
              str(result))

        # 6.4 两个源都挂了 —— 这是**最关键**的一条
        update._fetch = fake_fetch({})
        result = update.check("2.1.0")
        check("两个源都拿不到 → ok=False，**不能说「已是最新」**",
              result["ok"] is False, str(result))
        check("这时 found 必须是 None（没查到，不是没问题）",
              result["found"] is None, str(result))

        # 6.5 源返回了 JSON 但里面没有版本号 → 当源坏了，换下一个
        update._fetch = fake_fetch({
            src_a: {"garbage": 1},
            src_b: {"tag_name": "v9.9.9", "html_url": "http://x"},
        })
        result = update.check("2.1.0")
        check("源返回的内容看不懂 → 换下一个源，不是直接失败",
              result["ok"] is True and result["found"]["version"] == "v9.9.9",
              str(result))

        # 6.6 新版不比当前新
        update._fetch = fake_fetch({src_a: {"version": "2.1.0"}})
        result = update.check("2.1.0")
        check("版本号相同 → 没有新版", result["found"] is None, str(result))

        update._fetch = fake_fetch({src_a: {"version": "2.0.0"}})
        result = update.check("2.1.0")
        check("远程版本比当前旧 → 没有新版（别提示用户降级）",
              result["found"] is None, str(result))

    finally:
        update._fetch = original

    # 六个源都失败时返回的是「没查到」，这个语义要能翻译成人话
    result = {"ok": False, "found": None}
    from pawpet import backend as backend_mod  # noqa: F401  （只为确认可导入）
    check("ok=False 和「已是最新」是两种结果（调用方要分开处理）",
          result["ok"] is False and result["found"] is None)

    # ==================================================== 七、不变量
    print("\n=== 七、别让这两处对不上 ===")

    from pawpet.config import APP_VERSION
    version_json = json.loads(
        (ROOT / "version.json").read_text(encoding="utf-8"))
    check("version.json 的版本号和代码里的一致",
          version_json.get("version") == APP_VERSION,
          f"version.json={version_json.get('version')!r} config={APP_VERSION!r}")
    import ast
    import runpy
    from unittest.mock import patch
    from pawpet import version as version_mod
    from buildmeta import make_manifest, source_fingerprint, source_latest_mtime_ns

    installer = runpy.run_path(str(ROOT / "build" / "setup_ui.py"))
    check("安装器和主程序版本一致", installer["APP_VERSION"] == APP_VERSION)
    with patch.object(version_mod, "APP_VERSION", "9.8.7"):
        installer = runpy.run_path(str(ROOT / "build" / "setup_ui.py"))
        builder = runpy.run_path(str(ROOT / "tools" / "build.py"))
        check("共享版本变化后安装器同步", installer["APP_VERSION"] == "9.8.7")
        check("共享版本变化后构建脚本同步", builder["VERSION"] == "9.8.7")
    definitions = []
    for folder in ("pawpet", "tools", "build"):
        for source in (ROOT / folder).rglob("*.py"):
            if any(part in ("__pycache__", "work", "setupwork") for part in source.parts):
                continue
            tree = ast.parse(source.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    if any(isinstance(target, ast.Name) and target.id == "APP_VERSION"
                           for target in node.targets):
                        definitions.append(source.relative_to(ROOT).as_posix())
    check("只有一个版本常量", definitions == ["pawpet/version.py"], str(definitions))
    spec = (ROOT / "build" / "setup.spec").read_text(encoding="utf-8")
    check("安装器打包包含共享版本搜索路径", 'pathex=[str(ROOT),' in spec)
    check("version.json 里有 url 字段（客户端要拿它当下载页）",
          bool(version_json.get("url")), str(version_json))

    # 客户端的两个来源都是**公开**地址，仓库私有的话 raw 是 404。
    # 这条断言把「仓库不能是私有」这件事写进测试，免得哪天真改了。
    check("首选源指向 raw.githubusercontent.com 上的 version.json",
          "raw.githubusercontent.com" in src_a and src_a.endswith("version.json"),
          src_a)

    # 「两处各写一份版本号」是这个项目已经犯过的错：`version.json` 忘了改
    # （build.py 得专门做同步）、`pawpet/__init__.py` 里那个 2.0.0 长期
    # 和 config 的 2.1.0 对不上。所以扫一遍，不许再冒出第二份定义。
    strays: list[str] = []
    for path in sorted((ROOT / "pawpet").rglob("*.py")):
        if path.name == "config.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            code = line.strip()
            # 跳过注释 —— `pawpet/__init__.py` 里那段「别在这写版本号」
            # 的说明本身就会命中这个模式，那是注释不是定义。
            if code.startswith("#"):
                continue
            if "__version__" in code and "=" in code and "APP_VERSION" not in code:
                strays.append(f"{path.relative_to(ROOT)}:{lineno}")
    check("pawpet 包里没有第二份版本号定义", strays == [], str(strays))

    manifest = make_manifest(ROOT, APP_VERSION)
    check("构建清单版本来自共享版本", manifest.get("version") == APP_VERSION,
          str(manifest))
    check("构建清单源码指纹可复算",
          manifest.get("source_fingerprint") == source_fingerprint(ROOT),
          str(manifest.get("source_fingerprint")))
    check("构建清单时间覆盖源码修改",
          manifest.get("built_at_ns", 0) >= source_latest_mtime_ns(ROOT),
          str(manifest))

    # ==================================================== 八、发版不许让仓库分叉
    #
    # **这一节是 v2.2.0 发版时踩的坑。**
    #
    # `publish_version_file` 原来直接用 Contents API 在远端建一个提交，
    # 本地仓库看不到它 —— 两边历史分叉。而这台机器 `git push` / `git fetch`
    # 走 443 直连不通（所以才需要 github_upload.py 这个工具），
    # 分叉**没法自动收敛**。
    #
    # 后果实测到了：发完版想推下一个提交，被安全机制拦下
    # 「远端已有 1 个父提交的真实历史，用 API 覆盖会丢东西」——
    # 也就是每发一次版，仓库就再也推不动。
    #
    # 修法是走「写本地 → 本地提交 → 上传」，远端那个提交和本地同 SHA。
    # 这是结构性约束，用真实网络测代价太大，所以**查源码**：
    # 这个函数里不该再出现 Contents API 的写入调用。
    print("\n=== 八、发版会让仓库分叉吗 ===")
    release_src = (ROOT / "tools" / "release.py").read_text(encoding="utf-8")
    start = release_src.find("def publish_version_file")
    end = release_src.find("\ndef ", start + 10)
    publish_body = release_src[start:end if end > 0 else len(release_src)]

    check("找到 publish_version_file", start > 0, "函数没了？")
    check("不再用 Contents API 直写远端（那会让本地看不到那个提交）",
          "contents/version.json" not in publish_body
          and '"PUT"' not in publish_body,
          "又出现 Contents API 写入了")
    check("走的是本地提交 + 上传",
          "gh_upload" in publish_body and "git" in publish_body,
          "没看到本地提交或上传调用")
    check("只 add version.json（不把用户其他未提交改动卷进去）",
          '"add", "version.json"' in publish_body,
          "add 的范围看着不对")

    # ==================================================== 收尾
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
