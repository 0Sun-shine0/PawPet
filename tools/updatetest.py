"""版本号比较的单元测试。

这个函数写错的话，表现是「用户永远看不到新版」—— 不会报错、不会被
发现，只是悄悄失效。所以必须逐条验。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pawpet.update import is_newer, parse_version

PASS = 0
FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   | {label}")
    else:
        FAIL += 1
        print(f"  FAIL | {label}  got={got!r} want={want!r}")


print("=== parse_version ===")
check("普通三段", parse_version("2.1.0"), (2, 1, 0))
check("带 v 前缀", parse_version("v2.1.0"), (2, 1, 0))
check("大写 V", parse_version("V2.1.0"), (2, 1, 0))
check("两段", parse_version("2.1"), (2, 1))
check("带预发布后缀", parse_version("2.1.0-beta.1"), (2, 1, 0))
check("空串", parse_version(""), ())
check("垃圾输入", parse_version("abc"), ())
check("纯数字", parse_version("3"), (3,))

print()
print("=== is_newer：这是关键的一组 ===")
check("2.10 比 2.9 新（字符串比较会答错）", is_newer("2.10.0", "2.9.0"), True)
check("2.9 不比 2.10 新", is_newer("2.9.0", "2.10.0"), False)
check("相同", is_newer("2.1.0", "2.1.0"), False)
check("位数不齐视为相同", is_newer("2.2", "2.2.0"), False)
check("大版本更高", is_newer("3.0.0", "2.99.99"), True)
check("补零后比较：2.2.0 vs 2.1.9", is_newer("2.2.0", "2.1.9"), True)
check("补零后比较：2.1.0 vs 2.1", is_newer("2.10", "2.1"), True)
check("带 v 前缀", is_newer("v2.2.0", "2.1.0"), True)
check("新且带预发布", is_newer("2.2.0-rc1", "2.1.0"), True)
check("解析不了时保守返回 False", is_newer("", "2.1.0"), False)
check("旧值解析不了也返回 False", is_newer("2.2.0", ""), False)
check("两位数小版本 10 vs 2", is_newer("1.10", "1.9"), True)

print()
print(f"通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
