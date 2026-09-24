"""小爪助手：PySide6 + Qt Quick 桌面宠物 / 个人工作台。"""

# **不要在这里写版本号。**
#
# 版本号只有一个来源：`pawpet/version.py` 的 `APP_VERSION`。设置页显示的、
# 托盘提示里的、发版脚本读的、`version.json` 对齐的，全是它。
#
# 这里原来硬编码着 `__version__`，而其他地方的版本号已经变化 ——
# 一行调用方都没有，唯一的实际效果是**误导**：发版时看到这里写着 2.0.0，
# 会以为版本号还没改。这种「两处各写一份、改一处忘一处」正是
# `tools/build.py` 要费力去同步 `version.json` 的原因（见那儿的注释），
# 所以这里不再留第二份。
#
# 要读版本号：`from pawpet.config import APP_VERSION`。
# 不在这里转发的原因：config 在模块级会做数据目录解析（_resolve_data_dir），
# 让它挂到 `import pawpet` 上太重了。
